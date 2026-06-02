# VinMart → Odoo Import Middleware

FastAPI application for importing VinMart Excel order files into Odoo (`sale.order` + `sale.order.line`).

## Features

- Admin login (JWT cookie)
- Upload and parse VinMart `.xlsx` / `.xls` / `.csv`
- Map stores and products against Odoo
- Preview VALID / ERROR / SKIPPED orders
- Push valid orders into Odoo
- Import history and result screens

## Requirements

- Python 3.10+
- Odoo instance with XML-RPC enabled
- SQLite (default, local `data/app.db`)

## Quick start

```bash
cd odoo-middleware
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/Mac: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your secret key and admin password
mkdir -p data uploads
python -c "import database; database.init_db(); database.seed_admin()"
python -m uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 — default login from `.env` (`admin` / password you set).

## Odoo configuration

1. Log in as admin
2. Go to **Cài đặt** (`/settings`)
3. Enter Odoo URL, database name, username, and API key (or password)

Test connection:

```bash
python debug_odoo.py
```

## Docker (optional)

```bash
docker compose up --build
```

App runs on http://localhost:8000. Mount volumes for `data/` and `uploads/` as needed.

## Project layout

```
core/          # auth, parser, mapper, validator, importer, odoo client
routes/        # FastAPI routers
templates/     # Jinja2 HTML
static/        # CSS
data/          # SQLite DB (gitignored)
uploads/       # Uploaded Excel files (gitignored)
```

## Environment variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | JWT signing key (32+ random chars in production) |
| `APP_NAME` | UI title |
| `ADMIN_USERNAME` | Seed admin username |
| `ADMIN_PASSWORD` | Seed admin password |
| `APP_ENV` | `development` enables `/debug/odoo` for admins |

## Security

Never commit `.env`, `data/`, or `uploads/`. Use `.env.example` as a template only.
