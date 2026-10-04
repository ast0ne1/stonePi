"""StonePi Recover UI — HTML login (portal chrome) + restore picker."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __fonts_rev__, __version__

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8099"))
PASSWD_FILE = Path(os.environ.get("STONEPI_RECOVER_PASSWD", "/etc/stonepi/recover.passwd"))
# Pre-rename (Recovery) names — read as a fallback and migrated forward on startup.
LEGACY_PASSWD_FILE = Path("/etc/stonepi/recovery.passwd")
LEGACY_VAULT_KEY = "STONEPI_RECOVERY_PASSWORD"
HELPER = "/usr/local/sbin/stonepi-backup-helper"
SESSION_COOKIE = "stonepi_recover"
SESSION_MAX_AGE = 60 * 60 * 12  # 12 hours
CSRF_COOKIE = "stonepi_recover_csrf"
# Shared secret nginx adds as X-StonePi-Proxy on /recover/ (install.sh writes both, root 0600).
# Proxy headers are trusted only alongside it, so other local processes can't forge X-Real-IP.
PROXY_TOKEN_FILE = Path(os.environ.get("STONEPI_RECOVER_PROXY_TOKEN", "/etc/stonepi/recover-proxy.token"))
PROXY_TOKEN_HEADER = "x-stonepi-proxy"
# Platform SSO cookies (stonepi_auth.session.COOKIE_NAME / CSRF_COOKIE), named here so
# Recover can sign a browser out even when the shared packages fail to import.
PLATFORM_COOKIES = ("stonepi", "stonepi_csrf")
# Fixed login-page notices (?msg=<code>) so the query string can't inject arbitrary text.
LOGIN_NOTICES = {"signed-out": "Signed out."}
AUTH_ADDR = ("127.0.0.1", 8011)  # nginx upstream stonepi_auth
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES_PER_IP = 5
# Ceiling across every client so a spoofed or rotating source can't brute-force the password.
LOGIN_MAX_FAILURES_GLOBAL = 30
_login_failures: dict[str, list[float]] = defaultdict(list)
VAULT_PASSWORD_KEYS = ("STONEPI_RECOVER_PASSWORD", LEGACY_VAULT_KEY)
LOCAL_BACKUP_DIR = Path("/var/backups/stonepi/current")
USB_BACKUP_ROOT = Path("/mnt/stonepi-backup/RaspberryPi-Backup")
UNITS = [
    "nginx",
    "stonepi-auth",
    "stonepi-dashboard",
    "stonepi-notify",
    "stonepi-newscast",
    "stonepi-fileserve",
    "stonepi-eventtrakr",
    "stonepi-pinboard",
    "stonepi-studio",
    "stonepi-pricescout",
    "stonepi-sportguide",
    "stonepi-pricewatch",
    "stonepi-library",
]

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

log = logging.getLogger("stonepi.recover")

app = FastAPI(title="StonePi Recover", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _dev_mode() -> bool:
    """Windows run-dev or an explicit opt-in; never true on a normal Pi install."""
    return os.name == "nt" or (os.environ.get("RECOVER_DEV") or "").strip() == "1"


def _csrf_token(request: Request) -> str:
    current = (request.cookies.get(CSRF_COOKIE) or "").strip()
    return current or secrets.token_urlsafe(32)


def _set_csrf_cookie(response, token: str) -> None:
    response.set_cookie(CSRF_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="strict", path="/")


def _csrf_valid(request: Request, form_token: str | None) -> bool:
    """Double-submit check: the hidden form field must match Recover's own CSRF cookie."""
    cookie = (request.cookies.get(CSRF_COOKIE) or "").strip()
    supplied = (form_token or "").strip()
    if not cookie or not supplied:
        return False
    return hmac.compare_digest(cookie.encode(), supplied.encode())


def _csrf_reject() -> RedirectResponse:
    return RedirectResponse("./?err=That+form+expired.+Refresh+and+try+again.", status_code=303)


_LOOPBACK = {"127.0.0.1", "::1"}
# (path, mtime_ns, token); re-read when the file changes. The missing-file warning logs once.
_proxy_token_cache: tuple[str, int, str] | None = None
_proxy_token_warned = False


def _proxy_token() -> str:
    """nginx's shared secret from PROXY_TOKEN_FILE, or "" when missing/unreadable."""
    global _proxy_token_cache, _proxy_token_warned
    path = PROXY_TOKEN_FILE
    try:
        mtime = path.stat().st_mtime_ns
        cached = _proxy_token_cache
        if cached and cached[0] == str(path) and cached[1] == mtime:
            return cached[2]
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        token, mtime = "", -1
    _proxy_token_cache = (str(path), mtime, token)
    if not token and not _proxy_token_warned:
        _proxy_token_warned = True
        log.warning(
            "Recover proxy token %s missing or unreadable: treating every request as direct "
            "(nginx traffic shares the loopback login bucket). Re-run deploy/install.sh.",
            path,
        )
    return token


def _behind_proxy(request: Request) -> bool:
    """True when nginx proxied this request (served under /recover/), False on direct :8099.

    nginx connects from loopback and adds X-StonePi-Proxy with the root-only token from
    PROXY_TOKEN_FILE. Any other local process (or a :8099 client) lacks it, so its
    X-Real-IP / X-Forwarded-Host are ignored. No token file means nothing counts as proxied.
    """
    peer = request.client.host if request.client else ""
    if peer not in _LOOPBACK:
        return False
    supplied = (request.headers.get(PROXY_TOKEN_HEADER) or "").strip()
    if not supplied:
        return False
    expected = _proxy_token()
    if not expected:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


def _auth_up() -> bool:
    try:
        with socket.create_connection(AUTH_ADDR, timeout=0.5):
            return True
    except OSError:
        return False


def _portal_home(request: Request) -> str:
    """Portal Home: "/" through nginx; the same host on port 80 when reached on :8099 directly."""
    if _behind_proxy(request):
        return "/"
    host = request.url.hostname or ""
    if not host:
        return "/"
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    return f"http://{host}/"


def _page(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    token = _csrf_token(request)
    data = {
        "app_version": __version__,
        "asset_rev": __asset_rev__,
        "fonts_rev": __fonts_rev__,
        "csrf_token": token,
        "portal_home": _portal_home(request),
    }
    if ctx:
        data.update(ctx)
    response = templates.TemplateResponse(request, name, data, status_code=status_code)
    _set_csrf_cookie(response, token)
    return response


def _client_ip(request: Request) -> str:
    """Socket peer, or nginx's X-Real-IP when the request came through the local proxy.

    X-Real-IP counts only with nginx's X-StonePi-Proxy token (see _behind_proxy); any other
    loopback caller is keyed on its peer address, so they all share one "127.0.0.1" bucket.
    """
    peer = request.client.host if request.client else ""
    if _behind_proxy(request):
        real = (request.headers.get("x-real-ip") or "").strip()
        if real:
            return real
    return peer or "unknown"


def _rate_limited(request: Request) -> bool:
    now = time.time()
    limits = ((f"ip:{_client_ip(request)}", LOGIN_MAX_FAILURES_PER_IP), ("global", LOGIN_MAX_FAILURES_GLOBAL))
    for key, limit in limits:
        stamps = [t for t in _login_failures[key] if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[key] = stamps
        if len(stamps) >= limit:
            return True
    return False


def _record_failure(request: Request) -> None:
    now = time.time()
    _login_failures[f"ip:{_client_ip(request)}"].append(now)
    _login_failures["global"].append(now)


def _clear_failures(request: Request) -> None:
    _login_failures.pop(f"ip:{_client_ip(request)}", None)


def _run(cmd: list[str], timeout: float = 30) -> tuple[int, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout)
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        return result.returncode, out[-8000:]
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _disk_pct() -> str:
    try:
        target = Path("/var/lib/stonepi") if Path("/var/lib/stonepi").exists() else Path("/")
        usage = os.statvfs(target)
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
        if total <= 0:
            return "n/a"
        return f"{int(round(100 * (total - free) / total))}%"
    except Exception:
        return "n/a"


def _unit_rows() -> list[dict]:
    rows = []
    for unit in UNITS:
        _code, out = _run(["systemctl", "is-active", unit], timeout=5)
        status = (out or "unknown").strip() or "unknown"
        low = status.lower()
        if low == "active":
            kind = "ok"
        elif low in {"failed", "inactive", "deactivating"}:
            kind = "bad" if low == "failed" else "warn"
        elif low in {"activating", "reloading"}:
            kind = "busy"
        else:
            kind = "muted"
        rows.append({"unit": unit, "status": status, "status_kind": kind})
    return rows


def _load_passwd() -> tuple[str, str] | None:
    """recover.passwd (root 0600) is the only source on the Pi.

    The Vault is readable by every app user, so the Recover password never lives there.
    An env override is honoured only in dev (Windows run-dev or RECOVER_DEV=1).
    """
    if _dev_mode():
        env_pw = (os.environ.get("STONEPI_RECOVER_PASSWORD") or os.environ.get(LEGACY_VAULT_KEY) or "").strip()
        if env_pw:
            return ("stonepi", env_pw)
    for passwd_file in (PASSWD_FILE, LEGACY_PASSWD_FILE):
        if not passwd_file.is_file():
            continue
        try:
            line = passwd_file.read_text(encoding="utf-8").strip().splitlines()[0]
            if ":" in line:
                user, pw = line.split(":", 1)
                user, pw = user.strip(), pw.strip()
                if user and pw:
                    return (user, pw)
        except Exception:
            pass
    return None


def _signing_key(password: str) -> bytes:
    secret = (
        os.environ.get("STONEPI_SESSION_SECRET")
        or os.environ.get("SESSION_SECRET")
        or password
        or "stonepi-recover"
    )
    return hashlib.sha256(f"recover:{secret}:{password}".encode()).digest()


def _make_session_token(password: str) -> str:
    ts = str(int(time.time()))
    sig = hmac.new(_signing_key(password), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def _valid_session_token(token: str | None, password: str) -> bool:
    if not token or "." not in token:
        return False
    ts, sig = token.split(".", 1)
    try:
        age = int(time.time()) - int(ts)
    except ValueError:
        return False
    if age < 0 or age > SESSION_MAX_AGE:
        return False
    expect = hmac.new(_signing_key(password), ts.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expect)


def _session_admin(request: Request) -> bool:
    try:
        from stonepi_auth.session import COOKIE_NAME, decode_session
        from stonepi_vault import get_secret

        secret = (
            get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="") or ""
        ).strip() or (
            os.environ.get("STONEPI_SESSION_SECRET") or os.environ.get("SESSION_SECRET") or ""
        ).strip()
        if not secret:
            return False
        user = decode_session(request.cookies.get(COOKIE_NAME), secret)
        return bool(user and getattr(user, "is_admin", False))
    except Exception:
        return False


def _recover_session_ok(request: Request) -> bool:
    creds = _load_passwd()
    if creds is None:
        return False
    _user, password = creds
    return _valid_session_token(request.cookies.get(SESSION_COOKIE), password)


class AuthRedirect(Exception):
    """Redirect unauthenticated users to the Recover login page."""


def require_recover_auth(request: Request) -> None:
    if _session_admin(request) or _recover_session_ok(request):
        return
    raise AuthRedirect()


@app.exception_handler(AuthRedirect)
async def _auth_redirect_handler(_request: Request, _exc: AuthRedirect):
    return RedirectResponse("login", status_code=303)


def _list_snapshots() -> list[dict]:
    if not Path(HELPER).exists():
        # Dev / non-Pi: surface local current if present
        local = Path("/var/backups/stonepi/current")
        if local.is_dir():
            return [{"path": str(local), "name": "local-current", "kind": "local", "status": "unknown"}]
        return []
    code, out = _run([HELPER, "list"], timeout=30)
    if code != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _write_passwd_file(password: str) -> None:
    """Write recover.passwd atomically, created 0600 so it is never briefly world-readable."""
    PASSWD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PASSWD_FILE.with_name(PASSWD_FILE.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"stonepi:{password}\n")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(PASSWD_FILE)


def migrate_legacy_password() -> None:
    """Carry the pre-rename Recovery file forward and move any Vault copy into recover.passwd.

    Older builds kept the password in the Vault and preferred it over the file, so a Vault
    value is the one that was actually in use: write it to recover.passwd, then delete the
    Vault key(s) so app users can no longer read it.
    """
    if not PASSWD_FILE.is_file() and LEGACY_PASSWD_FILE.is_file():
        try:
            PASSWD_FILE.parent.mkdir(parents=True, exist_ok=True)
            LEGACY_PASSWD_FILE.replace(PASSWD_FILE)
            PASSWD_FILE.chmod(0o600)
        except Exception:
            pass
    try:
        from stonepi_vault import get_vault

        vault = get_vault()
        values = {key: (vault.get(key, "") or "").strip() for key in VAULT_PASSWORD_KEYS}
    except Exception:
        return
    if not any(values.values()):
        return
    password = values.get("STONEPI_RECOVER_PASSWORD") or values.get(LEGACY_VAULT_KEY) or ""
    try:
        _write_passwd_file(password)
    except Exception:
        # Keep the Vault copy until the file write succeeds, or nobody could sign in.
        return
    for key, value in values.items():
        if value:
            try:
                vault.delete(key)
            except Exception:
                pass


def ensure_recover_password() -> str | None:
    """Create recover.passwd if missing; return plaintext when newly created."""
    if PASSWD_FILE.is_file():
        return None
    password = secrets.token_urlsafe(12)
    _write_passwd_file(password)
    return password


@app.on_event("startup")
def _startup() -> None:
    migrate_legacy_password()
    ensure_recover_password()


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "recover", "auth_configured": _load_passwd() is not None}


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if _session_admin(request) or _recover_session_ok(request):
        return RedirectResponse("./", status_code=303)
    return _page(
        request,
        "login.html",
        {
            "username": "stonepi",
            "error": request.query_params.get("err") or "",
            "notice": LOGIN_NOTICES.get(request.query_params.get("msg") or "", ""),
        },
    )


@app.post("/login")
def login_post(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    if not _csrf_valid(request, csrf_token):
        return _page(
            request,
            "login.html",
            {"username": username or "stonepi", "error": "That form expired. Try again."},
            status_code=400,
        )
    if _rate_limited(request):
        return _page(
            request,
            "login.html",
            {"username": username or "stonepi", "error": "Too many failed attempts. Wait a few minutes and try again."},
            status_code=429,
        )
    creds = _load_passwd()
    if creds is None:
        return _page(
            request,
            "login.html",
            {"username": username or "stonepi", "error": "Recover password not configured"},
            status_code=503,
        )
    expect_user, expect_pw = creds
    user_ok = hmac.compare_digest((username or "").encode(), expect_user.encode())
    pass_ok = hmac.compare_digest((password or "").encode(), expect_pw.encode())
    if not (user_ok and pass_ok):
        _record_failure(request)
        return _page(
            request,
            "login.html",
            {"username": username or "stonepi", "error": "Invalid credentials"},
            status_code=401,
        )
    _clear_failures(request)
    resp = RedirectResponse("./", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        _make_session_token(expect_pw),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return resp


@app.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    if not _csrf_valid(request, csrf_token):
        return _csrf_reject()
    # An admin signed in through the platform SSO cookie skips the Recover form, so only
    # clearing stonepi_recover would bounce straight back to the console. Sign the
    # platform session out too: through Auth (revokes it server-side) when nginx and Auth
    # are up, otherwise by clearing the platform cookies here (direct :8099, Auth down).
    via_platform = _session_admin(request)
    if via_platform and _behind_proxy(request) and _auth_up():
        target = "/auth/logout?next=" + quote("/recover/login?msg=signed-out", safe="")
        resp = RedirectResponse(target, status_code=303)
    else:
        resp = RedirectResponse("login?msg=signed-out", status_code=303)
        if via_platform:
            for name in PLATFORM_COOKIES:
                resp.delete_cookie(name, path="/")
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/", response_class=HTMLResponse)
def home(request: Request, _auth: None = Depends(require_recover_auth)):
    msg = request.query_params.get("msg") or ""
    err = request.query_params.get("err") or ""
    return _page(
        request,
        "home.html",
        {
            "msg": msg,
            "err": err,
            "disk_pct": _disk_pct(),
            "units": _unit_rows(),
            "snapshots": _list_snapshots(),
        },
    )


@app.post("/action")
def action(
    request: Request,
    op: str = Form(...),
    unit: str = Form(""),
    csrf_token: str = Form(""),
    _auth: None = Depends(require_recover_auth),
):
    if not _csrf_valid(request, csrf_token):
        return _csrf_reject()
    op = (op or "").strip().lower()
    unit = (unit or "").strip()
    if op == "reboot":
        _run(["systemctl", "reboot"], timeout=5)
        return RedirectResponse("./?msg=Reboot+requested", status_code=303)
    if op == "failover-off":
        _run([HELPER, "failover-off"], timeout=30)
        return RedirectResponse("./?msg=Failover+cleared", status_code=303)
    if unit not in UNITS:
        return RedirectResponse("./?msg=Unknown+unit", status_code=303)
    if op == "restart":
        code, out = _run(["systemctl", "restart", unit])
        msg = f"Restarted {unit}" if code == 0 else f"Restart failed: {out[:200]}"
    elif op == "stop":
        if unit in {"stonepi-auth", "nginx", "stonepi-recover"}:
            return RedirectResponse("./?msg=Refusing+to+stop+critical+unit", status_code=303)
        code, out = _run(["systemctl", "stop", unit])
        msg = f"Stopped {unit}" if code == 0 else f"Stop failed: {out[:200]}"
    else:
        msg = "Unknown action"
    return RedirectResponse("./?msg=" + msg.replace(" ", "+"), status_code=303)


def _resolve_backup_path(raw: str) -> Path | None:
    """Resolve symlinks and '..', then accept only the local current copy or a USB snapshot dir."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        resolved = Path(raw).resolve(strict=True)
        local = LOCAL_BACKUP_DIR.resolve()
        usb_root = USB_BACKUP_ROOT.resolve()
    except (OSError, RuntimeError):
        return None
    if not resolved.is_dir():
        return None
    if resolved == local:
        return resolved
    if resolved != usb_root and resolved.is_relative_to(usb_root):
        return resolved
    return None


@app.post("/restore")
def restore(
    request: Request,
    path: str = Form(""),
    csrf_token: str = Form(""),
    _auth: None = Depends(require_recover_auth),
):
    if not _csrf_valid(request, csrf_token):
        return _csrf_reject()
    target = _resolve_backup_path(path)
    if target is None:
        return RedirectResponse("./?err=Invalid+backup+path", status_code=303)
    code, out = _run([HELPER, "restore", str(target)], timeout=600)
    if code == 0:
        return RedirectResponse("./?msg=Restore+complete.+Check+Destinations.", status_code=303)
    detail = (out or "Restore failed")[:280].replace(" ", "+")
    return RedirectResponse(f"./?err={detail}", status_code=303)


@app.get("/logs", response_class=HTMLResponse)
def logs(request: Request, _auth: None = Depends(require_recover_auth)):
    units = ["stonepi-dashboard", "stonepi-auth", "stonepi-notify", "nginx"]
    args = ["journalctl", "--no-pager", "-n", "80"]
    for u in units:
        args.extend(["-u", u])
    _code, out = _run(args, timeout=15)
    return _page(request, "logs.html", {"log_text": out or "(empty)"})


@app.get("/network", response_class=HTMLResponse)
def network(request: Request, _auth: None = Depends(require_recover_auth)):
    parts = []
    for cmd in (
        ["hostname", "-I"],
        ["ip", "-br", "addr"],
        ["tailscale", "status", "--json"] if shutil.which("tailscale") else ["echo", "tailscale not installed"],
    ):
        _code, out = _run(cmd, timeout=10)
        parts.append("$ " + " ".join(cmd) + "\n" + (out or ""))
    return _page(request, "network.html", {"net_text": "\n".join(parts)})
