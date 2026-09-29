"""Per-Display TRMNL push: payload, webhook secrets, scheduling decisions.

Each Display that pushes to TRMNL has its own private plugin, so its own
webhook (kept in the vault) and its own interval. The payload carries the
Display's layout (``L``/``G``) for the universal template plus only the
variables its widgets read, which keeps it under TRMNL's size limit.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from .displays import BUILTIN_DASHBOARD_ID, get_display, load_displays, update_trmnl_status
from .grid import device_grid, layout_rows
from .templates import BASE_KEYS, format_updated, snippet_keys
from .webhook import validate_webhook_url

logger = logging.getLogger("stonepi.display.push")

# TRMNL private-plugin webhook limit on the standard plan (TRMNL+ allows 5 KB).
PAYLOAD_LIMIT_BYTES = 2048
LEGACY_WEBHOOK_KEY = "DISPLAY_WEBHOOK_URL"
LEGACY_WEBHOOK_ENV = "STONEPI_DISPLAY_WEBHOOK"


def webhook_key(display_id: str) -> str:
    """Vault key for a Display's webhook. The built-in Dashboard keeps the original key."""
    if display_id == BUILTIN_DASHBOARD_ID:
        return LEGACY_WEBHOOK_KEY
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(display_id)).strip("_").upper()
    return f"{LEGACY_WEBHOOK_KEY}_{slug}"


def get_webhook(display_id: str) -> str:
    try:
        from stonepi_vault import get_secret
    except ImportError:
        return ""
    env_name = LEGACY_WEBHOOK_ENV if display_id == BUILTIN_DASHBOARD_ID else None
    try:
        return str(get_secret(webhook_key(display_id), env_name=env_name, default="") or "").strip()
    except Exception:
        return ""


def set_webhook(display_id: str, url: str) -> None:
    """Store (or clear, when ``url`` is empty) a Display's webhook. Validates first."""
    from stonepi_vault import get_vault

    value = (url or "").strip()
    if value:
        validate_webhook_url(value)
        get_vault().set(webhook_key(display_id), value)
    else:
        get_vault().delete(webhook_key(display_id))


def _size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))


def fit_payload(payload: dict[str, Any], limit: int = PAYLOAD_LIMIT_BYTES) -> dict[str, Any]:
    """Trim the payload until it fits: drop the tail of the longest list, then shorten strings.

    ``L`` and ``G`` are never trimmed — without them nothing draws.
    """
    out = json.loads(json.dumps(payload, default=str))
    protected = {"L", "G", "TB", "TN"}
    while _size(out) > limit:
        lists = [(len(json.dumps(v)), k) for k, v in out.items() if isinstance(v, list) and v and k not in protected]
        if lists:
            _, key = max(lists)
            out[key] = out[key][:-1]
            continue
        strings = [(len(v), k) for k, v in out.items() if isinstance(v, str) and len(v) > 8 and k not in protected]
        if not strings:
            break
        _, key = max(strings)
        out[key] = out[key][: max(8, len(out[key]) // 2)]
    return out


def display_payload(display: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    """merge_variables for one Display."""
    grid = device_grid(display.get("device"))
    rows = layout_rows(display)
    if (display.get("trmnl") or {}).get("template") == "legacy":
        # Plugin still holds pasted legacy markup, which reads any flat key.
        payload = dict(variables)
    else:
        keys = {k for k in BASE_KEYS if k not in {"L", "G", "TB", "TN"}}
        for code in {row[0] for row in rows}:
            keys |= snippet_keys(code)
        payload = {k: variables[k] for k in sorted(keys) if k in variables}
    payload["L"] = rows
    payload["G"] = [grid["cols"], grid["rows"]]
    payload["TB"] = display.get("title_bar") or "bottom"
    if payload["TB"] != "off":
        payload["TN"] = str(display.get("name") or "StonePi")[:40]
        if variables.get("updated_ts"):
            payload["updated_at"] = format_updated(variables["updated_ts"], display.get("date_format"))
    else:
        payload.pop("updated_at", None)
    return fit_payload(payload)


def push_display(display_id: str, variables: dict[str, Any]) -> dict[str, Any]:
    """POST one Display to its webhook and record the outcome on the Display."""
    display = get_display(display_id)
    now = datetime.now(timezone.utc).isoformat()
    if not display:
        return {"ok": False, "message": "Display not found.", "status": None}
    url = get_webhook(display_id)
    result: dict[str, Any]
    if not url:
        result = {"ok": False, "message": "Add this Display's TRMNL webhook URL first.", "status": None}
    else:
        payload = display_payload(display, variables)
        try:
            url = validate_webhook_url(url)
            with httpx.Client(timeout=20.0, follow_redirects=False) as client:
                response = client.post(url, json={"merge_variables": payload})
            ok = response.status_code < 400
            message = "Pushed to TRMNL." if ok else (response.text or f"HTTP {response.status_code}")[:240]
            if response.status_code == 429:
                ok, message = False, "TRMNL rate limit (try again later)."
            result = {"ok": ok, "message": message, "status": response.status_code, "bytes": _size(payload)}
        except ValueError as exc:
            result = {"ok": False, "message": str(exc)[:240], "status": None}
        except Exception as exc:  # noqa: BLE001 - network errors surface in the UI
            result = {"ok": False, "message": str(exc)[:240], "status": None}
    update_trmnl_status(
        display_id,
        last_push_at=now,
        last_push_ok=result["ok"],
        last_push_message=result["message"],
        last_push_status=result.get("status"),
    )
    return result


def _is_due(display: dict[str, Any], now: datetime) -> bool:
    trmnl = display.get("trmnl") or {}
    last = trmnl.get("last_push_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return now - last_dt >= timedelta(minutes=int(trmnl.get("interval_minutes") or 30))


def pushable_displays() -> list[dict[str, Any]]:
    """Enabled Displays with TRMNL push switched on and a webhook set."""
    return [
        d
        for d in load_displays()
        if d.get("enabled") and (d.get("trmnl") or {}).get("enabled") and get_webhook(d["id"])
    ]


def push_due(collect_fn: Callable[[], dict[str, Any]], *, force: bool = False) -> list[dict[str, Any]]:
    """Push every Display that is due (or all pushable ones when ``force``). Collects once."""
    now = datetime.now(timezone.utc)
    due = [d for d in pushable_displays() if force or _is_due(d, now)]
    if not due:
        return []
    variables = collect_fn()
    results = []
    for display in due:
        logger.info("TRMNL push for Display %s", display["id"])
        results.append({"display_id": display["id"], "name": display["name"], **push_display(display["id"], variables)})
    return results
