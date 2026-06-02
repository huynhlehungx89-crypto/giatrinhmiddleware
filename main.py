from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
import os

from config import APP_NAME, APP_ENV
import database
from core.odoo_client import clear_odoo_client_for_request

# Create required directories
os.makedirs("data", exist_ok=True)
os.makedirs("uploads", exist_ok=True)
os.makedirs("static", exist_ok=True)
os.makedirs("core", exist_ok=True)
os.makedirs("routes", exist_ok=True)

# Initialize database
database.init_db()
database.seed_admin()

app = FastAPI(title=APP_NAME)


@app.middleware("http")
async def odoo_client_request_scope(request, call_next):
    clear_odoo_client_for_request()
    try:
        return await call_next(request)
    finally:
        clear_odoo_client_for_request()


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
from routes import auth, upload, preview, settings, import_, result, history
app.include_router(auth.router)
app.include_router(upload.router)
app.include_router(preview.router)
app.include_router(settings.router)
app.include_router(import_.router)
app.include_router(result.router)
app.include_router(history.router)

if APP_ENV == "development":
    from routes import debug
    app.include_router(debug.router)

# Root redirect to login
@app.get("/")
async def root():
    return RedirectResponse(url="/login", status_code=302)
