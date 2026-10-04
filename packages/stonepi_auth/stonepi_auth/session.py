from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote, urlparse

from .catalog import canonical_app_id

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
    using_factory_admin: bool = False
    # Platform Phone alerts permission; None for cookies issued before it existed.
    phone_alerts: bool | None = None

    def can_access(self, app_id: str) -> bool:
        # Admins still go through the apps list so platform-disabled apps stay off.
        return app_id in self.apps

    def has_capability(self, app_id: str, capability: str) -> bool:
        if not self.can_access(app_id):
            return False
        if self.is_admin:
            return True
        granted = self.permissions.get(app_id) or {}
        if capability in granted:
            return bool(granted[capability])
        # Cookies issued before a capability existed don't carry it: use the catalog
        # default, as Auth does for grants saved before it existed.
        try:
            from .catalog import canonical_app_id, capabilities_for

            for item in capabilities_for(canonical_app_id(app_id)):
                if item.get("id") == capability:
                    return bool(item.get("default", False))
        except Exception:  # noqa: BLE001
            pass
        return False


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
    using_factory_admin: bool = False,
    phone_alerts: bool = False,
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
            "fac": bool(using_factory_admin and is_admin),
            "pa": bool(phone_alerts or is_admin),
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
    # canonical_app_id: cookies minted before an app rename keep working.
    apps = [canonical_app_id(str(item)) for item in (data.get("apps") or [])]
    raw_perms = data.get("perms") or {}
    permissions: dict[str, dict[str, bool]] = {}
    if isinstance(raw_perms, dict):
        for app_id, caps in raw_perms.items():
            if not isinstance(caps, dict):
                continue
            permissions[canonical_app_id(str(app_id))] = {str(k): bool(v) for k, v in caps.items()}
    is_admin = bool(data.get("adm"))
    return PlatformUser(
        user_id=user_id,
        username=username,
        display_name=str(data.get("dn") or username),
        is_admin=is_admin,
        apps=apps,
        permissions=permissions,
        session_id=str(data.get("sid") or ""),
        exp=int(data.get("exp") or 0),
        using_factory_admin=bool(data.get("fac")) and is_admin,
        phone_alerts=(bool(data["pa"]) or is_admin) if "pa" in data else None,
    )


def factory_admin_warning(user: PlatformUser | None) -> bool:
    """True when Auth encoded the shared cookie with the factory-password nudge."""
    return bool(user and user.is_admin and user.using_factory_admin)


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
    ports = allowed_ports or {80, 443, 8001, 8002, 8003, 8004, 8005, 8006, 8007, 8008, 8009, 8010, 8011, 8012, 8080, 8081, 8085}
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
