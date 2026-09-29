from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable

DEFAULT_NTFY_SERVER = "https://ntfy.sh"
VAULT_NTFY_TOKEN_KEY = "STONEPI_NTFY_TOKEN"

_data_dir: Path | None = None
_vault_token_fn: Callable[[], str] | None = None
_webhook_url_fn: Callable[[], str] | None = None
_lock = threading.Lock()


def configure(
    *,
    data_dir: Path,
    vault_token_fn: Callable[[], str] | None = None,
    webhook_url_fn: Callable[[], str] | None = None,
) -> None:
    global _data_dir, _vault_token_fn, _webhook_url_fn
    _data_dir = Path(data_dir)
    _vault_token_fn = vault_token_fn
    _webhook_url_fn = webhook_url_fn


configure_destinations = configure


def _path() -> Path:
    if _data_dir is None:
        raise RuntimeError("stonepi_notify.configure() was not called")
    return _data_dir / "destinations.json"


def _default() -> dict[str, Any]:
    return {
        "ntfy": {
            "enabled": False,
            "server": DEFAULT_NTFY_SERVER,
            # Optional household channel (shared topic for household events).
            # ``topic`` is the pre-personal-alerts name, kept as an alias.
            "household_topic": "",
            "topic": "",
            "default_priority": 3,
        },
        "trmnl": {
            "enabled": False,
            "webhook_url": "",
            "interval_minutes": 30,
            "device": "og",
            "design": "household",
            "display_id": "dashboard",
            "last_push_at": None,
            "last_push_ok": None,
            "last_push_message": None,
            "last_push_status": None,
        },
    }


def load_destinations() -> dict[str, Any]:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = _default()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for section in ("ntfy", "trmnl"):
                    if isinstance(data.get(section), dict):
                        cfg[section].update(data[section])
                legacy = data.get("ntfy") if isinstance(data.get("ntfy"), dict) else {}
                if "household_topic" not in legacy and legacy.get("topic"):
                    cfg["ntfy"]["household_topic"] = legacy["topic"]
        except (OSError, json.JSONDecodeError):
            pass
    ntfy = cfg["ntfy"]
    ntfy["enabled"] = bool(ntfy.get("enabled"))
    ntfy["server"] = str(ntfy.get("server") or DEFAULT_NTFY_SERVER).strip().rstrip("/") or DEFAULT_NTFY_SERVER
    ntfy["household_topic"] = str(ntfy.get("household_topic") or "").strip()
    ntfy["topic"] = ntfy["household_topic"]
    try:
        ntfy["default_priority"] = max(1, min(5, int(ntfy.get("default_priority") or 3)))
    except (TypeError, ValueError):
        ntfy["default_priority"] = 3

    trmnl = cfg["trmnl"]
    trmnl["enabled"] = bool(trmnl.get("enabled"))
    trmnl["webhook_url"] = str(trmnl.get("webhook_url") or "").strip()
    if _webhook_url_fn and not trmnl["webhook_url"]:
        trmnl["webhook_url"] = str(_webhook_url_fn() or "").strip()
    trmnl["display_id"] = str(trmnl.get("display_id") or "dashboard").strip() or "dashboard"
    try:
        trmnl["interval_minutes"] = max(10, min(120, int(trmnl.get("interval_minutes") or 30)))
    except (TypeError, ValueError):
        trmnl["interval_minutes"] = 30
    return cfg


def save_destinations(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = load_destinations()
    if "ntfy" in updates and isinstance(updates["ntfy"], dict):
        ntfy_updates = dict(updates["ntfy"])
        if "topic" in ntfy_updates and "household_topic" not in ntfy_updates:
            ntfy_updates["household_topic"] = ntfy_updates["topic"]
        ntfy_updates.pop("topic", None)
        cfg["ntfy"].update(ntfy_updates)
        cfg["ntfy"]["topic"] = cfg["ntfy"].get("household_topic") or ""
    if "trmnl" in updates and isinstance(updates["trmnl"], dict):
        cfg["trmnl"].update(updates["trmnl"])
    # Re-normalize via load path fields
    with _lock:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Don't persist vault-filled webhook into file if empty was intended
        to_write = {
            "ntfy": dict(cfg["ntfy"]),
            "trmnl": dict(cfg["trmnl"]),
        }
        path.write_text(json.dumps(to_write, indent=2), encoding="utf-8")
    return load_destinations()


def vault_ntfy_token() -> str:
    if _vault_token_fn:
        try:
            return str(_vault_token_fn() or "").strip()
        except Exception:
            return ""
    try:
        from stonepi_vault import get_secret

        return (get_secret(VAULT_NTFY_TOKEN_KEY, env_name=VAULT_NTFY_TOKEN_KEY, default="") or "").strip()
    except Exception:
        return ""
