"""Fire-and-forget event POST to Notify with one retry + optional disk queue."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .events import EventEnvelope, validate_event

logger = logging.getLogger("stonepi.contracts.emit")

RETRY_QUEUE = Path(os.environ.get("STONEPI_EMIT_RETRY_PATH") or "/var/lib/stonepi/notify/emit-retry.jsonl")


def notify_base_url() -> str:
    """Loopback base URL for the Notify ingest API."""
    # STONEPI_NOTIFICATIONS_* are pre-rename names; still honoured so an
    # un-migrated env file keeps emitters working.
    override = (
        os.environ.get("STONEPI_NOTIFY_URL") or os.environ.get("STONEPI_NOTIFICATIONS_URL") or ""
    ).strip().rstrip("/")
    if override:
        return override
    port = (
        os.environ.get("STONEPI_NOTIFY_PORT") or os.environ.get("STONEPI_NOTIFICATIONS_PORT") or "8012"
    ).strip() or "8012"
    return f"http://127.0.0.1:{port}"


# Pre-rename alias.
notifications_base_url = notify_base_url


def _in_tests() -> bool:
    """Under pytest, emit nothing for real (no network, no retry queue).

    Test runs otherwise post to a live dev Notify or pile events into the
    machine-wide retry queue. Set STONEPI_EMIT_IN_TESTS=1 to opt back in.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get("STONEPI_EMIT_IN_TESTS")


def _post_once(url: str, payload: dict[str, Any], timeout: float) -> bool:
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.post(url, json=payload)
        return response.status_code < 400


def _enqueue(payload: dict[str, Any]) -> None:
    try:
        RETRY_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with RETRY_QUEUE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("emit_event enqueue failed: %s", exc)


def drain_emit_retry_queue(
    *,
    base_url: str | None = None,
    timeout: float = 2.0,
    limit: int = 50,
    deliver: Callable[[dict[str, Any]], bool] | None = None,
) -> int:
    """Replay queued events after Notify comes back. Returns delivered count.

    ``deliver`` hands each payload to a local function instead of POSTing;
    Notify uses it to ingest its own queue without calling itself over HTTP.
    """
    if not RETRY_QUEUE.is_file():
        return 0
    try:
        lines = RETRY_QUEUE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    if not lines:
        return 0
    url = f"{(base_url or notify_base_url()).rstrip('/')}/api/events"
    remaining: list[str] = []
    delivered = 0
    for index, line in enumerate(lines):
        if index >= limit:
            remaining.extend(lines[index:])
            break
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            sent = deliver(payload) if deliver is not None else _post_once(url, payload, timeout)
            if sent:
                delivered += 1
            else:
                remaining.append(line)
        except Exception:
            remaining.append(line)
    try:
        if remaining:
            RETRY_QUEUE.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        else:
            RETRY_QUEUE.unlink(missing_ok=True)
    except OSError:
        pass
    return delivered


def emit_event(
    event: EventEnvelope | dict[str, Any],
    *,
    base_url: str | None = None,
    timeout: float = 2.0,
) -> bool:
    """Fire-and-forget event POST to Notify. Never raises into callers.

    On failure: one short retry, then append to a JSONL queue for later drain.
    """
    raw = event.to_dict() if isinstance(event, EventEnvelope) else event
    payload = validate_event(raw if isinstance(raw, dict) else None)
    if not payload:
        return False
    if _in_tests():
        logger.debug("emit_event skipped under pytest: %s", payload.get("id"))
        return False
    url = f"{(base_url or notify_base_url()).rstrip('/')}/api/events"
    for attempt in range(2):
        try:
            if _post_once(url, payload, timeout):
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("emit_event failed (attempt %s): %s", attempt + 1, exc)
        if attempt == 0:
            time.sleep(0.4)
    _enqueue(payload)
    return False
