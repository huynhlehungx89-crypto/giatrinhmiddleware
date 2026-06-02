import uuid
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from core.auth import (
    verify_password,
    create_access_token,
    get_user_by_username,
    update_last_login,
    get_current_user,
    hash_password,
)
from config import APP_NAME
import database

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    # If already logged in, redirect to upload
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/upload", status_code=302)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": None,
        "error": None,
    })


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    user = get_user_by_username(username)

    if not user or not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "app_name": APP_NAME,
            "user": None,
            "error": "Tên đăng nhập hoặc mật khẩu không đúng.",
        }, status_code=401)

    # Update last login timestamp
    update_last_login(user["id"])

    # Create JWT token
    token = create_access_token({
        "sub": user["username"],
        "role": user["role"],
        "user_id": user["id"],
    })

    # Set token in cookie and redirect
    response = RedirectResponse(url="/upload", status_code=302)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,       # Not accessible via JS
        max_age=60 * 480,    # 8 hours
        samesite="lax",
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response
