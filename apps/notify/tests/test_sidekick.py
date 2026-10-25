"""Notify → Sidekick: the admin page for Pebbles, talking to AgentLink's signed internal API."""

from __future__ import annotations

import pytest

from app import sidekick_routes
from tests.test_displays_trmnl import ADMIN, CSRF, admin_client, fresh  # noqa: F401  (fixture)

OVERVIEW = {
    "ok": True,
    "devices": [{
        "id": 7, "device_id": "pbl-a0b1c2d3e4f5", "nickname": "Kitchen", "owner_id": ADMIN, "owner": "Admin",
        "talk": {"hold": True, "toggle": False, "wake": False, "wake_phrase": "hey stonepi"}, "away_allowed": False,
        "rev": 3, "created": 1.0, "last_seen": None, "last_path": "lan", "fw": "0.1.0", "battery": "87", "model": "",
        "last_ask": None, "last_ask_ok": None,
        "tools": [{"name": "get_status", "tier": "read", "description": "StonePi's health.", "enabled": True}],
    }],
    "speech": {"mode": "dev", "stt": {"ok": True, "detail": "Dev stand-in"}, "tts": {"ok": True, "detail": "Dev chime"}},
    "ai": {"ok": False, "detail": "No AI model set up"},
    "hosts": {"lan": "http://192.168.1.20", "away": None},
    "recent": [{"ts": 1.0, "binding": "7", "action": "ask", "decision": "ALLOWED", "duration_ms": 4200, "params": {}}],
}


@pytest.fixture
def agentlink(monkeypatch):
    calls = []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if path.endswith("/overview"):
            return 200, OVERVIEW
        if path.endswith("/claim") and body and body.get("code") != "K7M4":
            return 404, {"ok": False, "message": "No Pebble is showing that code."}
        return 200, {"ok": True, "message": "Done."}

    monkeypatch.setattr(sidekick_routes, "_agentlink", fake)
    monkeypatch.setattr(sidekick_routes, "_people", lambda: [{"id": ADMIN, "name": "Admin"}, {"id": "u2", "name": "Eva"}])
    return calls


def test_page_lists_devices_and_readiness(fresh, agentlink):
    html = admin_client().get("/sidekick").text
    assert "Kitchen" in html and "Admin's Pebble" in html and "battery 87%" in html
    assert "Set up AI" in html  # AI not ready → link to Dashboard → Settings → AI
    assert 'href="/sidekick" class="is-active"' in html
    assert "Asked a question" in html


def test_add_sends_code_owner_and_name(fresh, agentlink):
    client = admin_client()
    r = client.post("/sidekick/add", data={"csrf_token": CSRF, "code": "k7m4", "owner": "u2", "nickname": "Desk"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/sidekick?pairing=1#pairing")
    method, path, body = agentlink[-1]
    assert path.endswith("/pebble/claim") and body == {"code": "K7M4", "user_id": "u2", "user_name": "Eva", "nickname": "Desk"}
    bad = client.post("/sidekick/add", data={"csrf_token": CSRF, "code": "ZZZZ", "owner": "u2"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]
    nobody = client.post("/sidekick/add", data={"csrf_token": CSRF, "code": "K7M4", "owner": "ghost"}, follow_redirects=False)
    assert "err=Pick" in nobody.headers["location"]


def test_save_maps_checkboxes(fresh, agentlink):
    r = admin_client().post(
        "/sidekick/7/save",
        data={"csrf_token": CSRF, "nickname": "Hall", "hold": "1", "wake": "1", "wake_phrase": "hey pebble", "away": "1",
              "tools_listed": "get_status"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    _m, path, body = next(c for c in agentlink if c[1].endswith("/pebble/device"))
    assert body["talk"] == {"hold": True, "toggle": False, "wake": True, "wake_phrase": "hey pebble"}
    assert body["away_allowed"] is True and body["tools"] == {"get_status": False} and body["nickname"] == "Hall"


def test_remove_and_csrf(fresh, agentlink):
    client = admin_client()
    assert "err=Form" in client.post("/sidekick/7/remove", data={"csrf_token": "nope"}, follow_redirects=False).headers["location"]
    client.post("/sidekick/7/remove", data={"csrf_token": CSRF}, follow_redirects=False)
    assert any(c[1].endswith("/pebble/revoke") and c[2] == {"id": 7} for c in agentlink)


def test_funnel_follows_away_switches(fresh, agentlink, monkeypatch):
    calls = []

    def fake_funnel(action):
        calls.append(action)
        if action == "status":
            return {"ok": True, "on": True}
        return {"ok": True, "on": action == "on", "message": "Away from home is off."}

    monkeypatch.setattr(sidekick_routes, "_funnel", fake_funnel)
    # OVERVIEW has no Pebble allowed away, but Funnel is on: saving turns it off.
    r = admin_client().post("/sidekick/7/save", data={"csrf_token": CSRF, "nickname": "Hall", "hold": "1", "tools_listed": ""},
                            follow_redirects=False)
    assert calls[-1] == "off" and "Away+from+home+is+off" in r.headers["location"].replace("%20", "+")


def test_funnel_failure_shows_enable_link(fresh, agentlink, monkeypatch):
    device = {**OVERVIEW["devices"][0], "away_allowed": True}
    monkeypatch.setitem(OVERVIEW, "devices", [device])

    def fake_funnel(action):
        if action == "status":
            return {"ok": True, "on": False}
        return {"ok": False, "on": False, "message": "Tailscale didn't allow Funnel.", "enable_url": "https://login.tailscale.com/f/funnel?node=x"}

    monkeypatch.setattr(sidekick_routes, "_funnel", fake_funnel)
    r = admin_client().post("/sidekick/7/save", data={"csrf_token": CSRF, "away": "1", "hold": "1", "tools_listed": ""}, follow_redirects=False)
    assert "err=" in r.headers["location"] and "login.tailscale.com" in r.headers["location"]


def test_trailing_slash_link_from_health(fresh, agentlink):
    """Dashboard's Health card links to /notify/sidekick/ (catalog path + slash)."""
    r = admin_client().get("/sidekick/", follow_redirects=False)
    assert r.status_code == 200 and "Kitchen" in r.text


def test_funnel_status_only_when_away_matters(fresh, agentlink, monkeypatch):
    calls = []
    monkeypatch.setattr(sidekick_routes, "_funnel", lambda action: calls.append(action) or {"ok": True, "on": False})
    sidekick_routes._FUNNEL_CACHE.update(at=-1e9, data=None)
    admin_client().get("/sidekick")
    assert calls == []  # no Pebble allowed away, no away URL: no sudo call
    device = {**OVERVIEW["devices"][0], "away_allowed": True}
    monkeypatch.setitem(OVERVIEW, "devices", [device])
    admin_client().get("/sidekick")
    admin_client().get("/sidekick")
    assert calls == ["status"]  # cached for a minute


def test_internal_sync_needs_signature_and_runs_sync(fresh, agentlink, monkeypatch):
    import threading as _threading

    from stonepi_auth.internal import sign_internal_v2

    from tests.test_displays_trmnl import SECRET

    ran = _threading.Event()
    monkeypatch.setattr(sidekick_routes, "_sync_funnel", lambda: ran.set() or ("", ""))
    client = admin_client()
    path = "/api/internal/sidekick/sync-funnel"
    assert client.post(path).status_code == 404  # unsigned
    r = client.post(path, headers=sign_internal_v2(SECRET, "POST", path, b""))
    assert r.status_code == 200 and ran.wait(2)


def test_periodic_sync_is_throttled(monkeypatch):
    calls = []
    monkeypatch.setattr(sidekick_routes, "_periodic_sync", lambda: calls.append(1))
    sidekick_routes._last_periodic["at"] = -1e9
    sidekick_routes.sync_funnel_periodic()
    sidekick_routes.sync_funnel_periodic()
    import time as _time

    _time.sleep(0.2)
    assert len(calls) == 1


def test_activity_names_tools_and_search_words(fresh, agentlink, monkeypatch):
    recent = [
        {"ts": 3.0, "binding": "7", "action": "ask", "decision": "ALLOWED", "duration_ms": 5100, "params": {}, "via": ""},
        {"ts": 2.0, "binding": "7", "action": "search_news", "decision": "ALLOWED", "duration_ms": 80, "params": {"query": "Mars"}, "via": "ask"},
        {"ts": 1.0, "binding": "7", "action": "get_events", "decision": "DENIED:no_app", "duration_ms": 1, "params": {}, "via": "ask"},
    ]
    monkeypatch.setitem(OVERVIEW, "recent", recent)
    html = admin_client().get("/sidekick").text
    assert "Asked a question" in html and "Searched news for “Mars”" in html
    assert "↳" in html and "Read upcoming events" in html and "DENIED:no_app" in html


def test_voice_card_and_choice(fresh, agentlink, monkeypatch):
    monkeypatch.setitem(OVERVIEW, "voice", {"current": "en_GB-alba-medium", "pending": "", "voices": [
        {"id": "en_GB-alba-medium", "label": "British English · female · Scottish accent", "installed": True, "current": True, "state": ""},
        {"id": "en_US-ryan-medium", "label": "American English · male", "installed": False, "current": False, "state": ""},
    ]})
    client = admin_client()
    html = client.get("/sidekick").text
    assert "Alba — British English" in html and "(in use)" in html and "Ryan — American English · male · download" in html
    r = client.post("/sidekick/voice", data={"csrf_token": CSRF, "voice": "en_US-ryan-medium"}, follow_redirects=False)
    assert r.status_code == 303 and agentlink[-1][1].endswith("/pebble/voice") and agentlink[-1][2] == {"voice": "en_US-ryan-medium"}


def test_quiet_hours_card_and_save(fresh, agentlink, monkeypatch):
    monkeypatch.setitem(OVERVIEW, "quiet_hours", {"enabled": True, "start": "23:00", "end": "06:30"})
    client = admin_client()
    html = client.get("/sidekick").text
    assert 'id="quiet"' in html and 'value="23:00"' in html and 'value="06:30"' in html
    r = client.post("/sidekick/quiet", data={"csrf_token": CSRF, "start": "22:30", "end": "07:00"}, follow_redirects=False)
    assert r.status_code == 303 and agentlink[-1][1].endswith("/pebble/settings")
    assert agentlink[-1][2] == {"quiet_hours": {"enabled": False, "start": "22:30", "end": "07:00"}}
    r = client.post("/sidekick/quiet", data={"csrf_token": "nope", "enabled": "1", "start": "22:30", "end": "07:00"}, follow_redirects=False)
    assert agentlink[-1][1].endswith("/pebble/settings") and agentlink[-1][2]["quiet_hours"]["enabled"] is False  # not sent


def test_page_waits_for_a_new_pebble(fresh, agentlink):
    client = admin_client()
    html = client.get("/sidekick?pairing=1").text
    assert "data-sk-pairing" in html and 'id="pairing"' in html
    assert 'data-sk-pairing' not in client.get("/sidekick").text
    r = client.get("/sidekick/devices.json")
    assert r.status_code == 200 and r.json() == {
        "ok": True, "devices": [{"id": d["id"], "nickname": d.get("nickname") or "Pebble"} for d in OVERVIEW["devices"]],
    }


def test_devices_json_needs_an_admin(fresh, agentlink):
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/sidekick/devices.json", follow_redirects=False).status_code == 403


NO_SPEECH = {"mode": "auto", "stt": {"ok": False, "detail": "faster-whisper isn't installed"}, "tts": {"ok": False, "detail": "piper isn't installed"}}


@pytest.fixture
def no_speech(monkeypatch, agentlink):
    monkeypatch.setitem(OVERVIEW, "speech", NO_SPEECH)
    calls = []
    state = {"status": {"ok": True, "state": "never", "step": "", "error": "", "arch": "aarch64", "supported": True}}

    def fake(action):
        calls.append(action)
        if action == "--start":
            return {"ok": True, "message": "Setting up speech. This takes a few minutes."}
        return dict(state["status"])

    monkeypatch.setattr(sidekick_routes, "_speech_setup", fake)
    return calls, state


def test_set_up_speech_button_when_pebble_cant_listen(fresh, no_speech):
    calls, state = no_speech
    html = admin_client().get("/sidekick").text
    assert 'action="/sidekick/speech-setup"' in html and "Set up speech" in html and "about 200 MB" in html
    assert calls == ["--status"]
    state["status"].update(state="failed", error="ERROR: curl failed 22")
    html = admin_client().get("/sidekick").text
    assert "Speech setup didn't finish: ERROR: curl failed 22" in html and "Try again" in html


def test_running_setup_shows_its_step_and_no_button(fresh, no_speech):
    _calls, state = no_speech
    state["status"].update(state="running", step="Downloading piper voice en_GB-alba-medium")
    html = admin_client().get("/sidekick").text
    assert "data-sk-speech" in html and "Setting up speech<span data-sk-speech-step>: Downloading piper voice" in html
    assert 'action="/sidekick/speech-setup"' not in html


def test_32_bit_pi_says_why_and_offers_nothing(fresh, no_speech):
    _calls, state = no_speech
    state["status"].update(arch="armv7l", supported=False)
    html = admin_client().get("/sidekick").text
    assert "needs 64-bit Raspberry Pi OS, and this one runs armv7l" in html
    assert 'action="/sidekick/speech-setup"' not in html


def test_without_the_helper_the_page_names_the_command(fresh, no_speech, monkeypatch):
    monkeypatch.setattr(sidekick_routes, "_speech_setup", lambda action: {"ok": False, "dev": True})
    html = admin_client().get("/sidekick").text
    assert "sudo stonepi-speech-setup" in html and 'action="/sidekick/speech-setup"' not in html


def test_ready_speech_never_asks_the_helper(fresh, agentlink, monkeypatch):
    monkeypatch.setattr(sidekick_routes, "_speech_setup", lambda action: pytest.fail("helper called"))
    html = admin_client().get("/sidekick").text
    assert "Set up speech" not in html


def test_start_needs_csrf_and_runs_the_helper(fresh, no_speech, monkeypatch):
    calls, _state = no_speech
    client = admin_client()
    bad = client.post("/sidekick/speech-setup", data={"csrf_token": "nope"}, follow_redirects=False)
    assert "err=Form" in bad.headers["location"] and "--start" not in calls
    r = client.post("/sidekick/speech-setup", data={"csrf_token": CSRF}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#ready") and "msg=Setting" in r.headers["location"]
    assert calls[-1] == "--start"
    monkeypatch.setattr(sidekick_routes, "_speech_setup", lambda action: {"ok": False, "message": "Speech needs 64-bit Raspberry Pi OS (this Pi runs armv7l)."})
    r = client.post("/sidekick/speech-setup", data={"csrf_token": CSRF}, follow_redirects=False)
    assert "err=Speech%20needs%2064-bit" in r.headers["location"]


def test_setup_status_json_for_the_poll(fresh, no_speech):
    _calls, state = no_speech
    state["status"].update(state="running", step="Speed check")
    assert admin_client().get("/sidekick/speech-setup.json").json() == {"ok": True, "state": "running", "step": "Speed check"}
