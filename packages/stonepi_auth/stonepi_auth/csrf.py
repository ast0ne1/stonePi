from __future__ import annotations

import hmac
import secrets
from typing import Any, Mapping
from urllib.parse import urlsplit

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


def _header(request: Any, name: str) -> str:
    headers = getattr(request, "headers", None)
    if headers is None:
        return ""
    try:
        value = headers.get(name) or headers.get(name.title()) or ""
    except Exception:  # noqa: BLE001
        value = ""
    return str(value or "").strip()


def _request_host(request: Any) -> str:
    host = _header(request, "x-forwarded-host") or _header(request, "host")
    if host:
        return host.split(",")[0].strip()
    return str(getattr(request, "host", "") or "").strip()


def origin_ok(
    origin: str | None,
    host: str | None,
    *,
    referer: str | None = None,
) -> bool:
    """True when Origin (or Referer) matches Host — blocks same-host cross-port CSRF (B1).

    Missing Origin and Referer is allowed (non-browser clients, curl, signed internal tools).
    When either is present, its host must equal the request Host (port included).
    """
    target = (host or "").strip().lower()
    if not target:
        return True
    candidate = (origin or "").strip()
    if not candidate:
        ref = (referer or "").strip()
        if not ref:
            return True
        parts = urlsplit(ref)
        if not parts.scheme or not parts.netloc:
            return True
        candidate = f"{parts.scheme}://{parts.netloc}"
    parts = urlsplit(candidate)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    return parts.netloc.lower() == target


def csrf_ok_request(
    cookies: Mapping[str, str] | Any | None,
    form_token: str | None = None,
    header_token: str | None = None,
    *,
    origin: str | None = None,
    host: str | None = None,
    referer: str | None = None,
) -> bool:
    """Accept form field or X-StonePi-CSRF header matching the CSRF cookie.

    Pass a Starlette/Flask ``request`` as the first argument (or ``host`` / ``origin`` /
    ``referer``) to also require same-origin — another port on the household address
    can read ``stonepi_csrf`` but cannot forge a matching Origin.
    """
    request = None
    cookie_map: Mapping[str, str] | None
    if cookies is not None and hasattr(cookies, "cookies") and hasattr(cookies, "headers"):
        request = cookies
        cookie_map = request.cookies
        host = host or _request_host(request)
        if origin is None:
            origin = _header(request, "origin") or None
        if referer is None:
            referer = _header(request, "referer") or getattr(request, "referrer", None) or None
    else:
        cookie_map = cookies  # type: ignore[assignment]

    cookie = (cookie_map or {}).get(CSRF_COOKIE)
    token_ok = False
    if form_token and csrf_ok(cookie, form_token):
        token_ok = True
    elif header_token and csrf_ok(cookie, header_token):
        token_ok = True
    if not token_ok:
        return False
    if host is not None or origin is not None or referer is not None:
        return origin_ok(origin, host, referer=referer)
    return True
