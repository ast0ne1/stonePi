from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.config import DATA_DIR, env
from stonepi_auth import (
    APP_CATALOG,
    LAUNCHER_APP_IDS,
    SERVICE_GROUP_LABELS,
    COOKIE_NAME,
    decode_session,
)

TIMEOUT = 1.0
APP_COLORS_PATH = DATA_DIR / "app-colors.json"
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_AUTH_HTTP = httpx.Client(timeout=8.0, follow_redirects=False)


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


def notify_page_url(page: str = "") -> str:
    """A Notify page (``displays``, ``alerts``, ``alerts#choose``): ``/notify/...`` behind nginx,
    Notify's own port in dev."""
    notify = next((a for a in APP_CATALOG if a["id"] == "notify"), None)
    base = app_public_url(notify) if notify else "/notify/"
    return f"{base}{page.lstrip('/')}"


def notify_settings_url(tab: str = "") -> str:
    """Old Notify Settings link (now redirects to the page for ``tab``)."""
    return notify_page_url("settings" + (f"?tab={tab}" if tab else ""))


_NOTIFY_SUMMARY: dict[str, object] = {"at": -1e9, "body": None}


def notify_admin_status(cookies: dict[str, str]) -> dict[str, str] | None:
    """Status lines for Dashboard -> Settings: Displays and Phone alerts. None if Notify can't say.

    Notify's summary itself asks Auth for the household, so it's cached for 30 s
    (admin-only and household-wide, so one copy serves every admin).
    """
    now = time.monotonic()
    body = _NOTIFY_SUMMARY["body"] if now - _NOTIFY_SUMMARY["at"] < 30 else None
    if body is None:
        status, body = notify_request("GET", "/api/admin/summary", cookies)
        if status != 200 or not body.get("ok"):
            return None
        _NOTIFY_SUMMARY.update(at=now, body=body)
    displays = body.get("displays") or {}
    count, pushing = int(displays.get("count") or 0), int(displays.get("pushing") or 0)
    if count:
        displays_line = f"{count} screen{'' if count == 1 else 's'}"
        displays_line += f" · {'all' if pushing == count and count > 1 else pushing} pushing to TRMNL" if pushing else " · not pushing yet"
    else:
        displays_line = "No screens yet"
    alerts = body.get("alerts") or {}
    if not alerts.get("enabled"):
        alerts_line = "Off · set up in a couple of minutes"
    else:
        people = int(alerts.get("people_on") or 0)
        alerts_line = (
            f"On · {alerts.get('approved', 0)} of {alerts.get('total', 0)} alerts approved"
            f" · {people} {'person' if people == 1 else 'people'}"
        )
        if alerts.get("needs_attention"):
            alerts_line += " · needs attention"
        elif not alerts.get("all_set"):
            alerts_line += " · setup not finished"
    return {"displays": displays_line, "alerts": alerts_line, "alerts_attention": bool(alerts.get("needs_attention"))}


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


def probe(url: str, client: httpx.Client | None = None) -> dict:
    try:
        if client is not None:
            response = client.get(url)
        else:
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as owned:
                response = owned.get(url)
        return {"ok": response.status_code < 500, "status": response.status_code}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc)}


def health_url(item: dict) -> str:
    """Always probe loopback — avoids mDNS + nginx on every health check."""
    return f"http://127.0.0.1:{item['port']}{item['health']}"


def unit_status(unit: str) -> str:
    """systemd unit state on the Pi (active/inactive/failed…); 'local' on Windows/dev."""
    return unit_statuses([unit]).get(unit, "unknown")


def unit_statuses(units: list[str]) -> dict[str, str]:
    """Batch `systemctl is-active` for many units (one subprocess on the Pi)."""
    cleaned = [str(unit or "").strip() for unit in units]
    if os.name == "nt" or shutil.which("systemctl") is None:
        return {unit: "local" for unit in cleaned if unit}
    unique: list[str] = []
    seen: set[str] = set()
    for unit in cleaned:
        if unit and unit not in seen:
            seen.add(unit)
            unique.append(unit)
    if not unique:
        return {}
    try:
        result = subprocess.run(
            ["systemctl", "is-active", *unique],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        lines = (result.stdout or "").strip().splitlines()
        out: dict[str, str] = {}
        for index, unit in enumerate(unique):
            value = lines[index].strip() if index < len(lines) else ""
            out[unit] = value or "unknown"
        return out
    except Exception:
        return {unit: "unknown" for unit in unique}


# Root helper (deploy/stonepi-service-helper.sh) that sudoers allows stonepi-dash to run:
# one validated stonepi-* unit per call, instead of wildcard systemctl/journalctl rules.
SERVICE_HELPER = "/usr/local/sbin/stonepi-service-helper"
_UNIT_RE = re.compile(r"^stonepi-[a-z0-9-]+(\.service)?$")


def control_unit(unit: str, action: str) -> tuple[bool, str]:
    if action not in {"start", "stop", "restart"}:
        return False, "Unsupported action"
    if not _UNIT_RE.match(unit or ""):
        return False, "Not a StonePi service"
    if os.name == "nt" or shutil.which("systemctl") is None:
        return False, "Service control is available on the Raspberry Pi via systemd."
    try:
        result = subprocess.run(
            ["sudo", "-n", SERVICE_HELPER, action, unit],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "systemctl failed").strip()
        return True, f"{unit} {action}ed"
    except Exception as exc:
        return False, str(exc)


def unit_logs(unit: str, lines: int = 40) -> str:
    if os.name == "nt" or shutil.which("journalctl") is None:
        return "Logs are available from Cockpit or journalctl on the Raspberry Pi."
    if not _UNIT_RE.match(unit or ""):
        return "Not a StonePi service."
    lines = max(1, min(int(lines), 2000))
    try:
        result = subprocess.run(
            ["sudo", "-n", SERVICE_HELPER, "logs", unit, str(lines)],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        return (result.stdout or result.stderr or "").strip()
    except Exception as exc:
        return str(exc)


class AuthAPIError(RuntimeError):
    """An Auth API call that failed: ``status_code`` is Auth's HTTP status, or 503 when
    Auth could not be reached at all (restarting, connection refused, timeout)."""

    def __init__(self, message: str, status_code: int, *, unreachable: bool = False):
        super().__init__(message)
        self.status_code = int(status_code)
        self.unreachable = unreachable

    @property
    def signed_out(self) -> bool:
        """Auth no longer knows this session (signed out elsewhere, password changed,
        account disabled, restore): the cookie still verifies here but is dead."""
        return self.status_code == 401

    @property
    def unavailable(self) -> bool:
        """Auth is down or restarting — worth retrying in a few seconds."""
        return self.unreachable or self.status_code in {502, 503, 504}


AUTH_RESTARTING_MESSAGE = "Auth is restarting — retrying…"


def auth_request(method: str, path: str, cookies: dict[str, str], json_body=None):
    """JSON Auth API helper. Raises :class:`AuthAPIError` (a RuntimeError) on failure."""
    response = auth_exchange(method, path, cookies, json_body=json_body)
    if not response.content:
        return {}
    return response.json()


def auth_exchange(method: str, path: str, cookies: dict[str, str], json_body=None) -> httpx.Response:
    """Raw Auth API call (keeps Set-Cookie headers for callers that must forward them)."""
    from stonepi_auth.session import CSRF_COOKIE

    url = urljoin(env.auth_url.rstrip("/") + "/", path.lstrip("/"))
    headers = {}
    csrf = (cookies or {}).get(CSRF_COOKIE) or (cookies or {}).get("stonepi_csrf")
    if csrf and method.upper() in {"POST", "PATCH", "PUT", "DELETE"}:
        headers["X-StonePi-CSRF"] = csrf
    try:
        response = _AUTH_HTTP.request(method, url, cookies=cookies, json=json_body, headers=headers or None)
    except httpx.TransportError as exc:
        raise AuthAPIError(AUTH_RESTARTING_MESSAGE, 503, unreachable=True) from exc
    if response.status_code >= 400:
        detail = response.text
        try:
            payload = response.json()
            detail = payload.get("detail") or payload
        except Exception:
            pass
        if response.status_code in {502, 503, 504} and not str(detail).strip():
            detail = AUTH_RESTARTING_MESSAGE
        raise AuthAPIError(str(detail), response.status_code)
    return response


_NOTIFY_HTTP = httpx.Client(timeout=5.0, follow_redirects=False)


def notify_request(method: str, path: str, cookies: dict[str, str], json_body=None) -> tuple[int, dict]:
    """Notify personal-alerts API as the signed-in person: ``(status, body)``.

    Never raises; a network failure comes back as ``(503, {"message": ...})``.
    """
    from stonepi_auth.session import CSRF_COOKIE, COOKIE_NAME
    from stonepi_contracts import notify_base_url

    url = f"{notify_base_url().rstrip('/')}/{path.lstrip('/')}"
    forward = {k: v for k, v in (cookies or {}).items() if k in {COOKIE_NAME, CSRF_COOKIE}}
    headers = {}
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and forward.get(CSRF_COOKIE):
        headers["X-StonePi-CSRF"] = forward[CSRF_COOKIE]
    try:
        response = _NOTIFY_HTTP.request(method, url, cookies=forward, json=json_body, headers=headers or None)
    except Exception as exc:  # noqa: BLE001
        return 503, {"ok": False, "message": f"Notify is not reachable ({type(exc).__name__})."}
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    return response.status_code, body


def forward_auth_cookies(response, auth_response: httpx.Response) -> None:
    """Copy Set-Cookie from an Auth response onto a Dashboard response (e.g. password change)."""
    # Prefer raw Set-Cookie headers so httponly / max-age / samesite survive.
    for key, value in auth_response.headers.multi_items():
        if key.lower() == "set-cookie" and value:
            response.headers.append("set-cookie", value)
            continue
    if any(k.lower() == "set-cookie" for k, _ in auth_response.headers.multi_items()):
        return
    # Fallback: cookie jar (loses some flags but still clears fac).
    from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE

    for name, value in auth_response.cookies.items():
        if name in {COOKIE_NAME, CSRF_COOKIE}:
            response.set_cookie(name, value, path="/", httponly=True, samesite="lax")


def backup_info() -> dict:
    """Return local and USB stamp info (plus legacy flat fields for older templates)."""
    from stonepi_watch import read_backup_info

    return read_backup_info(env.backup_stamp or None)


def _helper_run(args: list[str], timeout: float = 120, stdin: str | None = None) -> tuple[int, str]:
    helper = "/usr/local/sbin/stonepi-backup-helper"
    if os.name == "nt" or not Path(helper).exists():
        return 1, "helper unavailable (Pi only)"
    cmd = ["sudo", "-n", helper, *args]
    try:
        result = subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=False, timeout=timeout)
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        return result.returncode, out
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def list_usb_backups() -> list[dict]:
    """List local + USB snapshots (kind field on each row)."""
    code, out = _helper_run(["list"], timeout=30)
    if code != 0 or not out.strip():
        return []
    try:
        data = json.loads(out)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def local_backup_schedule() -> dict:
    code, out = _helper_run(["local-schedule-status"], timeout=15)
    defaults = {
        "enabled": 0,
        "conf_enabled": "0",
        "cadence": "weekly",
        "weekday": "Sun",
        "time": "03:30",
        "root": "/var/backups/stonepi",
        "next": "",
    }
    if code != 0 or not out.strip():
        return defaults
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            defaults.update(data)
            return defaults
    except json.JSONDecodeError:
        pass
    return defaults


def set_local_backup_schedule(
    enabled: bool,
    cadence: str = "weekly",
    time_of_day: str = "03:30",
    weekday: str = "Sun",
) -> tuple[bool, str]:
    cadence = cadence if cadence in {"daily", "weekly"} else "weekly"
    weekday = weekday if weekday in {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"} else "Sun"
    time_of_day = time_of_day.strip() or "03:30"
    code, out = _helper_run(
        [
            "local-schedule",
            "1" if enabled else "0",
            cadence,
            time_of_day,
            weekday,
        ],
        timeout=30,
    )
    if code != 0 and "password is required" in (out or "").lower():
        out = (
            "Backup helper needs passwordless sudo for stonepi-dash "
            "(missing /etc/sudoers.d/stonepi-dash entry for stonepi-backup-helper). "
            "Re-run the local-backup push overlay, or add that NOPASSWD line on the Pi."
        )
    return code == 0, out


def run_local_backup_now() -> tuple[bool, str]:
    code, out = _helper_run(["backup-now", "local"], timeout=30)
    return code == 0, out


def restore_drill_status() -> dict:
    path = Path("/var/lib/stonepi/last-restore-drill.txt")
    if not path.exists():
        return {"status": "none"}
    info: dict = {"path": str(path)}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip().lower()] = v.strip()
    return info


def run_restore_drill(backup_path: str = "") -> tuple[bool, str]:
    args = ["drill"]
    if backup_path:
        args.append(backup_path)
    code, out = _helper_run(args, timeout=180)
    return code == 0, out


def run_usb_restore(backup_path: str) -> tuple[bool, str]:
    code, out = _helper_run(["restore", backup_path], timeout=600)
    return code == 0, out


def failover_status() -> str:
    code, out = _helper_run(["failover-status"], timeout=10)
    if "failover=on" in (out or ""):
        return "on"
    return "off"


def set_failover(on: bool) -> tuple[bool, str]:
    code, out = _helper_run(["failover-on" if on else "failover-off"], timeout=30)
    return code == 0, out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_hex_color(value: str | None, fallback: str = "") -> str:
    raw = str(value or "").strip()
    if _HEX_COLOR_RE.match(raw):
        return raw.lower()
    if re.match(r"^#[0-9a-fA-F]{3}$", raw):
        return f"#{raw[1]*2}{raw[2]*2}{raw[3]*2}".lower()
    return fallback.lower() if fallback else ""


def default_app_colors() -> dict[str, str]:
    return {
        str(item["id"]): normalize_hex_color(str(item.get("color") or ""), "#6d645a")
        for item in APP_CATALOG
    }


def load_app_color_overrides() -> dict[str, str]:
    if not APP_COLORS_PATH.exists():
        return {}
    try:
        data = json.loads(APP_COLORS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    known = {item["id"] for item in APP_CATALOG}
    out: dict[str, str] = {}
    for app_id, color in data.items():
        key = str(app_id)
        if key not in known:
            continue
        hex_color = normalize_hex_color(str(color or ""))
        if hex_color:
            out[key] = hex_color
    return out


def resolved_app_colors() -> dict[str, str]:
    colors = default_app_colors()
    colors.update(load_app_color_overrides())
    return colors


def app_color_items() -> list[dict]:
    defaults = default_app_colors()
    current = resolved_app_colors()
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "color": current.get(item["id"], defaults.get(item["id"], "#6d645a")),
            "default": defaults.get(item["id"], "#6d645a"),
        }
        for item in APP_CATALOG
    ]


def save_app_colors(colors: dict[str, str], *, reset: bool = False) -> dict[str, str]:
    """Persist per-app tile accents. Only diffs from catalog defaults are stored."""
    APP_COLORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if reset:
        if APP_COLORS_PATH.exists():
            APP_COLORS_PATH.unlink()
        return resolved_app_colors()

    defaults = default_app_colors()
    overrides: dict[str, str] = {}
    for app_id, default in defaults.items():
        hex_color = normalize_hex_color(colors.get(app_id), default)
        if hex_color and hex_color != default:
            overrides[app_id] = hex_color
    if overrides:
        APP_COLORS_PATH.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif APP_COLORS_PATH.exists():
        APP_COLORS_PATH.unlink()
    return resolved_app_colors()


def catalog_apps(
    *,
    include_auth: bool = True,
    include_non_grantable: bool = True,
    cookies: dict[str, str] | None = None,
) -> list[dict]:
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

    color_overrides = load_app_color_overrides()
    items = []
    for item in APP_CATALOG:
        if not include_auth and item["id"] == "auth":
            continue
        if not include_non_grantable and item.get("grants") is False:
            continue
        merged = {**item, **(remote.get(item["id"]) or {})}
        # Household tile accents win over catalog / auth payload defaults.
        if item["id"] in color_overrides:
            merged["color"] = color_overrides[item["id"]]
        elif not merged.get("color"):
            merged["color"] = item.get("color") or ""
        merged["enabled"] = item["id"] not in disabled and merged.get("enabled", True) is not False
        items.append(merged)
    return items


def application_cards(cookies: dict[str, str] | None = None) -> list[dict]:
    from app import update_service

    # Auth is already in APP_CATALOG — do not insert a second card.
    base_items = catalog_apps(include_auth=True, cookies=cookies)
    statuses = unit_statuses([str(item.get("unit") or "") for item in base_items])

    def build(item: dict, client: httpx.Client) -> dict:
        unit = str(item.get("unit") or "")
        return {
            **item,
            "url": app_public_url(item),
            "health": probe(health_url(item), client=client),
            "unit_status": statuses.get(unit, "unknown" if unit else "local"),
            "version": update_service.current_version(item["id"]),
        }

    cards: list[dict] = []
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(base_items)))) as pool:
            futures = {pool.submit(build, item, client): item["id"] for item in base_items}
            by_id = {}
            for future in as_completed(futures):
                card = future.result()
                by_id[card["id"]] = card
    for item in base_items:
        cards.append(by_id[item["id"]])
    return cards


def application_card_groups(
    cookies: dict[str, str] | None = None,
    cards: list[dict] | None = None,
) -> list[tuple[str, list[dict]]]:
    """Services / Health groups: SYSTEM (Dashboard/Auth/…) then USER apps."""
    items = cards if cards is not None else application_cards(cookies)
    buckets: dict[str, list[dict]] = {key: [] for key, _ in SERVICE_GROUP_LABELS}
    for card in items:
        raw = str(card.get("group") or "user")
        # Legacy catalog key before SYSTEM/USER rename.
        key = "user" if raw == "apps" else raw
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(card)
    groups: list[tuple[str, list[dict]]] = []
    for key, label in SERVICE_GROUP_LABELS:
        group_items = buckets.get(key) or []
        if group_items:
            groups.append((label, group_items))
    known = {key for key, _ in SERVICE_GROUP_LABELS}
    for key, group_items in buckets.items():
        if key in known or not group_items:
            continue
        groups.append((key.replace("_", " ").title(), group_items))
    return groups


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


def apply_disabled_to_watch(watch: dict, disabled: set[str]) -> dict:
    """Mark disabled apps healthy in a Watch snapshot and recompute rollup level/reasons."""
    import copy

    import stonepi_watch

    out = copy.deepcopy(watch) if isinstance(watch, dict) else {}
    apps = list(out.get("apps") or [])
    for row in apps:
        if str(row.get("id") or "") not in disabled:
            continue
        row["enabled"] = False
        row["running"] = False
        row["level"] = stonepi_watch.LEVEL_HEALTHY
        row["health_ok"] = bool(row.get("health_ok"))
    out["apps"] = apps

    reasons: list[str] = []
    level = stonepi_watch.LEVEL_HEALTHY

    def raise_to(next_level: str, reason: str) -> None:
        nonlocal level
        order = {
            stonepi_watch.LEVEL_HEALTHY: 0,
            stonepi_watch.LEVEL_ATTENTION: 1,
            stonepi_watch.LEVEL_CRITICAL: 2,
        }
        if order[next_level] > order[level]:
            level = next_level
        reasons.append(reason)

    for row in apps:
        if not row.get("enabled", True):
            continue
        if row.get("level") == stonepi_watch.LEVEL_CRITICAL:
            raise_to(stonepi_watch.LEVEL_CRITICAL, f"{row.get('n') or row.get('id')} is down")
        elif row.get("level") == stonepi_watch.LEVEL_ATTENTION:
            raise_to(stonepi_watch.LEVEL_ATTENTION, f"{row.get('n') or row.get('id')} is not running")

    backup_status = str(out.get("backup_status") or "").strip().lower()
    age_days = out.get("backup_age_days")
    if backup_status in {"failed", "error"}:
        raise_to(stonepi_watch.LEVEL_CRITICAL, "Backup failed")
    elif age_days is None and backup_status in {"", "none", "unknown"}:
        raise_to(stonepi_watch.LEVEL_ATTENTION, "No backup recorded")
    elif isinstance(age_days, (int, float)) and age_days >= stonepi_watch.BACKUP_ATTENTION_DAYS:
        raise_to(stonepi_watch.LEVEL_ATTENTION, f"Backup {int(age_days)}d ago")

    disk = out.get("disk_pct")
    if disk is not None:
        try:
            disk_i = int(disk)
        except (TypeError, ValueError):
            disk_i = None
        if disk_i is not None:
            if disk_i >= stonepi_watch.DISK_CRITICAL_PCT:
                raise_to(stonepi_watch.LEVEL_CRITICAL, f"Disk {disk_i}% full")
            elif disk_i >= stonepi_watch.DISK_SERIOUS_PCT:
                raise_to(stonepi_watch.LEVEL_ATTENTION, f"Disk {disk_i}% used — serious")
            elif disk_i >= stonepi_watch.DISK_ATTENTION_PCT:
                raise_to(stonepi_watch.LEVEL_ATTENTION, f"Disk {disk_i}% used")

    out["level"] = level
    out["reasons"] = reasons[:8]
    out.update(stonepi_watch.summarize(reasons, apps))
    return out


def launcher_tiles(user, cookies: dict[str, str] | None = None, status: dict | None = None) -> list[dict]:
    """Product apps the signed-in user may open (not Auth/Dashboard chrome).

    ``status["order_live"]`` says whether the saved order really came from Auth
    (False while Auth is restarting: Home still shows tiles in catalog order).
    Raises :class:`AuthAPIError` when Auth says the session is gone, so Home
    can send the person to sign in instead of showing an order it can't save.
    """
    # /api/apps is admin-only: for members it was a round trip that always 403'd.
    catalog = {item["id"]: item for item in catalog_apps(include_auth=False, cookies=cookies if getattr(user, "is_admin", False) else None)}
    # Prefer the user's saved order from Auth; fall back to catalog order.
    # Factory-admin banner still uses the session `fac` claim (no /api/me for that).
    order = list(LAUNCHER_APP_IDS)
    order_live = False
    if cookies:
        try:
            me = auth_request("GET", "/api/me", cookies)
            order_live = True
            saved = me.get("launcher_order")
            if isinstance(saved, list) and saved:
                known = set(LAUNCHER_APP_IDS)
                preferred = [str(app_id) for app_id in saved if str(app_id) in known]
                rest = [app_id for app_id in LAUNCHER_APP_IDS if app_id not in preferred]
                order = preferred + rest
        except AuthAPIError as exc:
            if exc.signed_out:
                raise
        except Exception:
            pass
    if status is not None:
        status["order_live"] = order_live
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
                "description": _tile_status(item) or item.get("description") or "",
                "icon": item.get("icon") or app_id,
            }
        )
    return tiles


LIVE_TILE_STATES = frozenset({"downloading"})
_TILE_STATUS: dict[str, tuple[float, str]] = {}
_TILE_REFRESHING: set[str] = set()
_TILE_LOCK = threading.Lock()
_TILE_HTTP = httpx.Client(timeout=0.8)


def _refresh_tile_status(app_id: str, port: int) -> None:
    detail = ""
    try:
        resp = _TILE_HTTP.get(f"http://127.0.0.1:{port}/api/display")
        if resp.status_code == 200:
            data = resp.json()
            # Only work in progress replaces the tile's standard description; at rest
            # (e.g. Library's installed titles) the tile reads like every other app.
            if data.get("state") in LIVE_TILE_STATES:
                detail = str(data.get("detail") or "")[:80]
    except Exception:  # noqa: BLE001
        detail = ""
    with _TILE_LOCK:
        _TILE_STATUS[app_id] = (time.monotonic(), detail)
        _TILE_REFRESHING.discard(app_id)


def _tile_status(item: dict) -> str:
    """Live one-liner for ``live_status`` apps while work is in progress (Library "Installing Wikipedia · 71%").

    Read from the app's loopback /api/display in the background: Home shows the
    last known line (empty at first, so the tile falls back to its description)
    and never waits on a slow or stopped app.
    """
    if not item.get("live_status") or not item.get("port"):
        return ""
    app_id = item["id"]
    with _TILE_LOCK:
        cached = _TILE_STATUS.get(app_id)
        stale = not cached or time.monotonic() - cached[0] >= 20
        if stale and app_id not in _TILE_REFRESHING:
            _TILE_REFRESHING.add(app_id)
            threading.Thread(target=_refresh_tile_status, args=(app_id, int(item["port"])), daemon=True).start()
    return cached[1] if cached else ""


def save_launcher_order(cookies: dict[str, str], order: list[str]) -> list[str]:
    payload = auth_request("PUT", "/api/me/launcher-order", cookies, json_body={"order": order})
    saved = payload.get("launcher_order")
    return list(saved) if isinstance(saved, list) else order
