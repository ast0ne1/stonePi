"""Per-Display TRMNL: migration of the old destination, builder preview API, settings form."""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest

os.environ.setdefault("STONEPI_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("STONEPI_DATA_DIR", tempfile.mkdtemp(prefix="notify-test-"))
os.environ.setdefault("STONEPI_EMIT_RETRY_PATH", os.path.join(os.environ["STONEPI_DATA_DIR"], "emit-retry.jsonl"))

from fastapi.testclient import TestClient  # noqa: E402

import stonepi_display  # noqa: E402
import stonepi_notify  # noqa: E402
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session  # noqa: E402
from stonepi_display import push as push_mod  # noqa: E402

from app import boot  # noqa: E402
from app.main import app  # noqa: E402

SECRET = os.environ["STONEPI_SESSION_SECRET"]
CSRF = "csrf-token-123"
ADMIN = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    stonepi_notify.configure(data_dir=tmp_path)
    stonepi_display.configure_displays(tmp_path)
    vault: dict[str, str] = {}

    def fake_set(display_id, url):
        if url:
            vault[push_mod.webhook_key(display_id)] = url
        else:
            vault.pop(push_mod.webhook_key(display_id), None)

    monkeypatch.setattr(stonepi_display, "set_webhook", fake_set)
    monkeypatch.setattr(stonepi_display, "get_webhook", lambda display_id: vault.get(push_mod.webhook_key(display_id), ""))
    monkeypatch.setattr(push_mod, "get_webhook", lambda display_id: vault.get(push_mod.webhook_key(display_id), ""))
    yield vault


def admin_client() -> TestClient:
    client = TestClient(app, base_url="http://testserver")
    client.cookies.set(CSRF_COOKIE, CSRF)
    client.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=ADMIN, username="admin", display_name="Admin", is_admin=True, apps=["notify"], session_id="s1"
        ),
    )
    return client


def test_old_trmnl_destination_moves_onto_its_display(fresh):
    stonepi_notify.save_destinations(
        {
            "trmnl": {
                "enabled": True,
                "webhook_url": "https://trmnl.example/api/custom_plugins/old",
                "interval_minutes": 20,
                "device": "v2",
                "display_id": "dashboard",
            }
        }
    )
    boot._migrate_trmnl_destination()

    display = stonepi_display.get_display("dashboard")
    assert display["device"] == "v2"
    assert display["trmnl"]["enabled"] is True
    assert display["trmnl"]["interval_minutes"] == 20
    assert display["trmnl"]["template"] == "legacy"  # plugin still holds the old markup
    assert fresh["DISPLAY_WEBHOOK_URL"].endswith("/old")
    trmnl = stonepi_notify.load_destinations()["trmnl"]
    assert trmnl["migrated_to_display"] == "dashboard"
    assert trmnl["enabled"] is False

    # Runs once: a later boot leaves the Display alone.
    stonepi_display.update_trmnl_status("dashboard", enabled=False)
    boot._migrate_trmnl_destination()
    assert stonepi_display.get_display("dashboard")["trmnl"]["enabled"] is False


def test_preview_api_renders_an_unsaved_layout():
    client = admin_client()
    response = client.post(
        "/api/displays/dashboard/preview",
        json={"device": "og", "widgets": [{"id": "a", "widget_id": "stonepi.watch", "size": "small"}]},
        headers={"X-StonePi-CSRF": CSRF},
    )
    body = response.json()
    assert response.status_code == 200, body
    assert body["ok"] and "environment trmnl" in body["html"]
    assert body["widgets"][0]["x"] == 1 and body["bytes"] < body["limit"]
    # Unsaved: the stored Display is unchanged.
    assert [w["widget_id"] for w in stonepi_display.get_display("dashboard")["widgets"]] != ["stonepi.watch"]


def test_preview_api_needs_csrf():
    response = admin_client().post("/api/displays/dashboard/preview", json={})
    assert response.status_code == 403


def test_trmnl_form_saves_settings_and_webhook(fresh, monkeypatch):
    monkeypatch.setattr(stonepi_display, "set_webhook", lambda display_id, url: fresh.__setitem__(push_mod.webhook_key(display_id), url))
    response = admin_client().post(
        "/displays/dashboard/trmnl",
        data={
            "csrf_token": CSRF,
            "enabled": "1",
            "webhook": "https://trmnl.example/api/custom_plugins/new",
            "interval_minutes": "5",
            "template": "universal",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    trmnl = stonepi_display.get_display("dashboard")["trmnl"]
    assert trmnl["enabled"] is True
    assert trmnl["interval_minutes"] == 10  # clamped to TRMNL-friendly minimum
    assert fresh["DISPLAY_WEBHOOK_URL"].endswith("/new")


def test_layout_save_keeps_positions():
    response = admin_client().post(
        "/displays/dashboard/save",
        data={
            "csrf_token": CSRF,
            "name": "Dashboard",
            "device": "og",
            "widgets_json": '[{"id":"a","widget_id":"stonepi.storage","size":"small","x":4,"y":3}]',
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    widget = stonepi_display.get_display("dashboard")["widgets"][0]
    assert (widget["x"], widget["y"], widget["size"]) == (4, 3, "small")
