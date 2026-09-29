"""StonePi Recover UI — HTML login (portal chrome) + restore picker."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

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
]

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="StonePi Recover", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _page(request: Request, name: str, ctx: dict | None = None, status_code: int = 200):
    data = {
        "app_version": __version__,
        "asset_rev": __asset_rev__,
        "fonts_rev": __fonts_rev__,
    }
    if ctx:
        data.update(ctx)
    return templates.TemplateResponse(request, name, data, status_code=status_code)


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
    """Prefer Vault (Settings source of truth), then env, then recover.passwd."""
    try:
        from stonepi_vault import get_secret

        pw = (get_secret("STONEPI_RECOVER_PASSWORD", env_name="STONEPI_RECOVER_PASSWORD", default="") or "").strip()
        if not pw:
            pw = (get_secret(LEGACY_VAULT_KEY, env_name=LEGACY_VAULT_KEY, default="") or "").strip()
        if pw:
            return ("stonepi", pw)
    except Exception:
        pass
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


def migrate_legacy_password() -> None:
    """Carry the pre-rename Recovery password forward (file + Vault key)."""
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
        legacy = (vault.get(LEGACY_VAULT_KEY, "") or "").strip()
        if legacy:
            if not (vault.get("STONEPI_RECOVER_PASSWORD", "") or "").strip():
                vault.set("STONEPI_RECOVER_PASSWORD", legacy)
            vault.delete(LEGACY_VAULT_KEY)
    except Exception:
        pass


def ensure_recover_password() -> str | None:
    """Create recover.passwd if missing; return plaintext when newly created."""
    if PASSWD_FILE.is_file():
        return None
    password = secrets.token_urlsafe(12)
    PASSWD_FILE.parent.mkdir(parents=True, exist_ok=True)
    PASSWD_FILE.write_text(f"stonepi:{password}\n", encoding="utf-8")
    try:
        PASSWD_FILE.chmod(0o600)
    except Exception:
        pass
    try:
        from stonepi_vault import set_secret

        set_secret("STONEPI_RECOVER_PASSWORD", password)
    except Exception:
        pass
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
        {"username": "stonepi", "error": request.query_params.get("err") or ""},
    )


@app.post("/login")
def login_post(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
):
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
        return _page(
            request,
            "login.html",
            {"username": username or "stonepi", "error": "Invalid credentials"},
            status_code=401,
        )
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
def logout():
    resp = RedirectResponse("login", status_code=303)
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
    op: str = Form(...),
    unit: str = Form(""),
    _auth: None = Depends(require_recover_auth),
):
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


@app.post("/restore")
def restore(
    path: str = Form(""),
    _auth: None = Depends(require_recover_auth),
):
    path = (path or "").strip()
    local_ok = path in {"/var/backups/stonepi/current", "/var/backups/stonepi/current/"}
    usb_ok = path.startswith("/mnt/stonepi-backup/RaspberryPi-Backup/")
    if not (local_ok or usb_ok) or not Path(path).is_dir():
        return RedirectResponse("./?err=Invalid+backup+path", status_code=303)
    code, out = _run([HELPER, "restore", path], timeout=600)
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
