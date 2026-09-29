"""Dashboard Notifications page and top-bar bell (personal alerts)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import notifications, routes, services
from stonepi_auth.session import CSRF_COOKIE, PlatformUser

CSRF = "csrf-abc"


def _user(*, is_admin=False, perms=None):
    return PlatformUser(
        user_id="u-1",
        username="alice",
        display_name="Alice",
        is_admin=is_admin,
        apps=["dashboard", "newscast"],
        permissions=perms or {},
        session_id="sid",
    )


def _state(**over):
    state = {
        "ok": True,
        "available": True,
        "allowed": True,
        "is_admin": False,
        "subscription": None,
        "subscribe": None,
        "groups": [],
        "needs_setup": True,
    }
    state.update(over)
    return state


ENABLED = _state(
    needs_setup=False,
    subscription={
        "enabled": True,
        "topic": "stonepi-alice-abcdefghijklmnop",
        "quiet_hours": {"enabled": True, "start": "22:30", "end": "06:45"},
        "updated_at": None,
    },
    subscribe={
        "web_url": "https://ntfy.sh/stonepi-alice-abcdefghijklmnop",
        "app_url": "ntfy://ntfy.sh/stonepi-alice-abcdefghijklmnop",
    },
    groups=[
        {
            "id": "pricewatch",
            "label": "PriceWatch",
            "events": [
                {"id": "pricewatch.price_drop", "label": "Significant price drop", "blurb": "",
                 "audience": "personal", "audience_label": "Just for you", "ticked": True},
                {"id": "pricewatch.target_reached", "label": "Target price reached", "blurb": "",
                 "audience": "personal", "audience_label": "Just for you", "ticked": False},
            ],
        }
    ],
)


@pytest.fixture
def env(monkeypatch):
    calls: list[tuple] = []
    box = {"user": _user(), "state": _state(), "reply": (200, {"ok": True})}

    def fake_notify(method, path, cookies, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return 200, box["state"]
        return box["reply"]

    monkeypatch.setattr(services, "current_user", lambda cookies: box["user"])
    monkeypatch.setattr(services, "notify_request", fake_notify)
    monkeypatch.setattr(routes, "_alerts_bell", lambda request, user: {"show": True, "dot": True, "url": "/notifications"})
    monkeypatch.setattr(routes, "_factory_admin", lambda cookies, user: False)
    app = FastAPI()
    app.include_router(notifications.router)
    client = TestClient(app)
    client.cookies.set(CSRF_COOKIE, CSRF)
    box["client"] = client
    box["calls"] = calls
    return box


def test_not_allowed(env):
    env["state"] = _state(allowed=False, needs_setup=False)
    html = env["client"].get("/notifications").text
    assert "aren't turned on for your account" in html
    assert 'class="icon-btn alerts-bell' not in html  # bell hidden without phone alerts


def test_household_off_admin_gets_notify_link(env):
    env["user"] = _user(is_admin=True)
    env["state"] = _state(available=False, needs_setup=False)
    html = env["client"].get("/notifications").text
    assert "aren't turned on for the household yet" in html
    assert "/alerts" in html  # Notify's Phone alerts page: /notify/alerts behind nginx, Notify's port in dev


def test_household_off_member_has_no_notify_link(env):
    env["state"] = _state(available=False, needs_setup=False)
    html = env["client"].get("/notifications").text
    assert "/notify/settings" not in html
    assert 'class="icon-btn alerts-bell' not in html  # bell hidden until household alerts are on


def test_bell_status_hidden_while_household_alerts_off(monkeypatch):
    from stonepi_contracts import alerts

    alerts._cache.clear()
    alerts._failures.clear()
    monkeypatch.setattr(alerts, "_ask_notify", lambda *a: {"allowed": True, "available": False, "needs_setup": False})
    status = alerts.personal_alerts_status("u-6", cookie_name="stonepi", session_cookie="t", session_allowed=True)
    assert status == {"show": False, "dot": False}


def test_not_set_up_shows_turn_on_and_bell_dot(env):
    html = env["client"].get("/notifications").text
    assert "Turn on personal alerts" in html
    assert "alerts-bell has-dot" in html
    assert 'aria-current="page"' in html


def test_enabled_shows_qr_link_choices_and_quiet_hours(env):
    env["state"] = ENABLED
    html = env["client"].get("/notifications").text
    assert "<svg" in html.split('class="alerts-qr"', 1)[1][:400]
    assert "ntfy://ntfy.sh/stonepi-alice-abcdefghijklmnop" in html
    assert 'name="event_pricewatch.price_drop" value="1" checked' in html
    unticked = html.split('name="event_pricewatch.target_reached"', 1)[1].split("/>", 1)[0]
    assert "checked" not in unticked
    assert 'value="pricewatch.price_drop,pricewatch.target_reached"' in html
    assert 'value="22:30"' in html and 'value="06:45"' in html
    assert "has-dot" not in html


def test_notify_unreachable(env, monkeypatch):
    monkeypatch.setattr(services, "notify_request", lambda *a, **k: (503, {"message": "Notify is not reachable."}))
    html = env["client"].get("/notifications").text
    assert "can't be loaded right now" in html


def _post(env, **form):
    return env["client"].post("/notifications", data={"csrf_token": CSRF, **form}, follow_redirects=False)


def test_turn_on(env):
    response = _post(env, action="on")
    assert response.status_code == 303
    assert "msg=" in response.headers["location"]
    assert env["calls"][-1] == ("PUT", "/api/me/subscription", {"enabled": True})


def test_save_events_sends_unticked_as_false(env):
    _post(env, action="save_events", offered="pricewatch.price_drop,pricewatch.target_reached",
          **{"event_pricewatch.target_reached": "1"})
    assert env["calls"][-1] == (
        "PUT",
        "/api/me/subscription",
        {"events": {"pricewatch.price_drop": False, "pricewatch.target_reached": True}},
    )


def test_save_quiet_hours(env):
    _post(env, action="save_quiet", quiet_enabled="1", quiet_start="23:00", quiet_end="06:30")
    assert env["calls"][-1] == (
        "PUT",
        "/api/me/subscription",
        {"quiet_hours": {"enabled": True, "start": "23:00", "end": "06:30"}},
    )


def test_test_rotate_off(env):
    _post(env, action="test")
    assert env["calls"][-1][:2] == ("POST", "/api/me/test")
    _post(env, action="rotate")
    assert env["calls"][-1][:2] == ("POST", "/api/me/rotate")
    _post(env, action="off")
    assert env["calls"][-1] == ("PUT", "/api/me/subscription", {"enabled": False})


def test_notify_error_is_shown(env):
    env["reply"] = (409, {"ok": False, "message": "Turn on personal alerts first."})
    response = _post(env, action="test")
    assert "err=Turn%20on%20personal%20alerts%20first." in response.headers["location"]


def test_csrf_required(env):
    response = env["client"].post("/notifications", data={"action": "on"}, follow_redirects=False)
    assert "err=" in response.headers["location"]
    assert env["calls"] == []


def test_signed_out_redirects_to_login(env):
    env["user"] = None
    response = env["client"].get("/notifications", follow_redirects=False)
    assert response.status_code == 303


# -- shared bell status helper -------------------------------------------------


def test_bell_status_uses_notify_and_caches(monkeypatch):
    from stonepi_contracts import alerts

    alerts._cache.clear()
    asked = []

    def fake_ask(cookie_name, session, base_url, timeout):
        asked.append(session)
        return {"allowed": True, "available": True, "needs_setup": True}

    monkeypatch.setattr(alerts, "_ask_notify", fake_ask)
    first = alerts.personal_alerts_status("u-9", cookie_name="stonepi", session_cookie="tok", session_allowed=False)
    second = alerts.personal_alerts_status("u-9", cookie_name="stonepi", session_cookie="tok", session_allowed=False)
    assert first == second == {"show": True, "dot": True}
    assert asked == ["tok"]
    alerts.forget_alerts_status("u-9")
    alerts.personal_alerts_status("u-9", cookie_name="stonepi", session_cookie="tok", session_allowed=False)
    assert len(asked) == 2


def test_bell_status_falls_back_to_session_when_notify_down(monkeypatch):
    from stonepi_contracts import alerts

    alerts._cache.clear()
    monkeypatch.setattr(alerts, "_ask_notify", lambda *a: None)
    status = alerts.personal_alerts_status("u-8", cookie_name="stonepi", session_cookie="tok", session_allowed=True)
    assert status == {"show": True, "dot": False}
    assert "u-8" not in alerts._cache


def test_phone_alerts_allowed_from_session():
    from stonepi_auth.alerts import phone_alerts_allowed

    assert phone_alerts_allowed(_user(is_admin=True))
    assert phone_alerts_allowed(_user(perms={"newscast": {"can_use_ntfy": True}}))
    assert not phone_alerts_allowed(_user())
    assert not phone_alerts_allowed(None)


def test_bell_status_backs_off_after_failure(monkeypatch):
    from stonepi_contracts import alerts

    alerts._cache.clear()
    alerts._failures.clear()
    clock = {"t": 1000.0}
    asked = []
    monkeypatch.setattr(alerts, "_ask_notify", lambda *a: asked.append(1))  # returns None: a failure
    kw = {"cookie_name": "stonepi", "session_cookie": "tok", "session_allowed": True, "now": lambda: clock["t"]}
    alerts.personal_alerts_status("u-7", **kw)
    alerts.personal_alerts_status("u-7", **kw)
    assert len(asked) == 1  # second page view doesn't call Notify again
    clock["t"] += alerts.FAILURE_TTL_SECONDS + 1
    alerts.personal_alerts_status("u-7", **kw)
    assert len(asked) == 2


def test_notify_settings_url_dev_and_nginx(monkeypatch):
    monkeypatch.setattr(services, "app_public_url", lambda item: "http://127.0.0.1:8012/")
    assert services.notify_settings_url("prefs") == "http://127.0.0.1:8012/settings?tab=prefs"
    monkeypatch.setattr(services, "app_public_url", lambda item: "/notify/")
    assert services.notify_settings_url() == "/notify/settings"
