"""Add a Display → "Ripple family board": name, model and webhook; everything else set up."""

from __future__ import annotations

import pytest
import stonepi_display
from stonepi_auth.internal import sign_internal

from app import app_pages, boot
from tests.test_displays_trmnl import CSRF, admin_client, fresh  # noqa: F401  (fixture)

from tests.test_displays_trmnl import SECRET

WEBHOOK = "https://trmnl.com/api/plugin_settings/abc123/image"
STATUS = "/api/internal/web/app-displays/ripple"


@pytest.fixture()
def pushes(monkeypatch):
    """The first push runs in a thread; record it instead of capturing anything."""
    calls: list[str] = []
    monkeypatch.setattr(boot, "push_one_display", lambda display_id: calls.append(display_id) or {"ok": True})
    return calls


def _create(client, **form):
    data = {"csrf_token": CSRF, "kind": "app:ripple", "name": "Mum and Dad", "device": "og", "webhook": WEBHOOK, **form}
    return client.post("/displays/create", data=data, follow_redirects=False)


def _wait(calls: list, n: int = 1) -> None:
    import time

    deadline = time.time() + 3
    while len(calls) < n and time.time() < deadline:
        time.sleep(0.02)


def test_ripple_is_offered_and_preselected_from_its_link(fresh):
    client = admin_client()
    html = client.get("/displays").text
    assert 'value="app:ripple"' in html and "Ripple family board" in html
    assert 'value="app:ripple" data-kind data-app-label="Ripple family board" checked' not in html
    picked = client.get("/displays?add=ripple").text
    assert 'data-app-label="Ripple family board" checked' in picked
    assert 'value="Ripple family board" data-display-name' in picked
    assert 'name="webhook"' in picked and "required autofocus" in picked


def test_one_form_sets_up_everything(fresh, pushes):
    vault = fresh
    response = _create(admin_client())
    assert response.status_code == 303
    display_id = response.headers["location"].split("/displays/")[1].split("?")[0]
    assert "first+image" in response.headers["location"] or "first%20image" in response.headers["location"]
    display = stonepi_display.get_display(display_id)
    page = stonepi_display.get_page(display["web"]["page_id"])
    assert page["url"] == "http://127.0.0.1:8017/display" and page["preset"] == "none" and page["settle_s"] == 0
    assert display["kind"] == "web" and display["device"] == "og" and display["enabled"]
    assert (display["web"]["bits"], display["web"]["invert"], display["web"]["dither"]) == (0, "off", False)
    assert display["trmnl"]["enabled"] and display["trmnl"]["interval_minutes"] == 60
    assert stonepi_display.get_webhook(display_id) == WEBHOOK and len(vault) == 1
    _wait(pushes)
    assert pushes == [display_id]


def test_a_second_display_shares_the_page(fresh, pushes):
    client = admin_client()
    _create(client)
    _create(client, name="Spare", device="v2")
    displays = [d for d in stonepi_display.load_displays() if d.get("kind") == "web"]
    assert len(displays) == 2 and len(stonepi_display.load_pages()) == 1
    assert {d["device"] for d in displays} == {"og", "v2"}


@pytest.mark.parametrize("webhook", ["", "http://trmnl.com/x", "https://127.0.0.1/x"])
def test_bad_webhook_creates_nothing(fresh, pushes, webhook):
    response = _create(admin_client(), webhook=webhook)
    location = response.headers["location"]
    assert location.startswith("/displays?add=ripple&err=Webhook") and location.endswith("#add-display")
    assert [d for d in stonepi_display.load_displays() if d.get("kind") == "web"] == []
    assert pushes == []


def test_vault_failure_leaves_no_half_display(fresh, pushes, monkeypatch):
    def broken(display_id, url):
        raise OSError("vault locked")

    monkeypatch.setattr(stonepi_display, "set_webhook", broken)
    response = _create(admin_client())
    assert "vault" in response.headers["location"]
    assert [d for d in stonepi_display.load_displays() if d.get("kind") == "web"] == []


def test_unknown_app_kind_is_refused(fresh, pushes):
    response = _create(admin_client(), kind="app:pinboard")
    assert "err=" in response.headers["location"] and pushes == []


def test_status_for_the_app_is_signed(fresh, pushes):
    client = admin_client()
    assert client.get(STATUS).status_code == 404
    signed = client.get(STATUS, headers=sign_internal(SECRET, "GET", STATUS))
    assert signed.json() == {"ok": True, "displays": []}
    display_id = _create(client).headers["location"].split("/displays/")[1].split("?")[0]
    rows = client.get(STATUS, headers=sign_internal(SECRET, "GET", STATUS)).json()["displays"]
    assert rows == [
        {"id": display_id, "name": "Mum and Dad", "device": "TRMNL OG", "pushing": True,
         "last_push_at": "", "last_push_ok": False, "last_push_message": ""}
    ]
    other = "/api/internal/web/app-displays/pinboard"
    assert client.get(other, headers=sign_internal(SECRET, "GET", other)).status_code == 404


def test_display_apps_come_from_the_catalog():
    assert [a["id"] for a in app_pages.display_apps()] == ["ripple"]
    assert app_pages.page_url(app_pages.display_app("ripple")) == "http://127.0.0.1:8017/display"
