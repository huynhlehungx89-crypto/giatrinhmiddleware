import sys
sys.path.insert(0, ".")

from core.file_parser import parse_vinmart_file, group_rows_into_orders

import os

possible_paths = [
    "uploads/Danhsachdonhang_20260527-164030.xlsx",
    "Danhsachdonhang_20260527-164030.xlsx",
]

filepath = None
for p in possible_paths:
    if os.path.exists(p):
        filepath = p
        break

if not filepath:
    print("❌ File not found. Please copy the VinMart sample file into the uploads/ folder")
    print("   Then rerun this test")
else:
    print(f"✅ Found file at: {filepath}")
    rows = parse_vinmart_file(filepath)
    print(f"Rows parsed: {len(rows)}")
    orders = group_rows_into_orders(rows)
    print(f"Orders grouped: {len(orders)}")
    for o in orders:
        print(f"  Order {o['order_ref']}: {len(o['lines'])} lines, store: {o['store_code']}")
