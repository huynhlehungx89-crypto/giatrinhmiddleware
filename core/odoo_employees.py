import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def get_employee_list(odoo_client: Any) -> List[Dict]:
    """Fetch active employees from Odoo hr.employee."""
    rows = odoo_client.search_read(
        "hr.employee",
        [("active", "=", True)],
        ["id", "name"],
        limit=2000,
        order="name asc",
    )
    employees = []
    for row in rows or []:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        employees.append({"id": int(row["id"]), "name": name})
    logger.info("Fetched %s employees from Odoo", len(employees))
    return employees
