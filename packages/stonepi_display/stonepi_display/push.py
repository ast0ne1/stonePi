"""Per-Display TRMNL push: payload, webhook secrets, scheduling decisions.

Each Display that pushes to TRMNL has its own private plugin, so its own
webhook (kept in the vault) and its own interval. The payload carries the
Display's layout (``L``/``G``) for the universal template plus only the
variables its widgets read, which keeps it under TRMNL's size limit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from .displays import (
    BUILTIN_DASHBOARD_ID,
    get_display,
    load_displays,
    push_limits,
    trmnl_tier,
    update_trmnl_status,
    web_bits,
)
from .grid import device_grid, layout_rows
from .templates import BASE_KEYS, format_updated, snippet_keys
from .webhook import validate_webhook_url

logger = logging.getLogger("stonepi.display.push")

# Payload cap = the plan's limit (5 KB Standard, 10 KB TRMNL+) less a little headroom.
PAYLOAD_HEADROOM_BYTES = 200
PAYLOAD_LIMIT_BYTES = 5 * 1024 - PAYLOAD_HEADROOM_BYTES  # Standard; see payload_limit()
# Unchanged data isn't re-sent, but the screen still gets a fresh "Updated" stamp this often.
UNCHANGED_REFRESH = timedelta(hours=2)
# After a 429 without Retry-After, wait out TRMNL's hourly window.
RATE_LIMIT_BACKOFF = timedelta(hours=1)
LEGACY_WEBHOOK_KEY = "DISPLAY_WEBHOOK_URL"
LEGACY_WEBHOOK_ENV = "STONEPI_DISPLAY_WEBHOOK"
# TRMNL's Webhook Image plugin takes at most 1 MB per image.
WEBHOOK_IMAGE_MAX_BYTES = 1024 * 1024
# One push per Display at a time: the budget check and the push log written after the POST
# must not interleave with another push (a web capture can take a minute).
_push_locks: dict[str, threading.Lock] = {}
_push_locks_guard = threading.Lock()


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


def payload_limit(display: dict[str, Any]) -> int:
    return trmnl_tier(display.get("trmnl"))["payload_kb"] * 1024 - PAYLOAD_HEADROOM_BYTES


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
    return fit_payload(payload, payload_limit(display))


# Panel size per device: what a web Display captures and sends (TRMNL X lays out at
# 1040×780 CSS px and draws at 1.8×, like the universal template's preview).
WEB_SCREENS: dict[str, dict[str, Any]] = {
    "og": {"width": 800, "height": 480, "scale": 1.0},
    "v2": {"width": 1872, "height": 1404, "scale": 1.8},
}


def web_image(
    display: dict[str, Any], *, page: dict[str, Any] | None = None, max_age: float = 0.0
) -> tuple[bytes, dict[str, Any]]:
    """(PNG, info) for a web Display: capture its page (or ``page``, unsaved settings from the
    preview), convert for e-ink.

    Raises ValueError when no page is picked or the page is gone; capture errors pass through.
    """
    from . import webcapture, webimage
    from .webpages import get_page

    web = display.get("web") or {}
    page = page or get_page(str(web.get("page_id") or ""))
    if not page or not page.get("url"):
        raise ValueError("Pick a web page for this Display first.")
    screen = WEB_SCREENS.get(display.get("device") or "og", WEB_SCREENS["og"])
    bits = web_bits(display)
    capture = webcapture.capture(
        page, screen["width"], screen["height"], device_scale=screen["scale"], max_age=max_age, bits=bits
    )
    png, info = webimage.to_eink(
        capture,
        screen["width"],
        screen["height"],
        bits=bits,
        dither=bool(web.get("dither")),
        invert=str(web.get("invert") or "auto"),
        threshold=int(web.get("threshold") or webimage.DEFAULT_THRESHOLD),
    )
    return png, {**info, "page": page["name"], "width": screen["width"], "height": screen["height"]}


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _content_hash(payload: dict[str, Any]) -> str:
    """What the screen shows, ignoring the "Updated" stamp that changes every push."""
    body = {k: v for k, v in payload.items() if k != "updated_at"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


def _recent_pushes(trmnl: dict[str, Any], now: datetime) -> list[str]:
    hour_ago = now - timedelta(hours=1)
    return [ts for ts in trmnl.get("push_log") or [] if (_parse_ts(ts) or hour_ago) > hour_ago]


def _hhmm(when: datetime) -> str:
    return when.astimezone().strftime("%H:%M")


def push_display(
    display_id: str,
    variables: dict[str, Any],
    *,
    skip_unchanged: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """POST one Display to its webhook and record the outcome on the Display.

    Every request counts against the Display's hourly budget (its TRMNL plan, a
    little under TRMNL's limit); over budget or inside a 429 back-off nothing is
    sent. ``skip_unchanged`` (scheduled and automatic pushes) leaves an unchanged
    screen alone, refreshing it at least every UNCHANGED_REFRESH.
    """
    with _push_locks_guard:
        lock = _push_locks.setdefault(display_id, threading.Lock())
    if not lock.acquire(blocking=False):
        # "held": nothing was sent, but something may still need sending (a caller can retry);
        # an unchanged screen is skipped without it.
        return {"ok": True, "skipped": True, "quiet": True, "held": True,
                "message": "A push for this Display is already running.", "status": None}
    try:
        return _push_display(display_id, variables, skip_unchanged=skip_unchanged, now=now)
    finally:
        lock.release()


def _push_display(
    display_id: str,
    variables: dict[str, Any],
    *,
    skip_unchanged: bool,
    now: datetime | None,
) -> dict[str, Any]:
    display = get_display(display_id)
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    if not display:
        return {"ok": False, "message": "Display not found.", "status": None}
    trmnl = display.get("trmnl") or {}
    tier = push_limits(display)
    is_web = display.get("kind") == "web"
    recent = _recent_pushes(trmnl, now)

    def skipped(message: str, *, quiet: bool = False, held: bool = False) -> dict[str, Any]:
        update_trmnl_status(display_id, last_check_at=stamp, last_skip_message=message, push_log=recent)
        return {"ok": True, "skipped": True, "quiet": quiet, "held": held, "message": message, "status": None}

    backoff = _parse_ts(trmnl.get("backoff_until"))
    if backoff and backoff > now:
        return skipped(f"TRMNL asked us to slow down; next push after {_hhmm(backoff)}.", held=True)
    if len(recent) >= tier["budget"]:
        free_at = (_parse_ts(min(recent)) or now) + timedelta(hours=1)
        return skipped(
            f"Hourly limit for {tier['label']} reached ({tier['budget']} pushes); next slot {_hhmm(free_at)}.", held=True
        )

    def failed(message: str) -> dict[str, Any]:
        update_trmnl_status(display_id, last_push_at=stamp, last_check_at=stamp, last_push_ok=False,
                            last_push_message=message[:240], last_push_status=None, last_skip_message=None)
        return {"ok": False, "message": message[:240], "status": None}

    url = get_webhook(display_id)
    if not url:
        result: dict[str, Any] = {"ok": False, "message": "Add this Display's TRMNL webhook URL first.", "status": None}
        update_trmnl_status(display_id, last_push_at=stamp, last_push_ok=False, last_push_message=result["message"],
                            last_push_status=None)
        return result

    if is_web:
        try:
            # A capture from the last minute (the Preview button) is reused.
            image, info = web_image(display, max_age=60.0)
        except ValueError as exc:
            return failed(str(exc))
        except Exception as exc:  # noqa: BLE001 - capture errors (page down, no browser) show in the UI
            return failed(f"Couldn't capture the page: {exc}")
        digest = info["hash"]
        size = info["bytes"]
        if size > WEBHOOK_IMAGE_MAX_BYTES:
            return failed(
                f"The image is {size / 1024 / 1024:.1f} MB, over TRMNL's 1 MB. Use fewer greys, "
                "turn dithering off, or hide busy parts of the page."
            )
        post: dict[str, Any] = {"content": image, "headers": {"Content-Type": "image/png"}}
    else:
        payload = display_payload(display, variables)
        digest = _content_hash(payload)
        size = _size(payload)
        post = {"json": {"merge_variables": payload}}
    last_ok = _parse_ts(trmnl.get("last_push_at")) if trmnl.get("last_push_ok") else None
    # An unchanged image is never re-sent (no stamp on it to refresh); widgets get a fresh
    # "Updated" stamp every UNCHANGED_REFRESH.
    if (
        skip_unchanged
        and digest == trmnl.get("last_payload_hash")
        and last_ok is not None
        and (is_web or now - last_ok < UNCHANGED_REFRESH)
    ):
        return skipped("Nothing changed since the last push.", quiet=True)

    fields: dict[str, Any] = {}
    try:
        url = validate_webhook_url(url)
        with httpx.Client(timeout=20.0, follow_redirects=False) as client:
            response = client.post(url, **post)
        recent.append(stamp)  # TRMNL counts every request it receives
        ok = response.status_code < 400
        message = "Pushed to TRMNL." if ok else (response.text or f"HTTP {response.status_code}")[:240]
        if 400 <= response.status_code < 500 and response.status_code not in {413, 429}:
            # The usual cause: a webhook from the other kind of plugin (they take different bodies).
            plugin = "Webhook Image" if is_web else "Private Plugin"
            other = "Private Plugin" if is_web else "Webhook Image"
            what = "the image" if is_web else "the data"
            message = (
                f"TRMNL refused {what} (HTTP {response.status_code}). This Display needs a {plugin} webhook; "
                f"check it isn't from a {other}. TRMNL said: {(response.text or '').strip()[:120] or 'nothing'}"
            )
        if response.status_code == 413:
            message = f"TRMNL said the {'image' if is_web else 'data'} is too big (HTTP 413)."
        if response.status_code == 429:
            try:
                retry_after = (getattr(response, "headers", None) or {}).get("retry-after")
                wait = timedelta(seconds=int(retry_after or 0)) or RATE_LIMIT_BACKOFF
            except (TypeError, ValueError):
                wait = RATE_LIMIT_BACKOFF
            fields["backoff_until"] = (now + wait).isoformat()
            ok, message = False, f"TRMNL rate limit; pausing pushes until {_hhmm(now + wait)}."
        if ok:
            fields["last_payload_hash"] = digest
        result = {"ok": ok, "message": message, "status": response.status_code, "bytes": size}
    except ValueError as exc:
        result = {"ok": False, "message": str(exc)[:240], "status": None}
    except Exception as exc:  # noqa: BLE001 - network errors surface in the UI
        result = {"ok": False, "message": str(exc)[:240], "status": None}
    update_trmnl_status(
        display_id,
        last_push_at=stamp,
        last_check_at=stamp,
        last_push_ok=result["ok"],
        last_push_message=result["message"],
        last_push_status=result.get("status"),
        last_skip_message=None,
        push_log=recent,
        **fields,
    )
    return result


def _is_due(display: dict[str, Any], now: datetime) -> bool:
    trmnl = display.get("trmnl") or {}
    # A skipped check (unchanged, over budget) counts too, so it isn't retried every minute.
    times = [t for t in (_parse_ts(trmnl.get("last_push_at")), _parse_ts(trmnl.get("last_check_at"))) if t]
    if not times:
        return True
    return now - max(times) >= timedelta(minutes=int(trmnl.get("interval_minutes") or 30))


def pushable_displays() -> list[dict[str, Any]]:
    """Enabled Displays with TRMNL push switched on and a webhook set."""
    return [
        d
        for d in load_displays()
        if d.get("enabled") and (d.get("trmnl") or {}).get("enabled") and get_webhook(d["id"])
    ]


def push_due(collect_fn: Callable[[], dict[str, Any]], *, force: bool = False) -> list[dict[str, Any]]:
    """Push every Display that is due (or all pushable ones when ``force``). Collects once,
    and only when a widget Display is due (web Displays capture their page instead).

    Both the schedule and ``force`` (automations) skip unchanged screens and respect
    each Display's hourly budget; only a person's "Push now" sends unchanged data.
    """
    now = datetime.now(timezone.utc)
    due = [d for d in pushable_displays() if force or _is_due(d, now)]
    if not due:
        return []
    variables = collect_fn() if any(d.get("kind") != "web" for d in due) else {}
    results = []
    for display in due:
        logger.info("TRMNL push for Display %s", display["id"])
        results.append(
            {"display_id": display["id"], "name": display["name"],
             **push_display(display["id"], variables, skip_unchanged=True, now=now)}
        )
    return results
