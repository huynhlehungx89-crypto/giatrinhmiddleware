from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import APP_NAME
from core.auth import get_current_user
import database

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _get_settings_row():
    conn = database.get_db()
    row = conn.execute("SELECT * FROM odoo_settings WHERE id = 1").fetchone()
    conn.close()
    return dict(row) if row else {}


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.get("role") != "admin":
        return RedirectResponse(url="/upload", status_code=302)

    settings = _get_settings_row()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "user": user,
            "settings": settings,
            "success": request.query_params.get("success"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/settings")
async def settings_save(
    request: Request,
    url: str = Form(...),
    database_name: str = Form(...),
    username: str = Form(...),
    api_key: str = Form(...),
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if user.get("role") != "admin":
        return RedirectResponse(url="/upload", status_code=302)

    conn = database.get_db()
    existing = conn.execute("SELECT id FROM odoo_settings WHERE id = 1").fetchone()
    if existing:
        conn.execute(
            """
            UPDATE odoo_settings
            SET url = ?, database = ?, username = ?, api_key = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
            (url.strip(), database_name.strip(), username.strip(), api_key.strip()),
        )
    else:
        conn.execute(
            """
            INSERT INTO odoo_settings (id, url, database, username, api_key)
            VALUES (1, ?, ?, ?, ?)
            """,
            (url.strip(), database_name.strip(), username.strip(), api_key.strip()),
        )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/settings?success=1", status_code=302)
