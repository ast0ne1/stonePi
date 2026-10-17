"""Shared model health: ok / cooling until a time / last error, seen by every app.

One file in the Vault folder (``ai-health.json``), locked like stonepi_vault.quota, so a
429 in NewsCast makes Studio skip that model too until it has cooled off.

    {"models": {"gemini/gemini-3.5-flash-lite": {"cooling_until": "…", "last_error": "…",
                "last_error_at": "…", "last_ok_at": "…", "kind": "rate_limit"}},
     "functions": {"newscast.summary": {"failing_since": "…", "alerted": true}}}
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ._files import ai_root, locked, read_json, write_json

HEALTH_NAME = "ai-health.json"

# How long a model sits out after each kind of failure (a Retry-After header wins).
COOLDOWN_SECONDS = {
    "rate_limit": 15 * 60,
    "quota": 60 * 60,  # daily / monthly allowance used up
    "auth": 60 * 60,  # key rejected
    "model": 60 * 60,  # model not found / not allowed on this key
    "outage": 5 * 60,  # 5xx, overloaded, timeouts, no connection
}
KIND_LABELS = {
    "rate_limit": "rate limited",
    "quota": "free quota hit",
    "auth": "key rejected",
    "model": "model not available",
    "outage": "provider unreachable",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.replace(microsecond=0).isoformat()


def _parse(value: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def health_path() -> Path:
    return ai_root() / HEALTH_NAME


def _read() -> dict[str, Any]:
    data = read_json(health_path()) or {}
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    functions = data.get("functions") if isinstance(data.get("functions"), dict) else {}
    return {"models": models, "functions": functions}


def _update(change) -> Any:
    path = health_path()
    with locked(path):
        data = _read()
        result = change(data)
        write_json(path, data)
        return result


def snapshot() -> dict[str, Any]:
    return _read()


def cooling_until(model_id: str, data: dict[str, Any] | None = None) -> datetime | None:
    """When the model may be tried again, or None when it's available now."""
    data = data or _read()
    row = data["models"].get(model_id) or {}
    until = _parse(row.get("cooling_until"))
    return until if until and until > _now() else None


def available(model_id: str, data: dict[str, Any] | None = None) -> bool:
    return cooling_until(model_id, data) is None


OK_REFRESH_SECONDS = 600  # a healthy model's "last used" is rewritten at most this often


def mark_ok(model_id: str, function_id: str | None = None) -> bool:
    """Record a success. True when the model (or function) had been failing: a recovery.

    Successes are the common case (several per NewsCast story), so a healthy model's file
    entry is only rewritten every few minutes: fewer SD-card writes on the Pi.
    """
    data = _read()
    row = data["models"].get(model_id) or {}
    last_ok = _parse(row.get("last_ok_at"))
    failing = row.get("cooling_until") or (function_id and function_id in data["functions"])
    newer_error = _parse(row.get("last_error_at"))
    if (
        not failing
        and last_ok is not None
        and (newer_error is None or newer_error < last_ok)
        and (_now() - last_ok).total_seconds() < OK_REFRESH_SECONDS
    ):
        return False

    def change(data):
        row = data["models"].setdefault(model_id, {})
        was_down = bool(row.get("cooling_until") or row.get("last_error"))
        row["last_ok_at"] = _iso(_now())
        row.pop("cooling_until", None)
        row.pop("kind", None)
        recovered = was_down
        if function_id and function_id in data["functions"]:
            data["functions"].pop(function_id, None)
            recovered = True
        return recovered

    return _update(change)


def mark_failed(model_id: str, kind: str, error: str, *, retry_after: float | None = None) -> datetime | None:
    """Record a failure; models cool off for the kinds in COOLDOWN_SECONDS. Returns the cool-off end."""
    seconds = COOLDOWN_SECONDS.get(kind)
    if retry_after and kind in {"rate_limit", "quota", "outage"}:
        seconds = max(30.0, min(float(retry_after), 6 * 3600.0))
    now = _now()
    until = now + timedelta(seconds=seconds) if seconds else None

    def change(data):
        row = data["models"].setdefault(model_id, {})
        row["last_error"] = (error or kind).strip()[:300]
        row["last_error_at"] = _iso(now)
        row["kind"] = kind
        if until is not None:
            row["cooling_until"] = _iso(until)
        return until

    return _update(change)


def clear(model_id: str | None = None) -> None:
    """Forget a model's failures (Dashboard's "Try again"), or every model's."""

    def change(data):
        if model_id is None:
            data["models"].clear()
            data["functions"].clear()
        else:
            data["models"].pop(model_id, None)

    _update(change)


def function_failed(function_id: str) -> bool:
    """Every model for the function failed. True the first time (so one alert is sent)."""

    if (_read()["functions"].get(function_id) or {}).get("alerted"):
        return False  # already alerted: no write per story during an outage

    def change(data):
        row = data["functions"].setdefault(function_id, {"failing_since": _iso(_now())})
        if row.get("alerted"):
            return False
        row["alerted"] = True
        return True

    return _update(change)


def status_line(entry: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, str]:
    """{state: ok|cooling|error|unused, text} for Dashboard's status line under each model."""
    data = data or _read()
    row = data["models"].get(entry["id"]) or {}
    label = entry.get("label") or entry["id"]
    until = cooling_until(entry["id"], data)
    error_at = _parse(row.get("last_error_at"))
    ok_at = _parse(row.get("last_ok_at"))
    if until is not None:
        what = KIND_LABELS.get(str(row.get("kind") or ""), "failing")
        at = f" {_clock(error_at)}" if error_at else ""
        return {"state": "cooling", "text": f"{label}: {what}{at}, cooling until {_clock(until)}"}
    if error_at and (not ok_at or error_at > ok_at):
        return {"state": "error", "text": f"{label}: last call failed {_clock(error_at)}: {row.get('last_error', '')}"[:240]}
    if ok_at:
        return {"state": "ok", "text": f"{label}: working, last used {_clock(ok_at)}"}
    return {"state": "unused", "text": f"{label}: not used yet"}


def _clock(when: datetime) -> str:
    local = when.astimezone()
    today = _now().astimezone().date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%d %b %H:%M")
