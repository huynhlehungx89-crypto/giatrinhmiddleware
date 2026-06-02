import logging
import re
from typing import Dict, List, Tuple

import database
from core.auth import slugify_vietnamese

logger = logging.getLogger(__name__)

_USERNAME_SUFFIX_RE = re.compile(r"^(.+)_(\d+)$")


def is_blocked_username_slug(slug: str) -> bool:
    """Never auto-create accounts that collide with admin naming."""
    if not slug:
        return True
    return slug == "admin" or slug.startswith("admin")


def deduplicate_employees_by_slug(
    employees: List[Dict],
) -> Tuple[List[Dict], int]:
    """
    One Odoo employee per slugified name (lowest hr.employee id wins).
    Returns (unique_employees, duplicate_count_skipped).
    """
    by_slug: Dict[str, Dict] = {}
    skipped = 0

    for emp in employees:
        slug = slugify_vietnamese(emp.get("name", ""))
        if not slug:
            continue

        if slug not in by_slug:
            by_slug[slug] = emp
            continue

        skipped += 1
        current = by_slug[slug]
        if emp["id"] < current["id"]:
            by_slug[slug] = emp

    if skipped:
        logger.info("Bỏ qua %s nhân viên trùng tên trong Odoo", skipped)

    return list(by_slug.values()), skipped


def user_has_import_batches(conn, user_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM import_batches WHERE uploaded_by = ? LIMIT 1",
        (user_id,),
    ).fetchone()
    return row is not None


def cleanup_duplicate_users() -> Dict:
    """
    Remove username_2 / username_3 rows when base username exists.
    Never deletes role=admin or users with import_batches history.
    """
    conn = database.get_db()
    deleted: List[str] = []
    warnings: List[str] = []
    to_delete: List[str] = []

    try:
        rows = conn.execute(
            "SELECT id, username, role FROM users ORDER BY username"
        ).fetchall()
        usernames = {r["username"] for r in rows}

        for row in rows:
            user_id = row["id"]
            username = row["username"]
            role = row["role"]

            if role == "admin":
                continue

            should_delete = False

            if username == "admin_2":
                should_delete = True
            else:
                match = _USERNAME_SUFFIX_RE.match(username)
                if not match:
                    continue
                base = match.group(1)
                if base not in usernames:
                    continue
                should_delete = True

            if not should_delete:
                continue

            if user_has_import_batches(conn, user_id):
                msg = f"Giữ tài khoản {username} (có lịch sử import)"
                warnings.append(msg)
                logger.warning(msg)
                continue

            to_delete.append(user_id)
            deleted.append(username)

        for user_id in to_delete:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

        conn.commit()
    finally:
        conn.close()

    if deleted:
        logger.info("Đã xóa %s tài khoản trùng: %s", len(deleted), ", ".join(deleted))

    return {
        "deleted": deleted,
        "deleted_count": len(deleted),
        "warnings": warnings,
    }


def format_sync_result_message(created: int, existed: int, odoo_dup_skipped: int) -> str:
    return (
        "✅ Đồng bộ hoàn tất:\n"
        f"- {created} tài khoản tạo mới\n"
        f"- {existed} đã tồn tại (bỏ qua)\n"
        f"- {odoo_dup_skipped} trùng tên trong Odoo (bỏ qua)"
    )


def format_cleanup_result_message(stats: Dict) -> str:
    lines = [f"🧹 Dọn dẹp hoàn tất: đã xóa {stats['deleted_count']} tài khoản trùng."]
    if stats["deleted"]:
        lines.append("Đã xóa: " + ", ".join(stats["deleted"][:20]))
        if len(stats["deleted"]) > 20:
            lines.append(f"... và {len(stats['deleted']) - 20} tài khoản khác")
    if stats["warnings"]:
        lines.append(f"Cảnh báo: {len(stats['warnings'])} tài khoản được giữ (có import).")
    return "\n".join(lines)
