"""Network / Tailscale status for Dashboard Settings → Network and Health."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HELPER = Path("/usr/local/sbin/stonepi-tailscale")
HOSTNAME_HELPER = Path("/usr/local/sbin/stonepi-hostname")
INTERNET_PROBE_URL = "https://connectivitycheck.gstatic.com/generate_204"
INTERNET_TIMEOUT_S = 1.5
STATUS_HELPER_TIMEOUT_S = 4.0
TAILSCALE_CACHE_TTL_S = 20.0
INTERNET_CACHE_TTL_S = 60.0
HOSTNAME_HELPER_TIMEOUT_S = 20.0

_cache_lock = threading.Lock()
_tailscale_cache: dict[str, Any] | None = None
_tailscale_cache_at = 0.0
_internet_cache: dict[str, Any] | None = None
_internet_cache_at = 0.0


def _repo_data_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data"


def wanted_file_path() -> Path:
    """Admin toggle file — must be writable by stonepi-dash."""
    explicit = os.environ.get("STONEPI_TAILSCALE_WANTED_FILE", "").strip()
    if explicit:
        return Path(explicit)
    dash_dir = Path("/var/lib/stonepi/dashboard")
    if dash_dir.is_dir():
        return dash_dir / "tailscale_wanted"
    if Path("/var/lib/stonepi").is_dir():
        return Path("/var/lib/stonepi/tailscale_wanted")
    return _repo_data_dir() / "tailscale_wanted"


def _wanted_candidates() -> list[Path]:
    """Read path plus legacy locations (overlay may have seeded root-owned file)."""
    primary = wanted_file_path()
    seen: set[Path] = {primary}
    out = [primary]
    for path in (
        Path("/var/lib/stonepi/dashboard/tailscale_wanted"),
        Path("/var/lib/stonepi/tailscale_wanted"),
        _repo_data_dir() / "tailscale_wanted",
    ):
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def clear_network_cache() -> None:
    """Drop TTL caches so the next read hits the helper / probe."""
    global _tailscale_cache, _tailscale_cache_at, _internet_cache, _internet_cache_at
    with _cache_lock:
        _tailscale_cache = None
        _tailscale_cache_at = 0.0
        _internet_cache = None
        _internet_cache_at = 0.0


def tailscale_wanted() -> bool:
    for path in _wanted_candidates():
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip().splitlines()[0].strip().lower()
                return value in {"on", "1", "true", "yes", "enabled"}
        except OSError:
            continue
    return False


def set_tailscale_wanted(enabled: bool) -> Path:
    path = wanted_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ("on" if enabled else "off") + "\n"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        # Fall back to dashboard data under the app tree (dev / odd installs).
        fallback = _repo_data_dir() / "dashboard" / "tailscale_wanted"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(text, encoding="utf-8")
        path = fallback
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
    clear_network_cache()
    return path


def helper_available() -> bool:
    return HELPER.is_file() and os.name != "nt"


def hostname_helper_available() -> bool:
    return HOSTNAME_HELPER.is_file() and os.name != "nt"


def apply_hostname(name: str) -> tuple[bool, str]:
    """Apply appliance hostname via privileged helper, or file-only on Windows/dev.

    Returns (ok, message). Always writes the hot-read file when the name is valid.
    """
    from stonepi_auth import normalize_hostname, set_platform_hostname, valid_hostname

    cleaned = normalize_hostname(name)
    if not valid_hostname(cleaned):
        return False, "Hostname must be 1–63 letters, digits, or hyphens (not starting or ending with a hyphen)."

    if hostname_helper_available():
        try:
            proc = subprocess.run(
                ["sudo", "-n", str(HOSTNAME_HELPER), cleaned],
                capture_output=True,
                text=True,
                timeout=HOSTNAME_HELPER_TIMEOUT_S,
                check=False,
                errors="replace",
            )
        except Exception as exc:  # noqa: BLE001
            return False, str(exc) or "Could not run hostname helper."
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
            return False, detail[:240]
        # Helper already wrote the file; ensure package path matches for this process.
        try:
            set_platform_hostname(cleaned)
        except OSError:
            pass
        return True, cleaned

    try:
        set_platform_hostname(cleaned)
    except (OSError, ValueError) as exc:
        return False, str(exc) or "Could not save hostname."
    return True, cleaned


def _run_helper(subcommand: str, *, timeout: float = 20.0) -> tuple[int, str]:
    if not helper_available():
        return 127, ""
    try:
        proc = subprocess.run(
            ["sudo", "-n", str(HELPER), subcommand],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            errors="replace",
        )
        # Helper must print JSON on stdout only; never treat stderr as JSON.
        out = (proc.stdout or "").strip()
        if not out and (proc.stderr or "").strip():
            return proc.returncode, json.dumps(
                {
                    "Installed": True,
                    "BackendState": "Unknown",
                    "Error": (proc.stderr or "").strip()[:240],
                }
            )
        return proc.returncode, out
    except Exception as exc:  # noqa: BLE001
        return 1, json.dumps({"ok": False, "Installed": True, "BackendState": "Unknown", "error": str(exc)})


def _probe_internet() -> dict[str, Any]:
    """Outbound reachability from the Pi (not Tailscale-specific)."""
    try:
        req = urllib.request.Request(INTERNET_PROBE_URL, method="GET")
        with urllib.request.urlopen(req, timeout=INTERNET_TIMEOUT_S) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            ok = int(code) in {204, 200}
            return {"ok": ok, "detail": f"HTTP {code}" if ok else f"Unexpected HTTP {code}"}
    except urllib.error.HTTPError as exc:
        # Some captive portals return non-204; treat 2xx/3xx as online enough.
        if 200 <= int(exc.code) < 400:
            return {"ok": True, "detail": f"HTTP {exc.code}"}
        return {"ok": False, "detail": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc) or "unreachable"}


def internet_status(*, fresh: bool = False) -> dict[str, Any]:
    global _internet_cache, _internet_cache_at
    now = time.monotonic()
    with _cache_lock:
        if (
            not fresh
            and _internet_cache is not None
            and (now - _internet_cache_at) < INTERNET_CACHE_TTL_S
        ):
            return dict(_internet_cache)
    result = _probe_internet()
    with _cache_lock:
        _internet_cache = dict(result)
        _internet_cache_at = time.monotonic()
    return result


def parse_tailscale_status(raw: dict[str, Any] | None, *, wanted: bool) -> dict[str, Any]:
    """Normalize `tailscale status --json` (or helper wrapper) into UI fields."""
    data = raw if isinstance(raw, dict) else {}
    installed = bool(data.get("Installed", True))
    if data.get("Installed") is False:
        installed = False

    backend = str(data.get("BackendState") or "NoState")
    auth_url = (data.get("AuthURL") or "").strip() or None
    self_node = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    dns_name = (self_node.get("DNSName") or data.get("DNSName") or "").strip().rstrip(".")
    ips = self_node.get("TailscaleIPs") or data.get("TailscaleIPs") or []
    ipv4 = ""
    if isinstance(ips, list):
        for ip in ips:
            text = str(ip)
            if "." in text and ":" not in text:
                ipv4 = text
                break
        if not ipv4 and ips:
            ipv4 = str(ips[0])

    magic_enabled = False
    current = data.get("CurrentTailnet")
    if isinstance(current, dict):
        magic_enabled = bool(current.get("MagicDNSEnabled"))
    if not magic_enabled and data.get("MagicDNSSuffix"):
        magic_enabled = True
    if not magic_enabled and dns_name and ".ts.net" in dns_name:
        magic_enabled = backend == "Running"

    connected = backend == "Running" and bool(self_node or ipv4 or dns_name)
    needs_login = backend in {"NeedsLogin", "NeedsMachineAuth"} or bool(auth_url and not connected)

    serve_http = bool(data.get("ServeHTTP"))
    serve_enable_url = (data.get("ServeEnableURL") or "").strip() or None

    access_url = None
    if connected and serve_http and dns_name:
        # Tailscale Serve publishes HTTPS on the MagicDNS name → local nginx :80.
        access_url = f"https://{dns_name}/"
    elif connected and ipv4:
        # Plain HTTP over the encrypted tailnet (no Serve / HTTPS yet).
        access_url = f"http://{ipv4}/"

    state = "not_installed"
    if installed:
        if not wanted:
            state = "disabled"
        elif connected:
            state = "connected"
        elif needs_login:
            state = "needs_login"
        elif backend in {"Stopped", "NoState", "Starting"}:
            state = "disconnected"
        else:
            state = backend.lower() if backend else "unknown"

    return {
        "installed": installed,
        "wanted": wanted,
        "connected": connected,
        "needs_login": needs_login,
        "backend_state": backend,
        "state": state,
        "auth_url": auth_url,
        "dns_name": dns_name or None,
        "ipv4": ipv4 or None,
        "magicdns": bool(magic_enabled and dns_name),
        "magicdns_name": dns_name if (magic_enabled and dns_name) else None,
        "access_url": access_url,
        "serve_http": serve_http,
        "serve_enable_url": serve_enable_url,
        "error": (data.get("Error") or data.get("error") or None),
    }


def _raw_status_from_helper() -> dict[str, Any]:
    code, out = _run_helper("status", timeout=STATUS_HELPER_TIMEOUT_S)
    if not out:
        if not helper_available():
            return {"Installed": False, "BackendState": "NoState"}
        return {"Installed": True, "BackendState": "Unknown", "Error": f"helper exit {code} (empty stdout)"}
    # Prefer the last JSON object if any banner slipped through.
    candidates = [out]
    if "\n" in out:
        candidates = [line.strip() for line in out.splitlines() if line.strip()] + [out]
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return {
        "Installed": True,
        "BackendState": "Unknown",
        "Error": f"invalid status JSON: {out[:160]}",
    }


def tailscale_status(*, fresh: bool = False) -> dict[str, Any]:
    global _tailscale_cache, _tailscale_cache_at
    wanted = tailscale_wanted()
    if not helper_available():
        return parse_tailscale_status({"Installed": False, "BackendState": "NoState"}, wanted=wanted)

    now = time.monotonic()
    with _cache_lock:
        if (
            not fresh
            and _tailscale_cache is not None
            and (now - _tailscale_cache_at) < TAILSCALE_CACHE_TTL_S
        ):
            cached = dict(_tailscale_cache)
            cached["wanted"] = wanted
            return cached

    parsed = parse_tailscale_status(_raw_status_from_helper(), wanted=wanted)
    with _cache_lock:
        _tailscale_cache = dict(parsed)
        _tailscale_cache_at = time.monotonic()
    return parsed


def start_login() -> dict[str, Any]:
    """Start Tailscale login; return normalized status (may include auth_url)."""
    set_tailscale_wanted(True)
    clear_network_cache()
    if not helper_available():
        return parse_tailscale_status({"Installed": False, "BackendState": "NoState"}, wanted=True)
    # Helper captures AuthURL from CLI output (status JSON alone often omits it).
    code, out = _run_helper("up", timeout=35.0)
    raw: dict[str, Any] = {}
    if out:
        try:
            parsed = json.loads(out)
            if isinstance(parsed, dict):
                raw = parsed
        except json.JSONDecodeError:
            raw = {"Installed": True, "BackendState": "NeedsLogin", "Error": out[:200]}
    if not raw:
        raw = _raw_status_from_helper()
    if not raw.get("AuthURL") and raw.get("BackendState") != "Running":
        # One more status read — helper may have cached AuthURL from CLI output.
        cached = _raw_status_from_helper()
        if cached.get("AuthURL"):
            raw = cached
        elif code not in (0, 124):
            raw.setdefault("Error", f"connect failed (exit {code})")
    result = parse_tailscale_status(raw, wanted=True)
    with _cache_lock:
        global _tailscale_cache, _tailscale_cache_at
        _tailscale_cache = dict(result)
        _tailscale_cache_at = time.monotonic()
    return result


def disconnect() -> dict[str, Any]:
    if helper_available():
        _run_helper("down", timeout=20.0)
    clear_network_cache()
    return tailscale_status(fresh=True)


def network_snapshot(*, fresh: bool = False) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    from stonepi_auth import platform_hostname

    with ThreadPoolExecutor(max_workers=2) as pool:
        internet_f = pool.submit(internet_status, fresh=fresh)
        ts_f = pool.submit(tailscale_status, fresh=fresh)
        internet = internet_f.result()
        ts = ts_f.result()
    return {
        "internet": internet,
        "tailscale": ts,
        "helper_available": helper_available(),
        "hostname_helper_available": hostname_helper_available(),
        "appliance": helper_available(),
        "hostname": platform_hostname(),
    }
