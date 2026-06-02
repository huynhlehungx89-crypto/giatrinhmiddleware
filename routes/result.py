from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import APP_NAME
from core.auth import get_current_user
from core.odoo_client import get_odoo_settings
import database

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _load_batch(batch_id: str):
    conn = database.get_db()
    batch = conn.execute(
        "SELECT * FROM import_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if not batch:
        conn.close()
        return None, []
    orders = conn.execute(
        "SELECT * FROM import_orders WHERE batch_id = ? ORDER BY order_ref",
        (batch_id,),
    ).fetchall()
    conn.close()
    return dict(batch), [dict(o) for o in orders]


@router.get("/result/{batch_id}", response_class=HTMLResponse)
async def result_page(request: Request, batch_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    batch, orders = _load_batch(batch_id)
    if not batch:
        return RedirectResponse(url="/history", status_code=302)

    batch_status = batch.get("status", "")
    if batch_status == "IMPORTED":
        header = "✅ Import hoàn tất"
    elif batch_status == "PARTIAL":
        header = "⚠️ Import một phần"
    else:
        header = "❌ Import thất bại"

    success_orders = [
        o for o in orders if o.get("status") in ("SUCCESS", "PARTIAL_SUCCESS")
    ]
    failed_orders = [o for o in orders if o.get("status") == "FAILED"]
    skipped_orders = [o for o in orders if o.get("status") == "SKIPPED"]

    settings = get_odoo_settings() or {}
    odoo_url = (settings.get("url") or "").rstrip("/")

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "user": user,
            "batch": batch,
            "header": header,
            "success_orders": success_orders,
            "failed_orders": failed_orders,
            "skipped_orders": skipped_orders,
            "success_count": batch.get("success_orders") or len(success_orders),
            "skipped_count": batch.get("skipped_orders") or len(skipped_orders),
            "failed_count": batch.get("failed_orders") or len(failed_orders),
            "odoo_url": odoo_url,
        },
    )
