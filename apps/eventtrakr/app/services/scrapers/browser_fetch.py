from __future__ import annotations

from contextlib import contextmanager

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 EventTrakr/1.0"
)


@contextmanager
def browser_session():
    """Launch one headless Chromium instance to render several pages against
    (e.g. one sync pass over multiple sources), instead of paying browser
    startup cost per page. Pass the yielded browser into fetch_rendered_html.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def fetch_rendered_html(url: str, wait_selector: str | None = None, timeout_ms: int = 25000, browser=None) -> str:
    """Render a page with a real headless Chromium instance and return the
    resulting HTML.

    Used for every scraped web source (ICS/webcal feeds excluded -- those are
    plain data files, not pages to render). Several sites hand a plain HTTP
    client different or incomplete content versus what a real browser gets
    (client-rendered prices, date-range query params only honoured for
    browser-shaped requests, etc.) -- see EventbriteExtractor for the clearest
    example. This just renders the public page the way any visitor's browser
    would; it isn't spoofing or bypassing anything.

    Pass an existing `browser` (from browser_session()) to render against it
    instead of launching a fresh instance -- callers fetching several sources
    in one pass should do that so the browser is only started once.
    """
    if browser is not None:
        return _render(browser, url, wait_selector, timeout_ms)

    with browser_session() as browser:
        return _render(browser, url, wait_selector, timeout_ms)


def _render(browser, url: str, wait_selector: str | None, timeout_ms: int) -> str:
    page = browser.new_page(user_agent=USER_AGENT)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_selector:
            try:
                # state="attached" (not the "visible" default) so this
                # resolves immediately when the element is already in the
                # DOM but hidden (e.g. a duplicate responsive-layout copy),
                # rather than waiting out the full timeout. Short timeout
                # since the page may genuinely have no matches.
                page.wait_for_selector(wait_selector, state="attached", timeout=3000)
            except Exception:
                pass
        return page.content()
    finally:
        page.close()
