# DesignUploader — VinMart → Odoo Middleware

> **Living design document** for the `odoo-middleware` project.  
> Last updated: 2026-06-02 (MVP build session).

---

## 1. Purpose

Web middleware that lets staff:

1. Log in
2. Upload VinMart Excel order exports
3. Validate orders against Odoo (partner, product, duplicates)
4. Preview VALID / ERROR / SKIPPED
5. Push VALID orders into Odoo as `sale.order` + `sale.order.line`
6. Review import results and history

**Production Odoo (session-tested):** `http://192.168.1.188:8069`  
**Database:** `giatrinh-erp-3`  
**GitHub:** https://github.com/huynhlehungx89-crypto/giatrinhmiddleware

---

## 2. Tech stack

| Layer | Choice |
|-------|--------|
| API | FastAPI 0.111 |
| Server | Uvicorn |
| Templates | Jinja2 |
| Auth | JWT in HttpOnly cookie (`python-jose` + `passlib/bcrypt`) |
| DB | SQLite (`data/app.db`) |
| Excel | pandas + openpyxl |
| Odoo | XML-RPC (`/xmlrpc/2/common`, `/xmlrpc/2/object`) |

---

## 3. Project structure (source map)

```
odoo-middleware/
├── main.py                 # App entry, routers, DB init, static mount
├── config.py               # Env: SECRET_KEY, APP_NAME, ADMIN_*, APP_ENV, paths
├── database.py             # SQLite schema + seed_admin()
├── requirements.txt
├── .env.example            # Template only (committed)
├── .env                    # Local secrets (GITIGNORED)
├── debug_odoo.py           # CLI Odoo diagnostics + import verification
├── DesignUploader.MD       # This file
│
├── core/
│   ├── auth.py             # JWT, login helpers, require_login/admin
│   ├── file_parser.py      # parse_vinmart_file, group_rows_into_orders
│   ├── mapper.py           # map_order_to_odoo (partner/product/company)
│   ├── validator.py        # validate_batch → VALID | ERROR | SKIPPED
│   ├── importer.py         # import_batch → sale.order + lines in Odoo
│   ├── odoo_client.py      # OdooClient, search_read, process cache
│   └── odoo_diagnostics.py # Shared diagnostics for debug script + /debug/odoo
│
├── routes/
│   ├── auth.py             # GET/POST /login, GET /logout
│   ├── upload.py           # GET/POST /upload
│   ├── preview.py          # GET /preview/{batch_id}, errors CSV
│   ├── import_.py          # POST /import/{batch_id}
│   ├── result.py           # GET /result/{batch_id}
│   ├── history.py          # GET /history
│   ├── settings.py         # GET/POST /settings (admin, Odoo config)
│   └── debug.py            # GET /debug/odoo (APP_ENV=development, admin only)
│
├── templates/              # base, login, upload, preview, result, history, settings
├── static/style.css
├── data/                   # GITIGNORED — app.db
└── uploads/                # GITIGNORED — uploaded Excel files
```

---

## 4. End-to-end flow

```
/login → /upload (POST file)
    → parse_vinmart_file()
    → group_rows_into_orders()
    → map_order_to_odoo() per order (Odoo XML-RPC)
    → validate_batch()
    → save import_batches + import_orders (status VALIDATED)
    → /preview/{batch_id}

/preview → POST /import/{batch_id}
    → import_batch() — create sale.order + sale.order.line
    → /result/{batch_id}

/history → link to /result or /preview
```

### Batch statuses

| Status | Meaning |
|--------|---------|
| `VALIDATED` | Uploaded, preview ready |
| `IMPORTED` | All imports succeeded |
| `PARTIAL` | Some orders failed on import |
| `FAILED` | Import failed entirely |

### Order statuses (preview)

| Status | Meaning |
|--------|---------|
| `VALID` | Ready to import |
| `ERROR` | Mapping/validation errors |
| `SKIPPED` | Already exists in Odoo (`client_order_ref`) |

### Order statuses (after import)

| Status | Meaning |
|--------|---------|
| `SUCCESS` | SO + all lines created |
| `PARTIAL_SUCCESS` | SO created, some lines failed |
| `FAILED` | SO not created or import error |
| `SKIPPED` | Duplicate or already imported locally |

---

## 5. VinMart file format

- Sheet: `WIN` → `Sheet1` → first sheet
- Header row: 1
- Filter: `Trạng thái` == `Chờ NCC xử lý`
- One row = one order line; group by `Mã đơn hàng`

### Column mapping

| Excel column | Canonical field |
|--------------|-----------------|
| Mã đơn hàng | order_ref |
| Ngày đặt hàng | order_date |
| Ngày yêu cầu giao hàng | delivery_date |
| Mã điểm giao | store_code |
| Tên điểm giao | store_name |
| Mã Barcode hàng hóa | product_barcode |
| Tên hàng | product_name |
| Số lượng đặt hàng | quantity |
| Đơn giá | unit_price |
| Trạng thái | status |

### Sample file (tested)

`uploads/Danhsachdonhang_20260527-164030.xlsx`  
→ 19 rows → 5 orders (4 VALID + 1 ERROR for store 2BP9)

---

## 6. Odoo integration (confirmed behaviour)

### Authentication

- `common.authenticate(db, username, api_key, {})` → uid (tested: uid=2)
- Client cached per settings hash + per HTTP request (`get_odoo_client_for_request`)
- Timeout: 15s on XML-RPC

### Partner lookup (`core/mapper.py`)

**Important:** `ref` is empty on VinMart partners in this Odoo — use **name** matching.

Order of attempts:

1. `name ilike` + `customer_rank > 0` (multiple search terms)
2. `name ilike` without customer_rank (fallback)
3. Search terms built from: full `store_name`, stripped `WM+ HNI` / `WIN HNI` prefix, suffix after `HNI `
4. `ref = store_code`
5. `name ilike store_code`

Pick best name match when multiple results; warn if >5 results.

### Product lookup

1. `product.product` barcode
2. `product.template` barcode → `product_variant_ids[0]`
3. `product.product` default_code
4. `product.product` name ilike

Stores: `odoo_product_id`, `odoo_product_name`, `uom_id` in mapped line dict.

### Duplicate check

`sale.order` where `client_order_ref = order_ref`

### Import (`core/importer.py`)

**sale.order fields:**

- `partner_id`, `company_id` (optional retry without company)
- `client_order_ref`, `date_order`, `commitment_date` as `YYYY-MM-DD HH:MM:SS`
- `note`: `"Import từ VinMart"`

**sale.order.line fields:**

- `order_id`, `product_id`, `product_uom_qty` (float), `price_unit` (float)
- `product_uom` if uom_id known

SO id + name saved to DB immediately after SO create, before lines.

### Verified import (2026-06-02)

| VinMart PO | Odoo SO | Partner |
|------------|---------|---------|
| 4190155099 | S00376 | WIN HNI 314 Trần Cung |
| 4190170395 | S00377 | WIN HNI CT2E Chung cư VOV |
| 4190203412 | S00378 | WIN HNI TT7-7 KĐT mới Văn Phú |
| 4190206135 | S00379 | WM+ HNI CT2-105 KĐT Văn Khê |
| 4190187385 | — | ERROR: store 2BP9 not in Odoo |

---

## 7. Database schema (SQLite)

### `users`

id, username, password_hash, role, is_active, created_at, last_login

### `odoo_settings` (singleton id=1)

url, database, username, api_key, updated_at

### `import_batches`

id, filename, source, status, total_rows, valid_orders, error_orders,  
skipped_orders, success_orders, failed_orders, uploaded_by, uploaded_at, imported_at

### `import_orders`

id, batch_id, order_ref, store_code, store_name, order_date, delivery_date,  
line_count, status, error_message, odoo_order_id, odoo_order_name, raw_data (JSON), created_at

---

## 8. Routes reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | — | Redirect `/login` |
| GET/POST | `/login` | — | Login form |
| GET | `/logout` | cookie | Clear session |
| GET | `/upload` | user | Upload form |
| POST | `/upload` | user | Parse + map + save batch |
| GET | `/preview/{batch_id}` | user | Preview table |
| GET | `/preview/{batch_id}/errors` | user | CSV download |
| POST | `/import/{batch_id}` | user | Run import |
| GET | `/result/{batch_id}` | user | Result screen |
| GET | `/history` | user | Batch list (admin=all) |
| GET/POST | `/settings` | admin | Odoo connection |
| GET | `/debug/odoo` | admin | JSON diagnostics (`APP_ENV=development`) |

---

## 9. Environment variables

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | JWT signing |
| `APP_NAME` | UI title |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Seed admin |
| `APP_ENV` | `development` enables `/debug/odoo` |

Copy `.env.example` → `.env` for local run. Never commit `.env`.

---

## 10. Running locally

```bash
pip install -r requirements.txt
cp .env.example .env
python -c "import database; database.init_db(); database.seed_admin()"
python -m uvicorn main:app --reload --port 8000
```

Diagnostics:

```bash
python debug_odoo.py
```

Parser test:

```bash
python test_parser.py
```

---

## 11. Safety & conventions

- Max upload 10MB; extensions `.xlsx`, `.xls`, `.csv` only
- Never log API keys; debug prints `API key: configured` only
- Per-order try/except in map + import (one failure must not stop batch)
- Idempotent `POST /import` if batch already IMPORTED/PARTIAL/FAILED
- Gitignore: `.env`, `data/`, `uploads/`

---

## 12. Known issues / backlog

1. **Store 2BP9** (`WIN HNI 27A1 Green Stars`) — no matching partner in Odoo; order stays ERROR until master data fixed.
2. VinMart Excel uses `WM+ HNI` prefix; Odoo often has `WIN HNI` — mapper strips prefixes for search.
3. `uvicorn` may not be on PATH on Windows; use `python -m uvicorn`.
4. Future: user management UI, scheduled imports, confirm SO in Odoo workflow.

---

## 13. Key code entry points

| Task | File | Function |
|------|------|----------|
| Parse Excel | `core/file_parser.py` | `parse_vinmart_file` |
| Group orders | `core/file_parser.py` | `group_rows_into_orders` |
| Odoo mapping | `core/mapper.py` | `map_order_to_odoo` |
| Classify | `core/validator.py` | `validate_batch` |
| Import | `core/importer.py` | `import_batch` |
| Odoo RPC | `core/odoo_client.py` | `OdooClient.search_read` |

---

## 14. Change log (today)

1. Project scaffold + auth + SQLite
2. VinMart parser + mapper + validator
3. Upload + preview + settings
4. Import + result + history
5. Odoo connection fixes (partner name lookup, search_read, diagnostics)
6. E2E import verified (4 SOs created)
7. Git init + push to GitHub

---

*When extending this project, update this document in the same PR as code changes.*
