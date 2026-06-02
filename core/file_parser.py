import logging
import os
from datetime import date
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}

SOURCE_TO_CANONICAL = {
    "Mã đơn hàng": "order_ref",
    "Ngày đặt hàng": "order_date",
    "Ngày yêu cầu giao hàng": "delivery_date",
    "Mã điểm giao": "store_code",
    "Tên điểm giao": "store_name",
    "Mã Barcode hàng hóa": "product_barcode",
    "Tên hàng": "product_name",
    "Số lượng đặt hàng": "quantity",
    "Đơn giá": "unit_price",
    "Trạng thái": "status",
}
REQUIRED_SOURCE_COLUMNS = list(SOURCE_TO_CANONICAL.keys())
CANONICAL_COLUMNS = list(SOURCE_TO_CANONICAL.values())


def _parse_date_value(value):
    if pd.isna(value):
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def parse_vinmart_file(filepath: str) -> List[Dict]:
    try:
        if not os.path.exists(filepath):
            raise ValueError(f"Không tìm thấy file: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError("Định dạng file không hợp lệ. Chỉ hỗ trợ .xlsx, .xls, .csv.")

        file_size = os.path.getsize(filepath)
        if file_size > MAX_FILE_SIZE_BYTES:
            raise ValueError("File quá lớn (>10MB). Vui lòng giảm dung lượng trước khi import.")

        if ext == ".csv":
            df = pd.read_csv(filepath, dtype=object)
        else:
            excel_file = pd.ExcelFile(filepath)
            sheet_name = "WIN" if "WIN" in excel_file.sheet_names else None
            if not sheet_name and "Sheet1" in excel_file.sheet_names:
                sheet_name = "Sheet1"
            if not sheet_name:
                sheet_name = excel_file.sheet_names[0]
            df = pd.read_excel(filepath, sheet_name=sheet_name, dtype=object)

        df.columns = [str(c).strip() for c in df.columns]
        missing_columns = [c for c in REQUIRED_SOURCE_COLUMNS if c not in df.columns]
        if missing_columns:
            raise ValueError(f"Thiếu cột bắt buộc: {', '.join(missing_columns)}")

        total_rows = len(df)
        logger.info("Đã đọc %s dòng từ file %s", total_rows, filepath)

        canonical_df = df[REQUIRED_SOURCE_COLUMNS].rename(columns=SOURCE_TO_CANONICAL).copy()

        for col in ["order_ref", "store_code", "product_barcode"]:
            canonical_df[col] = canonical_df[col].fillna("").astype(str).str.strip()

        canonical_df["store_name"] = canonical_df["store_name"].fillna("").astype(str).str.strip()
        canonical_df["product_name"] = canonical_df["product_name"].fillna("").astype(str).str.strip()
        canonical_df["status"] = canonical_df["status"].fillna("").astype(str).str.strip()

        canonical_df["quantity"] = pd.to_numeric(canonical_df["quantity"], errors="coerce").fillna(0).astype(int)
        canonical_df["unit_price"] = pd.to_numeric(canonical_df["unit_price"], errors="coerce").fillna(0.0).astype(float)
        canonical_df["order_date"] = canonical_df["order_date"].apply(_parse_date_value)
        canonical_df["delivery_date"] = canonical_df["delivery_date"].apply(_parse_date_value)

        filtered_df = canonical_df[canonical_df["status"] == "Chờ NCC xử lý"].copy()
        filtered_rows = len(filtered_df)
        logger.info("Đã lọc còn %s dòng có trạng thái 'Chờ NCC xử lý'", filtered_rows)

        if filtered_rows == 0:
            logger.warning("Không có dòng nào thỏa điều kiện trạng thái 'Chờ NCC xử lý'.")
            return []

        return filtered_df[CANONICAL_COLUMNS].to_dict(orient="records")
    except Exception as exc:
        logger.exception("Lỗi parse file VinMart: %s", exc)
        raise


def group_rows_into_orders(rows: List[Dict]) -> List[Dict]:
    try:
        grouped: Dict[str, Dict] = {}
        for row in rows:
            order_ref = str(row.get("order_ref", "")).strip()
            if not order_ref:
                continue

            if order_ref not in grouped:
                grouped[order_ref] = {
                    "order_ref": order_ref,
                    "store_code": str(row.get("store_code", "")).strip(),
                    "store_name": str(row.get("store_name", "")).strip(),
                    "order_date": row.get("order_date"),
                    "delivery_date": row.get("delivery_date"),
                    "lines": [],
                }

            grouped[order_ref]["lines"].append(
                {
                    "product_barcode": str(row.get("product_barcode", "")).strip(),
                    "product_name": str(row.get("product_name", "")).strip(),
                    "quantity": int(row.get("quantity", 0) or 0),
                    "unit_price": float(row.get("unit_price", 0.0) or 0.0),
                }
            )

        orders = list(grouped.values())
        logger.info("Đã nhóm %s dòng thành %s đơn hàng", len(rows), len(orders))
        return orders
    except Exception as exc:
        logger.exception("Lỗi khi nhóm dòng thành đơn hàng: %s", exc)
        raise
