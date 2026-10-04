from __future__ import annotations

import secrets
import time
from collections import defaultdict
from urllib.parse import urlparse

from flask import Request, Response, g, redirect, request, session, url_for
from stonepi_auth.http import client_ip
from sqlalchemy.orm import Session

from app.config import DATA_DIR, env
from app.models import User
from app.services import hostname, settings, users as users_svc

COOKIE_MAX_AGE = 60 * 60 * 24 * 14
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, list[float]] = defaultdict(list)


def _platform_session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    return env.stonepi_session_secret.strip()


def _platform_settings():
    secret = _platform_session_secret()
    if not secret:
        return None
    from stonepi_auth.config import PlatformSettings
    from stonepi_auth.http import browser_auth_url

    return PlatformSettings(
        enabled=True,
        session_secret=secret,
        app_id=env.stonepi_app_id or "fileserve",
        prefix=env.stonepi_prefix,
        auth_url=browser_auth_url(env.stonepi_auth_url),
        public_origin=env.stonepi_public_origin,
        hostname=hostname.device_hostname(),
    )


def _platform_user():
    settings = _platform_settings()
    if settings is None:
        return None
    from flask import request as flask_request
    from stonepi_auth.session import decode_session

    return decode_session(flask_request.cookies.get("stonepi"), settings.session_secret)


def session_secret() -> str:
    if env.session_secret.strip():
        return env.session_secret.strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "session.secret"
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    value = secrets.token_hex(32)
    path.write_text(value, encoding="utf-8")
    return value


def request_is_https() -> bool:
    if request.is_secure:
        return True
    if not settings.https_enabled(getattr(g, "db", None)):
        return False
    return (request.headers.get("x-forwarded-proto") or "").lower() == "https"


def is_signed_in() -> bool:
    if _platform_settings() is not None:
        # Under StonePi the platform cookie is the sign-in. A FileServe session left
        # behind after StonePi sign-out (or by someone else on this browser) doesn't count.
        return _platform_user() is not None
    return current_user_id() is not None


def current_user_id() -> int | None:
    raw = session.get("uid")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def current_role() -> str:
    return str(session.get("role") or "")


def is_admin() -> bool:
    if _platform_settings() is not None:
        # Read the live cookie so a demotion applies before the FileServe session catches up.
        platform = _platform_user()
        return bool(platform and platform.is_admin and platform.can_access("fileserve"))
    return current_role() == "admin"


def current_user(db: Session | None = None) -> User | None:
    """The signed-in FileServe user, with ``can(cap)`` wired to StonePi capabilities.

    Under StonePi only the platform cookie counts (the local Flask session just mirrors
    it), so a role or capability change, a StonePi sign-out, or a different person signing
    in on the same browser applies on the next request.
    """
    session_db = db or getattr(g, "db", None)
    if session_db is None:
        return None
    if _platform_settings() is not None:
        platform = _platform_user()
        if platform is None or not platform.can_access("fileserve"):
            return None
        local = users_svc.get_or_create_from_platform(session_db, platform)
        if not local.active:
            return None
        local._platform = platform
        local._capabilities = dict((platform.permissions or {}).get("fileserve") or {})
        if session.get("uid") != local.id or session.get("role") != local.role:
            attach_session(local)
        return local
    uid = current_user_id()
    if uid is None:
        return None
    found = users_svc.get_user(session_db, uid)
    if found is None:
        return None
    # Solo FileServe: no StonePi grants, so local accounts keep publishing as before.
    found._platform = None
    found._capabilities = None
    return found


def platform_capability(app_id: str, cap: str) -> bool:
    """Another StonePi app's capability for the signed-in person (e.g. Studio's can_publish).

    Solo FileServe has no other apps, so it never blocks there; admins always pass.
    """
    if _platform_settings() is None:
        return True
    platform = _platform_user()
    if platform is None:
        return is_admin()
    if platform.is_admin:
        return True
    return platform.has_capability(app_id, cap)


def attach_session(user: User) -> None:
    session.permanent = True
    session["uid"] = user.id
    session["user"] = user.username
    session["role"] = user.role
    session.modified = True


def clear_session() -> None:
    session.clear()


def safe_next(value: str | None) -> str:
    if not value:
        return url_for("pages_list")
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/") or value.startswith("//"):
        return url_for("pages_list")
    return value


def wants_json(req: Request | None = None) -> bool:
    req = req or request
    accept = req.headers.get("accept", "")
    return "application/json" in accept or req.headers.get("x-requested-with") == "fetch"


def login_redirect() -> Response:
    nxt = request.full_path
    if nxt.endswith("?"):
        nxt = request.path
    pfx = (env.stonepi_prefix or "").rstrip("/")
    path_only = nxt.split("?", 1)[0]
    if pfx and path_only != pfx and not path_only.startswith(f"{pfx}/"):
        nxt = f"{pfx}{nxt}" if nxt.startswith("/") else f"{pfx}/{nxt}"
    settings = _platform_settings()
    if settings is not None:
        from stonepi_auth import login_url

        return redirect(login_url(settings, nxt))
    return redirect(url_for("login", next=nxt))


# Back-compat for page Basic auth hashing checks
def password_matches(stored: str, provided: str) -> bool:
    from app.services.passwords import verify_password

    return verify_password(stored, provided)


def login_rate_limited(req: Request | None = None, username: str = "") -> bool:
    req = req or request
    now = time.time()
    keys = [f"ip:{client_ip(req)}", f"user:{(username or '').strip().casefold()}"]
    for key in keys:
        stamps = [t for t in _login_failures[key] if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[key] = stamps
        if len(stamps) >= LOGIN_MAX_FAILURES:
            return True
    return False


def record_login_failure(req: Request | None = None, username: str = "") -> None:
    req = req or request
    now = time.time()
    _login_failures[f"ip:{client_ip(req)}"].append(now)
    _login_failures[f"user:{(username or '').strip().casefold()}"].append(now)


def clear_login_failures(req: Request | None = None, username: str = "") -> None:
    req = req or request
    _login_failures.pop(f"ip:{client_ip(req)}", None)
    _login_failures.pop(f"user:{(username or '').strip().casefold()}", None)
