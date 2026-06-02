import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def validate_batch(orders: List[Dict], odoo_client: Any = None) -> Dict:
    """Classify mapped orders as VALID, ERROR, or SKIPPED."""
    validated_orders: List[Dict] = []
    valid_count = 0
    error_count = 0
    skipped_count = 0

    for order in orders:
        try:
            order_copy = dict(order)
            if order_copy.get("already_exists"):
                order_copy["status"] = "SKIPPED"
                skipped_count += 1
            elif order_copy.get("errors"):
                order_copy["status"] = "ERROR"
                error_count += 1
            else:
                order_copy["status"] = "VALID"
                valid_count += 1
            validated_orders.append(order_copy)
        except Exception as exc:
            logger.exception("Lỗi validate đơn %s: %s", order.get("order_ref"), exc)
            validated_orders.append(
                {
                    **order,
                    "status": "ERROR",
                    "errors": order.get("errors", [])
                    + [f"Lỗi validate đơn hàng: {str(exc)}"],
                }
            )
            error_count += 1

    summary = {
        "total": len(validated_orders),
        "valid": valid_count,
        "error": error_count,
        "skipped": skipped_count,
        "orders": validated_orders,
    }
    logger.info(
        "Validate batch: total=%s valid=%s error=%s skipped=%s",
        summary["total"],
        summary["valid"],
        summary["error"],
        summary["skipped"],
    )
    return summary
