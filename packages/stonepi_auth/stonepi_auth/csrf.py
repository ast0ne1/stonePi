from __future__ import annotations

import hmac
import secrets
from typing import Any

from .session import CSRF_COOKIE


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Any, token: str, *, secure: bool = False) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=60 * 60 * 24,
        httponly=False,
        samesite="lax",
        secure=secure,
        path="/",
    )


def csrf_from_request(cookies: dict[str, str] | None) -> str:
    current = (cookies or {}).get(CSRF_COOKIE, "").strip()
    return current or new_csrf_token()


def csrf_ok(cookie_token: str | None, form_token: str | None) -> bool:
    left = (cookie_token or "").strip()
    right = (form_token or "").strip()
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)


def csrf_ok_request(cookies: dict[str, str] | None, form_token: str | None = None, header_token: str | None = None) -> bool:
    """Accept form field or X-StonePi-CSRF header matching the CSRF cookie."""
    cookie = (cookies or {}).get(CSRF_COOKIE)
    if form_token and csrf_ok(cookie, form_token):
        return True
    if header_token and csrf_ok(cookie, header_token):
        return True
    return False
