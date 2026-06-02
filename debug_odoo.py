"""Odoo XML-RPC diagnostic script. Run: python debug_odoo.py"""
import json
import sys

sys.path.insert(0, ".")

from core.mapper import _find_partner
from core.odoo_client import create_odoo_client, get_odoo_settings
from core.odoo_diagnostics import format_diagnostics_summary, run_odoo_diagnostics

IMPORT_VERIFY_REFS = [
    "4190155099",
    "4190170395",
    "4190203412",
    "4190206135",
]

VINMART_STORES = [
    ("4781", "WM+ HNI 314 Trần Cung"),
    ("3266", "WM+ HNI CT2E Chung cư VOV"),
    ("2BP9", "WIN HNI 27A1 Green Stars"),
    ("3552", "WM+ HNI TT7-7 KĐT mới Văn Phú"),
    ("3716", "WM+ HNI CT2-105 KĐT Văn Khê"),
]


def main():
    settings = get_odoo_settings()
    if not settings:
        print("Chưa có cài đặt Odoo. Vào /settings trước.")
        print("API key: not configured")
        return

    print("=== Odoo diagnostics ===")
    print(f"URL: {settings['url']}")
    print(f"Database: {settings['database']}")
    print(f"Username: {settings['username']}")
    print("API key: configured")

    diag = run_odoo_diagnostics(
        store_code="4781",
        store_name="314 Trần Cung",
        barcode="1101196000000",
        order_ref="4190155099",
    )

    if not diag.get("ok") and diag.get("error") and not diag.get("uid"):
        print(f"\nERROR: {diag['error']}")
        print("\n=== Summary ===")
        for line in format_diagnostics_summary(diag):
            print(line)
        return

    print(f"\nUser ID: {diag.get('uid')}")

    print("\n--- Partner lookup (mapper: name ilike + customer_rank) ---")
    try:
        client = create_odoo_client()
        for code, name in VINMART_STORES:
            partner, method = _find_partner(client, code, name)
            if partner:
                print(
                    f"  ✅ [{code}] {name} → id={partner['id']}, "
                    f"name={partner.get('name')!r}, method={method}"
                )
            else:
                print(f"  ❌ [{code}] {name} → NOT FOUND")
    except Exception as exc:
        print(f"  ERROR: {exc}")

    print("\n--- Product search (barcode=1101196000000) ---")
    for attempt in diag.get("product_attempts", []):
        if attempt.get("method") == "product_barcode":
            print(json.dumps(attempt, indent=2, ensure_ascii=False))

    print("\n--- Duplicate check (client_order_ref=4190155099) ---")
    print(json.dumps(diag.get("duplicate_check", []), indent=2, ensure_ascii=False))

    print("\n=== Summary ===")
    for line in format_diagnostics_summary(diag):
        print(line)

    print("\n=== Import verification (4 expected SOs) ===")
    try:
        client = create_odoo_client()
        rows = client.search_read(
            "sale.order",
            [("client_order_ref", "in", IMPORT_VERIFY_REFS)],
            [
                "name",
                "partner_id",
                "date_order",
                "amount_total",
                "state",
                "client_order_ref",
            ],
            limit=20,
            order="client_order_ref asc",
        )
        print(f"Found {len(rows)} / {len(IMPORT_VERIFY_REFS)} sale.order records")
        for row in rows:
            partner = row.get("partner_id")
            partner_name = partner[1] if isinstance(partner, (list, tuple)) else partner
            print(
                f"  SO {row.get('name')} | PO {row.get('client_order_ref')} | "
                f"Partner: {partner_name} | date_order: {row.get('date_order')} | "
                f"amount_total: {row.get('amount_total')} | state: {row.get('state')}"
            )
        missing = set(IMPORT_VERIFY_REFS) - {
            r.get("client_order_ref") for r in rows
        }
        if missing:
            print(f"  ❌ Missing PO refs: {', '.join(sorted(missing))}")
        else:
            print("  ✅ All 4 client_order_ref values exist in Odoo")
    except Exception as exc:
        print(f"  Verification ERROR: {exc}")


if __name__ == "__main__":
    main()
