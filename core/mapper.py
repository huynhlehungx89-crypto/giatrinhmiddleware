import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from core.validator import order_exists_in_odoo

logger = logging.getLogger(__name__)

PARTNER_FIELDS = ["id", "name", "parent_id", "x_studio_phan_vung"]


def excel_serial_to_str(serial) -> Optional[str]:
    """Convert Excel serial (e.g. 46175) or date/datetime to Odoo datetime string."""
    if serial is None or serial == "":
        return None
    if isinstance(serial, datetime):
        return serial.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(serial, date):
        return serial.strftime("%Y-%m-%d 00:00:00")
    try:
        dt = datetime(1899, 12, 30) + timedelta(days=int(float(serial)))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        return str(serial)


def _find_partner_by_store_code(
    client: Any, store_code: str
) -> Optional[Dict]:
    """partner_shipping via x_studio_ma_diem_giao = store_code."""
    store_code = (store_code or "").strip()
    if not store_code:
        return None
    try:
        rows = client.search_read(
            "res.partner",
            [("x_studio_ma_diem_giao", "=", store_code)],
            PARTNER_FIELDS,
            limit=1,
        )
        return rows[0] if rows else None
    except Exception as exc:
        logger.exception(
            "Partner lookup x_studio_ma_diem_giao=%s failed: %s",
            store_code,
            exc,
        )
        return None


def _map_partner_fields(
    client: Any, store_code: str, mapped: Dict
) -> bool:
    """
    Populate partner_shipping_id, partner_id, partner_invoice_id, company_id.
    Returns True on success, False if errors were added.
    """
    record = _find_partner_by_store_code(client, store_code)
    if not record:
        mapped["errors"].append(
            f"Không tìm thấy điểm giao có mã '{store_code}' trong Odoo "
            f"(field x_studio_ma_diem_giao)"
        )
        return False

    partner_shipping_id = record["id"]

    parent_raw = record.get("parent_id")
    if isinstance(parent_raw, (list, tuple)) and len(parent_raw) >= 1:
        parent_id_value = parent_raw[0]
    else:
        parent_id_value = None

    if parent_id_value is None:
        mapped["errors"].append(
            f"Điểm giao '{store_code}' chưa có công ty mẹ (parent_id) trong Odoo"
        )
        return False

    phan_vung_raw = record.get("x_studio_phan_vung")
    if isinstance(phan_vung_raw, (list, tuple)) and len(phan_vung_raw) >= 2:
        phan_vung = phan_vung_raw[1].strip()
    elif isinstance(phan_vung_raw, str):
        phan_vung = phan_vung_raw.strip()
    else:
        phan_vung = ""

    print(f"[mapper] store={store_code} phan_vung='{phan_vung}'")

    if phan_vung == "Lý Nam Đế":
        company_id = 1
    elif phan_vung == "Phạm Văn Đồng":
        company_id = 2
    else:
        mapped["errors"].append(
            f"Điểm giao '{store_code}' có phân vùng '{phan_vung}' "
            f"không hợp lệ. Cần là 'Lý Nam Đế' hoặc 'Phạm Văn Đồng'."
        )
        return False

    mapped["partner_shipping_id"] = partner_shipping_id
    mapped["partner_id"] = parent_id_value
    mapped["partner_invoice_id"] = parent_id_value
    mapped["company_id"] = company_id
    mapped["partner_shipping_name"] = record.get("name")
    mapped["x_studio_phan_vung"] = phan_vung
    return True


def _uom_id_from_row(row: Dict) -> Optional[int]:
    uom = row.get("uom_id")
    if isinstance(uom, (list, tuple)) and uom:
        return int(uom[0])
    if uom:
        return int(uom)
    return None


def _fetch_template_id(client: Any, product_id: int) -> Optional[int]:
    try:
        rows = client.search_read(
            "product.product",
            [("id", "=", product_id)],
            ["product_tmpl_id"],
            limit=1,
        )
        if not rows:
            return None
        tmpl = rows[0].get("product_tmpl_id")
        if isinstance(tmpl, (list, tuple)) and tmpl:
            return int(tmpl[0])
        if tmpl:
            return int(tmpl)
        return None
    except Exception as exc:
        logger.warning("product_tmpl_id fetch failed for %s: %s", product_id, exc)
        return None


def _find_product(
    client: Any, product_barcode: str, product_name: str
) -> Optional[Dict]:
    """
    Returns dict with product_id, template_id, uom_id or None.
    """
    product_barcode = (product_barcode or "").strip()
    product_name = (product_name or "").strip()

    def _attempt(n: int, result: Optional[Dict]) -> Optional[Dict]:
        print(f"[mapper] product attempt {n}: barcode='{product_barcode}' → {result}")
        return result

    # Attempt 1: product.product barcode
    if product_barcode:
        try:
            rows = client.search_read(
                "product.product",
                [("barcode", "=", product_barcode)],
                ["id", "name", "uom_id", "product_tmpl_id"],
                limit=1,
            )
            if rows:
                row = rows[0]
                pid = int(row["id"])
                return _attempt(
                    1,
                    {
                        "product_id": pid,
                        "template_id": _fetch_template_id(client, pid)
                        or _template_id_from_row(row),
                        "uom_id": _uom_id_from_row(row),
                        "name": row.get("name"),
                    },
                )
            _attempt(1, None)
        except Exception as exc:
            print(
                f"[mapper] product attempt 1: barcode='{product_barcode}' → None ({exc})"
            )

    # Attempt 2: product.template barcode → variant[0]
    if product_barcode:
        try:
            rows = client.search_read(
                "product.template",
                [("barcode", "=", product_barcode)],
                ["id", "product_variant_ids"],
                limit=1,
            )
            if rows and rows[0].get("product_variant_ids"):
                pid = int(rows[0]["product_variant_ids"][0])
                prows = client.search_read(
                    "product.product",
                    [("id", "=", pid)],
                    ["id", "name", "uom_id", "product_tmpl_id"],
                    limit=1,
                )
                if prows:
                    row = prows[0]
                    return _attempt(
                        2,
                        {
                            "product_id": pid,
                            "template_id": _fetch_template_id(client, pid)
                            or int(rows[0]["id"]),
                            "uom_id": _uom_id_from_row(row),
                            "name": row.get("name"),
                        },
                    )
            _attempt(2, None)
        except Exception as exc:
            print(
                f"[mapper] product attempt 2: barcode='{product_barcode}' → None ({exc})"
            )

    # Attempt 3: product.product default_code
    if product_barcode:
        try:
            rows = client.search_read(
                "product.product",
                [("default_code", "=", product_barcode)],
                ["id", "name", "uom_id", "product_tmpl_id"],
                limit=1,
            )
            if rows:
                row = rows[0]
                pid = int(row["id"])
                return _attempt(
                    3,
                    {
                        "product_id": pid,
                        "template_id": _fetch_template_id(client, pid)
                        or _template_id_from_row(row),
                        "uom_id": _uom_id_from_row(row),
                        "name": row.get("name"),
                    },
                )
            _attempt(3, None)
        except Exception as exc:
            print(
                f"[mapper] product attempt 3: barcode='{product_barcode}' → None ({exc})"
            )

    # Attempt 4: name ilike
    if product_name:
        try:
            rows = client.search_read(
                "product.product",
                [("name", "ilike", product_name)],
                ["id", "name", "uom_id", "product_tmpl_id"],
                limit=1,
            )
            if rows:
                row = rows[0]
                pid = int(row["id"])
                return _attempt(
                    4,
                    {
                        "product_id": pid,
                        "template_id": _fetch_template_id(client, pid)
                        or _template_id_from_row(row),
                        "uom_id": _uom_id_from_row(row),
                        "name": row.get("name"),
                    },
                )
            _attempt(4, None)
        except Exception as exc:
            print(
                f"[mapper] product attempt 4: barcode='{product_barcode}' → None ({exc})"
            )
    else:
        _attempt(4, None)

    return None


def _template_id_from_row(row: Dict) -> Optional[int]:
    tmpl = row.get("product_tmpl_id")
    if isinstance(tmpl, (list, tuple)) and tmpl:
        return int(tmpl[0])
    if tmpl:
        return int(tmpl)
    return None


def map_order_to_odoo(order: Dict, odoo_client: Any) -> Dict:
    try:
        order_ref = str(order.get("order_ref", "")).strip()
        store_code = str(order.get("store_code", "")).strip()
        store_name = str(order.get("store_name", "")).strip()

        mapped: Dict = {
            "order_ref": order_ref,
            "store_code": store_code,
            "store_name": store_name,
            "partner_shipping_id": None,
            "partner_shipping_name": None,
            "partner_id": None,
            "partner_invoice_id": None,
            "company_id": None,
            "x_studio_phan_vung": None,
            "order_date": excel_serial_to_str(order.get("order_date")),
            "delivery_date": excel_serial_to_str(order.get("delivery_date")),
            "already_exists": False,
            "lines": [],
            "errors": [],
            "warnings": [],
            "status": "READY",
        }

        if not store_code:
            mapped["errors"].append("Thiếu mã điểm giao (store_code) trong file Excel.")
        else:
            _map_partner_fields(odoo_client, store_code, mapped)

        mapped["already_exists"] = order_exists_in_odoo(odoo_client, order_ref)

        for line in order.get("lines", []):
            barcode = str(line.get("product_barcode", "")).strip()
            product_name = str(line.get("product_name", "")).strip()
            quantity = int(line.get("quantity", 0) or 0)
            unit_price = float(line.get("unit_price", 0.0) or 0.0)

            if quantity <= 0:
                mapped["errors"].append(
                    f"Số lượng không hợp lệ cho sản phẩm: {product_name} "
                    f"(barcode: {barcode})"
                )
                continue

            if unit_price <= 0:
                mapped["warnings"].append(
                    f"Đơn giá <= 0 cho sản phẩm: {product_name} (barcode: {barcode})"
                )

            product = _find_product(odoo_client, barcode, product_name)

            mapped_line: Dict = {
                "product_barcode": barcode,
                "product_name": product_name,
                "quantity": quantity,
                "unit_price": unit_price,
                "odoo_product_id": None,
                "odoo_template_id": None,
                "uom_id": None,
                "lookup_error": None,
            }

            if product:
                mapped_line["odoo_product_id"] = product["product_id"]
                mapped_line["odoo_template_id"] = product.get("template_id")
                mapped_line["uom_id"] = product.get("uom_id")
            else:
                mapped_line["lookup_error"] = (
                    f"Không tìm thấy sản phẩm: barcode='{barcode}' "
                    f"tên='{product_name}'"
                )
                mapped["errors"].append(mapped_line["lookup_error"])

            mapped["lines"].append(mapped_line)

        if not any(ln.get("odoo_product_id") for ln in mapped["lines"]):
            if not mapped["already_exists"]:
                mapped["status"] = "ERROR"
                if not any("sản phẩm" in e for e in mapped["errors"]):
                    mapped["errors"].append(
                        f"Đơn hàng {order_ref} không có dòng sản phẩm hợp lệ để import."
                    )

        logger.info(
            "Map đơn %s: partner=%s company=%s lines=%s errors=%s",
            order_ref,
            mapped.get("partner_shipping_id"),
            mapped.get("company_id"),
            len(mapped["lines"]),
            len(mapped["errors"]),
        )
        return mapped

    except Exception as exc:
        logger.exception("Lỗi map đơn %s: %s", order.get("order_ref"), exc)
        return {
            "order_ref": str(order.get("order_ref", "")),
            "partner_shipping_id": None,
            "partner_id": None,
            "partner_invoice_id": None,
            "company_id": None,
            "already_exists": False,
            "lines": [],
            "errors": [f"Lỗi hệ thống khi mapping đơn hàng: {str(exc)}"],
            "warnings": [],
            "status": "ERROR",
        }
