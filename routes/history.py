from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import APP_NAME
from core.auth import get_current_user
import database

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    conn = database.get_db()
    if user.get("role") == "admin":
        rows = conn.execute(
            """
            SELECT * FROM import_batches
            ORDER BY uploaded_at DESC
            LIMIT 50
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM import_batches
            WHERE uploaded_by = ?
            ORDER BY uploaded_at DESC
            LIMIT 50
            """,
            (user["id"],),
        ).fetchall()
    conn.close()

    batches = [dict(r) for r in rows]

    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "user": user,
            "batches": batches,
        },
    )
