"""Platform system health rollup for Dashboard Health + /system/health."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import stonepi_watch
from stonepi_auth import APP_CATALOG

from app import display
from app import network as network_svc
from app import services
from app.config import DATA_DIR

logger = logging.getLogger("stonepi.system_health")

NOTIFY_DATA = Path("/var/lib/stonepi/notify")
EXPECTED_LISTENERS = (
    {"port": 80, "proto": "tcp", "label": "nginx"},
    {"port": 443, "proto": "tcp", "label": "nginx-tls", "optional": True},
    {"port": 22, "proto": "tcp", "label": "ssh", "optional": True},
    {"port": 9090, "proto": "tcp", "label": "cockpit"},
    {"port": 41641, "proto": "udp", "label": "tailscale", "optional": True},
    {"port": 5353, "proto": "udp", "label": "mdns"},
    {"port": 8099, "proto": "tcp", "label": "recover", "optional": True},
)

# Ephemeral sockets from these platform daemons are expected (Tailscale DERP/STUN, Avahi, etc.).
ALLOWED_LISTENER_PROCS = frozenset(
    {
        "avahi-daemon",
        "tailscaled",
        "NetworkManager",
        "systemd-resolve",
        "dhcpcd",
    }
)


def destinations_status() -> dict[str, Any]:
    """ntfy/TRMNL configured flags — never returns secrets."""
    path = NOTIFY_DATA / "destinations.json"
    ntfy_enabled = False
    ntfy_topic = False
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ntfy = data.get("ntfy") if isinstance(data, dict) else {}
            if isinstance(ntfy, dict):
                ntfy_enabled = bool(ntfy.get("enabled"))
                ntfy_topic = bool(str(ntfy.get("topic") or "").strip())
        except (OSError, json.JSONDecodeError):
            pass

    # TRMNL push is per Display (Notify displays.json); webhooks live in the vault.
    trmnl_ids: list[str] = []
    displays_path = NOTIFY_DATA / "displays.json"
    try:
        if displays_path.is_file():
            raw = json.loads(displays_path.read_text(encoding="utf-8"))
            items = raw.get("displays") if isinstance(raw, dict) else raw
            for item in items if isinstance(items, list) else []:
                trmnl = item.get("trmnl") if isinstance(item, dict) else None
                if isinstance(trmnl, dict) and trmnl.get("enabled") and item.get("enabled", True):
                    trmnl_ids.append(str(item.get("id") or ""))
    except (OSError, json.JSONDecodeError):
        pass

    ntfy_token = False
    pushing = 0
    try:
        from stonepi_vault import get_secret

        ntfy_token = bool(
            (get_secret("STONEPI_NTFY_TOKEN", env_name="STONEPI_NTFY_TOKEN", default="") or "").strip()
        )
        from stonepi_display import get_webhook

        pushing = sum(1 for display_id in trmnl_ids if get_webhook(display_id))
    except Exception:
        pass

    ntfy_configured = ntfy_enabled and ntfy_topic
    return {
        "ntfy": {
            "enabled": ntfy_enabled,
            "configured": ntfy_configured,
            "token_set": ntfy_token,
        },
        "trmnl": {
            "enabled": bool(trmnl_ids),
            "configured": pushing > 0,
            "webhook_set": pushing > 0,
            "displays": pushing,
        },
    }


def listening_ports() -> dict[str, Any]:
    """Parse ss -tulpn into expected vs unexpected listeners."""
    if os.name == "nt" or shutil.which("ss") is None:
        return {"ok": True, "appliance": False, "listeners": [], "unexpected": []}

    lines: list[str] = []
    try:
        for args in (["ss", "-tulpnH"], ["ss", "-tulpn"]):
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if result.returncode == 0 and (result.stdout or "").strip():
                lines = (result.stdout or "").splitlines()
                break
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "listeners": [], "unexpected": []}

    return classify_listeners(lines)


def classify_listeners(lines: list[str]) -> dict[str, Any]:
    """Split ss lines into listeners vs unexpected (pure; used by listening_ports + tests)."""
    listeners: list[dict[str, Any]] = []
    seen_ports: set[tuple[str, int]] = set()
    for line in lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[0] not in {"tcp", "udp", "tcp6", "udp6"}:
            continue
        proto = "udp" if parts[0].startswith("udp") else "tcp"
        local = parts[4]
        port = _parse_port(local)
        if port is None:
            continue
        key = (proto, port)
        if key in seen_ports:
            continue
        seen_ports.add(key)
        proc = _parse_ss_process(line)
        listeners.append({"proto": proto, "port": port, "local": local, "proc": proc})

    expected_ports = {(e["proto"], e["port"]) for e in EXPECTED_LISTENERS}
    # Tailscale keeps WireGuard on 41641 but also opens ephemeral UDP (DERP/STUN).
    # Process names are often hidden from stonepi-dash, so treat companion UDP as expected.
    tailscale_up = any(row["proto"] == "udp" and row["port"] == 41641 for row in listeners)

    def is_unexpected(row: dict[str, Any]) -> bool:
        if (row["proto"], row["port"]) in expected_ports:
            return False
        local = str(row["local"])
        if local.startswith("127.") or local.startswith("[::1]"):
            return False
        # Tailscale CGNAT / interface binds are expected when remote access is on.
        if local.startswith("100.") or local.startswith("[fd7a:"):
            return False
        if row["port"] in range(8001, 8013):
            return False
        proc = row.get("proc") or ""
        if proc in ALLOWED_LISTENER_PROCS:
            return False
        if tailscale_up and row["proto"] == "udp" and row["port"] >= 32768:
            return False
        return True

    unexpected = [row for row in listeners if is_unexpected(row)]
    return {
        "ok": len(unexpected) == 0,
        "appliance": True,
        "listeners": listeners,
        "unexpected": unexpected,
        "expected": list(EXPECTED_LISTENERS),
    }


def _parse_port(local: str) -> int | None:
    text = local.strip()
    if text.startswith("[") and "]:" in text:
        try:
            return int(text.rsplit("]:", 1)[1])
        except ValueError:
            return None
    if ":" in text:
        try:
            return int(text.rsplit(":", 1)[1])
        except ValueError:
            return None
    return None


_SS_PROC_RE = re.compile(r'users:\(\("([^"]+)"')


def _parse_ss_process(line: str) -> str | None:
    """Extract process name from ss -p users:(("name",pid=…)) field."""
    match = _SS_PROC_RE.search(line)
    if not match:
        return None
    name = match.group(1).strip()
    # Avahi sometimes shows as avahi-daemon:r / similar suffixes after the binary name.
    if ":" in name:
        name = name.split(":", 1)[0]
    return name or None


def ntp_status() -> dict[str, Any]:
    if os.name == "nt" or shutil.which("timedatectl") is None:
        return {"ok": True, "synchronized": None, "timezone": None, "appliance": False}
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "Timezone", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
        timezone = lines[0] if lines else None
        synced_raw = (lines[1] if len(lines) > 1 else "").lower()
        synchronized = synced_raw in {"yes", "true", "1"} if synced_raw else None
        return {
            "ok": synchronized is not False,
            "synchronized": synchronized,
            "timezone": timezone,
            "appliance": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "synchronized": None, "timezone": None}


def recent_journal_errors(*, lines: int = 40) -> dict[str, Any]:
    """Last N journal lines matching priority err for stonepi-* / nginx."""
    if os.name == "nt" or shutil.which("journalctl") is None:
        return {"ok": True, "appliance": False, "entries": []}
    try:
        # Explicit units — glob -u stonepi-* is unreliable across journalctl versions.
        units = [str(item["unit"]) for item in APP_CATALOG if item.get("unit")] + ["nginx"]
        cmd = ["journalctl", "-p", "err", "-n", str(lines), "--no-pager", "-o", "short-iso"]
        for unit in units:
            cmd.extend(["-u", unit])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        entries = [ln for ln in (result.stdout or "").splitlines() if ln.strip()][-lines:]
        return {"ok": True, "appliance": True, "entries": entries, "count": len(entries)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "entries": []}


def enrich_watch(watch: dict[str, Any], *, listening: dict[str, Any] | None = None, hardware: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge listening/hardware/outputs-required into Watch level for Health banner + /system/health."""
    out = dict(watch or {})
    level = out.get("level") or stonepi_watch.LEVEL_HEALTHY
    reasons = list(out.get("reasons") or [])
    order = {
        stonepi_watch.LEVEL_HEALTHY: 0,
        stonepi_watch.LEVEL_ATTENTION: 1,
        stonepi_watch.LEVEL_CRITICAL: 2,
    }

    def raise_to(next_level: str, reason: str) -> None:
        nonlocal level
        if order[next_level] > order[level]:
            level = next_level
        if reason and reason not in reasons:
            reasons.append(reason)

    hw = hardware or {}
    if hw.get("undervoltage"):
        raise_to(stonepi_watch.LEVEL_CRITICAL, "Undervoltage detected")
    elif hw.get("throttling"):
        raise_to(stonepi_watch.LEVEL_ATTENTION, "CPU throttling detected")

    listen = listening or {}
    if listen.get("appliance") and not listen.get("ok"):
        unexpected = listen.get("unexpected") or []
        ports = ", ".join(f"{r.get('proto')}/{r.get('port')}" for r in unexpected[:4]) or "unknown"
        raise_to(stonepi_watch.LEVEL_ATTENTION, f"Unexpected listeners: {ports}")

    # Outputs-required: elevate notify-down to critical
    outputs_required = False
    try:
        prefs_path = Path("/var/lib/stonepi/notify/prefs.json")
        if prefs_path.is_file():
            data = json.loads(prefs_path.read_text(encoding="utf-8"))
            outputs_required = bool(data.get("outputs_required"))
    except Exception:
        outputs_required = False
    if outputs_required:
        apps = list(out.get("apps") or [])
        notifications = next((a for a in apps if a.get("id") == "notify"), None)
        if notifications and notifications.get("enabled", True) and not notifications.get("running"):
            raise_to(stonepi_watch.LEVEL_CRITICAL, "Outputs required but Notify is down")

    out["level"] = level
    out["reasons"] = reasons[:8]
    if reasons:
        out.update(stonepi_watch.summarize(reasons, list(out.get("apps") or [])))
    else:
        out.setdefault("summary", "All clear")
    out["outputs_required"] = outputs_required
    return out


def hardware_status() -> dict[str, Any]:
    """Throttling / undervoltage / CPU freq / boot count (Pi-oriented)."""
    throttled = None
    undervoltage = False
    throttling = False
    freq_mhz = None
    boot_count = None
    if os.name != "nt":
        try:
            result = subprocess.run(
                ["vcgencmd", "get_throttled"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            match = re.search(r"0x([0-9a-fA-F]+)", result.stdout or "")
            if match:
                throttled = int(match.group(1), 16)
                undervoltage = bool(throttled & 0x50005)
                throttling = bool(throttled & 0xE000E)
        except Exception:
            pass
        freq_mhz = _read_cpu_freq_mhz()
        boot_count = _read_boot_count()

    level = stonepi_watch.LEVEL_HEALTHY
    if undervoltage:
        level = stonepi_watch.LEVEL_CRITICAL
    elif throttling:
        level = stonepi_watch.LEVEL_ATTENTION
    return {
        "throttled_raw": throttled,
        "undervoltage": undervoltage,
        "throttling": throttling,
        "cpu_freq_mhz": freq_mhz,
        "boot_count": boot_count,
        "level": level,
    }


def _read_cpu_freq_mhz() -> int | None:
    """Prefer sysfs (works as stonepi-dash); fall back to vcgencmd (needs video group)."""
    for path in (
        Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"),
        Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq"),
    ):
        try:
            raw = int(path.read_text(encoding="utf-8").strip())
            if raw > 0:
                # sysfs reports kHz
                return int(round(raw / 1000))
        except Exception:
            continue
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_clock", "arm"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        match = re.search(r"frequency\(.*?\)=(\d+)", result.stdout or "")
        if match:
            return int(round(int(match.group(1)) / 1_000_000))
    except Exception:
        pass
    return None


def _read_boot_count() -> int | None:
    """journalctl needs systemd-journal; fall back to last(1) / wtmp."""
    try:
        result = subprocess.run(
            ["journalctl", "--list-boots", "--no-pager"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        boots = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
        if boots:
            return len(boots)
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["last", "-x", "reboot"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        # "reboot ..." lines; ignore "wtmp begins ..."
        count = sum(
            1
            for ln in (result.stdout or "").splitlines()
            if ln.lower().startswith("reboot")
        )
        if count:
            return count
    except Exception:
        pass
    return None


def maybe_emit_disk_warning(watch: dict[str, Any]) -> None:
    """Emit system.disk_warning once per day when disk attention/critical."""
    disk = watch.get("disk_pct")
    if disk is None:
        return
    try:
        disk_i = int(disk)
    except (TypeError, ValueError):
        return
    if disk_i < stonepi_watch.DISK_ATTENTION_PCT:
        return
    severity = "critical" if disk_i >= stonepi_watch.DISK_CRITICAL_PCT else "warning"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from stonepi_contracts import emit_event

        emit_event(
            {
                "id": "system.disk_warning",
                "source": "system",
                "title": f"Disk {disk_i}% used",
                "summary": f"StonePi host disk is at {disk_i}%",
                "severity": severity,
                "audience": "admin",
                "dedupe_key": f"system.disk_warning:{day}:{severity}",
                "data": {"disk_pct": disk_i},
                "url": "/overview",
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("disk warning emit failed: %s", exc)


def collect_system_health(cookies: dict[str, str] | None = None) -> dict[str, Any]:
    """Full platform health JSON for GET /system/health."""
    watch = display.watch_snapshot(cookies)
    maybe_emit_disk_warning(watch)
    overview = {
        "cpu_pct": display._read_cpu_pct(),
        "mem_pct": display._read_mem_pct(),
        "temp_c": display._read_temp_c(),
        "uptime": display._read_uptime(),
    }
    try:
        network = network_svc.network_snapshot()
    except Exception:  # noqa: BLE001
        network = {"internet": {"ok": False}, "tailscale": {}, "appliance": False}
    backup = services.backup_info()
    dest = destinations_status()
    listen = listening_ports()
    ntp = ntp_status()
    hw = hardware_status()
    journal = recent_journal_errors()
    watch = enrich_watch(watch, listening=listen, hardware=hw)

    apps = list(watch.get("apps") or [])
    notifications = next((a for a in apps if a.get("id") == "notify"), None)
    nginx_ok = True
    if os.name != "nt" and shutil.which("systemctl"):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "nginx"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3,
            )
            nginx_ok = (result.stdout or "").strip() == "active"
        except Exception:
            nginx_ok = False

    level = watch.get("level") or stonepi_watch.LEVEL_HEALTHY

    return {
        "ok": level == stonepi_watch.LEVEL_HEALTHY,
        "level": level,
        # Plain-text consumers (Notify, displays): "Multiple services are not running (A, B)".
        "summary": stonepi_watch.summary_text(watch) or "All clear",
        "summary_detail": watch.get("summary_detail") or "",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "stonepi": "OK" if level == stonepi_watch.LEVEL_HEALTHY else level,
        "os": "OK",
        "disk_pct": watch.get("disk_pct"),
        "memory_pct": overview["mem_pct"],
        "cpu_pct": overview["cpu_pct"],
        "temperature_c": overview["temp_c"],
        "uptime": overview["uptime"],
        "network": {
            "internet": bool((network.get("internet") or {}).get("ok")),
            "tailscale": bool((network.get("tailscale") or {}).get("connected")),
            "appliance": bool(network.get("appliance")),
        },
        "nginx": {"ok": nginx_ok},
        "notifications": {
            "ok": bool(notifications.get("running")) if notifications else False,
            "unit": (notifications or {}).get("unit"),
            "destinations": dest,
        },
        "ntfy": dest["ntfy"],
        "trmnl": dest["trmnl"],
        "backup": {
            "status": watch.get("backup_status"),
            "age_days": watch.get("backup_age_days"),
            "raw": {k: backup.get(k) for k in ("status", "timestamp", "finished_at", "time") if k in backup},
        },
        "watch": {
            "level": watch.get("level"),
            "reasons": watch.get("reasons") or [],
            "apps": apps,
        },
        "listening": listen,
        "ntp": ntp,
        "hardware": hw,
        "journal_errors": journal,
        "data_dir": str(DATA_DIR),
    }
