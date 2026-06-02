import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import database

logger = logging.getLogger(__name__)

ODOO_LOST_CONNECTION = "Mất kết nối Odoo trong quá trình import"
IMPORT_NOTE = "Import từ VinMart"


def _log_odoo_call(action: str, **kwargs) -> None:
    safe = {k: v for k, v in kwargs.items() if k != "api_key"}
    logger.info("[%s] Odoo API: %s %s", datetime.now().isoformat(), action, safe)


def _format_odoo_datetime(value, field_label: str, order_ref: str) -> str:
    """Odoo datetime: YYYY-MM-DD HH:MM:SS"""
    if value is None or value == "":
        logger.warning(
            "%s thiếu cho đơn %s, dùng ngày hôm nay",
            field_label,
            order_ref,
        )
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d 00:00:00")
        text = str(value).strip()
        if "T" in text:
            text = text.replace("T", " ")
        if len(text) == 10:
            return f"{text} 00:00:00"
        if len(text) >= 19:
            return text[:19]
        parsed = datetime.fromisoformat(text.split(" ")[0])
        return parsed.strftime("%Y-%m-%d 00:00:00")
    except Exception as exc:
        logger.warning(
            "%s không hợp lệ cho đơn %s (value=%r): %s — dùng hôm nay",
            field_label,
            order_ref,
            value,
            exc,
        )
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_int(value, field_name: str, order_ref: str) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError) as exc:
        logger.error(
            "Chuyển đổi %s thất bại cho đơn %s: value=%r error=%s",
            field_name,
            order_ref,
            value,
            exc,
        )
        return None


def _safe_float(value, field_name: str, order_ref: str) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError) as exc:
        logger.error(
            "Chuyển đổi %s thất bại cho đơn %s: value=%r error=%s",
            field_name,
            order_ref,
            value,
            exc,
        )
        return None


def _load_valid_orders(batch_id: str) -> List[Dict]:
    conn = database.get_db()
    rows = conn.execute(
        """
        SELECT * FROM import_orders
        WHERE batch_id = ? AND status = 'VALID'
        ORDER BY order_ref
        """,
        (batch_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _order_imported_locally(conn, order_id: str) -> bool:
    row = conn.execute(
        """
        SELECT status, odoo_order_id FROM import_orders
        WHERE id = ? AND status IN ('SUCCESS', 'PARTIAL_SUCCESS')
        """,
        (order_id,),
    ).fetchone()
    return row is not None and row["odoo_order_id"] is not None


def _exists_in_odoo(odoo_client: Any, order_ref: str) -> bool:
    _log_odoo_call("sale.order search duplicate", order_ref=order_ref)
    rows = odoo_client.search_read(
        "sale.order",
        [("client_order_ref", "=", order_ref)],
        ["id"],
        limit=1,
    )
    return bool(rows)


def _save_order_result(
    conn,
    order_id: str,
    status: str,
    odoo_order_id: Optional[int] = None,
    odoo_order_name: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    conn.execute(
        """
        UPDATE import_orders
        SET status = ?, odoo_order_id = ?, odoo_order_name = ?, error_message = ?
        WHERE id = ?
        """,
        (status, odoo_order_id, odoo_order_name, error_message, order_id),
    )
    conn.commit()


def _read_order_name(odoo_client: Any, so_id: int) -> str:
    _log_odoo_call("sale.order read name", so_id=so_id)
    rows = odoo_client.search_read(
        "sale.order",
        [("id", "=", so_id)],
        ["name"],
        limit=1,
    )
    if rows:
        return rows[0].get("name", str(so_id))
    return str(so_id)


def _import_one_order(
    odoo_client: Any,
    batch_id: str,
    order_row: Dict,
    mapped: Dict,
    conn,
) -> Dict:
    order_id = order_row["id"]
    order_ref = str(order_row.get("order_ref", "")).strip()

    if _order_imported_locally(conn, order_id):
        return {
            "order_ref": order_ref,
            "status": "SKIPPED",
            "odoo_order_name": order_row.get("odoo_order_name"),
            "odoo_order_id": order_row.get("odoo_order_id"),
            "error_message": "Đơn đã được import trước đó (local).",
        }

    if _exists_in_odoo(odoo_client, order_ref):
        _save_order_result(
            conn,
            order_id,
            "SKIPPED",
            error_message="Đơn đã tồn tại trên Odoo (client_order_ref trùng).",
        )
        return {
            "order_ref": order_ref,
            "status": "SKIPPED",
            "odoo_order_name": None,
            "error_message": "Đơn đã tồn tại trên Odoo.",
        }

    partner_id = _safe_int(
        mapped.get("odoo_partner_id"), "partner_id", order_ref
    )
    if not partner_id:
        msg = "Thiếu partner_id Odoo hợp lệ, không thể tạo đơn hàng."
        _save_order_result(conn, order_id, "FAILED", error_message=msg)
        return {"order_ref": order_ref, "status": "FAILED", "error_message": msg}

    company_id = _safe_int(
        mapped.get("odoo_company_id"), "company_id", order_ref
    )

    date_order = _format_odoo_datetime(
        mapped.get("order_date") or order_row.get("order_date"),
        "date_order",
        order_ref,
    )
    commitment_date = _format_odoo_datetime(
        mapped.get("delivery_date") or order_row.get("delivery_date"),
        "commitment_date",
        order_ref,
    )

    so_vals: Dict[str, Any] = {
        "partner_id": partner_id,
        "client_order_ref": order_ref,
        "date_order": date_order,
        "commitment_date": commitment_date,
        "note": IMPORT_NOTE,
    }
    if company_id:
        so_vals["company_id"] = company_id

    so_id: Optional[int] = None
    try:
        _log_odoo_call("sale.order create", order_ref=order_ref, partner_id=partner_id)
        so_id = int(odoo_client.execute_kw("sale.order", "create", [so_vals]))
    except Exception as exc:
        if company_id:
            logger.warning(
                "Tạo SO với company_id thất bại đơn %s, thử không company_id: %s",
                order_ref,
                exc,
            )
            try:
                so_vals_no_co = {k: v for k, v in so_vals.items() if k != "company_id"}
                so_id = int(
                    odoo_client.execute_kw("sale.order", "create", [so_vals_no_co])
                )
            except Exception as exc2:
                exc = exc2
        if so_id is None:
            msg = f"Không tạo được đơn bán hàng trên Odoo: {str(exc)}"
            logger.exception("Create SO failed for %s", order_ref)
            _save_order_result(conn, order_id, "FAILED", error_message=msg)
            return {"order_ref": order_ref, "status": "FAILED", "error_message": msg}

    so_name = _read_order_name(odoo_client, so_id)
    _save_order_result(conn, order_id, "SUCCESS", so_id, so_name)

    line_errors: List[str] = []
    lines = mapped.get("lines") or []

    for line in lines:
        try:
            product_id = _safe_int(
                line.get("odoo_product_id"), "product_id", order_ref
            )
            qty = _safe_float(line.get("quantity"), "product_uom_qty", order_ref)
            price = _safe_float(line.get("unit_price"), "price_unit", order_ref)

            if not product_id:
                line_errors.append(
                    f"Thiếu product_id cho {line.get('product_name', '')}"
                )
                continue
            if qty is None or qty <= 0:
                line_errors.append(
                    f"Số lượng không hợp lệ cho {line.get('product_name', '')}"
                )
                continue
            if price is None:
                price = 0.0

            line_vals: Dict[str, Any] = {
                "order_id": so_id,
                "product_id": product_id,
                "product_uom_qty": qty,
                "price_unit": price,
            }
            uom_id = _safe_int(line.get("uom_id"), "uom_id", order_ref)
            if uom_id:
                line_vals["product_uom"] = uom_id

            _log_odoo_call(
                "sale.order.line create",
                order_ref=order_ref,
                product_id=product_id,
            )
            odoo_client.execute_kw("sale.order.line", "create", [line_vals])
        except Exception as line_exc:
            logger.exception(
                "Line create failed order=%s product=%s",
                order_ref,
                line.get("product_name"),
            )
            line_errors.append(
                f"{line.get('product_name', '')}: {str(line_exc)}"
            )

    if line_errors:
        note = "Một số dòng không tạo được: " + "; ".join(line_errors)
        _save_order_result(
            conn,
            order_id,
            "PARTIAL_SUCCESS",
            so_id,
            so_name,
            note,
        )
        return {
            "order_ref": order_ref,
            "status": "PARTIAL_SUCCESS",
            "odoo_order_name": so_name,
            "odoo_order_id": so_id,
            "error_message": note,
        }

    return {
        "order_ref": order_ref,
        "status": "SUCCESS",
        "odoo_order_name": so_name,
        "odoo_order_id": so_id,
    }


def import_batch(batch_id: str, odoo_client: Any) -> Dict:
    """Import all VALID orders in a batch into Odoo."""
    results: List[Dict] = []
    success_count = 0
    skipped_count = 0
    failed_count = 0

    valid_orders = _load_valid_orders(batch_id)
    conn = database.get_db()

    try:
        for order_row in valid_orders:
            try:
                mapped = json.loads(order_row.get("raw_data") or "{}")
            except json.JSONDecodeError:
                mapped = {}

            try:
                result = _import_one_order(
                    odoo_client, batch_id, order_row, mapped, conn
                )
                results.append(result)
                st = result.get("status")
                if st in ("SUCCESS", "PARTIAL_SUCCESS"):
                    success_count += 1
                elif st == "SKIPPED":
                    skipped_count += 1
                else:
                    failed_count += 1
            except Exception as exc:
                logger.exception("Import order %s failed", order_row.get("order_ref"))
                msg = f"Lỗi import đơn hàng: {str(exc)}"
                _save_order_result(
                    conn, order_row["id"], "FAILED", error_message=msg
                )
                results.append(
                    {
                        "order_ref": order_row.get("order_ref"),
                        "status": "FAILED",
                        "error_message": msg,
                    }
                )
                failed_count += 1

        if failed_count == 0 and success_count > 0:
            batch_status = "IMPORTED"
        elif success_count == 0 and failed_count > 0:
            batch_status = "FAILED"
        elif success_count > 0:
            batch_status = "PARTIAL"
        else:
            batch_status = "FAILED"

        conn.execute(
            """
            UPDATE import_batches
            SET status = ?,
                success_orders = ?,
                skipped_orders = ?,
                failed_orders = ?,
                imported_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                batch_status,
                success_count,
                skipped_count,
                failed_count,
                batch_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    summary = {
        "success": success_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "orders": results,
    }
    logger.info("Import batch %s summary: %s", batch_id, summary)
    return summary


def mark_valid_orders_failed(batch_id: str, message: str) -> None:
    conn = database.get_db()
    conn.execute(
        """
        UPDATE import_orders
        SET status = 'FAILED', error_message = ?
        WHERE batch_id = ? AND status = 'VALID'
        """,
        (message, batch_id),
    )
    conn.execute(
        """
        UPDATE import_batches
        SET status = 'FAILED',
            failed_orders = (
                SELECT COUNT(*) FROM import_orders
                WHERE batch_id = ? AND status = 'FAILED'
            ),
            imported_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (batch_id, batch_id),
    )
    conn.commit()
    conn.close()
