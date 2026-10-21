"""Screenshot a web page with the shared headless Chromium (F14 web pages on the displays).

One launch per capture, behind ``chromium_lock`` so it never runs alongside the
EventTrakr/SportGuide scrapers. The viewport is the screen the image is for, so the
page lays itself out at that size; ``scale`` zooms it (a desktop dashboard on a small
panel) without changing the output size.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from .lock import ChromiumBusy, chromium_lock

logger = logging.getLogger("stonepi.browser.capture")

NAV_TIMEOUT_S = 20.0
IDLE_TIMEOUT_S = 8.0
MAX_SETTLE_S = 15.0
# How long a capture waits for the scrapers to let go of Chromium before giving up.
LOCK_TIMEOUT_S = 60.0


class CaptureError(RuntimeError):
    """The page couldn't be captured (browser missing, page unreachable, timeout)."""


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    try:
        port = parts.port or {"http": 80, "https": 443}.get(parts.scheme)
    except ValueError:
        port = None
    return parts.scheme, host, port


def _loopback_address(text: str) -> bool:
    try:
        address = ipaddress.ip_address(text.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)  # ::ffff:127.0.0.1
    return address.is_loopback or address.is_unspecified or bool(mapped and mapped.is_loopback)


def resolve_host(host: str) -> list[str]:
    """The addresses ``host`` resolves to (empty when it doesn't)."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]
    except (OSError, UnicodeError):
        return []


def _is_loopback(host: str, resolve: Callable[[str], list[str]] = resolve_host) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        # A name: it's loopback if it resolves there (127.0.0.1.nip.io, localtest.me).
        return any(_loopback_address(a) for a in resolve(host))
    return _loopback_address(host)


def blocked_request(page_url: str, request_url: str, resolve: Callable[[str], list[str]] = resolve_host) -> bool:
    """True for a request the captured page mustn't make: one to this Pi's loopback services
    (Notify, the apps), unless it goes to the page's own origin (a Homepage on 127.0.0.1).

    Names are resolved here first. Chromium resolves again when it connects, so a name that
    changes its answer in between (DNS rebinding) can still slip past: Notify's loopback
    endpoints also refuse anything a page can send without a CORS preflight."""
    if _origin(request_url) == _origin(page_url):
        return False
    scheme, host, _ = _origin(request_url)
    return scheme in {"http", "https"} and _is_loopback(host, resolve)


def _safe_url(url: str) -> str:
    """The URL without its query or fragment (they can carry tokens), for logs."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def capture_page(
    url: str,
    width: int,
    height: int,
    *,
    scale: float = 1.0,
    css: str = "",
    settle_s: float = 3.0,
    nav_timeout_s: float = NAV_TIMEOUT_S,
) -> bytes:
    """PNG (colour) of the top ``width``×``height`` of ``url``.

    ``scale`` < 1 fits more of the page (the viewport is width/scale wide, drawn at
    ``scale``); > 1 enlarges it. ``css`` is added after load (hide a background, force
    light colours). Waits for network idle (capped: dashboards that poll never go idle),
    then ``settle_s`` for widgets that fill in late.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
    except ImportError as exc:  # pragma: no cover - depends on the app venv
        raise CaptureError("Playwright isn't installed for this app.") from exc

    scale = min(4.0, max(0.25, float(scale or 1.0)))
    settle_s = min(MAX_SETTLE_S, max(0.0, float(settle_s or 0.0)))
    viewport = {"width": max(1, round(width / scale)), "height": max(1, round(height / scale))}
    started = time.monotonic()
    try:
        with chromium_lock(timeout=LOCK_TIMEOUT_S):
            png = _capture(url, viewport, scale, css, settle_s, nav_timeout_s)
    except ChromiumBusy as exc:
        raise CaptureError("The browser is busy with another app's scrape; try again in a minute.") from exc
    except PlaywrightError as exc:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise CaptureError(message[:200]) from exc
    logger.info("captured %s at %sx%s in %.1fs", _safe_url(url), width, height, time.monotonic() - started)
    return png


def _capture(url: str, viewport: dict[str, int], scale: float, css: str, settle_s: float, nav_timeout_s: float) -> bytes:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                viewport=viewport,
                device_scale_factor=scale,
                ignore_https_errors=True,  # internal dashboards often use self-signed certificates
                # A service worker's requests skip context.route, so none may run.
                service_workers="block",
            )
            # Never let the page's own scripts call this Pi's loopback services (Notify's
            # ingest, the apps); a Homepage on 127.0.0.1 can still reach its own origin.
            # Names are resolved once per capture (a dashboard loads many files per host).
            lookups: dict[str, list[str]] = {}

            def resolve(host: str) -> list[str]:
                if host not in lookups:
                    lookups[host] = resolve_host(host)
                return lookups[host]

            context.route(
                "**/*",
                lambda route: route.abort("blockedbyclient")
                if blocked_request(url, route.request.url, resolve)
                else route.continue_(),
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout_s * 1000)
            try:
                page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_S * 1000)
            except PlaywrightError:
                pass  # still polling: settle_s covers what's left
            if css.strip():
                page.add_style_tag(content=css)
            if settle_s:
                page.wait_for_timeout(settle_s * 1000)
            return page.screenshot(type="png", full_page=False, animations="disabled", caret="hide")
        finally:
            browser.close()
