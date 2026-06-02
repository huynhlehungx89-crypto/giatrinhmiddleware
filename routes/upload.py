import json
import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import APP_NAME, UPLOAD_DIR
from core.auth import get_current_user
from core.file_parser import group_rows_into_orders, parse_vinmart_file
from core.mapper import map_order_to_odoo
from core.odoo_client import get_odoo_client_for_request, get_odoo_settings
from core.validator import validate_batch
import database

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
ODOO_CONNECTION_ERROR = "Không thể kết nối Odoo để xác thực dữ liệu"


def _date_to_str(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _save_batch_to_db(
    batch_id: str,
    original_filename: str,
    user_id: str,
    parsed_row_count: int,
    summary: dict,
    grouped_orders: list,
):
    conn = database.get_db()
    conn.execute(
        """
        INSERT INTO import_batches (
            id, filename, source, status, total_rows,
            valid_orders, error_orders, skipped_orders,
            uploaded_by
        ) VALUES (?, ?, 'VINMART', 'VALIDATED', ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            original_filename,
            parsed_row_count,
            summary["valid"],
            summary["error"],
            summary["skipped"],
            user_id,
        ),
    )

    grouped_by_ref = {o["order_ref"]: o for o in grouped_orders}

    for order in summary["orders"]:
        order_ref = order.get("order_ref", "")
        source = grouped_by_ref.get(order_ref, {})
        error_message = "; ".join(order.get("errors", []))
        if order.get("warnings"):
            warn_text = "; ".join(order["warnings"])
            error_message = f"{error_message}; {warn_text}".strip("; ")

        conn.execute(
            """
            INSERT INTO import_orders (
                id, batch_id, order_ref, store_code, store_name,
                order_date, delivery_date, line_count, status,
                error_message, raw_data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                batch_id,
                order_ref,
                source.get("store_code") or "",
                source.get("store_name") or "",
                _date_to_str(source.get("order_date")),
                _date_to_str(source.get("delivery_date")),
                len(source.get("lines", [])),
                order.get("status", "ERROR"),
                error_message or None,
                json.dumps(order, default=str),
            ),
        )

    conn.commit()
    conn.close()


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "upload.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "user": user,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)

        if not get_odoo_settings():
            return templates.TemplateResponse(
                "upload.html",
                {
                    "request": request,
                    "app_name": APP_NAME,
                    "user": user,
                    "error": "Vui lòng cấu hình kết nối Odoo trước",
                },
                status_code=400,
            )

        original_filename = file.filename or "upload.xlsx"
        ext = os.path.splitext(original_filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return templates.TemplateResponse(
                "upload.html",
                {
                    "request": request,
                    "app_name": APP_NAME,
                    "user": user,
                    "error": "Định dạng file không hợp lệ. Chỉ hỗ trợ .xlsx, .xls, .csv.",
                },
                status_code=400,
            )

        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            return templates.TemplateResponse(
                "upload.html",
                {
                    "request": request,
                    "app_name": APP_NAME,
                    "user": user,
                    "error": "File quá lớn (>10MB). Vui lòng giảm dung lượng trước khi import.",
                },
                status_code=400,
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        saved_name = f"{timestamp}_{original_filename}"
        saved_path = os.path.join(UPLOAD_DIR, saved_name)
        with open(saved_path, "wb") as f:
            f.write(content)

        rows = parse_vinmart_file(saved_path)
        grouped_orders = group_rows_into_orders(rows)

        mapped_orders = []
        odoo_reachable = True
        odoo_client = None

        try:
            odoo_client = get_odoo_client_for_request()
            odoo_client._company_cache = None
        except Exception as exc:
            logger.warning("Không kết nối được Odoo: %s", exc)
            odoo_reachable = False

        if odoo_reachable and odoo_client:
            for order in grouped_orders:
                try:
                    mapped_orders.append(map_order_to_odoo(order, odoo_client))
                except Exception as exc:
                    logger.exception("Lỗi map đơn %s: %s", order.get("order_ref"), exc)
                    mapped_orders.append(
                        {
                            "order_ref": order.get("order_ref"),
                            "order_date": order.get("order_date"),
                            "delivery_date": order.get("delivery_date"),
                            "already_exists": False,
                            "lines": [],
                            "errors": [f"Lỗi mapping đơn hàng: {str(exc)}"],
                            "warnings": [],
                        }
                    )
        else:
            for order in grouped_orders:
                mapped_orders.append(
                    {
                        "order_ref": order.get("order_ref"),
                        "order_date": order.get("order_date"),
                        "delivery_date": order.get("delivery_date"),
                        "already_exists": False,
                        "lines": [],
                        "errors": [ODOO_CONNECTION_ERROR],
                        "warnings": [],
                    }
                )

        summary = validate_batch(mapped_orders, odoo_client)
        batch_id = str(uuid.uuid4())
        _save_batch_to_db(
            batch_id,
            original_filename,
            user["id"],
            len(rows),
            summary,
            grouped_orders,
        )

        return RedirectResponse(url=f"/preview/{batch_id}", status_code=302)

    except Exception as exc:
        logger.exception("Lỗi upload file: %s", exc)
        return templates.TemplateResponse(
            "upload.html",
            {
                "request": request,
                "app_name": APP_NAME,
                "user": user,
                "error": str(exc),
            },
            status_code=400,
        )
