from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote, urlparse

COOKIE_NAME = "stonepi"
CSRF_COOKIE = "stonepi_csrf"
COOKIE_MAX_AGE = 60 * 60 * 24 * 14


@dataclass
class PlatformUser:
    user_id: str
    username: str
    display_name: str
    is_admin: bool
    apps: list[str] = field(default_factory=list)
    permissions: dict[str, dict[str, bool]] = field(default_factory=dict)
    session_id: str = ""
    exp: int = 0

    def can_access(self, app_id: str) -> bool:
        # Admins still go through the apps list so platform-disabled apps stay off.
        return app_id in self.apps

    def has_capability(self, app_id: str, capability: str) -> bool:
        if not self.can_access(app_id):
            return False
        if self.is_admin:
            return True
        return bool((self.permissions.get(app_id) or {}).get(capability))


def encode_session(
    *,
    secret: str,
    user_id: str,
    username: str,
    display_name: str,
    is_admin: bool,
    apps: list[str],
    session_id: str,
    permissions: dict[str, dict[str, bool]] | None = None,
    max_age: int = COOKIE_MAX_AGE,
) -> str:
    body = json.dumps(
        {
            "uid": user_id,
            "u": username,
            "dn": display_name or username,
            "adm": bool(is_admin),
            "apps": list(apps),
            "perms": permissions or {},
            "sid": session_id,
            "exp": int(time.time()) + max_age,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
    return f"{token}.{_sign(secret, body)}"


def decode_session(value: str | None, secret: str) -> PlatformUser | None:
    if not value or "." not in value or not secret:
        return None
    token, signature = value.rsplit(".", 1)
    pad = "=" * (-len(token) % 4)
    try:
        body = base64.urlsafe_b64decode(token + pad)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_sign(secret, body), signature):
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if int(data.get("exp") or 0) < time.time():
        return None
    user_id = str(data.get("uid") or "")
    username = str(data.get("u") or "")
    if not user_id or not username:
        return None
    apps = [str(item) for item in (data.get("apps") or [])]
    raw_perms = data.get("perms") or {}
    permissions: dict[str, dict[str, bool]] = {}
    if isinstance(raw_perms, dict):
        for app_id, caps in raw_perms.items():
            if not isinstance(caps, dict):
                continue
            permissions[str(app_id)] = {str(k): bool(v) for k, v in caps.items()}
    return PlatformUser(
        user_id=user_id,
        username=username,
        display_name=str(data.get("dn") or username),
        is_admin=bool(data.get("adm")),
        apps=apps,
        permissions=permissions,
        session_id=str(data.get("sid") or ""),
        exp=int(data.get("exp") or 0),
    )


def read_request_session(cookies: Mapping[str, str] | None, secret: str) -> PlatformUser | None:
    if not cookies:
        return None
    return decode_session(cookies.get(COOKIE_NAME), secret)


def set_cookie(response: Any, value: str, *, secure: bool = False, max_age: int = COOKIE_MAX_AGE) -> None:
    response.set_cookie(
        COOKIE_NAME,
        value,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_cookie(response: Any) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def login_url(settings, next_url: str = "/") -> str:
    base = (settings.auth_url or "/auth").rstrip("/")
    nxt = next_url or "/"
    return f"{base}/login?next={quote(nxt, safe='')}"


def logout_url(settings, next_url: str = "/") -> str:
    base = (settings.auth_url or "/auth").rstrip("/")
    nxt = next_url or "/"
    return f"{base}/logout?next={quote(nxt, safe='')}"


def safe_next(value: str | None, *, allowed_ports: set[int] | None = None) -> str:
    if not value:
        return "/"
    parsed = urlparse(value)
    if not parsed.scheme and not parsed.netloc:
        if value.startswith("/") and not value.startswith("//"):
            return value
        return "/"
    host = (parsed.hostname or "").lower()
    if not _host_allowed_for_redirect(host):
        return "/"
    ports = allowed_ports or {80, 443, 8001, 8002, 8003, 8004, 8005, 8006, 8007, 8010, 8011, 8080, 8081, 8085}
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ports:
        return "/"
    return value


def _host_allowed_for_redirect(host: str) -> bool:
    """Allow loopback, private LAN IPs, and common household LAN DNS suffixes."""
    if not host:
        return False
    if host in {"127.0.0.1", "localhost", "stonepi.local"}:
        return True
    if host.endswith((".local", ".home", ".lan", ".internal")):
        return True
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return False


def _sign(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
