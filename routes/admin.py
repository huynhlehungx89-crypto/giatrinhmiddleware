import logging
import uuid

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import APP_NAME
from core.auth import (
    DEFAULT_SYNC_PASSWORD,
    get_current_user,
    get_user_by_id,
    get_user_by_odoo_employee_id,
    get_user_by_username_any,
    hash_password,
    slugify_vietnamese,
)
from core.flash import clear_flash_cookie, get_flash, set_flash
from core.odoo_client import get_odoo_client_for_request
from core.odoo_employees import get_employee_list
from core.user_sync import (
    deduplicate_employees_by_slug,
    format_sync_result_message,
    is_blocked_username_slug,
)
import database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="templates")


def _require_admin_page(request: Request):
    user = get_current_user(request)
    if not user:
        return None, RedirectResponse(url="/login", status_code=302)
    if user.get("role") != "admin":
        return None, RedirectResponse(url="/upload", status_code=302)
    return user, None


def _list_users():
    conn = database.get_db()
    rows = conn.execute(
        """
        SELECT id, username, display_name, odoo_employee_name,
               role, is_active, odoo_employee_id, created_at
        FROM users
        ORDER BY role DESC, display_name COLLATE NOCASE, username
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _render(request: Request, template: str, user: dict, **ctx):
    response = templates.TemplateResponse(
        template,
        {
            "request": request,
            "app_name": APP_NAME,
            "user": user,
            "flash": get_flash(request),
            **ctx,
        },
    )
    if get_flash(request):
        clear_flash_cookie(response)
    return response


@router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request):
    admin, redirect = _require_admin_page(request)
    if redirect:
        return redirect
    return _render(
        request,
        "admin/users.html",
        admin,
        users=_list_users(),
    )


@router.post("/users/sync")
async def users_sync(request: Request):
    admin, redirect = _require_admin_page(request)
    if redirect:
        return redirect

    try:
        odoo_client = get_odoo_client_for_request()
        employees = get_employee_list(odoo_client)
    except Exception:
        resp = RedirectResponse(url="/admin/users", status_code=302)
        return set_flash(
            resp,
            "error",
            "Không thể kết nối Odoo. Kiểm tra cài đặt kết nối.",
        )

    if not employees:
        resp = RedirectResponse(url="/admin/users", status_code=302)
        return set_flash(
            resp,
            "warning",
            "Odoo không trả về nhân viên nào. Không có tài khoản nào được tạo.",
        )

    employees, odoo_dup_skipped = deduplicate_employees_by_slug(employees)

    created = 0
    existed = 0
    conn = database.get_db()
    default_hash = hash_password(DEFAULT_SYNC_PASSWORD)

    try:
        for emp in employees:
            emp_id = emp["id"]
            emp_name = emp["name"]

            linked = get_user_by_odoo_employee_id(emp_id)
            if linked:
                conn.execute(
                    """
                    UPDATE users
                    SET odoo_employee_id = ?,
                        odoo_employee_name = ?,
                        display_name = COALESCE(display_name, ?)
                    WHERE id = ?
                    """,
                    (emp_id, emp_name, emp_name, linked["id"]),
                )
                existed += 1
                continue

            base_slug = slugify_vietnamese(emp_name)
            if not base_slug:
                continue

            if is_blocked_username_slug(base_slug):
                existed += 1
                continue

            by_username = get_user_by_username_any(base_slug)
            if by_username:
                conn.execute(
                    """
                    UPDATE users
                    SET odoo_employee_id = ?,
                        odoo_employee_name = ?,
                        display_name = COALESCE(display_name, ?)
                    WHERE id = ?
                    """,
                    (emp_id, emp_name, emp_name, by_username["id"]),
                )
                existed += 1
                continue

            user_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, role, is_active,
                    odoo_employee_id, odoo_employee_name, display_name
                ) VALUES (?, ?, ?, 'user', 1, ?, ?, ?)
                """,
                (
                    user_id,
                    base_slug,
                    default_hash,
                    emp_id,
                    emp_name,
                    emp_name,
                ),
            )
            created += 1

        conn.commit()
    finally:
        conn.close()

    resp = RedirectResponse(url="/admin/users", status_code=302)
    return set_flash(
        resp,
        "success",
        format_sync_result_message(created, existed, odoo_dup_skipped),
    )


@router.post("/users/cleanup")
async def users_cleanup(request: Request):
    admin, redirect = _require_admin_page(request)
    if redirect:
        return redirect

    conn = database.get_db()
    deleted = 0
    try:
        rows = conn.execute(
            """
            SELECT id, username FROM users
            WHERE role != 'admin'
            AND (
                instr(username, '_2') > 0
                OR instr(username, '_3') > 0
                OR instr(username, '_4') > 0
                OR instr(username, '_5') > 0
            )
            """
        ).fetchall()

        for row in rows:
            user_id = row["id"]
            has_import = conn.execute(
                "SELECT 1 FROM import_batches WHERE uploaded_by = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if not has_import:
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                deleted += 1

        admin2 = conn.execute(
            "SELECT id FROM users WHERE username = 'admin_2' AND role != 'admin'"
        ).fetchone()
        if admin2:
            has_import = conn.execute(
                "SELECT 1 FROM import_batches WHERE uploaded_by = ? LIMIT 1",
                (admin2["id"],),
            ).fetchone()
            if not has_import:
                conn.execute("DELETE FROM users WHERE id = ?", (admin2["id"],))
                deleted += 1

        conn.commit()
    finally:
        conn.close()

    resp = RedirectResponse(url="/admin/users", status_code=302)
    return set_flash(resp, "success", f"Đã xóa {deleted} tài khoản trùng lặp")


@router.post("/users/{user_id}/reset-password")
async def user_reset_password(request: Request, user_id: str):
    admin, redirect = _require_admin_page(request)
    if redirect:
        return redirect

    target = get_user_by_id(user_id)
    if not target:
        resp = RedirectResponse(url="/admin/users", status_code=302)
        return set_flash(resp, "error", "Không tìm thấy người dùng.")

    if target.get("role") == "admin" and target.get("username") != admin.get("username"):
        resp = RedirectResponse(url="/admin/users", status_code=302)
        return set_flash(resp, "error", "Không thể reset mật khẩu tài khoản admin khác.")

    conn = database.get_db()
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(DEFAULT_SYNC_PASSWORD), user_id),
    )
    conn.commit()
    conn.close()

    resp = RedirectResponse(url="/admin/users", status_code=302)
    return set_flash(
        resp,
        "success",
        f"Đã reset mật khẩu cho {target.get('username')} về {DEFAULT_SYNC_PASSWORD}.",
    )


@router.get("/users/{user_id}/edit", response_class=HTMLResponse)
async def user_edit_page(request: Request, user_id: str):
    admin, redirect = _require_admin_page(request)
    if redirect:
        return redirect

    target = get_user_by_id(user_id)
    if not target:
        resp = RedirectResponse(url="/admin/users", status_code=302)
        return set_flash(resp, "error", "Không tìm thấy người dùng.")

    employees = []
    try:
        odoo_client = get_odoo_client_for_request()
        employees = get_employee_list(odoo_client)
    except Exception:
        pass

    return _render(
        request,
        "admin/user_edit.html",
        admin,
        edit_user=target,
        employees=employees,
        error=None,
    )


@router.post("/users/{user_id}/edit")
async def user_edit_save(
    request: Request,
    user_id: str,
    display_name: str = Form(""),
    role: str = Form("user"),
    new_password: str = Form(""),
    odoo_employee_id: str = Form(""),
):
    admin, redirect = _require_admin_page(request)
    if redirect:
        return redirect

    target = get_user_by_id(user_id)
    if not target:
        resp = RedirectResponse(url="/admin/users", status_code=302)
        return set_flash(resp, "error", "Không tìm thấy người dùng.")

    if target.get("role") == "admin" and target.get("id") != admin.get("id"):
        resp = RedirectResponse(url="/admin/users", status_code=302)
        return set_flash(resp, "error", "Không thể sửa tài khoản admin khác.")

    if role not in ("user", "admin"):
        role = "user"

    if target.get("username") == admin.get("username"):
        role = "admin"

    emp_id = None
    emp_name = target.get("odoo_employee_name")
    if odoo_employee_id and odoo_employee_id.strip():
        try:
            emp_id = int(odoo_employee_id)
            other = get_user_by_odoo_employee_id(emp_id)
            if other and other["id"] != user_id:
                return _render(
                    request,
                    "admin/user_edit.html",
                    admin,
                    edit_user=target,
                    employees=[],
                    error="Nhân viên Odoo đã được gán cho user khác.",
                )
            try:
                odoo_client = get_odoo_client_for_request()
                for e in get_employee_list(odoo_client):
                    if e["id"] == emp_id:
                        emp_name = e["name"]
                        break
            except Exception:
                pass
        except ValueError:
            emp_id = target.get("odoo_employee_id")

    conn = database.get_db()
    params = [
        display_name or target.get("odoo_employee_name") or target.get("username"),
        role,
        emp_name,
        emp_id,
    ]
    sql = """
        UPDATE users
        SET display_name = ?, role = ?,
            odoo_employee_name = ?, odoo_employee_id = ?
    """
    if new_password.strip():
        sql += ", password_hash = ?"
        params.append(hash_password(new_password.strip()))
    sql += " WHERE id = ?"
    params.append(user_id)
    conn.execute(sql, tuple(params))
    conn.commit()
    conn.close()

    resp = RedirectResponse(url="/admin/users", status_code=302)
    return set_flash(resp, "success", "Đã lưu thay đổi người dùng.")

