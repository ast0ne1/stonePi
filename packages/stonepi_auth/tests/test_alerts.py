"""Shared personal-alerts chrome: templates, bell/card state, event owners."""

from __future__ import annotations

import time

import pytest
from jinja2 import DictLoader, Environment

from stonepi_auth import alerts
from stonepi_auth.session import COOKIE_NAME, PlatformUser, encode_session


def _user(*, is_admin=False, phone_alerts=None):
    return PlatformUser(
        user_id="7c9e6679-7425-40de-944b-e07fc1f90ae7",
        username="jo",
        display_name="Jo",
        is_admin=is_admin,
        apps=["dashboard", "pricewatch"],
        session_id="s",
        exp=int(time.time()) + 60,
        phone_alerts=phone_alerts,
    )


def _env(**templates) -> Environment:
    env = Environment(loader=DictLoader(templates), autoescape=True)
    alerts.add_shared_templates(env)
    return env


# -- templates -------------------------------------------------------------------


def test_shared_templates_load_and_app_templates_win():
    env = _env(**{"page.html": "app", "stonepi/alerts.html": "{% macro alerts_bell(b) %}APP{% endmacro %}"})
    assert env.get_template("page.html").render() == "app"
    # An app may override the shared file; its own copy takes priority.
    assert "APP" in env.from_string('{% from "stonepi/alerts.html" import alerts_bell %}{{ alerts_bell({}) }}').render()


def test_add_shared_templates_is_idempotent():
    env = _env(**{"page.html": "x"})
    alerts.add_shared_templates(env)
    alerts.add_shared_templates(env)
    shared = [l for l in env.loader.loaders if getattr(l, "searchpath", None) == [str(alerts.SHARED_TEMPLATES_DIR)]]
    assert len(shared) == 1


def _render_bell(bell) -> str:
    env = _env()
    return env.from_string('{% from "stonepi/alerts.html" import alerts_bell %}{{ alerts_bell(b) }}').render(b=bell)


def test_bell_hidden_unless_show():
    assert _render_bell({"show": False}).strip() == ""
    assert _render_bell(None).strip() == ""


def test_bell_dot_and_active():
    html = _render_bell({"show": True, "dot": True, "active": True, "url": "https://pi.local/notifications"})
    assert 'href="https://pi.local/notifications"' in html
    assert "has-dot" in html and "alerts-bell-dot" in html
    assert 'aria-current="page"' in html
    assert "not set up yet" in html
    plain = _render_bell({"show": True, "dot": False, "url": "/notifications"})
    assert "alerts-bell-dot" not in plain and "aria-current" not in plain


# -- bell state ------------------------------------------------------------------


def test_bell_context_hidden_signed_out_or_standalone():
    assert alerts.bell_context(None, session_cookie="x", home_url="")["show"] is False
    assert alerts.bell_context(_user(is_admin=True), session_cookie="x", home_url="", enabled=False)["show"] is False


def test_bell_context_asks_notify(monkeypatch):
    import stonepi_contracts

    seen = {}

    def fake_status(user_id, *, cookie_name, session_cookie, session_allowed):
        seen.update(cookie_name=cookie_name, session_cookie=session_cookie, allowed=session_allowed)
        return {"show": True, "dot": True}

    monkeypatch.setattr(stonepi_contracts, "personal_alerts_status", fake_status)
    bell = alerts.bell_context(_user(phone_alerts=True), session_cookie="tok", home_url="https://pi.local/")
    assert bell == {"show": True, "dot": True, "url": "https://pi.local/notifications", "active": False}
    assert seen == {"cookie_name": COOKIE_NAME, "session_cookie": "tok", "allowed": True}


def test_bell_context_survives_notify_errors(monkeypatch):
    import stonepi_contracts

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(stonepi_contracts, "personal_alerts_status", boom)
    bell = alerts.bell_context(_user(phone_alerts=True), session_cookie="tok", home_url="")
    assert bell["show"] is True and bell["dot"] is False


# -- Settings card ---------------------------------------------------------------


def test_card_none_when_signed_out():
    assert alerts.notifications_card_context("pricewatch", None, home_url="") is None


def test_card_for_member_with_alerts():
    card = alerts.notifications_card_context("pricewatch", _user(phone_alerts=True), home_url="https://pi.local")
    assert card["manage_url"] == "https://pi.local/notifications"
    assert card["events"] == [
        "Target price reached",
        "Significant price drop",
        "Cheaper offer from low-rated shop",
    ]
    assert card["blurb"] == "PriceWatch can send phone alerts about:"
    assert card["is_admin"] is False and card["destinations_url"] == ""


def test_card_for_member_without_alerts():
    card = alerts.notifications_card_context("pricewatch", _user(phone_alerts=False), home_url="")
    assert card["manage_url"] == ""


def test_card_for_admin_links_to_notify():
    card = alerts.notifications_card_context("studio", _user(is_admin=True), home_url="https://pi.local")
    assert card["destinations_url"] == "https://pi.local/notify/alerts"
    assert card["approvals_url"] == "https://pi.local/notify/alerts#choose"


def test_card_for_app_without_events():
    card = alerts.notifications_card_context("nothing", _user(is_admin=True), home_url="")
    assert card["events"] == []
    assert "doesn't send phone alerts yet" in card["blurb"]


def test_card_renders():
    env = _env()
    card = alerts.notifications_card_context("pricewatch", _user(is_admin=True), home_url="")
    html = env.from_string(
        '{% from "stonepi/alerts.html" import notifications_card %}{{ notifications_card(c) }}'
    ).render(c=card)
    assert "Manage personal alerts" in html
    assert "Target price reached" in html
    assert "/notify/alerts#choose" in html and "Set up phone alerts" in html
    member = alerts.notifications_card_context("pricewatch", _user(phone_alerts=False), home_url="")
    html = env.from_string(
        '{% from "stonepi/alerts.html" import notifications_card %}{{ notifications_card(c) }}'
    ).render(c=member)
    assert "Manage personal alerts" not in html and "Choose alerts" not in html


# -- owners ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7c9e6679-7425-40de-944b-e07fc1f90ae7", "7c9e6679-7425-40de-944b-e07fc1f90ae7"),
        ("  abc-123  ", "abc-123"),
        ("local", None),
        ("LOCAL", None),
        ("", None),
        (None, None),
        (7, None),
        ("42", None),
        (True, None),
    ],
)
def test_auth_user_id(value, expected):
    assert alerts.auth_user_id(value) == expected


def test_session_user_id():
    cookie = encode_session(
        secret="s3", user_id="7c9e6679-7425-40de-944b-e07fc1f90ae7", username="jo", display_name="Jo",
        is_admin=False, apps=[], session_id="sid",
    )
    assert alerts.session_user_id({COOKIE_NAME: cookie}, "s3") == "7c9e6679-7425-40de-944b-e07fc1f90ae7"
    assert alerts.session_user_id({COOKIE_NAME: cookie}, "wrong") is None
    assert alerts.session_user_id({}, "s3") is None
    assert alerts.session_user_id({COOKIE_NAME: cookie}, "") is None


@pytest.mark.parametrize(
    ("home", "tab", "expected"),
    [
        ("http://stonepi.local", "prefs", "http://stonepi.local/notify/settings?tab=prefs"),
        ("https://pi.example.home/", "", "https://pi.example.home/notify/settings"),
        ("", "destinations", "/notify/settings?tab=destinations"),
        # Local dev: no nginx, Notify on its own port.
        ("http://127.0.0.1:8010", "prefs", "http://127.0.0.1:8012/settings?tab=prefs"),
        ("http://localhost:8010/", "", "http://localhost:8012/settings"),
    ],
)
def test_notify_settings_url(home, tab, expected):
    assert alerts.notify_settings_url(home, tab) == expected


@pytest.mark.parametrize(
    ("home", "page", "expected"),
    [
        ("http://stonepi.local", "alerts", "http://stonepi.local/notify/alerts"),
        ("http://stonepi.local/", "alerts#choose", "http://stonepi.local/notify/alerts#choose"),
        ("", "displays", "/notify/displays"),
        ("http://127.0.0.1:8010", "alerts", "http://127.0.0.1:8012/alerts"),
    ],
)
def test_notify_page_url(home, page, expected):
    assert alerts.notify_page_url(home, page) == expected
