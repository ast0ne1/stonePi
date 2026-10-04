"""0.0.7: retailer trust scores, low-rated offers, delivery toggle, Compare page."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, routes
from app.config import env
from app.services import strike, trust
from app.sources import brightdata_trustpilot as bd
from app.sources.pricerunner import _merchant_domain, _merchant_rating

OWNER = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
XM6 = "1000000001"  # mock: Example Electronics 1899 (TP 4.4), Nordic Audio 1949+49 (TP 3.9),
#                     Used Gear 1499+39 used (no TP), BudgetBay 1599+99 (TP 2.1, PR 4.9)


# -- PriceRunner merchant data ---------------------------------------------------------


def test_pricerunner_merchant_domain_and_rating():
    merlin = {
        "otcUrl": "https://app.klarna.com/one-time-card/start?merchantUrl=merlin.dk&origin=x",
        "rating": {"count": 172, "average": "4.7"},
    }
    assert _merchant_domain(merlin) == "merlin.dk"
    assert _merchant_rating(merlin) == (4.7, 172)
    # "0.0 (0)" and null mean no rating
    assert _merchant_rating({"rating": {"count": 0, "average": "0.0"}}) == (None, None)
    assert _merchant_rating({"rating": None}) == (None, None)
    assert _merchant_domain({"otcUrl": "https://x/?merchantUrl=www.Power.dk"}) == "power.dk"
    assert _merchant_domain({}) is None


# -- Bright Data parsing (shapes from the live test, 2026-09-30) ---------------------------

LIVE_NDJSON = "\n".join(
    json.dumps(r)
    for r in [
        {"input": {"url": "https://www.trustpilot.com/review/no-such-shop.dk"}, "error": "No data", "error_code": "dead_page"},
        {"company_overall_rating": 3.5, "company_total_reviews": 146737, "company_website": "https://www.elgiganten.dk",
         "url": "https://www.trustpilot.com/review/elgiganten.dk", "input": {"url": "https://www.trustpilot.com/review/elgiganten.dk"}},
        {"company_overall_rating": 4.3, "company_total_reviews": 2477, "company_website": "https://www.merlin.dk",
         "url": "https://www.trustpilot.com/review/merlin.dk", "input": {"url": "https://www.trustpilot.com/review/merlin.dk"}},
        # Extra review records for the same shop despite num_of_reviews_limit: 1
        {"company_overall_rating": 3.5, "company_total_reviews": 146737,
         "input": {"url": "https://www.trustpilot.com/review/elgiganten.dk"}},
    ]
)


def test_brightdata_ndjson_is_mapped_per_shop():
    records = bd.parse_records(LIVE_NDJSON)
    assert len(records) == 4
    results = bd.map_records(["elgiganten.dk", "merlin.dk", "no-such-shop.dk", "unseen.dk"], records)
    assert results["elgiganten.dk"].status == "ok"
    assert results["elgiganten.dk"].score == 3.5
    assert results["elgiganten.dk"].review_count == 146737
    assert results["merlin.dk"].score == 4.3
    assert results["no-such-shop.dk"].status == "not_found"
    assert results["unseen.dk"].status == "not_found"
    assert bd.parse_records(json.dumps([{"a": 1}])) == [{"a": 1}]


def test_brightdata_other_errors_are_errors():
    rec = {"input": {"url": "https://www.trustpilot.com/review/shop.dk"}, "error": "Blocked", "error_code": "blocked"}
    assert bd.map_records(["shop.dk"], [rec])["shop.dk"].status == "error"


# -- strike flows (mock source + fixture Trustpilot scores) --------------------------------


@pytest.fixture
def pw(tmp_path, monkeypatch):
    import app.config as cfg

    monkeypatch.setattr(env, "mock", True)
    for mod in (db, cfg):
        monkeypatch.setattr(mod, "DB_PATH", tmp_path / "t.sqlite", raising=False)
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path, raising=False)
    db.init_db()
    monkeypatch.setattr(trust, "_backoff_until", None)
    import stonepi_vault.quota as quota_mod

    real_get_quota = quota_mod.get_quota
    monkeypatch.setattr(quota_mod, "get_quota", lambda provider="brightdata", root=None: real_get_quota(provider, tmp_path / "vault"))
    sent: list[dict] = []
    import stonepi_contracts

    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: sent.append(ev.to_dict()) or True)
    return sent


def _watch(**kw) -> int:
    payload = {
        "user_key": OWNER,
        "source_id": "mock",
        "product_id": XM6,
        "product_name": "Sony WH-1000XM6",
        "target_price": 1700,
        "condition": "new",
        "in_stock_required": True,
        "include_delivery": False,
        "schedule_minutes": 360,
    }
    payload.update(kw)
    return db.create_watch(payload)


def _scores_on(*, fallback: bool = False):
    db.set_setting("trust_brightdata_enabled", "1")
    db.set_setting("trust_pricerunner_fallback", "1" if fallback else "0")
    strike.fetch_offers("mock", XM6, use_cache=False)  # records the shops
    assert trust.refresh_scores()["ok"]


def test_scores_are_looked_up_for_seen_shops(pw):
    _scores_on()
    merchants = {m["name"]: m for m in db.list_merchants()}
    assert merchants["BudgetBay"]["tp_score"] == 2.1
    assert merchants["Example Electronics"]["tp_status"] == "ok"
    assert merchants["Used Gear DK"]["tp_status"] == "not_found"


def test_low_rated_cheaper_offer_is_not_a_strike_and_ignore_sends_nothing(pw):
    _scores_on()
    wid = _watch(min_trust_score=4.0, low_rated_mode="ignore")
    result = strike.check_watch_now(wid)
    watch = db.get_watch(wid)
    assert result["qualifying"] == 0 and result["low_rated"] == 1
    assert watch["status"] == "watching"
    assert watch["low_rated"]["retailer"] == "BudgetBay"
    assert pw == []


def test_warn_mode_sends_one_warning(pw):
    _scores_on()
    wid = _watch(min_trust_score=4.0, low_rated_mode="warn")
    strike.check_watch_now(wid)
    assert [e["id"] for e in pw] == ["pricewatch.low_rated_offer"]
    assert pw[0]["severity"] == "warning"
    assert "BudgetBay" in pw[0]["summary"] and "Not counted as a strike" in pw[0]["summary"]
    strike.check_watch_now(wid)  # same shop, same price → no second warning
    assert len(pw) == 1
    assert db.get_watch(wid)["status"] == "watching"


def test_trusted_strike_carries_the_cheaper_low_rated_line(pw):
    _scores_on()
    wid = _watch(target_price=2000, min_trust_score=4.0, low_rated_mode="warn")
    strike.check_watch_now(wid)
    watch = db.get_watch(wid)
    assert watch["status"] == "strike_found"
    assert watch["strike"]["retailer"] == "Example Electronics"
    assert [e["id"] for e in pw] == ["pricewatch.target_reached"]  # no separate warning
    assert "★4.4 Trustpilot" in pw[0]["summary"]
    assert "Cheaper at BudgetBay" in pw[0]["summary"]


def test_without_minimum_every_shop_is_trusted(pw):
    _scores_on()
    wid = _watch()
    strike.check_watch_now(wid)
    watch = db.get_watch(wid)
    assert watch["strike"]["retailer"] == "BudgetBay"
    assert watch["low_rated"] is None


def test_include_delivery_compares_totals(pw):
    wid = _watch(target_price=1650, include_delivery=False)
    assert strike.check_watch_now(wid)["qualifying"] == 1  # BudgetBay 1599
    wid2 = _watch(target_price=1650, include_delivery=True)
    assert strike.check_watch_now(wid2)["qualifying"] == 0  # 1599 + 99 delivery
    assert db.get_watch(wid2)["current_lowest"] == 1698


def test_require_rating_treats_unrated_shops_as_low_rated(pw):
    _scores_on()
    # Used Gear DK (1499, used) has no Trustpilot page and no PriceRunner rating.
    common = dict(condition="used", target_price=1550, min_trust_score=3.5)
    lenient = _watch(**common, require_rating=False)
    strict = _watch(**common, require_rating=True)
    assert strike.check_watch_now(lenient)["qualifying"] == 1
    assert strike.check_watch_now(strict)["qualifying"] == 0
    assert db.get_watch(strict)["low_rated"]["retailer"] == "Used Gear DK"


def test_pricerunner_fallback_needs_enough_reviews(pw):
    db.set_setting("trust_pricerunner_fallback", "1")
    strike.fetch_offers("mock", XM6, use_cache=False)
    merchants = db.get_merchants("mock", ["m2", "m4"])
    settings = trust.trust_settings()
    nordic = trust.effective_score(merchants["m2"], settings)  # 5.0 from 3 reviews
    budget = trust.effective_score(merchants["m4"], settings)  # 4.9 from 88 reviews
    assert nordic["source"] == "pricerunner" and nordic["counts"] is False
    assert budget["counts"] is True
    # Fallback off → no rating at all
    db.set_setting("trust_pricerunner_fallback", "0")
    assert trust.effective_score(merchants["m4"], trust.trust_settings()) is None


def test_domain_override_requeues_and_survives_new_offers(pw):
    strike.fetch_offers("mock", XM6, use_cache=False)
    db.set_merchant_domain_override("mock", "m4", "budgetbay.se")
    strike.fetch_offers("mock", XM6, use_cache=False)
    m = db.get_merchant("mock", "m4")
    assert m["effective_domain"] == "budgetbay.se" and m["tp_status"] == "pending"


def test_monthly_cap_stops_lookups(pw, monkeypatch):
    monkeypatch.setattr(env, "mock", False)
    monkeypatch.setattr(trust, "get_api_key", lambda: "k")
    db.set_setting("trust_brightdata_enabled", "1")
    trust.set_limit(0)
    db.upsert_merchants("mock", [{"merchant_id": "x", "retailer": "X", "merchant_domain": "x.dk"}])
    called = []
    monkeypatch.setattr(bd, "fetch_scores", lambda *a, **k: called.append(a) or ({}, 0))
    assert trust.refresh_scores()["skipped"] == "cap reached"
    assert called == []


def test_lookup_settles_shared_usage_and_respects_pace(pw, monkeypatch):
    import stonepi_vault.quota as quota_mod

    # Fixed mid-period clock, so the result can't depend on the run date: the pace allowance is
    # the same every run, and the reservation and the settle always land in the same period (a
    # run crossing the reset day's midnight UTC would reset the count and leave used == 0).
    clock = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(quota_mod, "_now", lambda: clock)
    monkeypatch.setattr(env, "mock", False)
    monkeypatch.setattr(trust, "get_api_key", lambda: "k")
    db.set_setting("trust_brightdata_enabled", "1")
    db.upsert_merchants("mock", [{"merchant_id": "x", "retailer": "X", "merchant_domain": "x.dk"}])
    result = bd.TrustResult(status="ok", score=4.1, review_count=99)
    monkeypatch.setattr(bd, "fetch_scores", lambda key, domains: ({d: result for d in domains}, 2))
    assert trust.refresh_scores()["looked_up"] == 1
    usage = trust.usage()
    assert usage["used"] == 2 and usage["by_use"] == {"pricewatch/trustpilot": 2}


def test_refused_request_hands_records_back(pw, monkeypatch):
    monkeypatch.setattr(env, "mock", False)
    monkeypatch.setattr(trust, "get_api_key", lambda: "k")
    db.set_setting("trust_brightdata_enabled", "1")
    db.upsert_merchants("mock", [{"merchant_id": "x", "retailer": "X", "merchant_domain": "x.dk"}])

    def refused(*_a, **_k):
        raise bd.BrightDataError("HTTP 401", billed=False)

    monkeypatch.setattr(bd, "fetch_scores", refused)
    assert trust.refresh_scores()["ok"] is False
    assert trust.usage()["used"] == 0


def test_copy_key_from_eventtrakr(pw, monkeypatch):
    stored = {}
    monkeypatch.setattr(trust, "_vault_get", lambda name: {"BRIGHTDATA_API_KEY": "evt-key"}.get(name, stored.get(name, "")))
    monkeypatch.setattr(trust, "set_api_key", lambda value: stored.__setitem__(trust.KEY_NAME, value))
    assert trust.get_api_key() == ""  # never falls back to EventTrakr's key
    assert trust.copy_key_from_eventtrakr() is True
    assert trust.get_api_key() == "evt-key"


def test_existing_watches_migrate_to_product_price(tmp_path, monkeypatch):
    import sqlite3

    import app.config as cfg

    path = tmp_path / "old.sqlite"
    for mod in (db, cfg):
        monkeypatch.setattr(mod, "DB_PATH", path, raising=False)
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path, raising=False)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE watches (id INTEGER PRIMARY KEY, user_key TEXT, source_id TEXT, product_id TEXT,"
        " product_name TEXT, variant TEXT, manufacturer TEXT, image_url TEXT, product_url TEXT,"
        " market TEXT DEFAULT 'DK', currency TEXT DEFAULT 'DKK', target_price REAL, condition TEXT DEFAULT 'new',"
        " in_stock_required INTEGER DEFAULT 1, schedule_minutes INTEGER DEFAULT 360, status TEXT DEFAULT 'watching',"
        " current_lowest REAL, last_checked_at TEXT, next_check_at TEXT, last_error TEXT, strike_snapshot_json TEXT,"
        " strike_fingerprint TEXT, strike_at TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO watches (user_key, source_id, product_id, product_name, target_price, created_at, updated_at)"
        " VALUES ('local', 'mock', '1', 'Old', 100, 'x', 'x')"
    )
    conn.commit()
    conn.close()
    db.init_db()
    old = db.list_watches()[0]
    assert old["include_delivery"] is False
    assert old["min_trust_score"] is None and old["low_rated_mode"] == "ignore"


# -- pages ---------------------------------------------------------------------------------


@pytest.fixture
def client(pw, monkeypatch):
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _csrf(client) -> str:
    client.get("/settings?tab=general")
    from stonepi_auth.csrf import CSRF_COOKIE

    return client.cookies.get(CSRF_COOKIE)


def test_compare_lists_every_retailer_with_badges(client):
    _scores_on(fallback=True)
    html = client.get(f"/compare?product_id={XM6}&source_id=mock&name=Sony+WH-1000XM6").text
    for shop in ("Example Electronics", "Nordic Audio", "Used Gear DK", "BudgetBay"):
        assert shop in html
    assert "★ 4.4" in html and "trust-trustpilot" in html
    assert "Watch this" in html and "/watches/new?" in html
    trust_sorted = client.get(f"/compare?product_id={XM6}&source_id=mock&sort=trust").text
    assert trust_sorted.index("Example Electronics") < trust_sorted.index("BudgetBay")


def test_new_watch_form_and_create_with_trust_fields(client):
    html = client.get(f"/watches/new?product_id={XM6}&source_id=mock&name=Sony").text
    assert 'name="min_trust_score"' in html and 'name="include_delivery"' in html
    token = _csrf(client)
    resp = client.post(
        "/watches/new",
        data={
            "csrf_token": token, "source_id": "mock", "product_id": XM6, "product_name": "Sony",
            "target_price": "1700", "include_delivery": "1", "min_trust_score": "4.0",
            "require_rating": "1", "low_rated_mode": "warn",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    watch = db.list_watches()[0]
    assert watch["include_delivery"] and watch["min_trust_score"] == 4.0
    assert watch["require_rating"] and watch["low_rated_mode"] == "warn"


def test_trust_options_are_dropped_without_a_minimum(client):
    token = _csrf(client)
    client.post(
        "/watches/new",
        data={"csrf_token": token, "source_id": "mock", "product_id": XM6, "product_name": "Sony",
              "target_price": "1700", "min_trust_score": "", "require_rating": "1", "low_rated_mode": "warn"},
    )
    watch = db.list_watches()[0]
    assert watch["min_trust_score"] is None and not watch["require_rating"] and watch["low_rated_mode"] == "ignore"


def test_detail_shows_low_rated_card_and_edit(client):
    _scores_on()
    wid = _watch(min_trust_score=4.0)
    strike.check_watch_now(wid)
    html = client.get(f"/watches/{wid}").text
    assert "Cheaper, low-rated" in html and "below your minimum" in html
    token = _csrf(client)
    client.post(f"/watches/{wid}/edit", data={"csrf_token": token, "target_price": "2000", "min_trust_score": "4.0"})
    watch = db.get_watch(wid)
    assert watch["target_price"] == 2000 and watch["status"] == "strike_found"
    assert watch["strike"]["retailer"] == "Example Electronics"


def test_dashboard_row_shows_retailer_and_badge(client):
    _scores_on()
    wid = _watch(target_price=2000, min_trust_score=4.0)
    strike.check_watch_now(wid)
    html = client.get("/").text
    assert "at Example Electronics" in html and "★ 4.4" in html
    # The row price is the named shop's (1.899), not the cheaper low-rated BudgetBay (1.599)
    assert "1.899 DKK" in html and "1.599 DKK" not in html


def test_settings_trust_tab_and_retailer_override(client):
    _scores_on()
    html = client.get("/settings?tab=trust").text
    assert "Trust scores via Bright Data" in html and "BudgetBay" in html
    assert "PRICEWATCH_BRIGHTDATA_API_KEY" in html
    token = _csrf(client)
    client.post("/settings/trust/retailers/mock/m4", data={"csrf_token": token, "domain": "https://www.BudgetBay.se/x"})
    assert db.get_merchant("mock", "m4")["domain_override"] == "budgetbay.se"


def test_settings_shows_and_saves_shared_brightdata_limit(client):
    html = client.get("/settings?tab=trust").text
    assert "Bright Data monthly limit (records, all apps)" in html and 'value="5000"' in html
    token = _csrf(client)
    client.post("/settings/trust", data={"csrf_token": token, "brightdata_limit": "6000", "brightdata_reset_day": "12"})
    usage = trust.usage()
    assert (usage["limit"], usage["reset_day"]) == (6000, 12)
    assert "Resets on day of the month" in client.get("/settings?tab=trust").text


# -- review fixes ---------------------------------------------------------------------------


def _live_mode(monkeypatch):
    """Real Bright Data path (not the fixture), with a fake key and a fake client."""
    monkeypatch.setattr(env, "mock", False)
    monkeypatch.setattr(trust, "get_api_key", lambda: "k")
    db.set_setting("trust_brightdata_enabled", "1")
    db.upsert_merchants("mock", [{"merchant_id": "x", "retailer": "X", "merchant_domain": "x.dk"}])


def test_failed_call_keeps_shops_queued_and_backs_off(pw, monkeypatch):
    _live_mode(monkeypatch)
    calls = []

    def boom(*a, **k):
        calls.append(a)
        raise bd.BrightDataError("HTTP 401")

    monkeypatch.setattr(bd, "fetch_scores", boom)
    assert trust.refresh_scores()["ok"] is False
    assert db.get_merchant("mock", "x")["tp_status"] == "pending"  # not marked as looked up
    assert trust.refresh_scores()["skipped"] == "backing off"  # scheduled run waits
    assert len(calls) == 1
    # The Settings button ignores the pause; success clears it
    monkeypatch.setattr(bd, "fetch_scores", lambda key, domains: ({d: bd.TrustResult("ok", 4.0, 100) for d in domains}, 1))
    assert trust.refresh_scores(force=True)["looked_up"] == 1
    assert db.get_merchant("mock", "x")["tp_score"] == 4.0
    assert trust._backoff_until is None


def test_single_shop_error_is_retried_after_hours_not_weekly(pw, monkeypatch):
    _live_mode(monkeypatch)
    db.update_merchant_score("mock", "x", status="error", error="Blocked")
    assert db.merchants_due_for_score(refresh_days=7) == []  # just failed: wait
    with db.db() as conn:
        conn.execute("UPDATE merchants SET tp_fetched_at = ? WHERE merchant_id = 'x'",
                     (db._iso(db._utc_now() - __import__("datetime").timedelta(hours=7)),))
    assert [m["merchant_id"] for m in db.merchants_due_for_score(refresh_days=7)] == ["x"]


def test_scheduler_runs_lookups_in_background(monkeypatch):
    from app.services import schedule

    started = []
    monkeypatch.setattr(schedule.strike, "check_due_watches", lambda: {"checked": 0})
    monkeypatch.setattr(schedule.trust, "refresh_in_background", lambda **k: started.append(k) or True)
    monkeypatch.setattr(schedule.trust, "refresh_scores", lambda **k: pytest.fail("must not block the tick"))
    schedule._scheduled_tick()
    assert started == [{}]


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "//evil.example/x", "ftp://x/y"])
def test_compare_and_create_drop_non_http_links(client, bad):
    resp = client.get("/compare", params={"product_id": XM6, "source_id": "mock", "name": "Sony", "product_url": bad, "image_url": bad})
    assert resp.status_code == 200 and "Retailers" in resp.text
    assert bad not in resp.text
    token = _csrf(client)
    client.post(
        "/watches/new",
        data={"csrf_token": token, "source_id": "mock", "product_id": XM6, "product_name": "Sony",
              "target_price": "1700", "product_url": bad, "image_url": bad},
    )
    watch = db.list_watches()[0]
    assert watch["product_url"] is None and watch["image_url"] is None


def test_http_links_are_kept(client):
    url = "https://www.pricerunner.dk/pl/0-1000000001"
    resp = client.get("/compare", params={"product_id": XM6, "source_id": "mock", "name": "Sony", "product_url": url})
    assert resp.status_code == 200
    assert f'href="{url}"' in resp.text


def test_switching_ignore_to_warn_warns_about_the_known_offer(client, pw):
    _scores_on()
    wid = _watch(min_trust_score=4.0, low_rated_mode="ignore")
    strike.check_watch_now(wid)
    assert pw == [] and db.get_watch(wid)["low_rated"]
    token = _csrf(client)
    client.post(f"/watches/{wid}/edit", data={"csrf_token": token, "target_price": "1700",
                                              "min_trust_score": "4.0", "low_rated_mode": "warn"})
    assert [e["id"] for e in pw] == ["pricewatch.low_rated_offer"]
    # Saving again without changing the rules doesn't repeat it
    client.post(f"/watches/{wid}/edit", data={"csrf_token": token, "target_price": "1700",
                                              "min_trust_score": "4.0", "low_rated_mode": "warn"})
    assert len(pw) == 1


def test_form_hint_describes_few_reviews_correctly(client):
    html = client.get(f"/watches/new?product_id={XM6}&source_id=mock&name=Sony").text
    assert "don’t count" in html and "otherwise they’re allowed" in html


# -- review fixes (2026-10-03) ---------------------------------------------------------------


def _shift_prices(monkeypatch, retailer: str, delta: dict, **overrides):
    base = strike.fetch_offers_for_watch

    def fake(watch, *, use_cache=True):
        offers = [dict(o) for o in base(watch, use_cache=use_cache)]
        for o in offers:
            o.update(overrides)
            if o.get("retailer") == retailer:
                o["product_price"] = float(o["product_price"]) + delta["v"]
                if o.get("total_price") is not None:
                    o["total_price"] = float(o["total_price"]) + delta["v"]
        return offers

    monkeypatch.setattr(strike, "fetch_offers_for_watch", fake)


def test_low_rated_warning_does_not_repeat_when_price_bounces_back(pw, monkeypatch):
    _scores_on()
    delta = {"v": 0.0}
    _shift_prices(monkeypatch, "BudgetBay", delta)
    wid = _watch(min_trust_score=4.0, low_rated_mode="warn")
    strike.check_watch_now(wid)
    assert len(pw) == 1
    delta["v"] = 50  # 1599 → 1649
    strike.check_watch_now(wid)
    delta["v"] = 0  # back to the 1599 already warned about
    strike.check_watch_now(wid)
    assert len(pw) == 1
    delta["v"] = -50  # genuinely cheaper than the warning
    strike.check_watch_now(wid)
    assert len(pw) == 2


def test_current_lowest_falls_back_when_nothing_passes_the_filters(pw, monkeypatch):
    _shift_prices(monkeypatch, "", {"v": 0}, availability="out_of_stock")
    wid = _watch(in_stock_required=True)
    strike.check_watch_now(wid)
    assert db.get_watch(wid)["current_lowest"] is not None


def _User(user_id: str, *, admin: bool = False):
    from stonepi_auth.session import PlatformUser

    return PlatformUser(user_id=user_id, username=user_id, display_name=user_id, is_admin=admin, apps=["pricewatch"])


def test_watches_are_private_to_their_owner(client, monkeypatch):
    wid = _watch()
    token = _csrf(client)
    monkeypatch.setattr(routes, "_user", lambda request: _User("someone-else"))
    assert "Sony WH-1000XM6" not in client.get("/").text
    resp = client.get(f"/watches/{wid}", follow_redirects=False)
    assert "not+found" in resp.headers["location"].lower() or "not%20found" in resp.headers["location"].lower()
    client.post(f"/watches/{wid}/delete", data={"csrf_token": token}, follow_redirects=False)
    client.post(f"/watches/{wid}/edit", data={"csrf_token": token, "target_price": "1"}, follow_redirects=False)
    assert db.get_watch(wid)["target_price"] == 1700
    monkeypatch.setattr(routes, "_user", lambda request: _User(OWNER))
    assert "Sony WH-1000XM6" in client.get("/").text
    monkeypatch.setattr(routes, "_user", lambda request: _User("admin-id", admin=True))
    assert client.get(f"/watches/{wid}").status_code == 200


def test_schedule_is_limited_to_the_offered_options(client):
    token = _csrf(client)
    client.post(
        "/watches/new",
        data={"csrf_token": token, "source_id": "mock", "product_id": XM6, "product_name": "Sony",
              "target_price": "1700", "schedule_minutes": "0"},
        follow_redirects=False,
    )
    assert all(w["schedule_minutes"] == routes.DEFAULT_SCHEDULE_MINUTES for w in db.list_watches())


# -- shared Bright Data settings are admin-only ------------------------------------------------


def _as_source_manager(monkeypatch):
    from stonepi_auth.session import PlatformUser

    manager = PlatformUser(
        user_id="7",
        username="sam",
        display_name="Sam",
        is_admin=False,
        apps=["pricewatch"],
        permissions={"pricewatch": {"can_manage_sources": True}},
    )
    monkeypatch.setattr(routes, "_session_secret", lambda: "s3cret")
    monkeypatch.setattr(routes, "_user", lambda request: manager)


def test_source_manager_cannot_change_shared_brightdata_limit_or_key(client, monkeypatch):
    _as_source_manager(monkeypatch)
    html = client.get("/settings?tab=trust").text
    assert 'name="brightdata_limit"' not in html and 'name="brightdata_reset_day"' not in html
    assert 'action="/settings/trust/key"' not in html
    token = _csrf(client)
    before = trust.usage()
    resp = client.post(
        "/settings/trust",
        data={"csrf_token": token, "brightdata_limit": "1", "brightdata_reset_day": "3"},
        follow_redirects=False,
    )
    assert "error=" in resp.headers["location"]
    after = trust.usage()
    assert (after["limit"], after["reset_day"]) == (before["limit"], before["reset_day"])

    calls = []
    monkeypatch.setattr(trust, "copy_key_from_eventtrakr", lambda: calls.append("copy") or True)
    monkeypatch.setattr(trust, "set_api_key", lambda value: calls.append(("set", value)))
    for data in ({"action": "copy"}, {"action": "save", "api_key": "k"}, {"action": "remove"}):
        resp = client.post("/settings/trust/key", data={"csrf_token": token, **data}, follow_redirects=False)
        assert "error=" in resp.headers["location"]
    assert calls == []


def test_source_manager_can_still_toggle_trust_feature(client, monkeypatch):
    _as_source_manager(monkeypatch)
    token = _csrf(client)
    resp = client.post(
        "/settings/trust",
        data={"csrf_token": token, "pricerunner_fallback": "1", "min_reviews": "10", "refresh_days": "7"},
        follow_redirects=False,
    )
    assert "msg=" in resp.headers["location"]
    assert db.get_setting("trust_min_reviews") == "10"


def test_missing_session_secret_fails_closed_outside_dev(client, monkeypatch):
    monkeypatch.setattr(routes, "_session_secret", lambda: "")
    monkeypatch.setattr(routes, "_auth_optional", lambda: False)
    assert client.get("/settings?tab=trust").status_code == 503
