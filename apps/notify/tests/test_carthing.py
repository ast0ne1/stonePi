"""Displays → Car Thing: editor page, signed internal API for the panel, draft preview, config admin."""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest

os.environ.setdefault("STONEPI_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("STONEPI_DATA_DIR", tempfile.mkdtemp(prefix="notify-test-"))
os.environ.setdefault("STONEPI_EMIT_RETRY_PATH", os.path.join(os.environ["STONEPI_DATA_DIR"], "emit-retry.jsonl"))

from fastapi.testclient import TestClient  # noqa: E402
from stonepi_auth.internal import sign_internal  # noqa: E402
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session  # noqa: E402
from stonepi_display import carthing  # noqa: E402

from app import carthing_routes  # noqa: E402
from app.main import app  # noqa: E402

SECRET = os.environ["STONEPI_SESSION_SECRET"]
CSRF = "csrf-token-123"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    carthing.configure_carthing(tmp_path)
    monkeypatch.setattr(carthing_routes, "_secret", lambda: SECRET)
    monkeypatch.setattr(carthing_routes, "run_helper", lambda action: {"ok": True})
    with carthing_routes._draft_lock:
        carthing_routes._draft["config"] = None
    return tmp_path


def admin_client(is_admin: bool = True) -> TestClient:
    client = TestClient(app, base_url="http://testserver")
    client.cookies.set(CSRF_COOKIE, CSRF)
    client.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=str(uuid.uuid4()), username="a", display_name="A", is_admin=is_admin,
            apps=["notify"], session_id="s1",
        ),
    )
    return client


def signed(method: str, path: str) -> dict:
    return sign_internal(SECRET, method, path)


def test_internal_state_needs_signature():
    client = TestClient(app)
    assert client.get("/api/internal/carthing/state").status_code == 403
    data = client.get("/api/internal/carthing/state", headers=signed("GET", "/api/internal/carthing/state")).json()
    assert data["ok"] and data["config"]["id"] == "default" and data["enabled"] is False


def test_pair_issues_token_matching_state():
    client = TestClient(app)
    token = client.post("/api/internal/carthing/pair", headers=signed("POST", "/api/internal/carthing/pair")).json()["token"]
    state = client.get("/api/internal/carthing/state", headers=signed("GET", "/api/internal/carthing/state")).json()
    assert carthing.token_ok(token, state["token_hash"])


def test_draft_preview_does_not_touch_saved_config():
    client = admin_client()
    client.get("/displays/carthing")  # seeds default + draft
    draft = carthing.active_config()
    draft["clock"]["face"] = "words"
    assert client.post("/api/carthing/draft", json=draft, headers={"X-StonePi-CSRF": CSRF}).json()["ok"]
    path = "/api/internal/carthing/state"
    preview = TestClient(app).get(path, params={"draft": "1"}, headers=signed("GET", path)).json()
    assert preview["config"]["clock"]["face"] == "words" and preview["enabled"] is True
    assert carthing.active_config()["clock"]["face"] == "digital_bold"


def test_save_config_and_snapshot():
    client = admin_client()
    client.get("/displays/carthing")
    cfg = carthing.active_config()
    cfg["clock"]["color"] = "#ff4d4d"
    body = client.post("/api/carthing/config/default", json=cfg, headers={"X-StonePi-CSRF": CSRF}).json()
    assert body["ok"] and body["config"]["clock"]["color"] == "#FF4D4D"
    assert len(body["snapshots"]) == 1
    assert client.post("/api/carthing/config/default", json=cfg).status_code == 403  # no CSRF


def test_editor_page_and_displays_card():
    client = admin_client()
    page = client.get("/displays/carthing")
    assert page.status_code == 200 and "data-ct-editor" in page.text and "/carthing/panel/" in page.text
    assert "Car Thing" in client.get("/displays").text


def test_non_admin_is_refused():
    client = admin_client(is_admin=False)
    assert client.get("/displays/carthing").status_code == 403
    assert client.get("/carthing/panel/").status_code == 403


def test_device_enable_pin_and_configs():
    client = admin_client()
    client.get("/displays/carthing")
    form = {"csrf_token": CSRF}
    client.post("/displays/carthing/device", data={**form, "action": "enable"}, follow_redirects=False)
    assert carthing.load_device()["enabled"] is True
    client.post("/displays/carthing/device", data={**form, "action": "pin", "pin": "4821", "pin_enabled": "1"}, follow_redirects=False)
    device = carthing.load_device()
    assert device["pin"]["enabled"] and carthing.pin_ok("4821", device["pin"])
    client.post("/displays/carthing/device", data={**form, "action": "pin"}, follow_redirects=False)
    assert carthing.load_device()["pin"]["enabled"] is False  # PIN off, still set
    client.post("/displays/carthing/configs", data={**form, "action": "copy", "config_id": "default", "name": "Night"}, follow_redirects=False)
    client.post("/displays/carthing/configs", data={**form, "action": "activate", "config_id": "night"}, follow_redirects=False)
    assert carthing.load_device()["active_config"] == "night"
    exported = client.get("/displays/carthing/configs/night/export")
    assert exported.json()["stonepi_carthing_config"] == 2


def test_system_feed_shapes_services():
    overview = {
        "apps": [
            {"id": "newscast", "n": "NewsCast", "running": True, "d": "4 feeds", "m": ""},
            {"id": "pinboard", "n": "Pinboard", "running": False, "d": "—", "m": ""},
        ],
        "apps_up": 1,
        "apps_total": 2,
        "system": {"temp": 52},
        "temp_disp": "52°C",
        "cpu_disp": "9%",
        "disk_disp": "41%",
        "backup_when": "6h ago",
        "cpu_pct": 9,
        "mem_pct": 38,
        "temp_c": 52,
        "disk_pct": 41,
        "uptime": "3d 4h",
        "hostname": "stonepi",
    }
    feed = carthing_routes.system_feed(overview)
    assert feed["card"]["headline"].startswith("52°C") and feed["card"]["level"] == "down"
    # Numbers for the large System widget; unreadable values stay None (shown as "–").
    assert feed["stats"] == {
        "cpu": 9, "mem": 38, "temp": 52, "disk": 41, "uptime": "3d 4h", "hostname": "stonepi",
        "up": 1, "total": 2, "backup": "6h ago", "backup_ok": False,
    }
    assert carthing_routes.system_feed({**overview, "cpu_pct": None})["stats"]["cpu"] is None
    units = {i["id"]: i["unit"] for i in feed["items"]}
    assert units == {"newscast": "stonepi-newscast", "pinboard": "stonepi-pinboard"}
    assert feed["items"][1]["badge"] == "Down"


def test_missing_secret_fails_closed_outside_dev(monkeypatch):
    monkeypatch.setattr(carthing_routes, "_secret", lambda: "")
    monkeypatch.setattr(carthing_routes, "_auth_optional", lambda: False)
    client = TestClient(app, base_url="http://testserver")
    assert client.get("/displays/carthing").status_code == 403
    assert client.get("/carthing/panel/").status_code == 403
    assert client.get("/api/carthing/status").status_code == 403
    monkeypatch.setattr(carthing_routes, "_auth_optional", lambda: True)
    assert carthing_routes._admin(None) == (None, None)


def test_editor_shows_pages_and_old_configs_load(store):
    import json

    folder = store / "carthing" / "configs"
    folder.mkdir(parents=True, exist_ok=True)
    old = {"id": "hall", "name": "Hall", "home": {"widgets": ["pinboard"]}, "allowed_actions": ["home", "back"]}
    (folder / "hall.json").write_text(json.dumps(old), encoding="utf-8")
    client = admin_client()
    page = client.get("/displays/carthing?config=hall")
    assert page.status_code == 200
    assert 'data-ct-tab="pages"' in page.text and "data-ct-pages" in page.text
    assert 'data-input="dial_press"' in page.text and "ct-callouts" in page.text
    with carthing_routes._draft_lock:
        draft = carthing_routes._draft["config"]
    assert draft["pages"] == [{"name": "Home", "widgets": [{"id": "pinboard", "size": "small"}]}]


def test_draft_with_pages_reaches_the_preview():
    client = admin_client()
    client.get("/displays/carthing")
    draft = carthing.active_config()
    draft["pages"].append({"name": "Clock", "widgets": [{"id": "clock", "size": "full"}]})
    draft["rotation"] = {"every_s": 20, "resume_after_s": 45}
    body = client.post("/api/carthing/draft", json=draft, headers={"X-StonePi-CSRF": CSRF}).json()
    assert body["ok"] and len(body["config"]["pages"]) == 2
    path = "/api/internal/carthing/state"
    preview = TestClient(app).get(path, params={"draft": "1"}, headers=signed("GET", path)).json()
    assert preview["config"]["pages"][1]["widgets"] == [{"id": "clock", "size": "full"}]
    assert preview["config"]["rotation"] == {"every_s": 20, "resume_after_s": 45}
