import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from core.auth import get_current_user
from core.importer import import_batch, mark_valid_orders_failed
from core.odoo_client import get_odoo_client_for_request
import database

logger = logging.getLogger(__name__)
router = APIRouter()

IMPORTABLE_STATUSES = ("VALIDATED", "UPLOADED")
FINISHED_STATUSES = ("IMPORTED", "PARTIAL", "FAILED")


@router.post("/import/{batch_id}")
async def run_import(request: Request, batch_id: str):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    try:
        conn = database.get_db()
        batch = conn.execute(
            "SELECT * FROM import_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        conn.close()

        if not batch:
            return RedirectResponse(url="/upload", status_code=302)

        batch = dict(batch)
        status = batch.get("status", "")

        if status in FINISHED_STATUSES:
            return RedirectResponse(url=f"/result/{batch_id}", status_code=302)

        if status not in IMPORTABLE_STATUSES:
            return RedirectResponse(url=f"/preview/{batch_id}", status_code=302)

        try:
            odoo_client = get_odoo_client_for_request()
            import_batch(batch_id, odoo_client)
        except Exception as exc:
            logger.exception("Odoo import failed for batch %s: %s", batch_id, exc)
            mark_valid_orders_failed(
                batch_id,
                "Mất kết nối Odoo trong quá trình import",
            )

        return RedirectResponse(url=f"/result/{batch_id}", status_code=302)

    except Exception as exc:
        logger.exception("Import route error: %s", exc)
        return RedirectResponse(url=f"/preview/{batch_id}", status_code=302)
