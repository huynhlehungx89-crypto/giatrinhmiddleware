import re
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

from config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES
import database

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
DEFAULT_SYNC_PASSWORD = "12345678"


def slugify_vietnamese(name: str) -> str:
    """
    Convert Vietnamese display name to username slug.
    Lowercase, no accents, alphanumeric only, no spaces.
    """
    if not name:
        return ""
    text = str(name).strip()
    text = text.replace("đ", "d").replace("Đ", "d")
    normalized = unicodedata.normalize("NFD", text)
    without_marks = "".join(
        ch for ch in normalized if unicodedata.category(ch) != "Mn"
    )
    lowered = without_marks.lower()
    slug = re.sub(r"[^a-z0-9]", "", lowered)
    return slug


def allocate_unique_username(conn, base_slug: str, reserved: set) -> str:
    """Resolve username collisions with _2, _3 suffixes (DB + in-batch)."""
    if not base_slug:
        base_slug = "user"
    candidate = base_slug
    suffix = 2
    while True:
        if candidate in reserved:
            candidate = f"{base_slug}_{suffix}"
            suffix += 1
            continue
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (candidate,)
        ).fetchone()
        if row:
            candidate = f"{base_slug}_{suffix}"
            suffix += 1
            continue
        reserved.add(candidate)
        return candidate


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(request: Request) -> Optional[dict]:
    """Read JWT from cookie and return user dict, or None if invalid."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        user_id: str = payload.get("user_id")
        if not username:
            return None
        return {"username": username, "role": role, "id": user_id}
    except JWTError:
        return None


def require_login(request: Request) -> dict:
    """Use this in routes that require any logged-in user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def require_admin(request: Request) -> dict:
    """Use this in routes that require admin role."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin mới có quyền truy cập.")
    return user


def get_user_by_username(username: str) -> Optional[dict]:
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_user_by_username_any(username: str) -> Optional[dict]:
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_user_by_id(user_id: str) -> Optional[dict]:
    conn = database.get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_user_by_odoo_employee_id(employee_id: int) -> Optional[dict]:
    conn = database.get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE odoo_employee_id = ?", (employee_id,)
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def update_last_login(user_id: str):
    conn = database.get_db()
    conn.execute(
        "UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user_id,)
    )
    conn.commit()
    conn.close()
