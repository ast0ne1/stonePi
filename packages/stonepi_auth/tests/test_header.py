"""Shared app header: switcher, avatar menu, per-app bell panel (backlog U10 / U2)."""

from __future__ import annotations

import time

import pytest
from jinja2 import DictLoader, Environment

from stonepi_auth import alerts, header
from stonepi_auth.session import PlatformUser

PI = "https://stonepi.home"
DEV = "http://127.0.0.1:8010"


def _user(*, is_admin=False, apps=("eventtrakr", "newscast"), phone_alerts=True):
    return PlatformUser(
        user_id="7c9e6679-7425-40de-944b-e07fc1f90ae7",
        username="jo.smith",
        display_name="Jo Smith",
        is_admin=is_admin,
        apps=list(apps),
        session_id="s",
        exp=int(time.time()) + 60,
        phone_alerts=phone_alerts,
    )


def _render(source: str, **ctx) -> str:
    env = Environment(loader=DictLoader({}), autoescape=True)
    alerts.add_shared_templates(env)
    return env.from_string('{% from "stonepi/header.html" import header_search, header_tools, header_fab %}' + source).render(**ctx)


# -- URLs ------------------------------------------------------------------------


def test_app_page_url_behind_nginx_and_in_dev():
    assert header.app_page_url(PI, "newscast") == f"{PI}/news/"
    assert header.app_page_url(PI, "dashboard") == f"{PI}/"
    assert header.app_page_url(PI, "notify", "api/me/apps/x/seen") == f"{PI}/notify/api/me/apps/x/seen"
    assert header.app_page_url(DEV, "newscast") == "http://127.0.0.1:8001/"
    assert header.app_page_url(DEV, "dashboard") == f"{DEV}/"
    assert alerts.notify_page_url(DEV, "alerts") == "http://127.0.0.1:8012/alerts"


# -- switcher + avatar -------------------------------------------------------------


def test_switcher_lists_user_apps_in_dashboard_order_with_current_highlighted():
    apps = header.switcher_apps(_user(apps=("newscast", "eventtrakr", "library")), PI, "eventtrakr")
    assert [a["id"] for a in apps] == ["newscast", "eventtrakr", "library"]
    assert [a["id"] for a in apps if a["current"]] == ["eventtrakr"]


def test_switcher_shows_admins_all_nine_and_never_auth():
    ids = [a["id"] for a in header.switcher_apps(_user(is_admin=True, apps=()), PI, "studio")]
    assert len(ids) == 9 and "auth" not in ids and "dashboard" not in ids
    assert ids[0] == "newscast"


def test_account_identity_and_manage_link():
    account = header.account_context(_user(), PI)
    assert account["initials"] == "JS" and account["username"] == "jo.smith" and account["role"] == "Member"
    assert account["manage_url"] == f"{PI}/settings?tab=general#account-password"
    assert header.account_context(None, PI) is None


# -- bell_context with app_id -------------------------------------------------------


@pytest.fixture
def fake_status(monkeypatch):
    calls = {}

    def fake(user_id, app_id, **kwargs):
        calls["app"] = app_id
        return calls.get(
            "result",
            {
                "show": True, "dot": True, "reachable": True, "allowed": True, "available": True,
                "enabled": True, "needs_setup": False,
                "events": [{"id": "pricewatch.target_reached", "label": "Target price reached", "ticked": True},
                           {"id": "pricewatch.low_rated_offer", "label": "Cheaper offer from low-rated shop", "ticked": False}],
                "last": {"title": "Lamp hit £40", "at": "2026-10-10T08:00:00+00:00", "ok": True, "silent": False, "message": ""},
                "seen_at": None,
            },
        )

    import stonepi_contracts

    monkeypatch.setattr(stonepi_contracts, "app_alerts_status", fake)
    return calls


def test_bell_context_with_app_carries_panel_and_header(fake_status):
    state = alerts.bell_context(_user(), session_cookie="c", home_url=PI, app_id="pricewatch")
    assert fake_status["app"] == "pricewatch"
    assert state["show"] and state["dot"]
    panel = state["panel"]
    assert panel["app_name"] == "PriceWatch" and panel["last"]["delivery"] == "Sent to your phone"
    assert panel["seen_url"] == f"{PI}/notify/api/me/apps/pricewatch/seen"
    assert panel["preferences_url"] == f"{PI}/me/notifications"
    assert state["header"]["account"]["name"] == "Jo Smith"


def test_bell_context_notify_down_lists_catalog_alerts_without_ticks(fake_status):
    fake_status["result"] = {"show": True, "dot": False, "reachable": False, "events": [], "last": None}
    panel = alerts.bell_context(_user(), session_cookie="c", home_url=PI, app_id="pricewatch")["panel"]
    assert panel["events"] and all(e["ticked"] is None for e in panel["events"])


def test_bell_context_standalone_still_has_header_without_account():
    state = alerts.bell_context(_user(), session_cookie="c", home_url=PI, enabled=False, app_id="eventtrakr")
    assert state["show"] is False and state["panel"] is None
    assert state["header"]["account"] is None and state["header"]["apps"] == []


# -- rendering ----------------------------------------------------------------------


def test_header_tools_render_bell_panel_switcher_and_avatar(fake_status):
    state = alerts.bell_context(_user(), session_cookie="c", home_url=PI, app_id="pricewatch")
    html = _render('{{ header_tools(s, logout_action="/logout", csrf_token="tok") }}', s=state)
    assert 'data-sp-bell data-sp-app="pricewatch"' in html and "has-dot" in html
    assert "Target price reached" in html and "Cheaper offer from low-rated shop" in html
    assert "Lamp hit £40" in html and ">Preferences<" in html
    home = html[html.index('class="sp-app-dashboard'):]
    home = home[: home.index("</a>")]
    assert f'href="{PI}/"' in home and "<span>Home</span>" in home  # Dashboard, as "Home" with the house icon
    assert "aria-current" not in home  # current app is PriceWatch, not Home
    assert f'href="{PI}/news/"' in html
    assert "Jo Smith" in html and "Log out" in html and 'name="csrf_token" value="tok"' in html
    assert 'data-sp-theme="dark"' in html and "Manage account" in html


def test_header_tools_standalone_shows_avatar_with_logout_only():
    state = alerts.bell_context(None, session_cookie=None, home_url="", enabled=False, app_id="eventtrakr")
    html = _render('{{ header_tools(s, local_name="admin") }}', s=state)
    assert "data-sp-bell data-sp-app" not in html and "Switch app" not in html
    assert "Log out" in html and "Manage account" not in html


def test_header_search_and_fab():
    html = _render('{{ header_search("/search", "Search all events and articles", app="eventtrakr") }}{{ header_fab("/add-event", "Add") }}')
    assert 'action="/search"' in html and 'name="q"' in html and 'placeholder="Search all events and articles"' in html
    assert 'class="sp-fab" href="/add-event"' in html


def _panel_for(fake_status, **overrides):
    base = {
        "show": True, "dot": False, "reachable": True, "allowed": True, "available": True,
        "enabled": True, "needs_setup": False, "events": [], "last": None, "seen_at": None,
    }
    fake_status["result"] = {**base, **overrides}
    state = alerts.bell_context(_user(), session_cookie="c", home_url=PI, app_id="pricewatch")
    return state, _render("{{ header_tools(s) }}", s=state)


def test_panel_with_nothing_ticked_says_so_and_offers_setup(fake_status):
    state, html = _panel_for(fake_status, events=[{"id": "pricewatch.price_drop", "label": "Significant price drop", "ticked": False}])
    assert state["panel"]["state"] == "none_on"
    assert "None of PriceWatch&#39;s alerts are on for you yet." in html and ">Choose alerts<" in html
    assert "Last notification" not in html  # nothing to report: the message leads
    assert 'data-sp-trigger' in html  # still a panel, not a link straight to the page


def test_panel_never_set_up_or_app_without_alerts(fake_status):
    state, html = _panel_for(fake_status, needs_setup=True, enabled=False)
    assert state["panel"]["state"] == "needs_setup" and ">Set up phone alerts<" in html
    assert f'href="{PI}/me/notifications"' in html
    state, html = _panel_for(fake_status, events=[])
    assert state["panel"]["state"] == "no_events" and "PriceWatch has no alerts you can turn on yet." in html


def test_bell_hidden_until_admin_turns_alerts_on(fake_status):
    state, html = _panel_for(fake_status, show=False, available=False)
    assert state["show"] is False and "data-sp-bell data-sp-app" not in html


def _dashboard(fake_status, **overrides):
    base = {
        "show": True, "dot": False, "reachable": True, "allowed": True, "available": True,
        "enabled": True, "needs_setup": False, "events": [], "last": None, "recent": [], "seen_at": None,
    }
    fake_status["result"] = {**base, **overrides}
    state = alerts.bell_context(_user(), session_cookie="c", home_url=PI, app_id="dashboard")
    return state, _render("{{ header_tools(s) }}", s=state)


def test_dashboard_panel_is_an_overview_across_apps(fake_status):
    events = [
        {"id": "pricewatch.target_reached", "label": "Target price reached", "app": "pricewatch", "app_label": "PriceWatch", "ticked": True},
        {"id": "pricewatch.price_drop", "label": "Significant price drop", "app": "pricewatch", "app_label": "PriceWatch", "ticked": False},
        {"id": "newscast.publication_available", "label": "Paper published", "app": "newscast", "app_label": "NewsCast", "ticked": True},
    ]
    recent = [
        {"title": "Lamp hit £40", "at": "2026-10-10T08:00:00+00:00", "app": "pricewatch", "app_label": "PriceWatch", "ok": True, "silent": False, "message": ""},
        {"title": "Your paper is ready", "at": "2026-10-10T06:00:00+00:00", "app": "newscast", "app_label": "NewsCast", "ok": False, "silent": False, "message": "HTTP 500"},
    ]
    state, html = _dashboard(fake_status, events=events, recent=recent)
    assert fake_status["app"] == "dashboard" and state["panel"]["state"] == "ready"
    assert "Your alerts" in html and "On for you" in html
    assert "<strong>PriceWatch</strong><span>Target price reached</span>" in html
    assert "<strong>NewsCast</strong><span>Paper published</span>" in html
    assert "Significant price drop" not in html  # only what's on
    assert "Recent notifications" in html and "Lamp hit £40" in html and "Your paper is ready" in html
    assert "Didn&#39;t reach your phone: HTTP 500" in html
    assert state["panel"]["seen_url"] == f"{PI}/notify/api/me/apps/dashboard/seen"


def test_dashboard_panel_with_nothing_on_leads_with_message(fake_status):
    state, html = _dashboard(fake_status, events=[{"id": "x", "label": "X", "app": "pricewatch", "app_label": "PriceWatch", "ticked": False}])
    assert state["panel"]["state"] == "none_on"
    assert "None of your alerts are on yet." in html and ">Choose alerts<" in html
    assert "On for you" not in html and "Recent notifications" not in html


def test_empty_panels_say_so_in_plain_words(fake_status):
    ticked = [{"id": "pricewatch.target_reached", "label": "Target price reached", "ticked": True}]
    import html as html_mod

    _state, html = _panel_for(fake_status, events=ticked)
    html = html_mod.unescape(html)
    assert "No alerts from PriceWatch yet. When it sends you one, you'll see it here." in html
    assert "Nothing from" not in html
    fake_status["result"] = {**fake_status["result"], "recent": []}
    state = alerts.bell_context(_user(), session_cookie="c", home_url=PI, app_id="dashboard")
    html = html_mod.unescape(_render("{{ header_tools(s) }}", s=state))
    assert "No notifications yet. When one of your apps sends you something, you'll see it here." in html
    assert "Nothing sent to you" not in html


def test_panel_has_clear_button_and_hidden_empty_text(fake_status):
    _state, html = _panel_for(
        fake_status,
        events=[{"id": "pricewatch.target_reached", "label": "Target price reached", "ticked": True}],
        last={"title": "Lamp hit £40", "at": "2026-10-10T08:00:00+00:00", "ok": True, "silent": False, "message": ""},
    )
    assert "data-sp-clear>Clear</button>" in html
    assert f'data-sp-clear-url="{PI}/notify/api/me/apps/pricewatch/clear"' in html
    assert 'data-sp-item data-at="2026-10-10T08:00:00+00:00" data-app="pricewatch"' in html
    assert '<p class="sp-empty" data-sp-empty hidden>No alerts from PriceWatch yet.' in html
