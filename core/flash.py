import base64
import json
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.responses import RedirectResponse

FLASH_COOKIE = "flash_message"


def set_flash(response: RedirectResponse, level: str, message: str) -> RedirectResponse:
    payload = json.dumps({"level": level, "message": message}, ensure_ascii=False)
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    response.set_cookie(
        FLASH_COOKIE,
        encoded,
        max_age=120,
        httponly=False,
        samesite="lax",
    )
    return response


def get_flash(request: Request) -> Optional[Dict[str, str]]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    try:
        try:
            decoded = base64.b64decode(raw.encode("ascii")).decode("utf-8")
            data = json.loads(decoded)
        except Exception:
            data = json.loads(raw)
        if isinstance(data, dict) and data.get("message"):
            return {"level": data.get("level", "success"), "message": data["message"]}
    except (json.JSONDecodeError, ValueError):
        return {"level": "error", "message": raw}
    return None


def clear_flash_cookie(response) -> None:
    response.delete_cookie(FLASH_COOKIE)


def template_context(request: Request, **extra: Any) -> Dict[str, Any]:
    ctx = dict(extra)
    ctx["flash"] = get_flash(request)
    return ctx
