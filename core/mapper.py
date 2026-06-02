import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _normalize_name(value: str) -> str:
    return " ".join(str(value or "").lower().split())


def _partner_search_terms(store_name: str) -> List[str]:
    """Build search strings: full name, without channel prefix, suffix after HNI."""
    store_name = (store_name or "").strip()
    terms: List[str] = []
    if store_name:
        terms.append(store_name)
    for prefix in ("WM+ HNI ", "WIN HNI ", "WM+ ", "WIN "):
        if store_name.startswith(prefix):
            stripped = store_name[len(prefix) :].strip()
            if stripped:
                terms.append(stripped)
    if " HNI " in store_name:
        suffix = store_name.split(" HNI ", 1)[-1].strip()
        if suffix:
            terms.append(suffix)
    seen = set()
    unique: List[str] = []
    for t in terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def _pick_best_partner_match(rows: List[Dict], store_name: str) -> Dict:
    """Prefer partner whose name is closest to store_name."""
    target = _normalize_name(store_name)
    if not rows:
        return {}

    def score(row: Dict) -> Tuple[int, int]:
        name = _normalize_name(row.get("name", ""))
        if name == target:
            return (0, 0)
        if target in name or name in target:
            return (1, abs(len(name) - len(target)))
        return (2, abs(len(name) - len(target)))

    return min(rows, key=score)


def _find_partner(
    client: Any, store_code: str, store_name: str
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Returns ({id, name}, method_used).
  Primary: name ilike store_name + customer_rank > 0
    """
    store_name = (store_name or "").strip()
    store_code = (store_code or "").strip()

    if store_name:
        for search_term in _partner_search_terms(store_name):
            for method_label, domain in [
                (
                    "name_ilike_customer",
                    [("name", "ilike", search_term), ("customer_rank", ">", 0)],
                ),
                (
                    "name_ilike",
                    [("name", "ilike", search_term)],
                ),
            ]:
                try:
                    rows = client.search_read(
                        "res.partner",
                        domain,
                        ["id", "name", "ref"],
                        limit=10,
                    )
                    if len(rows) > 5:
                        logger.warning(
                            "Tìm thấy %s kết quả cho %s (term=%s), dùng kết quả phù hợp nhất",
                            len(rows),
                            store_name,
                            search_term,
                        )
                    if rows:
                        best = _pick_best_partner_match(rows, store_name)
                        logger.info(
                            "Odoo partner match [%s] via %s term=%r → id=%s name=%s",
                            store_code,
                            method_label,
                            search_term,
                            best.get("id"),
                            best.get("name"),
                        )
                        return {
                            "id": int(best["id"]),
                            "name": best.get("name", ""),
                        }, f"{method_label}:{search_term}"
                except Exception as exc:
                    logger.warning(
                        "Partner %s lookup failed (term=%r): %s",
                        method_label,
                        search_term,
                        exc,
                    )

    if store_code:
        try:
            rows = client.search_read(
                "res.partner",
                [("ref", "=", store_code)],
                ["id", "name", "ref"],
                limit=1,
            )
            if rows:
                row = rows[0]
                logger.info(
                    "Odoo partner match [%s] via ref → id=%s",
                    store_code,
                    row["id"],
                )
                return {"id": int(row["id"]), "name": row.get("name", "")}, "ref"
        except Exception as exc:
            logger.warning("Partner ref lookup failed: %s", exc)

        try:
            rows = client.search_read(
                "res.partner",
                [
                    ("name", "ilike", store_code),
                    ("customer_rank", ">", 0),
                ],
                ["id", "name", "ref"],
                limit=5,
            )
            if rows:
                row = rows[0]
                logger.info(
                    "Odoo partner match [%s] via name_ilike_code → id=%s",
                    store_code,
                    row["id"],
                )
                return {"id": int(row["id"]), "name": row.get("name", "")}, "name_ilike_code"
        except Exception as exc:
            logger.warning("Partner name_ilike code lookup failed: %s", exc)

    return None, None


def _find_product(
    client: Any,
    barcode: str,
    product_name: str,
    product_code: Optional[str] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Returns ({id, name, uom_id}, method_used).
    """
    barcode = (barcode or "").strip()
    product_name = (product_name or "").strip()
    code = (product_code or barcode or "").strip()

    def _product_from_row(row: Dict, method: str) -> Tuple[Dict, str]:
        uom_id = row.get("uom_id")
        if isinstance(uom_id, (list, tuple)) and uom_id:
            uom_id = int(uom_id[0])
        elif uom_id:
            uom_id = int(uom_id)
        else:
            uom_id = None
        info = {
            "id": int(row["id"]),
            "name": row.get("name", ""),
            "uom_id": uom_id,
        }
        logger.info(
            "Odoo product match [%s] via %s → id=%s name=%s",
            barcode,
            method,
            info["id"],
            info["name"],
        )
        return info, method

    if barcode:
        try:
            rows = client.search_read(
                "product.product",
                [("barcode", "=", barcode)],
                ["id", "name", "barcode", "default_code", "uom_id"],
                limit=5,
            )
            if len(rows) > 1:
                logger.warning(
                    "Tìm thấy nhiều kết quả cho %s, dùng ID đầu tiên",
                    barcode,
                )
            if rows:
                return _product_from_row(rows[0], "barcode")
        except Exception as exc:
            logger.warning("Product barcode lookup failed: %s", exc)

        try:
            rows = client.search_read(
                "product.template",
                [("barcode", "=", barcode)],
                ["id", "name", "barcode", "product_variant_ids"],
                limit=5,
            )
            if rows:
                tmpl = rows[0]
                variant_ids = tmpl.get("product_variant_ids") or []
                if variant_ids:
                    product_id = int(variant_ids[0])
                    prows = client.search_read(
                        "product.product",
                        [("id", "=", product_id)],
                        ["id", "name", "uom_id"],
                        limit=1,
                    )
                    if prows:
                        return _product_from_row(prows[0], "template_barcode")
        except Exception as exc:
            logger.warning("Product template barcode lookup failed: %s", exc)

    if code:
        try:
            rows = client.search_read(
                "product.product",
                [("default_code", "=", code)],
                ["id", "name", "default_code", "uom_id"],
                limit=5,
            )
            if rows:
                return _product_from_row(rows[0], "default_code")
        except Exception as exc:
            logger.warning("Product default_code lookup failed: %s", exc)

    if product_name:
        try:
            rows = client.search_read(
                "product.product",
                [("name", "ilike", product_name)],
                ["id", "name", "uom_id"],
                limit=5,
            )
            if rows:
                return _product_from_row(rows[0], "name_ilike")
        except Exception as exc:
            logger.warning("Product name_ilike lookup failed: %s", exc)

    return None, None


def _get_company(client: Any) -> Optional[Dict]:
    """Cached default company for this client instance (one Odoo call per batch)."""
    if getattr(client, "_company_cache", None) is not None:
        return client._company_cache

    try:
        rows = client.search_read(
            "res.company",
            [],
            ["id", "name"],
            limit=1,
            order="id asc",
        )
        company = rows[0] if rows else None
        client._company_cache = company
        if company:
            logger.info(
                "Odoo company cached → id=%s name=%s",
                company.get("id"),
                company.get("name"),
            )
        return company
    except Exception as exc:
        logger.exception("Company lookup failed: %s", exc)
        client._company_cache = None
        return None


def _check_duplicate_order(client: Any, order_ref: str) -> bool:
    try:
        rows = client.search_read(
            "sale.order",
            [("client_order_ref", "=", order_ref)],
            ["id"],
            limit=1,
        )
        return bool(rows)
    except Exception as exc:
        logger.exception("Duplicate check failed for %s: %s", order_ref, exc)
        return False


def map_order_to_odoo(order: Dict, odoo_client: Any) -> Dict:
    try:
        order_ref = str(order.get("order_ref", "")).strip()
        store_code = str(order.get("store_code", "")).strip()
        store_name = str(order.get("store_name", "")).strip()

        mapped = {
            "order_ref": order_ref,
            "odoo_partner_id": None,
            "odoo_partner_name": None,
            "odoo_company_id": None,
            "odoo_company_name": None,
            "order_date": order.get("order_date"),
            "delivery_date": order.get("delivery_date"),
            "already_exists": False,
            "lines": [],
            "errors": [],
            "warnings": [],
            "status": "READY",
            "partner_match_method": None,
            "line_match_methods": [],
        }

        partner, partner_method = _find_partner(odoo_client, store_code, store_name)
        mapped["partner_match_method"] = partner_method
        if partner:
            mapped["odoo_partner_id"] = partner["id"]
            mapped["odoo_partner_name"] = partner.get("name")
        else:
            mapped["errors"].append(
                f"Không tìm thấy cửa hàng: {store_code} - {store_name}"
            )

        company = _get_company(odoo_client)
        if company:
            mapped["odoo_company_id"] = int(company["id"])
            mapped["odoo_company_name"] = company.get("name")
        else:
            mapped["errors"].append("Không tìm thấy công ty mặc định trên Odoo.")

        mapped["already_exists"] = _check_duplicate_order(odoo_client, order_ref)

        for line in order.get("lines", []):
            try:
                barcode = str(line.get("product_barcode", "")).strip()
                product_name = str(line.get("product_name", "")).strip()
                product_code = str(line.get("product_code", "") or barcode).strip()
                quantity = int(line.get("quantity", 0) or 0)
                unit_price = float(line.get("unit_price", 0.0) or 0.0)

                if quantity <= 0:
                    mapped["errors"].append(
                        f"Số lượng không hợp lệ cho sản phẩm: {product_name} (barcode: {barcode})"
                    )
                    continue

                if unit_price <= 0:
                    mapped["warnings"].append(
                        f"Đơn giá <= 0 cho sản phẩm: {product_name} (barcode: {barcode})"
                    )

                product, product_method = _find_product(
                    odoo_client, barcode, product_name, product_code
                )
                if not product:
                    mapped["errors"].append(
                        f"Không tìm thấy sản phẩm: {product_name} (barcode: {barcode})"
                    )
                    continue

                mapped["lines"].append(
                    {
                        "odoo_product_id": product["id"],
                        "odoo_product_name": product.get("name"),
                        "uom_id": product.get("uom_id"),
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "product_name": product_name,
                        "match_method": product_method,
                    }
                )
                mapped["line_match_methods"].append(
                    {"barcode": barcode, "method": product_method}
                )
            except Exception as line_exc:
                logger.exception("Lỗi xử lý dòng đơn %s: %s", order_ref, line_exc)
                mapped["errors"].append(
                    f"Lỗi xử lý dòng sản phẩm cho đơn {order_ref}: {str(line_exc)}"
                )
                continue

        if len(mapped["lines"]) == 0 and not mapped["already_exists"]:
            mapped["status"] = "ERROR"
            mapped["errors"].append(
                f"Đơn hàng {order_ref} không có dòng hợp lệ để import."
            )

        logger.info(
            "Map đơn %s: lines=%s errors=%s partner_method=%s exists=%s",
            order_ref,
            len(mapped["lines"]),
            len(mapped["errors"]),
            partner_method,
            mapped["already_exists"],
        )
        return mapped
    except Exception as exc:
        logger.exception("Lỗi map đơn hàng %s: %s", order.get("order_ref"), exc)
        return {
            "order_ref": str(order.get("order_ref", "")),
            "odoo_partner_id": None,
            "odoo_partner_name": None,
            "odoo_company_id": None,
            "already_exists": False,
            "lines": [],
            "errors": [f"Lỗi hệ thống khi mapping đơn hàng: {str(exc)}"],
            "warnings": [],
            "status": "ERROR",
        }
