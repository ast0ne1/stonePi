"""F14: Web page Displays in Notify (create with their page, edit the page on the Display,
delete, the Car Thing editor's "+ New web page")."""

from __future__ import annotations

from stonepi_display import carthing

import stonepi_display  # noqa: E402

from tests.test_displays_trmnl import CSRF, admin_client, fresh  # noqa: F401  (fixture)

URL = "http://192.168.0.10:3000/"


def _create(client, **form):
    return client.post("/displays/create", data={"csrf_token": CSRF, **form}, follow_redirects=False)


def test_create_web_display_makes_its_page(fresh):
    client = admin_client()
    response = _create(client, name="Homepage panel", kind="web", url=URL)
    assert response.status_code == 303
    display = next(d for d in stonepi_display.load_displays() if d["kind"] == "web")
    page = stonepi_display.get_page(display["web"]["page_id"])
    assert page["name"] == "Homepage panel" and page["url"] == URL
    assert display["trmnl"]["interval_minutes"] == 15
    # A second one can reuse the page instead of making another.
    _create(client, name="Second", kind="web", page_id=page["id"], url="")
    assert len(stonepi_display.load_pages()) == 1
    # Web page without an address and no page picked: refused, nothing created.
    bad = _create(client, name="Bad", kind="web", url="")
    assert "err=" in bad.headers["location"] and len(stonepi_display.load_displays()) == 3


def test_display_page_shows_checklist_and_saves_page_settings(fresh):
    client = admin_client()
    location = _create(client, name="Home", kind="web", url=URL).headers["location"]
    display_id = location.split("/displays/")[1].split("?")[0]
    html = client.get(f"/displays/{display_id}").text
    assert "Webhook Image" in html and "setup-list" in html and "Only this Display shows" in html
    response = client.post(
        f"/displays/{display_id}/web",
        data={"csrf_token": CSRF, "web_settings": "1", "url": "http://10.0.0.5/", "preset": "none",
              "hide": "#information-widgets", "scale": "0.8", "settle_s": "5", "bits": "2", "invert": "on",
              "threshold": "140", "device": "og"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and "msg=Saved" in response.headers["location"]
    display = stonepi_display.get_display(display_id)
    page = stonepi_display.get_page(display["web"]["page_id"])
    assert (page["url"], page["preset"], page["hide"], page["scale"], page["settle_s"]) == (
        "http://10.0.0.5/", "none", "#information-widgets", 0.8, 5)
    assert (display["web"]["bits"], display["web"]["invert"], display["web"]["threshold"]) == (2, "on", 140)
    widgets_html = client.get("/displays/dashboard").text
    assert "Private Plugin" in widgets_html and "data-display-builder" in widgets_html


def test_switch_page_and_delete_keeps_shared_pages(fresh):
    client = admin_client()
    first = _create(client, name="One", kind="web", url=URL).headers["location"].split("/displays/")[1].split("?")[0]
    stonepi_display.upsert_page({"id": "other", "name": "Other", "url": "http://10.0.0.9/"})
    client.post(f"/displays/{first}/web", data={"csrf_token": CSRF, "action": "switch", "page_id": "other"})
    assert stonepi_display.get_display(first)["web"]["page_id"] == "other"
    # "other" is also on a Car Thing page: deleting the Display keeps it.
    cfg = carthing.normalize_config({"id": "default", "pages": [{"widgets": [{"id": "web:other"}]}]})
    carthing.save_config(cfg)
    client.post(f"/displays/{first}/delete", data={"csrf_token": CSRF})
    assert stonepi_display.get_page("other") is not None
    # A page nothing else uses goes with its Display.
    second = _create(client, name="Two", kind="web", url=URL).headers["location"].split("/displays/")[1].split("?")[0]
    page_id = stonepi_display.get_display(second)["web"]["page_id"]
    response = client.post(f"/displays/{second}/delete", data={"csrf_token": CSRF}, follow_redirects=False)
    assert "its+web+page" in response.headers["location"] and stonepi_display.get_page(page_id) is None


def test_editor_adds_a_web_page(fresh):
    client = admin_client()
    assert client.post("/api/web-pages", json={"name": "Homepage", "url": URL}).status_code == 403  # no CSRF
    response = client.post("/api/web-pages", json={"name": "Homepage", "url": URL}, headers={"X-StonePi-CSRF": CSRF})
    body = response.json()
    assert body["ok"] and body["widget"] == "web:homepage" and stonepi_display.get_page("homepage")["url"] == URL
    bad = client.post("/api/web-pages", json={"name": "X", "url": "nope"}, headers={"X-StonePi-CSRF": CSRF})
    assert bad.status_code == 400


def test_loopback_endpoints_need_json(fresh):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    # A web page can only send these as a "simple" (no-preflight) request: refused.
    event = '{"id": "x.y", "source": "x", "title": "Fake"}'
    assert client.post("/api/events", content=event, headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/push-trmnl").status_code == 415
    assert client.post("/api/events", json={"id": "x.y", "source": "x", "title": "Real"}).status_code == 200


def test_internal_web_image_needs_a_signature(fresh):
    from fastapi.testclient import TestClient

    from app.main import app

    stonepi_display.create_page({"name": "Home", "url": URL})
    assert TestClient(app).get("/api/internal/web/home.png").status_code == 403


def test_preview_is_admins_only(fresh):
    from fastapi.testclient import TestClient

    from app.main import app

    stonepi_display.create_page({"name": "Home", "url": URL})
    assert TestClient(app).get("/api/web/home/preview.png").status_code == 403
