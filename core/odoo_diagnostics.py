"""
Shared Odoo connection diagnostics for debug_odoo.py and /debug/odoo route.
"""
from typing import Any, Dict, List, Optional, Tuple

from core.odoo_client import OdooClient, get_odoo_settings


def _rows_summary(rows: List[Dict], fields: List[str]) -> List[Dict]:
    out = []
    for row in rows:
        out.append({f: row.get(f) for f in fields if f in row})
    return out


def _try_partner_lookups(
    client: OdooClient, store_code: str, store_name: str
) -> Tuple[List[Dict], Optional[str]]:
    attempts = [
        ("ref", "res.partner", [("ref", "=", store_code)]),
        ("name_ilike", "res.partner", [("name", "ilike", store_name)]),
        ("street_ilike", "res.partner", [("street", "ilike", store_code)]),
    ]
    results = []
    working_method = None
    fields = ["id", "name", "ref", "street"]

    for method_key, model, domain in attempts:
        try:
            rows = client.search_read(model, domain, fields, limit=5)
            entry = {
                "method": method_key,
                "model": model,
                "domain": domain,
                "count": len(rows),
                "records": _rows_summary(rows, fields),
            }
            results.append(entry)
            if rows and working_method is None:
                working_method = method_key
        except Exception as exc:
            results.append(
                {
                    "method": method_key,
                    "model": model,
                    "domain": domain,
                    "error": str(exc),
                }
            )
    return results, working_method


def _try_product_lookups(
    client: OdooClient, barcode: str, product_name: str = ""
) -> Tuple[List[Dict], Optional[str]]:
    attempts = [
        ("product_barcode", "product.product", [("barcode", "=", barcode)]),
        ("template_barcode", "product.template", [("barcode", "=", barcode)]),
        (
            "default_code",
            "product.product",
            [("default_code", "=", barcode)],
        ),
        (
            "name_ilike",
            "product.product",
            [("name", "ilike", product_name or barcode)],
        ),
    ]
    results = []
    working_method = None
    fields = ["id", "name", "barcode", "default_code"]

    for method_key, model, domain in attempts:
        try:
            rows = client.search_read(model, domain, fields, limit=5)
            entry = {
                "method": method_key,
                "model": model,
                "domain": domain,
                "count": len(rows),
                "records": _rows_summary(rows, fields),
            }
            if model == "product.template" and rows:
                tmpl_id = rows[0]["id"]
                variants = client.search_read(
                    "product.product",
                    [("product_tmpl_id", "=", tmpl_id)],
                    fields,
                    limit=5,
                )
                entry["variants"] = _rows_summary(variants, fields)
            results.append(entry)
            if rows and working_method is None:
                working_method = method_key
        except Exception as exc:
            results.append(
                {
                    "method": method_key,
                    "model": model,
                    "domain": domain,
                    "error": str(exc),
                }
            )
    return results, working_method


def _try_duplicate_check(
    client: OdooClient, order_ref: str
) -> Tuple[List[Dict], bool]:
    fields = ["id", "name", "client_order_ref"]
    domain = [("client_order_ref", "=", order_ref)]
    try:
        rows = client.search_read("sale.order", domain, fields, limit=5)
        return _rows_summary(rows, fields), True
    except Exception as exc:
        return [{"error": str(exc)}], False


def run_odoo_diagnostics(
    store_code: str = "4781",
    store_name: str = "314 Trần Cung",
    barcode: str = "1101196000000",
    order_ref: str = "4190155099",
) -> Dict[str, Any]:
    settings = get_odoo_settings()
    if not settings:
        return {
            "ok": False,
            "error": "Chưa có cài đặt Odoo. Vào /settings trước.",
            "api_key_status": "not configured",
        }

    result: Dict[str, Any] = {
        "ok": True,
        "url": settings["url"],
        "database": settings["database"],
        "username": settings["username"],
        "api_key_status": "configured",
        "store_code": store_code,
        "barcode": barcode,
        "order_ref": order_ref,
    }

    try:
        client = OdooClient(
            settings["url"],
            settings["database"],
            settings["username"],
            settings["api_key"],
        )
        client.connect()
        result["uid"] = client.uid

        partner_attempts, partner_method = _try_partner_lookups(
            client, store_code, store_name
        )
        result["partner_attempts"] = partner_attempts
        result["partner_working_method"] = partner_method
        result["partner_ok"] = partner_method is not None

        product_attempts, product_method = _try_product_lookups(
            client, barcode
        )
        result["product_attempts"] = product_attempts
        result["product_working_method"] = product_method
        result["product_ok"] = product_method is not None

        duplicate_rows, duplicate_api_ok = _try_duplicate_check(
            client, order_ref
        )
        result["duplicate_check"] = duplicate_rows
        result["duplicate_api_ok"] = duplicate_api_ok
        result["duplicate_found"] = len(duplicate_rows) > 0

    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
        result["partner_ok"] = False
        result["product_ok"] = False
        result["duplicate_api_ok"] = False

    return result


def format_diagnostics_summary(diag: Dict[str, Any]) -> List[str]:
    lines = []

    if diag.get("uid"):
        lines.append(f"User ID: {diag['uid']}")

    partner_ok = diag.get("partner_ok")
    lines.append(
        f"{'✅' if partner_ok else '❌'} Partner lookup: {'works' if partner_ok else 'failed'}"
        + (f" (method: {diag.get('partner_working_method')})" if partner_ok else "")
    )

    product_ok = diag.get("product_ok")
    lines.append(
        f"{'✅' if product_ok else '❌'} Product lookup: {'works' if product_ok else 'failed'}"
        + (f" (method: {diag.get('product_working_method')})" if product_ok else "")
    )

    dup_ok = diag.get("duplicate_api_ok")
    lines.append(
        f"{'✅' if dup_ok else '❌'} Duplicate check: {'works' if dup_ok else 'failed'}"
    )
    if diag.get("error") and not diag.get("uid"):
        lines.append(f"Connection error: {diag['error']}")
    return lines
