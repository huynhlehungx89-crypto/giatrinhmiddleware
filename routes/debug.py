from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from config import APP_ENV
from core.auth import get_current_user
from core.odoo_diagnostics import run_odoo_diagnostics

router = APIRouter()


@router.get("/debug/odoo")
async def debug_odoo_api(request: Request):
    if APP_ENV != "development":
        raise HTTPException(status_code=404, detail="Not found")

    user = get_current_user(request)
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin mới có quyền truy cập.")

    diag = run_odoo_diagnostics()
    return JSONResponse(diag)
