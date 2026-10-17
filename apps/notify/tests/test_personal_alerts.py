"""Notify: admin-only guard, event approvals form, and the /api/me personal alerts API."""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest

_DATA = tempfile.mkdtemp(prefix="notify-test-")
SECRET = "test-session-secret"
os.environ["STONEPI_SESSION_SECRET"] = SECRET
os.environ["STONEPI_DATA_DIR"] = _DATA
os.environ["STONEPI_EMIT_RETRY_PATH"] = os.path.join(_DATA, "emit-retry.jsonl")

from fastapi.testclient import TestClient  # noqa: E402

import stonepi_notify  # noqa: E402
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session  # noqa: E402

from app import me_routes, people  # noqa: E402
from app.main import app  # noqa: E402

ADMIN = str(uuid.uuid4())
ALICE = str(uuid.uuid4())
BOB = str(uuid.uuid4())
CSRF = "csrf-token-123"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    roster = {
        ADMIN: {"is_admin": True, "phone_alerts": True},
        ALICE: {"is_admin": False, "phone_alerts": True},
        BOB: {"is_admin": False, "phone_alerts": False},
    }
    stonepi_notify.configure(data_dir=tmp_path, people_fn=lambda: roster)
    monkeypatch.setattr(me_routes, "load_people", lambda: roster)
    monkeypatch.setattr(people, "load_people", lambda **_: roster)
    stonepi_notify.save_destinations({"ntfy": {"enabled": True, "server": "https://ntfy.sh"}})
    sent: list[dict] = []

    def fake_deliver(**kwargs):
        sent.append(kwargs)
        return {"ok": True, "message": "Sent."}

    monkeypatch.setattr(stonepi_notify, "deliver_ntfy", fake_deliver)
    household = [
        {"id": ADMIN, "display_name": "Adam", "is_admin": True, "phone_alerts": True, "enabled": True},
        {"id": ALICE, "display_name": "Alice", "is_admin": False, "phone_alerts": True, "enabled": True},
        {"id": BOB, "display_name": "Bob", "is_admin": False, "phone_alerts": False, "enabled": True},
    ]
    toggles: list[tuple] = []
    monkeypatch.setattr(people, "auth_users", lambda cookies: [dict(u) for u in household])
    monkeypatch.setattr(people, "set_phone_alerts", lambda cookies, uid, on: toggles.append((uid, on)) or (True, "Saved."))
    yield {"roster": roster, "sent": sent, "household": household, "toggles": toggles}


def client_for(uid: str | None, *, is_admin: bool = False, username: str = "person") -> TestClient:
    client = TestClient(app, base_url="http://testserver")
    client.cookies.set(CSRF_COOKIE, CSRF)
    if uid:
        client.cookies.set(
            COOKIE_NAME,
            encode_session(
                secret=SECRET,
                user_id=uid,
                username=username,
                display_name=username,
                is_admin=is_admin,
                apps=["notify", "newscast"],
                session_id="sid",
            ),
        )
    return client


def admin() -> TestClient:
    return client_for(ADMIN, is_admin=True, username="adam")


def alice() -> TestClient:
    return client_for(ALICE, username="alice")


H = {"X-StonePi-CSRF": CSRF}


# -- admin-only Notify ---------------------------------------------------------


def test_non_admin_denied_notify_pages():
    for path in ("/alerts", "/history", "/displays"):
        response = alice().get(path, follow_redirects=False)
        assert response.status_code == 403, path
        assert "administrators" in response.text


def test_admin_sees_approvals_with_needs_review():
    response = admin().get("/alerts")
    assert response.status_code == 200
    assert "Choose alerts" in response.text
    assert "Needs review" in response.text
    assert "Paper sent to reader" in response.text


def test_prefs_form_saves_approvals_and_passes_csrf():
    form = {
        "csrf_token": CSRF,
        "event_known_system.disk_warning": "1",
        "approve_system.disk_warning": "1",
        "urgent_system.disk_warning": "1",
        "event_known_studio.site_published": "1",
    }
    response = admin().post("/alerts/approvals", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert "err=" not in response.headers["location"]
    events = stonepi_notify.load_prefs()["events"]
    assert events["system.disk_warning"] == {"approved": True, "admin_only": False, "urgent": True}
    assert events["studio.site_published"]["approved"] is False


def test_advanced_form_sets_household_channel():
    form = {"csrf_token": CSRF, "server": "https://ntfy.sh", "household_topic": "kitchen-abc", "action": "save"}
    response = admin().post("/alerts/connection", data=form, follow_redirects=False)
    assert "err=" not in response.headers["location"]
    assert stonepi_notify.load_destinations()["ntfy"]["household_topic"] == "kitchen-abc"


# -- /api/me -------------------------------------------------------------------


def test_me_requires_sign_in():
    assert client_for(None).get("/api/me/subscription").status_code == 401


def test_me_disabled_user_is_signed_out(fresh):
    fresh["roster"].pop(ALICE)
    assert alice().get("/api/me/subscription").status_code == 401


def test_me_not_allowed_without_phone_alerts():
    bob = client_for(BOB, username="bob")
    body = bob.get("/api/me/subscription").json()
    assert body["allowed"] is False
    assert body["subscription"] is None
    assert bob.put("/api/me/subscription", json={"enabled": True}, headers=H).status_code == 403


def test_me_offers_only_approved_events():
    stonepi_notify.save_prefs(
        {"events": {"pricewatch.price_drop": True, "system.disk_warning": True, "studio.site_published": False}}
    )
    ids = {e["id"] for g in alice().get("/api/me/subscription").json()["groups"] for e in g["events"]}
    assert ids == {"pricewatch.price_drop"}  # disk warning is admin-only; studio not approved
    admin_ids = {e["id"] for g in admin().get("/api/me/subscription").json()["groups"] for e in g["events"]}
    assert admin_ids == {"pricewatch.price_drop", "system.disk_warning"}


def test_me_turn_on_choose_quiet_hours_and_off():
    stonepi_notify.save_prefs({"events": {"pricewatch.price_drop": True}})
    client = alice()
    assert client.get("/api/me/subscription").json()["needs_setup"] is True

    on = client.put("/api/me/subscription", json={"enabled": True}, headers=H).json()
    topic = on["subscription"]["topic"]
    assert topic.startswith("stonepi-alice-")
    assert on["subscribe"]["web_url"] == f"https://ntfy.sh/{topic}"
    assert on["subscribe"]["app_url"] == f"ntfy://ntfy.sh/{topic}"
    assert on["needs_setup"] is False

    body = client.put(
        "/api/me/subscription",
        json={
            "events": {"pricewatch.price_drop": True, "system.disk_warning": True},
            "quiet_hours": {"enabled": True, "start": "22:30", "end": "06:45"},
        },
        headers=H,
    ).json()
    sub = stonepi_notify.get_subscription(ALICE)
    assert sub["events"] == {"pricewatch.price_drop": True}  # not-offered tick ignored
    assert sub["quiet_hours"] == {"enabled": True, "start": "22:30", "end": "06:45"}
    assert body["groups"][0]["events"][0]["ticked"] is True

    off = client.put("/api/me/subscription", json={"enabled": False}, headers=H).json()
    assert off["subscription"]["enabled"] is False
    assert off["subscription"]["topic"] == topic
    assert off["needs_setup"] is False  # set up before: no "not set up" nudge after turning off


def test_me_keeps_ticks_for_temporarily_unapproved_events():
    stonepi_notify.save_prefs({"events": {"pricewatch.price_drop": True, "studio.site_published": True}})
    client = alice()
    client.put("/api/me/subscription", json={"enabled": True, "events": {"studio.site_published": True}}, headers=H)
    stonepi_notify.save_prefs({"events": {"pricewatch.price_drop": True, "studio.site_published": False}})
    client.put("/api/me/subscription", json={"events": {"pricewatch.price_drop": True}}, headers=H)
    assert stonepi_notify.get_subscription(ALICE)["events"] == {
        "studio.site_published": True,
        "pricewatch.price_drop": True,
    }


def test_me_writes_need_csrf():
    client = alice()
    assert client.put("/api/me/subscription", json={"enabled": True}).status_code == 403
    assert client.post("/api/me/test").status_code == 403
    assert client.post("/api/me/rotate").status_code == 403


def test_me_cannot_enable_when_household_alerts_off():
    stonepi_notify.save_destinations({"ntfy": {"enabled": False}})
    client = alice()
    assert client.get("/api/me/subscription").json()["available"] is False
    assert client.put("/api/me/subscription", json={"enabled": True}, headers=H).status_code == 409


def test_me_test_goes_to_own_topic(fresh):
    client = alice()
    assert client.post("/api/me/test", headers=H).status_code == 409
    topic = client.put("/api/me/subscription", json={"enabled": True}, headers=H).json()["subscription"]["topic"]
    assert client.post("/api/me/test", headers=H).json()["ok"] is True
    assert fresh["sent"][-1]["topic"] == topic
    assert fresh["sent"][-1]["user"] == ALICE


def test_me_test_shows_in_your_recent_notifications(fresh, monkeypatch):
    """A personal test is recorded as delivered to you, so the Dashboard bell lists it."""
    from stonepi_notify import ntfy as ntfy_mod

    monkeypatch.setattr(stonepi_notify, "deliver_ntfy", ntfy_mod.deliver_ntfy)
    monkeypatch.setattr(ntfy_mod, "publish_ntfy", lambda **kw: {"ok": True, "message": "Sent."})
    client = alice()
    client.put("/api/me/subscription", json={"enabled": True}, headers=H)
    assert client.post("/api/me/test", headers=H).json()["ok"] is True
    recent = client.get("/api/me/apps/dashboard").json()["recent"]
    assert recent and recent[0]["title"] == "StonePi" and recent[0]["app_label"] == "Notify" and recent[0]["ok"]
    assert client_for(BOB, username="bob").get("/api/me/apps/dashboard").json()["recent"] == []


def test_me_rotate_changes_topic():
    client = alice()
    first = client.put("/api/me/subscription", json={"enabled": True}, headers=H).json()["subscription"]["topic"]
    second = client.post("/api/me/rotate", headers=H).json()["subscription"]["topic"]
    assert second != first


def test_me_only_touches_own_record():
    client = alice()
    client.put("/api/me/subscription", json={"enabled": True}, headers=H)
    admin_before = stonepi_notify.enable_subscription(ADMIN, username="adam", is_admin=True)
    # Body fields naming another user are ignored; the session decides whose record changes.
    client.put("/api/me/subscription", json={"user": ADMIN, "enabled": False}, headers=H)
    assert stonepi_notify.get_subscription(ADMIN) == admin_before
    assert stonepi_notify.get_subscription(ALICE)["enabled"] is False


def test_me_falls_back_to_session_when_auth_unreachable(monkeypatch):
    monkeypatch.setattr(me_routes, "load_people", lambda: None)
    body = client_for(BOB, username="bob").get("/api/me/subscription").json()
    # Session has no NewsCast can_use_ntfy capability -> not allowed.
    assert body["allowed"] is False
    assert admin().get("/api/me/subscription").json()["allowed"] is True


# -- Notify's own top-bar bell -------------------------------------------------


def test_notify_bell_dot_until_admin_sets_up():
    html = admin().get("/history").text
    bell = html.find('class="icon-btn sp-bell has-dot"')
    assert 0 < bell < html.find('aria-label="Account:')  # shared header: bell, then the avatar menu
    stonepi_notify.enable_subscription(ADMIN, username="adam", is_admin=True)
    html = admin().get("/history").text
    assert 'class="icon-btn sp-bell"' in html and "sp-bell has-dot" not in html


def test_notify_uses_shared_header_and_drops_home_from_nav():
    html = admin().get("/displays").text
    assert "sign-out" not in html  # the old sign-out icon is gone; Log out lives in the avatar menu
    assert 'class="sp-menu-logout"' in html and 'action="/logout"' in html
    nav = html[html.index('<nav class="nav"') :]
    assert "<span>Home</span>" not in nav[: nav.index("</nav>")]


_real_load_people = people.load_people  # the autouse fixture stubs it per test


def test_roster_backs_off_after_auth_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(people, "_fetch", lambda: calls.append(1))  # None: Auth failed
    people.reset_cache()
    assert _real_load_people() is None
    assert _real_load_people() is None
    assert len(calls) == 1
    people.reset_cache()


def test_notify_bell_hidden_while_household_alerts_off():
    stonepi_notify.save_destinations({"ntfy": {"enabled": False}})
    assert 'class="icon-btn sp-bell' not in admin().get("/history").text


def test_notify_bell_no_dot_after_turning_alerts_off():
    stonepi_notify.enable_subscription(ADMIN, username="adam", is_admin=True)
    stonepi_notify.disable_subscription(ADMIN)
    html = admin().get("/history").text
    assert 'class="icon-btn sp-bell"' in html


# -- Phone alerts page (/alerts) ----------------------------------------------------


def _post(client, path, **form):
    return client.post(path, data={"csrf_token": CSRF, **form}, follow_redirects=False)


def test_old_settings_links_redirect():
    c = admin()
    for tab, target in (("", "/alerts"), ("destinations", "/alerts"), ("prefs", "/alerts#choose"),
                        ("history", "/history"), ("about", "/about")):
        r = c.get(f"/settings?tab={tab}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == target, tab


def test_first_run_card_when_alerts_off():
    stonepi_notify.save_destinations({"ntfy": {"enabled": False}})
    html = admin().get("/alerts").text
    assert "Turn on phone alerts" in html and "Recommended" in html
    assert "Setup ·" not in html and 'id="choose"' not in html


def test_connect_with_ntfy_sh():
    stonepi_notify.save_destinations({"ntfy": {"enabled": False, "server": "https://old.example"}})
    r = _post(admin(), "/alerts/connect", server_choice="ntfysh")
    assert r.headers["location"].endswith("#choose")
    ntfy = stonepi_notify.load_destinations()["ntfy"]
    assert ntfy["enabled"] is True and ntfy["server"] == "https://ntfy.sh"


def test_connect_own_server_needs_address():
    stonepi_notify.save_destinations({"ntfy": {"enabled": False}})
    r = _post(admin(), "/alerts/connect", server_choice="own", server="")
    assert "err=" in r.headers["location"]
    assert stonepi_notify.load_destinations()["ntfy"]["enabled"] is False
    _post(admin(), "/alerts/connect", server_choice="own", server="https://ntfy.example.com")
    assert stonepi_notify.load_destinations()["ntfy"]["server"] == "https://ntfy.example.com"


def test_checklist_shows_progress_and_next_step():
    stonepi_notify.save_prefs({"events": {}})
    html = admin().get("/alerts").text
    assert "Setup · 1 of 3 done" in html  # connected; who can use alerts is optional, not a step
    assert "Approve all except admin-only" in html
    assert "2 of 3 household members" in html  # admin counts (always on) + Alice
    assert '<li class="is-optional">' in html and ">?</span>" in html
    assert "Not set up on your phone yet" in html


def test_approve_all_except_admin_only():
    stonepi_notify.save_prefs({"events": {"studio.site_published": {"approved": False, "admin_only": True, "urgent": False}}})
    r = _post(admin(), "/alerts/approve-recommended")
    location = r.headers["location"]
    assert "Disk%20warning" in location and "AI%20unavailable%20left%20for%20you%20to%20decide" in location
    events = stonepi_notify.load_prefs()["events"]
    assert events["pricewatch.price_drop"]["approved"] is True
    assert events["studio.site_published"] == {"approved": True, "admin_only": True, "urgent": False}  # flags kept
    assert "system.disk_warning" not in events  # admin-only left undecided


def test_all_set_shows_summary_and_last_delivery(fresh):
    stonepi_notify.save_prefs({"events": {"pricewatch.price_drop": True}})
    stonepi_notify.enable_subscription(ADMIN, username="adam", is_admin=True)
    stonepi_notify.append_history(channel="ntfy", ok=True, title="Deal", message="Sent to 2 of 2 recipient(s).",
                                  deliveries=[{"ok": True}, {"ok": True}])
    html = admin().get("/alerts").text
    assert "Phone alerts are on" in html and "Setup ·" not in html
    assert "sent to 2 phones" in html


def test_last_failed_delivery_is_shown():
    stonepi_notify.append_history(channel="ntfy", ok=False, title="Deal", message="server didn't answer")
    html = admin().get("/alerts").text
    assert "Last alert failed" in html and "server didn&#39;t answer" in html


def test_people_switch_calls_auth(fresh):
    html = admin().get("/alerts").text
    assert "Alice" in html and "Bob" in html and "admin, always on" in html
    _post(admin(), f"/alerts/people/{BOB}", phone_alerts="1")
    _post(admin(), f"/alerts/people/{ALICE}")
    assert fresh["toggles"] == [(BOB, True), (ALICE, False)]


def test_people_unavailable_message(monkeypatch):
    monkeypatch.setattr(people, "auth_users", lambda cookies: None)
    html = admin().get("/alerts").text
    assert "Couldn&#39;t load the household" in html


def test_send_test_prefers_own_phone(fresh):
    r = _post(admin(), "/alerts/test")
    assert "err=" in r.headers["location"]  # no phone, no household channel
    stonepi_notify.save_destinations({"ntfy": {"household_topic": "kitchen-abc"}})
    _post(admin(), "/alerts/test")
    assert fresh["sent"][-1]["topic"] == "kitchen-abc"
    topic = stonepi_notify.enable_subscription(ADMIN, username="adam", is_admin=True)["topic"]
    r = _post(admin(), "/alerts/test")
    assert fresh["sent"][-1]["topic"] == topic and "your%20phone" in r.headers["location"]


def test_turn_off_from_advanced():
    _post(admin(), "/alerts/connection", action="off")
    assert stonepi_notify.load_destinations()["ntfy"]["enabled"] is False


def test_admin_summary_api():
    stonepi_notify.save_prefs({"events": {"pricewatch.price_drop": True}})
    body = admin().get("/api/admin/summary").json()
    assert body["ok"] is True
    assert body["alerts"]["enabled"] is True and body["alerts"]["approved"] == 1
    assert body["alerts"]["people_on"] == 2 and body["alerts"]["needs_attention"] is False
    assert body["displays"]["carthing"] == {"enabled": False, "connected": False}
    assert alice().get("/api/admin/summary").status_code == 403


def test_summary_needs_attention_when_last_send_failed():
    stonepi_notify.save_prefs({"events": {"pricewatch.price_drop": True}})
    stonepi_notify.append_history(channel="ntfy", ok=False, title="Deal", message="boom")
    assert admin().get("/api/admin/summary").json()["alerts"]["needs_attention"] is True


def test_nav_has_phone_alerts_and_history():
    html = admin().get("/displays").text
    assert 'href="/alerts"' in html and 'href="/history"' in html and 'href="/settings"' not in html


def test_admin_pages_link_back_to_dashboard_settings():
    import re

    c = admin()
    for path in ("/alerts", "/displays", "/history", "/about"):
        html = c.get(path).text
        m = re.search(r'<a class="settings-back" href="([^"]+)">&larr; Settings</a>', html)
        assert m, path
        assert m.group(1).endswith("/settings"), (path, m.group(1))
    html = c.get("/displays/dashboard").text
    assert '<a class="settings-back" href="/displays">&larr; Displays</a>' in html


# -- /api/me/apps/{app}: the header bell panel -----------------------------------


def _deliver_to(uid: str, event_id: str, *, ok: bool = True, title: str = "Lamp hit £40") -> None:
    stonepi_notify.append_history(
        channel="ntfy",
        ok=ok,
        title=title,
        source=event_id.split(".")[0],
        event_id=event_id,
        deliveries=[{"kind": "user", "user": uid, "topic": "stonepi-…", "ok": ok, "silent": False, "message": "" if ok else "HTTP 500"}],
    )


def _approve(*event_ids: str) -> None:
    prefs = stonepi_notify.load_prefs()
    prefs.setdefault("events", {}).update({e: {"approved": True} for e in event_ids})
    stonepi_notify.save_prefs(prefs)


def test_app_panel_lists_this_apps_alerts_and_ticks():
    _approve("pricewatch.target_reached", "pricewatch.price_drop", "newscast.publication_available")
    stonepi_notify.enable_subscription(ALICE, username="alice")
    stonepi_notify.save_subscription(ALICE, {"events": {"pricewatch.target_reached": True}})
    body = alice().get("/api/me/apps/pricewatch").json()
    assert body["enabled"] is True and body["needs_setup"] is False
    assert [(e["id"], e["ticked"]) for e in body["events"]] == [
        ("pricewatch.target_reached", True),
        ("pricewatch.price_drop", False),
    ]
    assert body["last"] is None and body["unseen"] is False


def test_app_panel_last_alert_is_your_own_delivery_from_that_app():
    _deliver_to(ALICE, "pricewatch.target_reached", title="Mine")
    _deliver_to(BOB, "pricewatch.price_drop", title="Bob's")
    _deliver_to(ALICE, "newscast.publication_available", title="Other app")
    body = alice().get("/api/me/apps/pricewatch").json()
    assert body["last"]["title"] == "Mine" and body["last"]["ok"] is True
    assert body["unseen"] is True


def test_app_panel_seen_clears_the_dot_until_the_next_alert():
    client = alice()
    _deliver_to(ALICE, "pricewatch.target_reached")
    assert client.post("/api/me/apps/pricewatch/seen").status_code == 403  # CSRF
    res = client.post("/api/me/apps/pricewatch/seen", data={"csrf_token": CSRF})
    assert res.status_code == 200 and res.json()["seen_at"]
    assert client.get("/api/me/apps/pricewatch").json()["unseen"] is False
    _deliver_to(ALICE, "pricewatch.price_drop", ok=False)
    body = client.get("/api/me/apps/pricewatch").json()
    assert body["unseen"] is True and body["last"]["ok"] is False and body["last"]["message"] == "HTTP 500"


def test_app_panel_needs_sign_in_and_a_known_app():
    assert client_for(None).get("/api/me/apps/pricewatch").status_code == 401
    assert alice().get("/api/me/apps/nope").status_code == 404
    assert alice().post("/api/me/apps/nope/seen", headers=H).status_code == 404


def test_app_panel_without_phone_alerts_permission_is_empty():
    body = client_for(BOB, username="bob").get("/api/me/apps/pricewatch").json()
    assert body["allowed"] is False and body["events"] == [] and body["last"] is None


def test_dashboard_scope_covers_every_app_with_recent_alerts():
    _approve("pricewatch.target_reached", "newscast.publication_available")
    stonepi_notify.enable_subscription(ALICE, username="alice")
    _deliver_to(ALICE, "newscast.publication_available", title="Paper")
    _deliver_to(ALICE, "pricewatch.target_reached", title="Lamp")
    _deliver_to(BOB, "pricewatch.target_reached", title="Not mine")
    body = alice().get("/api/me/apps/dashboard").json()
    assert {e["app"] for e in body["events"]} == {"pricewatch", "newscast"}
    assert [(r["title"], r["app_label"]) for r in body["recent"]] == [("Lamp", "PriceWatch"), ("Paper", "NewsCast")]
    assert body["last"]["title"] == "Lamp" and body["unseen"] is True


def test_clear_hides_panel_entries_for_you_only_and_keeps_history():
    import time

    _approve("pricewatch.target_reached", "newscast.publication_available")
    stonepi_notify.enable_subscription(ALICE, username="alice")
    _deliver_to(ALICE, "newscast.publication_available", title="Paper")
    _deliver_to(ALICE, "pricewatch.target_reached", title="Lamp")
    _deliver_to(BOB, "pricewatch.target_reached", title="Bob's lamp")
    client = alice()
    assert client.post("/api/me/apps/pricewatch/clear").status_code == 403  # CSRF
    assert client.post("/api/me/apps/nope/clear", data={"csrf_token": CSRF}).status_code == 404
    time.sleep(0.01)
    # Clearing PriceWatch hides just PriceWatch's alerts (there and on the Dashboard).
    assert client.post("/api/me/apps/pricewatch/clear", data={"csrf_token": CSRF}).json()["ok"] is True
    assert client.get("/api/me/apps/pricewatch").json()["last"] is None
    assert [r["title"] for r in client.get("/api/me/apps/dashboard").json()["recent"]] == ["Paper"]
    # Bob's panel is untouched; Notify's history still has every delivery.
    bob_titles = [e["title"] for e in stonepi_notify.load_history()]
    assert {"Paper", "Lamp", "Bob's lamp"} <= set(bob_titles)
    # Clearing the Dashboard clears every app; something new shows again.
    client.post("/api/me/apps/dashboard/clear", data={"csrf_token": CSRF})
    assert client.get("/api/me/apps/dashboard").json()["recent"] == []
    assert client.get("/api/me/apps/newscast").json()["last"] is None
    time.sleep(0.01)
    _deliver_to(ALICE, "newscast.publication_available", title="Tomorrow's paper")
    assert client.get("/api/me/apps/dashboard").json()["recent"][0]["title"] == "Tomorrow's paper"
