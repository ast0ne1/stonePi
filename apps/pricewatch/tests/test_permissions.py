"""Capabilities: Manage watches, Strike alerts, admin-only household and Bright Data spend.

Sessions decide what a signed-in person may do; Auth's roster decides for the
scheduler and alerts, which run without a cookie (None = unknown → as before).
"""

from __future__ import annotations

import pytest

from app import db, routes
from app.services import access, ntfy, strike, trust
from stonepi_auth.session import PlatformUser

from test_trust import OWNER, XM6, _csrf, _scores_on, _watch, client, pw  # noqa: F401 (fixtures)

ADMIN = "0f1e2d3c-0000-4000-8000-00000000a0a0"


def _sign_in(monkeypatch, *, admin: bool = False, user_id: str = OWNER, **caps) -> PlatformUser:
    user = PlatformUser(
        user_id=user_id, username="jo", display_name="Jo", is_admin=admin, apps=["pricewatch"],
        permissions={"pricewatch": dict(caps)},
    )
    monkeypatch.setattr(routes, "_session_secret", lambda: "s3cret")
    monkeypatch.setattr(routes, "_user", lambda request: user)
    return user


def _roster(monkeypatch, people: list[dict] | None):
    """Serve Auth's roster (None = Auth unreachable)."""
    monkeypatch.setattr(access, "session_secret", lambda: "s3cret")
    body = None if people is None else {"people": people}
    monkeypatch.setattr("stonepi_auth.internal.get_internal_json", lambda *a, **k: body)


def _person(uid: str, *, admin: bool = False, app: bool = True, **caps) -> dict:
    return {"id": uid, "is_admin": admin, "permissions": {"pricewatch": dict(caps)} if app else {}}


def _post(client, path: str, **data):
    return client.post(path, data={"csrf_token": _csrf(client), **data}, follow_redirects=False)


# -- Manage watches (session) ---------------------------------------------------------------


def test_member_without_manage_watches_can_view_but_not_change(client, monkeypatch):
    wid = _watch()
    _sign_in(monkeypatch, can_manage_watches=False)
    home = client.get("/").text
    assert "Sony WH-1000XM6" in home
    assert 'href="/watches/new"' not in home and 'action="/check-all"' not in home
    detail = client.get(f"/watches/{wid}")
    assert detail.status_code == 200
    assert f"/watches/{wid}/check" not in detail.text and f"/watches/{wid}/edit" not in detail.text
    assert "Manage watches" in detail.text

    # Search and compare fetch PriceRunner (and queue Bright Data lookups for new shops).
    assert client.get("/watches/new").status_code == 403
    assert client.get("/watches/new?q=sony").status_code == 403
    assert client.get(f"/compare?product_id={XM6}&source_id=mock").status_code == 403

    fetched = []
    monkeypatch.setattr(strike, "check_watch_now", lambda watch_id: fetched.append(watch_id) or {"ok": True})
    token = _csrf(client)
    for path, extra in (
        ("/watches/new", {"source_id": "mock", "product_id": XM6, "product_name": "X", "target_price": "1"}),
        (f"/watches/{wid}/edit", {"target_price": "1"}),
        (f"/watches/{wid}/pause", {}),
        (f"/watches/{wid}/resume", {}),
        (f"/watches/{wid}/rearm", {}),
        (f"/watches/{wid}/check", {}),
        ("/check-all", {}),
        (f"/watches/{wid}/delete", {}),
    ):
        resp = client.post(path, data={"csrf_token": token, **extra}, follow_redirects=False)
        assert resp.status_code == 403, path
    assert fetched == []
    assert len(db.list_watches()) == 1
    assert db.get_watch(wid)["target_price"] == 1700 and db.get_watch(wid)["status"] != "paused"


def test_member_with_default_manage_watches_can_search_compare_and_change(client, monkeypatch):
    _sign_in(monkeypatch)  # no explicit grant → catalog default (True)
    assert 'href="/watches/new"' in client.get("/").text
    assert client.get("/watches/new?q=sony").status_code == 200
    assert client.get(f"/compare?product_id={XM6}&source_id=mock").status_code == 200
    resp = _post(client, "/watches/new", source_id="mock", product_id=XM6, product_name="Sony", target_price="1700")
    wid = int(resp.headers["location"].split("/watches/")[1].split("?")[0])
    assert db.get_watch(wid)["user_key"] == OWNER
    assert "msg=" in _post(client, f"/watches/{wid}/pause").headers["location"]
    assert db.get_watch(wid)["status"] == "paused"
    _post(client, f"/watches/{wid}/delete")
    assert db.get_watch(wid) is None


def test_admin_check_all_skips_watches_of_revoked_owners(client, monkeypatch):
    mine = _watch(user_key=ADMIN)
    revoked = _watch(user_key=OWNER)
    _sign_in(monkeypatch, admin=True, user_id=ADMIN)
    _roster(monkeypatch, [_person(ADMIN, admin=True), _person(OWNER, can_manage_watches=False)])
    checked = []
    monkeypatch.setattr(strike, "check_watch_now", lambda watch_id: checked.append(watch_id) or {"ok": True})
    assert "msg=" in _post(client, "/check-all").headers["location"]
    assert checked == [mine]
    assert revoked not in checked


# -- scheduler (roster) ------------------------------------------------------------------


@pytest.fixture
def evaluated(pw, monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(strike, "evaluate_watch", lambda watch, force=False: seen.append(int(watch["id"])) or {"ok": True})
    return seen


def test_scheduler_skips_watches_whose_owner_lost_access(evaluated, monkeypatch):
    revoked = _watch(user_key=OWNER)
    gone = _watch(user_key="deleted-user")
    no_app = _watch(user_key="no-app-user")
    admin = _watch(user_key=ADMIN)
    local = _watch(user_key="local")
    allowed = _watch(user_key="member-ok")
    _roster(monkeypatch, [
        _person(OWNER, can_manage_watches=False),
        _person("no-app-user", app=False),
        _person(ADMIN, admin=True),
        _person("member-ok"),  # catalog default: can_manage_watches on
    ])
    summary = strike.check_due_watches()
    assert sorted(evaluated) == sorted([admin, local, allowed])
    assert summary["skipped"] == 3
    for wid in (revoked, gone, no_app):
        assert db.get_watch(wid) is not None  # kept, just not checked


def test_scheduler_checks_everyone_when_roster_is_unknown(evaluated, monkeypatch):
    ids = [_watch(user_key=OWNER), _watch(user_key="local")]
    _roster(monkeypatch, None)
    summary = strike.check_due_watches()
    assert sorted(evaluated) == sorted(ids)
    assert summary["skipped"] == 0


# -- Strike alerts (roster) ----------------------------------------------------------------

WATCH = {"id": 3, "product_name": "Headphones", "target_price": 900, "currency": "DKK", "user_key": OWNER}
OFFER = {"product_price": 850, "retailer": "Shop", "product_url": "https://shop.example/p"}


@pytest.mark.parametrize(
    "people, sends",
    [
        (None, True),  # Auth unknown → send as before
        ([_person(OWNER)], True),  # default "Strike alerts" on
        ([_person(OWNER, can_use_alerts=False)], False),
        ([_person(OWNER, app=False)], False),
        ([_person("someone-else")], False),  # disabled/deleted owner
        ([_person(OWNER, admin=True, can_use_alerts=False)], True),
    ],
)
def test_alerts_follow_roster_strike_alerts(pw, monkeypatch, people, sends):
    _roster(monkeypatch, people)
    assert ntfy.notify_strike(WATCH, OFFER) is sends
    assert ntfy.notify_low_rated({**WATCH, "min_trust_score": 4.0}, OFFER) is sends
    assert len(pw) == (2 if sends else 0)


def test_revoked_alerts_stop_on_a_scheduled_strike(pw, monkeypatch):
    wid = _watch(target_price=5000)  # every mock offer is a strike
    _roster(monkeypatch, [_person(OWNER, can_use_alerts=False)])
    result = strike.check_due_watches()["results"][0]
    assert result["qualifying"] and result["notified"] is False
    assert db.get_watch(wid)["status"] == "strike_found"
    assert pw == []


def test_alert_options_hidden_without_strike_alerts(client, monkeypatch):
    wid = _watch(min_trust_score=4.0, low_rated_mode="warn")
    _sign_in(monkeypatch, can_use_alerts=False)
    detail = client.get(f"/watches/{wid}").text
    assert "Alert me with a warning" not in detail
    assert 'name="low_rated_mode" value="warn"' in detail  # stored mode kept on save
    assert "Alert me with a warning" not in client.get("/watches/new?product_id=1&source_id=mock").text
    assert "Strike alerts are off for your account" in client.get("/settings?tab=notifications").text
    _sign_in(monkeypatch)
    assert "Alert me with a warning" in client.get(f"/watches/{wid}").text


# -- household settings: admin only ------------------------------------------------------------


def test_general_settings_are_admin_only(client, monkeypatch):
    db.set_setting("retention_days", "90")
    _sign_in(monkeypatch, can_manage_sources=True)
    html = client.get("/settings?tab=general").text
    assert "set by a StonePi admin" in html
    resp = _post(client, "/settings/general", retention_days="7", default_condition="used")
    assert "error=" in resp.headers["location"]
    assert db.get_setting("retention_days") == "90"
    _sign_in(monkeypatch, admin=True, user_id=ADMIN)
    resp = _post(client, "/settings/general", retention_days="30", default_condition="used")
    assert "msg=" in resp.headers["location"]
    assert db.get_setting("retention_days") == "30" and db.get_setting("default_condition") == "used"


# -- trust settings: spending Bright Data is admin-only ----------------------------------------


def test_source_manager_cannot_turn_on_or_schedule_lookups(client, monkeypatch):
    _sign_in(monkeypatch, can_manage_sources=True)
    html = client.get("/settings?tab=trust").text
    assert 'name="brightdata_enabled"' not in html and 'name="refresh_days"' not in html
    assert 'formaction="/settings/trust/refresh"' not in html
    started = []
    monkeypatch.setattr(trust, "refresh_in_background", lambda force=False: started.append(force) or True)
    for data in ({"brightdata_enabled": "1"}, {"refresh_days": "1"}):
        resp = _post(client, "/settings/trust", min_reviews="50", **data)
        assert "error=" in resp.headers["location"]
    assert db.get_setting("trust_brightdata_enabled") != "1"
    assert db.get_setting("trust_refresh_days") in ("", "7")
    # Non-spending display options stay with "Manage sources".
    resp = _post(client, "/settings/trust", pricerunner_fallback="1", min_reviews="10")
    assert "msg=" in resp.headers["location"]
    assert db.get_setting("trust_min_reviews") == "10" and db.get_setting("trust_pricerunner_fallback") == "1"
    assert db.get_setting("trust_brightdata_enabled") != "1"
    # Refresh now and per-shop re-queue spend records too.
    db.set_setting("trust_brightdata_enabled", "1")
    assert "error=" in _post(client, "/settings/trust/refresh").headers["location"]
    strike.fetch_offers("mock", XM6, use_cache=False)
    shop = db.list_merchants()[0]
    db.update_merchant_score(shop["source_id"], shop["merchant_id"], status="ok", score=4.0, review_count=100)
    for action in ("refetch", "save"):
        resp = _post(client, f"/settings/trust/retailers/{shop['source_id']}/{shop['merchant_id']}",
                     action=action, domain="other.dk")
        assert "error=" in resp.headers["location"]
    after = db.get_merchant(shop["source_id"], shop["merchant_id"])
    assert after["tp_status"] == "ok" and not after.get("domain_override")
    assert started == []


def test_source_manager_saving_display_options_keeps_lookups_on(client, monkeypatch):
    db.set_setting("trust_brightdata_enabled", "1")
    db.set_setting("trust_refresh_days", "14")
    _sign_in(monkeypatch, can_manage_sources=True)
    resp = _post(client, "/settings/trust", min_reviews="5")
    assert "msg=" in resp.headers["location"]
    assert db.get_setting("trust_brightdata_enabled") == "1" and db.get_setting("trust_refresh_days") == "14"


def test_admin_controls_lookups(client, monkeypatch):
    _sign_in(monkeypatch, admin=True, user_id=ADMIN)
    started = []
    monkeypatch.setattr(trust, "refresh_in_background", lambda force=False: started.append(force) or True)
    html = client.get("/settings?tab=trust").text
    assert 'name="brightdata_enabled"' in html and 'formaction="/settings/trust/refresh"' in html
    resp = _post(client, "/settings/trust", brightdata_enabled="1", refresh_days="3", min_reviews="50")
    assert "msg=" in resp.headers["location"]
    assert db.get_setting("trust_brightdata_enabled") == "1" and db.get_setting("trust_refresh_days") == "3"
    assert started == [True]
    # Admin unticking the box (absent from the form) turns lookups off.
    _post(client, "/settings/trust", refresh_days="3", min_reviews="50")
    assert db.get_setting("trust_brightdata_enabled") == "0"
    db.set_setting("trust_brightdata_enabled", "1")
    assert "msg=" in _post(client, "/settings/trust/refresh").headers["location"]
    strike.fetch_offers("mock", XM6, use_cache=False)
    shop = db.list_merchants()[0]
    resp = _post(client, f"/settings/trust/retailers/{shop['source_id']}/{shop['merchant_id']}", action="refetch")
    assert "msg=" in resp.headers["location"]
