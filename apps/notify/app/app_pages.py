"""Web pages served by StonePi apps behind a one-off capture key (Ripple's family display).

An app that marks ``capture_key`` in the catalog opens its display page only for a key it
issued over a signed internal call. When a web page's address is that app on this Pi
(``http://127.0.0.1:<port>/…``), every capture first fetches a fresh key
(``stonepi_display.webcapture`` adds it to the address with the panel's size and depth).

The app also tells Notify when its page changed (``/api/internal/web/changed``). Notify waits
REFRESH_DELAY_S so a run of quick edits makes one push, then pushes the Displays showing
that app's page: within their hourly limit, and only if the image changed.

An app that also names a ``display_page`` is offered as its own kind in Add a Display: the
admin gives a name, the TRMNL model and the webhook, and ``create_app_display`` sets up the
rest (the page, the right greys, no inverting or dithering, push on) and pushes once.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from stonepi_auth.catalog import APP_CATALOG
from stonepi_auth.internal import sign_internal_v2

logger = logging.getLogger("notify.app_pages")

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
REFRESH_DELAY_S = 20.0
# A push held back (already running, hourly limit, TRMNL back-off) is tried again this often,
# for up to an hour; the Display's own schedule takes over after that.
RETRY_DELAY_S = 120.0
MAX_RETRIES = 30
# A set-up app Display pushes on its own every hour; the app's change nudges do the rest.
APP_DISPLAY_INTERVAL_MIN = 60
DEVICE_LABELS = {"og": "TRMNL OG", "v2": "TRMNL X"}

_client = httpx.Client(timeout=5.0)
_timers: dict[str, threading.Timer] = {}
_timers_lock = threading.Lock()


def _secret() -> str:
    from app.people import session_secret

    return session_secret()


def app_for_url(url: str) -> dict[str, Any] | None:
    """The catalog app behind ``url`` when it's a key-protected app page on this Pi."""
    try:
        parts = urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return None
    if (parts.hostname or "").lower() not in LOOPBACK_HOSTS or port is None:
        return None
    return next((a for a in APP_CATALOG if a.get("capture_key") and a.get("port") == port), None)


def capture_key(url: str) -> str | None:
    """The key provider for ``stonepi_display.webcapture``: None for ordinary pages."""
    app = app_for_url(url)
    if app is None:
        return None
    secret = _secret()
    if not secret:
        raise RuntimeError(f"StonePi sign-in isn't set up, so {app['name']} can't open its page for a capture.")
    path = app["capture_key"]
    try:
        response = _client.post(f"http://127.0.0.1:{app['port']}{path}", headers=sign_internal_v2(secret, "POST", path, b""))
        key = response.json().get("key") if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"Couldn't reach {app['name']} for a capture key: {exc}") from exc
    if not key:
        raise RuntimeError(f"{app['name']} didn't give a capture key (HTTP {response.status_code}).")
    return str(key)


def display_apps() -> list[dict[str, Any]]:
    """Apps offered as their own kind in Add a Display."""
    return [a for a in APP_CATALOG if a.get("capture_key") and a.get("display_page")]


def display_app(app_id: str) -> dict[str, Any] | None:
    return next((a for a in display_apps() if a["id"] == app_id), None)


def page_url(app: dict[str, Any]) -> str:
    """The app's display page on this Pi (straight to the app, never through nginx)."""
    return f"http://127.0.0.1:{app['port']}{app['display_page']}"


def _app_page(app: dict[str, Any]) -> dict[str, Any]:
    """The shared web page for the app's display, made the first time it's needed."""
    import stonepi_display

    for page in stonepi_display.load_pages():
        found = app_for_url(page["url"])
        if found and found["id"] == app["id"]:
            return page
    name = app.get("display_label") or f"{app['name']} display"
    return stonepi_display.upsert_page(
        # The page lays itself out for e-ink: no clean-up CSS and nothing to wait for.
        {"id": stonepi_display.new_page_id(name), "name": name, "url": page_url(app), "preset": "none", "settle_s": 0}
    )


def create_app_display(app: dict[str, Any], *, name: str, device: str, webhook: str) -> str:
    """A ready-to-push Display for the app's page. Raises ValueError for a bad webhook."""
    import stonepi_display
    from stonepi_display.webhook import validate_webhook_url

    validate_webhook_url(webhook)
    page = _app_page(app)
    display_id = str(uuid.uuid4())[:8]
    stonepi_display.upsert_display(
        {
            "id": display_id,
            "name": name,
            "enabled": True,
            "builtin": False,
            "kind": "web",
            "device": device if device in DEVICE_LABELS else "og",
            "widgets": [],
            # The app prepares its own photos: the model's greys, never inverted, no dithering.
            "web": {"page_id": page["id"], "bits": 0, "invert": "off", "dither": False},
            "trmnl": {"enabled": True, "interval_minutes": APP_DISPLAY_INTERVAL_MIN},
        }
    )
    try:
        stonepi_display.set_webhook(display_id, webhook)
    except Exception:
        stonepi_display.delete_display(display_id)
        raise
    return display_id


def app_display_status(app_id: str) -> list[dict[str, Any]]:
    """The app's Displays and how their pushes went, for the app's own settings page."""
    import stonepi_display

    found = []
    for display in stonepi_display.load_displays():
        if display.get("kind") != "web":
            continue
        page = stonepi_display.get_page(str((display.get("web") or {}).get("page_id") or ""))
        app = app_for_url(page["url"]) if page else None
        if not app or app["id"] != app_id:
            continue
        trmnl = display.get("trmnl") or {}
        found.append(
            {
                "id": display["id"],
                "name": display.get("name") or display["id"],
                "device": DEVICE_LABELS.get(display.get("device") or "og", "TRMNL"),
                "pushing": bool(display.get("enabled") and trmnl.get("enabled") and stonepi_display.get_webhook(display["id"])),
                "last_push_at": trmnl.get("last_push_at") or "",
                "last_push_ok": bool(trmnl.get("last_push_ok")),
                "last_push_message": str(trmnl.get("last_push_message") or "")[:160],
            }
        )
    return found


def displays_for_app(app_id: str) -> list[dict[str, Any]]:
    """Web Displays that push and show a page of ``app_id``."""
    import stonepi_display

    found = []
    for display in stonepi_display.pushable_displays():
        if display.get("kind") != "web":
            continue
        page = stonepi_display.get_page(str((display.get("web") or {}).get("page_id") or ""))
        app = app_for_url(page["url"]) if page else None
        if app and app["id"] == app_id:
            found.append(display)
    return found


def refresh_app_pages(app_id: str, display_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Push the Displays showing ``app_id`` now (or just ``display_ids``); skip-unchanged and
    the hourly limit apply."""
    import stonepi_display
    from stonepi_display import webcapture

    from app.boot import _record

    displays = [d for d in displays_for_app(app_id) if display_ids is None or d["id"] in display_ids]
    if not displays:
        return []
    # The app's page changed: don't reuse a capture from before (other pages keep theirs).
    pages = (stonepi_display.get_page(str((d.get("web") or {}).get("page_id") or "")) for d in displays)
    webcapture.forget({p["url"] for p in pages if p})
    results = []
    for display in displays:
        result = stonepi_display.push_display(display["id"], {}, skip_unchanged=True)
        results.append({"display_id": display["id"], "name": display.get("name"), **result})
    _record(results)
    return results


def _schedule(app_id: str, delay: float, display_ids: set[str] | None, attempt: int) -> None:
    def run() -> None:
        with _timers_lock:
            if _timers.get(app_id) is timer:  # a newer change may have replaced this timer
                _timers.pop(app_id)
        try:
            results = refresh_app_pages(app_id, display_ids)
        except Exception:  # noqa: BLE001 - the next scheduled push catches up
            logger.exception("Couldn't push the Displays for %s", app_id)
            return
        # Held back (a push already running, the hourly limit, a TRMNL back-off): the change
        # hasn't reached those screens, so try them again rather than wait for the schedule.
        held = {r["display_id"] for r in results if r.get("held")}
        if held and attempt < MAX_RETRIES:
            with _timers_lock:
                newer = app_id in _timers
            if not newer:  # a newer change will push everything anyway
                _schedule(app_id, RETRY_DELAY_S, held, attempt + 1)

    timer = threading.Timer(delay, run)
    timer.daemon = True
    with _timers_lock:
        old = _timers.pop(app_id, None)
        if old:
            old.cancel()
        _timers[app_id] = timer
    timer.start()


def schedule_refresh(app_id: str, delay: float | None = None) -> None:
    """Push ``app_id``'s Displays after a short wait; a newer change restarts the wait."""
    _schedule(app_id, REFRESH_DELAY_S if delay is None else delay, None, 0)
