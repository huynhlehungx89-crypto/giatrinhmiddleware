import csv
import io
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from config import APP_NAME
from core.auth import get_current_user
import database

router = APIRouter()
templates = Jinja2Templates(directory="templates")

SKIPPED_REASON = (
    "Đơn hàng này đã được import trước đó (mã SO đã tồn tại trong Odoo)"
)
SKIPPED_TOOLTIP = (
    "Đơn đã tồn tại trong Odoo, hệ thống bỏ qua để tránh trùng lặp"
)
SKIPPED_SUMMARY_TOOLTIP = "Đã tồn tại trong Odoo"


def _enrich_orders_for_display(orders: list) -> list:
    """Ensure SKIPPED rows show a friendly reason in the preview table."""
    enriched = []
    for order in orders:
        row = dict(order)
        if row.get("status") == "SKIPPED" and not (row.get("error_message") or "").strip():
            row["error_message"] = SKIPPED_REASON
        enriched.append(row)
    return enriched


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


@router.get("/preview/{batch_id}", response_class=HTMLResponse)
async def preview_page(request: Request, batch_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    batch, orders = _load_batch(batch_id)
    if not batch:
        return RedirectResponse(url="/upload", status_code=302)

    batch_imported = batch.get("status") in ("IMPORTED", "PARTIAL", "FAILED")
    valid_count = batch.get("valid_orders") or 0
    orders = _enrich_orders_for_display(orders)

    return templates.TemplateResponse(
        "preview.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "user": user,
            "batch": batch,
            "orders": orders,
            "batch_imported": batch_imported,
            "valid_count": valid_count,
            "skipped_reason": SKIPPED_REASON,
            "skipped_tooltip": SKIPPED_TOOLTIP,
            "skipped_summary_tooltip": SKIPPED_SUMMARY_TOOLTIP,
        },
    )


@router.get("/preview/{batch_id}/errors")
async def preview_errors_download(request: Request, batch_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    batch, orders = _load_batch(batch_id)
    if not batch:
        return RedirectResponse(url="/upload", status_code=302)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Mã đơn", "Cửa hàng", "Mã cửa hàng", "Trạng thái", "Lý do lỗi"])
    for order in orders:
        if order.get("status") in ("ERROR", "SKIPPED"):
            writer.writerow(
                [
                    order.get("order_ref", ""),
                    order.get("store_name", ""),
                    order.get("store_code", ""),
                    order.get("status", ""),
                    order.get("error_message", ""),
                ]
            )

    output.seek(0)
    filename = f"errors_{batch_id[:8]}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

