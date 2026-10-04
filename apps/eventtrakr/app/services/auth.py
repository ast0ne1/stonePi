from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable

from flask import Request, Response, abort, flash, g, jsonify, redirect, request, url_for
from stonepi_auth.http import request_is_https

from app.config import DATA_DIR, env
from app.db import SessionLocal
from app.models import User
from app.services import capabilities, hostname, passwords

COOKIE_NAME = "eventtrakr_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 14
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5


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
        app_id=env.stonepi_app_id or "eventtrakr",
        prefix=env.stonepi_prefix,
        auth_url=browser_auth_url(env.stonepi_auth_url),
        public_origin=env.stonepi_public_origin,
        hostname=hostname.device_hostname(),
    )


def _platform_user():
    settings = _platform_settings()
    if settings is None:
        return None
    from stonepi_auth.session import decode_session

    return decode_session(request.cookies.get("stonepi"), settings.session_secret)


@dataclass
class SessionUser:
    username: str
    user_id: int
    role: str = "user"
    is_public: bool = False
    default_location: str = "Copenhagen, Denmark"
    # StonePi capabilities for EventTrakr (see services/capabilities.py).
    capabilities: dict[str, bool] = field(default_factory=dict)

    def can(self, cap: str) -> bool:
        if self.role == "admin":
            return True
        if cap in self.capabilities:
            return bool(self.capabilities[cap])
        return bool(capabilities.defaults().get(cap, False))


_login_failures: dict[str, list[float]] = defaultdict(list)


def session_secret() -> str:
    if env.session_secret.strip():
        return env.session_secret.strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "session.secret"
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    val = secrets.token_hex(32)
    path.write_text(val, encoding="utf-8")
    return val


def _secret_bytes() -> bytes:
    return session_secret().encode("utf-8")


def _sign(payload: bytes) -> str:
    return hmac.new(_secret_bytes(), payload, hashlib.sha256).hexdigest()


def create_session_token(user_id: int, username: str, role: str) -> str:
    now = int(time.time())
    data = {
        "uid": user_id,
        "u": username,
        "r": role,
        "iat": now,
        "exp": now + COOKIE_MAX_AGE,
    }
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii")
    sig = _sign(raw)
    return f"{b64}.{sig}"


def parse_session_token(token: str | None) -> SessionUser | None:
    if not token or "." not in token:
        return None
    b64, sig = token.split(".", 1)
    try:
        raw = base64.urlsafe_b64decode(b64.encode("ascii"))
    except Exception:
        return None
    expected = _sign(raw)
    if not passwords.secrets_compare(sig, expected):
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None

    exp = data.get("exp", 0)
    if time.time() > exp:
        return None

    uid = data.get("uid")
    username = data.get("u")
    role = data.get("r", "user")

    if not uid or not username:
        return None

    with SessionLocal() as db:
        user = db.get(User, uid)
        if not user or not user.active:
            return None
        return SessionUser(
            username=user.username,
            user_id=user.id,
            role=user.role,
            is_public=user.is_public,
            default_location=user.default_location,
            capabilities=capabilities.for_user(user),
        )


def check_rate_limit(ip: str) -> bool:
    now = time.time()
    cutoff = now - LOGIN_WINDOW_SECONDS
    attempts = [t for t in _login_failures[ip] if t > cutoff]
    _login_failures[ip] = attempts
    return len(attempts) < LOGIN_MAX_FAILURES


def record_login_failure(ip: str) -> None:
    _login_failures[ip].append(time.time())


def clear_login_failures(ip: str) -> None:
    _login_failures.pop(ip, None)


def get_current_user() -> SessionUser | None:
    if hasattr(g, "current_user") and g.current_user is not None:
        return g.current_user
    platform = _platform_user()
    if platform is not None:
        if not platform.can_access("eventtrakr"):
            g.current_user = None
            return None
        from app.services import users as users_svc

        local = users_svc.get_or_create_from_platform(platform)
        g.current_user = SessionUser(
            username=local.username,
            user_id=local.id,
            role=local.role,
            is_public=local.is_public,
            default_location=local.default_location,
            capabilities=capabilities.from_platform(platform),
        )
        return g.current_user
    token = request.cookies.get(COOKIE_NAME)
    user = parse_session_token(token)
    g.current_user = user
    return user


def login_required(f: Callable) -> Callable:
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        user = get_current_user()
        if not user:
            settings = _platform_settings()
            pfx = (env.stonepi_prefix or "").rstrip("/")
            path = request.path or "/"
            if pfx and path != pfx and not path.startswith(f"{pfx}/"):
                path = f"{pfx}{path}" if path.startswith("/") else f"{pfx}/{path}"
            if settings is not None:
                from stonepi_auth import login_url

                return redirect(login_url(settings, path))
            return redirect(url_for("auth.login", next=path))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f: Callable) -> Callable:
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        user = get_current_user()
        if not user:
            pfx = (env.stonepi_prefix or "").rstrip("/")
            path = request.path or "/"
            if pfx and path != pfx and not path.startswith(f"{pfx}/"):
                path = f"{pfx}{path}" if path.startswith("/") else f"{pfx}/{path}"
            return redirect(url_for("auth.login", next=path))
        if user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def capability_required(cap: str, *, redirect_endpoint: str, **redirect_args: Any) -> Callable:
    """Refuse members without an EventTrakr capability (use under ``login_required``).

    Pages and forms flash "Ask an admin to allow …" and go back to
    ``redirect_endpoint``; JSON endpoints get a 403 with the same message.
    """

    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated_function(*args: Any, **kwargs: Any) -> Any:
            user = get_current_user()
            if user is not None and not user.can(cap):
                message = capabilities.denied_message(cap)
                if _wants_json():
                    return jsonify({"error": message, "capability": cap}), 403
                flash(message, "error")
                return redirect(url_for(redirect_endpoint, **redirect_args))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def _wants_json() -> bool:
    path = request.path or ""
    pfx = (env.stonepi_prefix or "").rstrip("/")
    if pfx and path.startswith(pfx):
        path = path[len(pfx):]
    return path.startswith("/api/") or request.is_json


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax")
