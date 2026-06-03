import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import database

logger = logging.getLogger(__name__)

ODOO_LOST_CONNECTION = "Mất kết nối Odoo trong quá trình import"
IMPORT_NOTE = "Import từ VinMart"


def _log_odoo_call(action: str, **kwargs) -> None:
    safe = {k: v for k, v in kwargs.items() if k != "api_key"}
    logger.info("[%s] Odoo API: %s %s", datetime.now().isoformat(), action, safe)


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
    from core.validator import order_exists_in_odoo

    _log_odoo_call("sale.order search duplicate", order_ref=order_ref)
    return order_exists_in_odoo(odoo_client, order_ref)


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


def _create_sale_order_line(odoo_client: Any, line_payload: Dict) -> int:
    return int(
        odoo_client.execute_kw("sale.order.line", "create", [line_payload])
    )


def _import_order_lines(
    odoo_client: Any, odoo_order_id: int, lines: List[Dict]
) -> tuple[int, int]:
    """Create sale.order.line records. Returns (created_lines, failed_lines)."""
    total_lines = len(lines)
    created_lines = 0
    failed_lines = 0

    for line in lines:
        if line.get("odoo_product_id") is None:
            print(f"[importer] SKIP line: {line.get('lookup_error')}")
            failed_lines += 1
            continue

        line_payload = {
            "order_id": odoo_order_id,
            "product_id": line["odoo_product_id"],
            "product_template_id": line.get("odoo_template_id"),
            "product_uom_qty": float(line["quantity"]),
            "price_unit": float(line["unit_price"]),
        }
        if line.get("uom_id"):
            line_payload["product_uom"] = line["uom_id"]

        print(f"[importer] line payload: {line_payload}")

        try:
            line_id = _create_sale_order_line(odoo_client, line_payload)
            print(f"[importer] line created id={line_id}")
            created_lines += 1
            continue
        except Exception as exc:
            print(f"[importer] line attempt 1 failed: {exc}")

        line_payload2 = {
            k: v
            for k, v in line_payload.items()
            if k != "product_template_id"
        }
        try:
            line_id = _create_sale_order_line(odoo_client, line_payload2)
            print(f"[importer] line created (attempt 2) id={line_id}")
            created_lines += 1
            continue
        except Exception as exc:
            print(f"[importer] line attempt 2 failed: {exc}")

        line_payload3 = {
            k: v for k, v in line_payload2.items() if k != "product_uom"
        }
        try:
            line_id = _create_sale_order_line(odoo_client, line_payload3)
            print(f"[importer] line created (attempt 3 no uom) id={line_id}")
            created_lines += 1
            continue
        except Exception as exc:
            print(f"[importer] line attempt 3 failed: {exc}")
            failed_lines += 1

    return created_lines, failed_lines


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
            error_message="Đơn đã tồn tại trên Odoo (mã PO trùng).",
        )
        return {
            "order_ref": order_ref,
            "status": "SKIPPED",
            "odoo_order_name": None,
            "error_message": "Đơn đã tồn tại trên Odoo.",
        }

    if mapped.get("partner_id") is None:
        msg = (
            "; ".join(mapped.get("errors") or [])
            or "Thiếu partner_id (công ty mẹ), không thể tạo đơn hàng."
        )
        _save_order_result(conn, order_id, "FAILED", error_message=msg)
        return {"order_ref": order_ref, "status": "FAILED", "error_message": msg}

    if mapped.get("company_id") is None:
        msg = (
            "; ".join(mapped.get("errors") or [])
            or "Thiếu company_id (phân vùng), không thể tạo đơn hàng."
        )
        _save_order_result(conn, order_id, "FAILED", error_message=msg)
        return {"order_ref": order_ref, "status": "FAILED", "error_message": msg}

    order = {
        "order_ref": order_ref,
        "date_order": mapped.get("order_date"),
        "commitment_date": mapped.get("delivery_date"),
    }

    so_payload = {
        "x_studio_nguoi_mua_ma_po": order["order_ref"],
        "partner_id": mapped["partner_id"],
        "partner_invoice_id": mapped["partner_invoice_id"],
        "partner_shipping_id": mapped["partner_shipping_id"],
        "company_id": mapped["company_id"],
        "date_order": order["date_order"],
        "commitment_date": order["commitment_date"],
        "note": IMPORT_NOTE,
    }
    print(f"[importer] SO payload: {so_payload}")

    try:
        _log_odoo_call("sale.order create", order_ref=order_ref, so_vals=so_payload)
        so_id = int(odoo_client.execute_kw("sale.order", "create", [so_payload]))
    except Exception as exc:
        msg = f"Không tạo được đơn bán hàng trên Odoo: {str(exc)}"
        logger.exception("Create SO failed for %s vals=%s", order_ref, so_payload)
        _save_order_result(conn, order_id, "FAILED", error_message=msg)
        return {"order_ref": order_ref, "status": "FAILED", "error_message": msg}

    so_name = _read_order_name(odoo_client, so_id)
    lines = mapped.get("lines") or []
    total_lines = len(lines)
    created_lines, failed_lines = _import_order_lines(
        odoo_client, so_id, lines
    )

    if created_lines == 0 and total_lines > 0:
        status = "FAILED"
        error_message = "SO tạo thành công nhưng 0 dòng sản phẩm được tạo."
        _save_order_result(conn, order_id, status, so_id, so_name, error_message)
        return {
            "order_ref": order_ref,
            "status": status,
            "odoo_order_name": so_name,
            "odoo_order_id": so_id,
            "error_message": error_message,
        }

    if failed_lines > 0:
        status = "PARTIAL_SUCCESS"
        error_message = (
            f"Một số dòng không tạo được ({failed_lines}/{total_lines} dòng thất bại)."
        )
        _save_order_result(conn, order_id, status, so_id, so_name, error_message)
        return {
            "order_ref": order_ref,
            "status": status,
            "odoo_order_name": so_name,
            "odoo_order_id": so_id,
            "error_message": error_message,
        }

    status = "SUCCESS"
    _save_order_result(conn, order_id, status, so_id, so_name)
    logger.info(
        "Order %s imported: SO %s with %s/%s line(s)",
        order_ref,
        so_name,
        created_lines,
        total_lines,
    )
    return {
        "order_ref": order_ref,
        "status": status,
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
