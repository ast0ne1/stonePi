from __future__ import annotations

import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.config import env
from stonepi_auth import APP_CATALOG, LAUNCHER_APP_IDS, COOKIE_NAME, decode_session

TIMEOUT = 1.5


def session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    if env.session_secret.strip():
        return env.session_secret.strip()
    from app.config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "session.secret"
    if path.exists():
        stored = path.read_text(encoding="utf-8").strip()
        if stored:
            return stored
    return ""


def current_user(cookies: dict[str, str]):
    return decode_session(cookies.get(COOKIE_NAME), session_secret())


def app_public_url(item: dict) -> str:
    """Browser-facing app URL for launcher / Services open links.

    Path installs use **relative** paths so the hostname the user opened
    (``.home`` / ``.local`` / LAN IP) never flips to ``PUBLIC_ORIGIN``.
    Check path/nginx **before** loopback ``PUBLIC_ORIGIN`` — Pi installs often
    keep ``http://127.0.0.1:8010`` internally while browsers use ``stonepi.*``.
    Windows / loopback solo-dev keeps per-port absolute URLs.
    """
    path_fronted = getattr(env, "routing", "path") == "path" or Path(
        "/etc/nginx/sites-enabled/stonepi"
    ).exists()
    # Solo-dev on Windows has no nginx — per-port URLs still work for Open links.
    if path_fronted and os.name != "nt":
        if item["id"] == "dashboard":
            return "/"
        if item["id"] == "auth":
            return "/auth/"
        path = str(item.get("path") or "/")
        return path if path.endswith("/") else f"{path}/"
    origin = (env.public_origin or "").rstrip("/")
    if "127.0.0.1" in origin or "localhost" in origin or os.name == "nt":
        return f"http://127.0.0.1:{item['port']}/"
    if item["id"] == "dashboard":
        return origin + "/"
    if item["id"] == "auth":
        return origin + "/auth/"
    return origin + item["path"]


def app_display_url(item: dict, *, access_origin: str = "") -> str:
    """URL text on Health cards — same host the browser used to open Health."""
    origin = (access_origin or "").rstrip("/")
    path_fronted = getattr(env, "routing", "path") == "path" or Path(
        "/etc/nginx/sites-enabled/stonepi"
    ).exists()
    if item["id"] == "dashboard":
        path = "/"
    elif item["id"] == "auth":
        path = "/auth/"
    else:
        path = str(item.get("path") or "/")
        if not path.endswith("/"):
            path = f"{path}/"

    if origin and path_fronted and os.name != "nt":
        return origin + path

    if origin:
        from urllib.parse import urlparse

        parsed = urlparse(origin)
        host = parsed.hostname or (env.hostname or "stonepi").strip() or "stonepi"
        scheme = parsed.scheme or "http"
        return f"{scheme}://{host}:{item['port']}/"

    host = (env.hostname or "stonepi").strip() or "stonepi"
    if path_fronted and os.name != "nt":
        return f"http://{host}.local{path}"
    return f"http://{host}.local:{item['port']}/"


def probe(url: str) -> dict:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.get(url)
        return {"ok": response.status_code < 500, "status": response.status_code}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc)}


def health_url(item: dict) -> str:
    if os.name == "nt" or not Path("/etc/nginx/sites-enabled/stonepi").exists():
        return f"http://127.0.0.1:{item['port']}{item['health']}"
    origin = env.public_origin.rstrip("/")
    if item["id"] == "dashboard":
        return origin + item["health"]
    if item["id"] == "auth":
        return origin + "/auth" + item["health"]
    return origin + item["path"].rstrip("/") + item["health"]


def unit_status(unit: str) -> str:
    """systemd unit state on the Pi (active/inactive/failed…); 'local' on Windows/dev."""
    if os.name == "nt" or shutil.which("systemctl") is None:
        return "local"
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )
        return (result.stdout or result.stderr or "").strip() or "unknown"
    except Exception:
        return "unknown"


def control_unit(unit: str, action: str) -> tuple[bool, str]:
    if action not in {"start", "stop", "restart"}:
        return False, "Unsupported action"
    if os.name == "nt" or shutil.which("systemctl") is None:
        return False, "Service control is available on the Raspberry Pi via systemd."
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", action, unit],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "systemctl failed").strip()
        return True, f"{unit} {action}ed"
    except Exception as exc:
        return False, str(exc)


def unit_logs(unit: str, lines: int = 40) -> str:
    if os.name == "nt" or shutil.which("journalctl") is None:
        return "Logs are available from Cockpit or journalctl on the Raspberry Pi."
    try:
        result = subprocess.run(
            ["sudo", "-n", "journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            check=False,
        )
        return (result.stdout or result.stderr or "").strip()
    except Exception as exc:
        return str(exc)


def auth_request(method: str, path: str, cookies: dict[str, str], json_body=None):
    from stonepi_auth.session import CSRF_COOKIE

    url = urljoin(env.auth_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {}
    csrf = (cookies or {}).get(CSRF_COOKIE) or (cookies or {}).get("stonepi_csrf")
    if csrf and method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
        headers["X-StonePi-CSRF"] = csrf
    with httpx.Client(timeout=8.0, follow_redirects=False) as client:
        response = client.request(method, url, cookies=cookies, json=json_body, headers=headers or None)
    if response.status_code >= 400:
        detail = response.text
        try:
            payload = response.json()
            detail = payload.get("detail") or payload
        except Exception:
            pass
        raise RuntimeError(str(detail))
    if not response.content:
        return {}
    return response.json()


def backup_info() -> dict:
    candidates = []
    if env.backup_stamp:
        candidates.append(Path(env.backup_stamp))
    candidates.extend(
        [
            Path("/var/lib/stonepi/backup-info.txt"),
            Path("/var/lib/stonepi/last-backup.json"),
        ]
    )
    for path in candidates:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            if path.suffix == ".json":
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
            info = {"path": str(path)}
            for line in text.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    info[key.strip().lower()] = value.strip()
            return info
    return {"status": "none", "message": "No backup has been recorded yet."}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def catalog_apps(*, include_auth: bool = True, cookies: dict[str, str] | None = None) -> list[dict]:
    disabled: set[str] = set()
    if cookies is not None:
        try:
            payload = auth_request("GET", "/api/apps", cookies)
            disabled = set(payload.get("disabled") or [])
            remote = {item["id"]: item for item in payload.get("apps") or [] if isinstance(item, dict)}
        except Exception:
            remote = {}
    else:
        remote = {}

    items = []
    for item in APP_CATALOG:
        if not include_auth and item["id"] == "auth":
            continue
        merged = {**item, **(remote.get(item["id"]) or {})}
        merged["enabled"] = item["id"] not in disabled and merged.get("enabled", True) is not False
        items.append(merged)
    return items


def application_cards(cookies: dict[str, str] | None = None) -> list[dict]:
    from app import update_service

    # Auth is already in APP_CATALOG — do not insert a second card.
    base_items = catalog_apps(include_auth=True, cookies=cookies)

    def build(item: dict) -> dict:
        return {
            **item,
            "url": app_public_url(item),
            "health": probe(health_url(item)),
            "unit_status": unit_status(item["unit"]),
            "version": update_service.current_version(item["id"]),
        }

    cards: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(base_items)))) as pool:
        futures = {pool.submit(build, item): item["id"] for item in base_items}
        by_id = {}
        for future in as_completed(futures):
            card = future.result()
            by_id[card["id"]] = card
    for item in base_items:
        cards.append(by_id[item["id"]])
    return cards


def parse_permissions_form(form, apps: list[dict]) -> dict[str, dict[str, bool]]:
    permissions: dict[str, dict[str, bool]] = {}
    for app in apps:
        app_id = app["id"]
        caps = {}
        for cap in app.get("capabilities") or []:
            field = f"perm_{app_id}_{cap['id']}"
            caps[cap["id"]] = form.get(field) == "1"
        if caps:
            permissions[app_id] = caps
    return permissions


def ensure_fileserve_for_studio_publish(apps: list[str], permissions: dict) -> list[str]:
    """Studio Publish lands in FileServe — grant FileServe when can_publish is on."""
    out = list(apps)
    studio_perms = (permissions or {}).get("studio") or {}
    if studio_perms.get("can_publish") and "fileserve" not in out:
        out.append("fileserve")
    return out


def launcher_tiles(user, cookies: dict[str, str] | None = None) -> list[dict]:
    """Product apps the signed-in user may open (not Auth/Dashboard chrome)."""
    catalog = {item["id"]: item for item in catalog_apps(include_auth=False, cookies=cookies)}
    order = list(LAUNCHER_APP_IDS)
    if cookies:
        try:
            me = auth_request("GET", "/api/me", cookies)
            saved = me.get("launcher_order")
            if isinstance(saved, list) and saved:
                seen = set()
                ordered = []
                for app_id in saved:
                    value = str(app_id or "").strip()
                    if value in LAUNCHER_APP_IDS and value not in seen:
                        ordered.append(value)
                        seen.add(value)
                for app_id in LAUNCHER_APP_IDS:
                    if app_id not in seen:
                        ordered.append(app_id)
                order = ordered
        except Exception:
            pass
    tiles = []
    for app_id in order:
        item = catalog.get(app_id) or next((a for a in APP_CATALOG if a["id"] == app_id), None)
        if item is None:
            continue
        if item.get("enabled") is False:
            continue
        # Admins see all enabled launcher apps even if the session cookie was
        # issued before those apps existed in the catalog.
        if not user.is_admin and not user.can_access(app_id):
            continue
        tiles.append(
            {
                **item,
                "url": app_public_url(item),
                "description": item.get("description") or "",
                "icon": item.get("icon") or app_id,
            }
        )
    return tiles


def save_launcher_order(cookies: dict[str, str], order: list[str]) -> list[str]:
    payload = auth_request("PUT", "/api/me/launcher-order", cookies, json_body={"order": order})
    saved = payload.get("launcher_order")
    return list(saved) if isinstance(saved, list) else order
