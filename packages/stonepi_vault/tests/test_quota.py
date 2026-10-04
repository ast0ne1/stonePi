from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

import stonepi_vault.quota as quota_mod
from stonepi_vault.quota import Quota, QuotaExceeded, pace_allowance, period_bounds


def _at(monkeypatch, *args):
    monkeypatch.setattr(quota_mod, "_now", lambda: datetime(*args, tzinfo=timezone.utc))


def test_defaults_to_5000_and_limit_can_change(tmp_path):
    q = Quota("brightdata", tmp_path)
    assert q.status()["limit"] == 5000
    assert q.set_limit(8000)["limit"] == 8000
    assert Quota("brightdata", tmp_path).status()["limit"] == 8000
    assert q.set_limit(1000)["limit"] == 1000


def test_reserve_then_settle_counts_actual_per_app(tmp_path):
    q = Quota("brightdata", tmp_path)
    r = q.reserve("eventtrakr/facebook", 30)
    assert q.status()["used"] == 30
    r.settle(12)
    r.settle(99)  # second settle is ignored
    q.reserve("pricewatch/trustpilot", 5).settle(5)
    s = q.status()
    assert s["used"] == 17
    assert s["by_app"] == {"eventtrakr": 12, "pricewatch": 5}
    assert s["by_use"]["eventtrakr/facebook"] == 12


def test_limit_is_shared_and_hard(tmp_path):
    q = Quota("brightdata", tmp_path)
    q.set_limit(40)
    Quota("brightdata", tmp_path).reserve("eventtrakr/facebook", 30)  # another app's process
    with pytest.raises(QuotaExceeded) as exc:
        q.reserve("pricewatch/trustpilot", 11)
    assert exc.value.reason == "limit"
    q.reserve("pricewatch/trustpilot", 10)
    assert q.status()["remaining"] == 0


def test_paced_calls_keep_to_the_month_so_far(tmp_path, monkeypatch):
    _at(monkeypatch, 2026, 10, 3, 12)  # day 3 of 31 → 484 of 5000
    assert pace_allowance(5000, date(2026, 10, 3)) == 484
    q = Quota("brightdata", tmp_path)
    q.reserve("eventtrakr/instagram", 480, paced=True)
    with pytest.raises(QuotaExceeded) as exc:
        q.reserve("eventtrakr/instagram", 10, paced=True)
    assert exc.value.reason == "pace"
    q.reserve("eventtrakr/facebook-event", 10)  # manual action: only the hard limit applies


def test_new_month_resets_count_but_keeps_limit(tmp_path, monkeypatch):
    _at(monkeypatch, 2026, 10, 31, 23)
    q = Quota("brightdata", tmp_path)
    q.set_limit(3000)
    q.reserve("eventtrakr/facebook", 100)
    _at(monkeypatch, 2026, 11, 1, 0)
    s = q.status()
    assert (s["period_start"], s["used"], s["limit"]) == ("2026-11-01", 0, 3000)


def test_period_bounds_follow_the_reset_day():
    assert period_bounds(date(2026, 10, 3), 1) == (date(2026, 10, 1), date(2026, 11, 1))
    assert period_bounds(date(2026, 10, 3), 15) == (date(2026, 9, 15), date(2026, 10, 15))
    assert period_bounds(date(2026, 10, 15), 15) == (date(2026, 10, 15), date(2026, 11, 15))
    assert period_bounds(date(2026, 1, 10), 15) == (date(2025, 12, 15), date(2026, 1, 15))
    # 31st in a short month falls on its last day
    assert period_bounds(date(2027, 2, 28), 31) == (date(2027, 2, 28), date(2027, 3, 31))
    assert period_bounds(date(2027, 2, 27), 31) == (date(2027, 1, 31), date(2027, 2, 28))
    assert pace_allowance(3000, date(2026, 10, 17), 15) == 291  # day 3 of 31


def test_reset_day_moves_period_and_keeps_usage(tmp_path, monkeypatch):
    _at(monkeypatch, 2026, 10, 20, 9)
    q = Quota("brightdata", tmp_path)
    q.reserve("eventtrakr/facebook", 100)
    s = q.set_reset_day(15)
    assert (s["period_start"], s["resets_on"], s["used"]) == ("2026-10-15", "2026-11-15", 100)
    _at(monkeypatch, 2026, 11, 14, 23)
    assert q.status()["used"] == 100
    _at(monkeypatch, 2026, 11, 15, 0)
    assert (q.status()["used"], q.status()["reset_day"]) == (0, 15)


def test_use_allowance_inside_the_limit(tmp_path, monkeypatch):
    _at(monkeypatch, 2026, 10, 31, 12)  # last day: pace allows the whole allowance
    q = Quota("brightdata", tmp_path)
    q.set_use_limit("eventtrakr/instagram", 25)
    q.reserve("eventtrakr/instagram", 20).settle(20)
    with pytest.raises(QuotaExceeded):
        q.reserve("eventtrakr/instagram", 10)
    q.reserve("eventtrakr/facebook", 30)  # other uses only see the overall limit
    s = q.status()
    assert s["use_limits"]["eventtrakr/instagram"] == {"limit": 25, "used": 20, "remaining": 5, "pace_allowance": 25}
    assert q.set_use_limit("eventtrakr/instagram", None)["use_limits"] == {}
    # The default applies only once: a removed allowance stays removed
    assert q.ensure_use_limit("eventtrakr/instagram", 2000)["use_limits"] == {}
    assert q.ensure_use_limit("eventtrakr/other", 7)["use_limits"]["eventtrakr/other"]["limit"] == 7


def test_legacy_calendar_month_file_keeps_its_count(tmp_path, monkeypatch):
    _at(monkeypatch, 2026, 10, 3, 12)
    (tmp_path / "quota-brightdata.json").write_text(json.dumps({"month": "2026-10", "limit": 4000, "used": 42, "by_use": {"pricewatch/trustpilot": 42}}))
    s = Quota("brightdata", tmp_path).status()
    assert (s["used"], s["limit"], s["period_start"]) == (42, 4000, "2026-10-01")
