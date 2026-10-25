"""App pages behind a one-off capture key (Ripple's family display) and the app's change nudge."""

from __future__ import annotations

import json

import pytest
import stonepi_display
from stonepi_auth.internal import sign_internal_v2, verify_internal_v2
from stonepi_display import webcapture

from app import app_pages
from tests.test_displays_trmnl import SECRET, admin_client, fresh  # noqa: F401  (fixture)

RIPPLE = "http://127.0.0.1:8017/display"
CHANGED = "/api/internal/web/changed"


class _Response:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


@pytest.fixture()
def ripple_key(monkeypatch):
    """Ripple's capture-key endpoint, faked: records the signed request and answers a key."""
    calls: list[dict] = []

    def post(url, headers):
        calls.append({"url": url, "headers": headers})
        return _Response(200, {"ok": True, "key": f"key-{len(calls)}"})

    monkeypatch.setattr(app_pages._client, "post", post)
    return calls


# -- which pages are app pages ---------------------------------------------------------


@pytest.mark.parametrize(
    "url, app_id",
    [
        (RIPPLE, "ripple"),
        ("http://localhost:8017/display?x=1", "ripple"),
        ("http://stonepi.local/ripple/display", None),  # through nginx: not the app itself
        ("http://192.168.1.20:8017/display", None),
        ("http://127.0.0.1:8004/", None),  # Pinboard issues no capture keys
        ("http://127.0.0.1:3000/", None),
        ("not a url", None),
    ],
)
def test_app_for_url(url, app_id):
    app = app_pages.app_for_url(url)
    assert (app["id"] if app else None) == app_id


def test_capture_key_is_fetched_with_a_signed_call(ripple_key):
    assert app_pages.capture_key(RIPPLE) == "key-1"
    call = ripple_key[0]
    assert call["url"] == "http://127.0.0.1:8017/api/internal/capture-key"
    assert verify_internal_v2(SECRET, "POST", "/api/internal/capture-key", call["headers"], b"")
    assert app_pages.capture_key("http://192.168.0.10:3000/") is None and len(ripple_key) == 1


def test_capture_key_failure_is_an_error(monkeypatch):
    monkeypatch.setattr(app_pages._client, "post", lambda url, headers: _Response(404, {"ok": False}))
    with pytest.raises(RuntimeError, match="Ripple didn't give a capture key"):
        app_pages.capture_key(RIPPLE)


# -- the capture address ---------------------------------------------------------------


def test_capture_adds_a_fresh_key_and_the_panel(monkeypatch, ripple_key):
    import stonepi_browser

    seen: list[str] = []
    monkeypatch.setattr(stonepi_browser, "capture_page", lambda url, w, h, **kw: seen.append(url) or b"png")
    webcapture.clear()
    page = {"id": "ripple", "url": RIPPLE + "?w=1&k=old", "preset": "none"}
    webcapture.capture(page, 1872, 1404, device_scale=1.8, bits=4)
    assert seen == ["http://127.0.0.1:8017/display?k=key-1&w=1040&h=780&scale=1.8&bits=4"]
    webcapture.capture(page, 800, 480, bits=1)
    assert seen[1] == "http://127.0.0.1:8017/display?k=key-2&w=800&h=480&scale=1&bits=1"


def test_ordinary_pages_are_captured_as_they_are(monkeypatch, ripple_key):
    import stonepi_browser

    seen: list[str] = []
    monkeypatch.setattr(stonepi_browser, "capture_page", lambda url, w, h, **kw: seen.append(url) or b"png")
    webcapture.clear()
    webcapture.capture({"id": "home", "url": "http://192.168.0.10:3000/?a=1", "preset": "none"}, 800, 480, bits=1)
    assert seen == ["http://192.168.0.10:3000/?a=1"] and ripple_key == []


def test_web_display_push_passes_its_bit_depth(monkeypatch):
    captured: dict = {}

    def fake_capture(page, width, height, **kw):
        captured.update(kw)
        raise RuntimeError("stop here")

    monkeypatch.setattr(webcapture, "capture", fake_capture)
    stonepi_display.upsert_page({"id": "ripple", "name": "Ripple", "url": RIPPLE})
    display = {"id": "fam", "kind": "web", "device": "og", "web": {"page_id": "ripple", "bits": 1}}
    with pytest.raises(RuntimeError):
        stonepi_display.web_image(display)
    assert captured["bits"] == 1


# -- the nudge -------------------------------------------------------------------------


def _signed(body: bytes) -> dict:
    return {"Content-Type": "application/json", **sign_internal_v2(SECRET, "POST", CHANGED, body)}


def test_changed_nudge_is_signed_and_schedules_a_push(monkeypatch):
    scheduled: list[str] = []
    monkeypatch.setattr(app_pages, "schedule_refresh", lambda app_id, delay=None: scheduled.append(app_id))
    client = admin_client()
    body = json.dumps({"app": "ripple"}).encode()
    assert client.post(CHANGED, content=body, headers={"Content-Type": "application/json"}).status_code == 404
    assert client.post(CHANGED, content=body, headers=_signed(body)).json() == {"ok": True}
    other = json.dumps({"app": "pinboard"}).encode()
    assert client.post(CHANGED, content=other, headers=_signed(other)).status_code == 400
    assert scheduled == ["ripple"]


def test_refresh_pushes_only_displays_showing_the_app(monkeypatch):
    stonepi_display.upsert_page({"id": "ripple", "name": "Ripple", "url": RIPPLE})
    stonepi_display.upsert_page({"id": "home", "name": "Homepage", "url": "http://192.168.0.10:3000/"})
    displays = [
        {"id": "parents", "name": "Parents", "kind": "web", "web": {"page_id": "ripple"}},
        {"id": "hall", "name": "Hall", "kind": "web", "web": {"page_id": "home"}},
        {"id": "widgets", "name": "Widgets", "kind": "widgets", "web": {}},
    ]
    monkeypatch.setattr(stonepi_display, "pushable_displays", lambda: displays)
    pushed: list[tuple] = []
    monkeypatch.setattr(
        stonepi_display, "push_display",
        lambda display_id, variables, **kw: pushed.append((display_id, kw)) or {"ok": True, "message": "Pushed to TRMNL."},
    )
    results = app_pages.refresh_app_pages("ripple")
    assert pushed == [("parents", {"skip_unchanged": True})]
    assert results[0]["name"] == "Parents"


def test_quick_changes_make_one_push(monkeypatch):
    import threading

    done = threading.Event()
    runs: list[str] = []
    monkeypatch.setattr(app_pages, "refresh_app_pages", lambda app_id, ids=None: runs.append(app_id) or done.set() or [])
    for _ in range(3):
        app_pages.schedule_refresh("ripple", delay=0.2)
    assert done.wait(3)
    done.clear()
    assert not done.wait(0.5) and runs == ["ripple"]


def test_held_pushes_are_tried_again_for_those_displays_only(monkeypatch):
    import threading

    monkeypatch.setattr(app_pages, "RETRY_DELAY_S", 0.1)
    calls: list = []
    done = threading.Event()

    def refresh(app_id, ids=None):
        calls.append(ids)
        if len(calls) == 1:
            return [{"display_id": "parents", "held": True}, {"display_id": "spare", "ok": True}]
        done.set()
        return [{"display_id": "parents", "ok": True}]

    monkeypatch.setattr(app_pages, "refresh_app_pages", refresh)
    app_pages.schedule_refresh("ripple", delay=0.05)
    assert done.wait(3)
    assert calls == [None, {"parents"}]


def test_a_running_refresh_keeps_a_newer_timer(monkeypatch):
    import threading

    entered, release = threading.Event(), threading.Event()

    def slow(app_id, ids=None):
        entered.set()
        release.wait(3)
        return []

    monkeypatch.setattr(app_pages, "refresh_app_pages", slow)
    app_pages.schedule_refresh("ripple", delay=0.01)
    assert entered.wait(3)
    app_pages.schedule_refresh("ripple", delay=30)  # a change while the first push runs
    newer = app_pages._timers["ripple"]
    release.set()
    import time

    time.sleep(0.2)
    try:
        assert app_pages._timers.get("ripple") is newer
    finally:
        newer.cancel()
        app_pages._timers.pop("ripple", None)


def test_a_change_only_forgets_that_apps_captures(monkeypatch):
    import stonepi_browser

    monkeypatch.setattr(stonepi_browser, "capture_page", lambda url, w, h, **kw: b"png")
    monkeypatch.setattr(app_pages._client, "post", lambda url, headers: _Response(200, {"key": "k"}))
    webcapture.clear()
    home = {"id": "home", "url": "http://192.168.0.10:3000/", "preset": "none"}
    ripple = {"id": "ripple", "url": RIPPLE, "preset": "none"}
    webcapture.capture(home, 800, 480)
    webcapture.capture(ripple, 800, 480, bits=1)
    webcapture.forget({RIPPLE})
    assert list(webcapture._cache_urls.values()) == ["http://192.168.0.10:3000/"]
