"""Health headline: one app down names it; several collapse to "Multiple services are not running"."""

from __future__ import annotations

import stonepi_watch

from app import services


def _row(name, level="attention", enabled=True):
    return {"id": name.lower(), "n": name, "level": level, "enabled": enabled, "running": level == "healthy"}


def test_single_app_keeps_its_name():
    out = stonepi_watch.summarize(["NewsCast is not running"], [_row("NewsCast"), _row("Auth", "healthy")])
    assert out == {"summary": "NewsCast is not running", "summary_detail": ""}


def test_several_apps_collapse_with_names_in_detail():
    apps = [_row("NewsCast"), _row("Studio"), _row("PriceScout"), _row("Auth", "healthy")]
    reasons = ["NewsCast is not running", "Studio is not running", "PriceScout is not running"]
    out = stonepi_watch.summarize(reasons, apps)
    assert out["summary"] == "Multiple services are not running"
    assert out["summary_detail"] == "NewsCast, Studio, PriceScout"
    assert stonepi_watch.summary_text(out) == "Multiple services are not running (NewsCast, Studio, PriceScout)"


def test_other_first_reason_is_left_alone():
    # Auth down (critical) leads; the not-running apps stay in reasons, not the headline.
    apps = [_row("Auth", "critical"), _row("NewsCast"), _row("Studio")]
    out = stonepi_watch.summarize(["Auth is down", "NewsCast is not running", "Studio is not running"], apps)
    assert out == {"summary": "Auth is down", "summary_detail": ""}


def test_all_clear():
    assert stonepi_watch.summarize([], []) == {"summary": "All clear", "summary_detail": ""}
    assert stonepi_watch.summary_text({"summary": "All clear", "summary_detail": ""}) == "All clear"


def test_disabled_app_drops_out_of_the_group():
    watch = {
        "level": "attention",
        "apps": [_row("NewsCast"), _row("Studio"), _row("Auth", "healthy")],
        "backup_status": "ok",
        "backup_age_days": 0,
        "disk_pct": 10,
    }
    both = services.apply_disabled_to_watch(watch, set())
    assert both["summary"] == "Multiple services are not running"
    assert both["summary_detail"] == "NewsCast, Studio"
    one = services.apply_disabled_to_watch(watch, {"studio"})
    assert one["summary"] == "NewsCast is not running"
    assert one["summary_detail"] == ""

