"""Personal alerts routing: approvals, audiences, fan-out, quiet hours."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

import stonepi_notify
from stonepi_notify import ingest as ingest_mod
from stonepi_notify import subscriptions as subs_mod

ADMIN = "11111111-aaaa"
ALICE = "22222222-bbbb"
BOB = "33333333-cccc"
PEOPLE = {
    ADMIN: {"is_admin": True, "phone_alerts": True},
    ALICE: {"is_admin": False, "phone_alerts": True},
    BOB: {"is_admin": False, "phone_alerts": True},
}
NOON = datetime(2026, 9, 27, 12, 0)
NIGHT = datetime(2026, 9, 27, 23, 30)


@pytest.fixture
def sent(monkeypatch, tmp_path: Path):
    """Configure stonepi_notify in tmp_path and capture ntfy publishes."""
    calls: list[dict] = []

    def fake_publish(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "message": "Sent.", "status": 200}

    stonepi_notify.configure(data_dir=tmp_path, people_fn=lambda: dict(PEOPLE))
    monkeypatch.setattr(ingest_mod, "publish_ntfy", fake_publish)
    stonepi_notify.save_destinations({"ntfy": {"enabled": True, "household_topic": ""}})
    return calls


def _approve(*event_ids: str, **flags) -> None:
    stonepi_notify.save_prefs({"events": {eid: {"approved": True, **flags} for eid in event_ids}})


def _subscribe(uid: str, *events: str, quiet: dict | None = None, enabled: bool = True) -> str:
    sub = stonepi_notify.enable_subscription(uid, username=uid.split("-")[0])
    stonepi_notify.save_subscription(
        uid,
        {"enabled": enabled, "events": {e: True for e in events}, "quiet_hours": quiet or {"enabled": False}},
    )
    return sub["topic"]


def _event(event_id: str, **extra) -> dict:
    return {"id": event_id, "source": event_id.split(".")[0], "title": "T", "severity": "success", **extra}


def _topics(calls) -> set[str]:
    return {c["topic"] for c in calls}


# -- approvals ----------------------------------------------------------------


def test_unknown_event_is_not_delivered_and_needs_review(sent):
    _subscribe(ALICE, "mystery.thing")
    result = stonepi_notify.ingest_event(_event("mystery.thing", severity="critical"), now=NOON)
    assert result["message"] == "not approved"
    assert sent == []
    assert "mystery.thing" in stonepi_notify.load_needs_review()


def test_explicitly_unapproved_event_is_not_reviewed(sent):
    stonepi_notify.save_prefs({"events": {"studio.site_published": False}})
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert sent == []
    assert stonepi_notify.load_needs_review() == {}


def test_approving_clears_review(sent):
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert "studio.site_published" in stonepi_notify.load_needs_review()
    _approve("studio.site_published")
    assert stonepi_notify.load_needs_review() == {}


def test_legacy_boolean_prefs_read_as_approved(tmp_path, sent):
    (tmp_path / "prefs.json").write_text(json.dumps({"events": {"studio.site_published": True}}))
    assert stonepi_notify.event_approval("studio.site_published") == {
        "approved": True,
        "admin_only": False,
        "urgent": False,
    }


def test_ntfy_off_delivers_nothing(sent):
    stonepi_notify.save_destinations({"ntfy": {"enabled": False}})
    _approve("studio.site_published")
    _subscribe(ALICE, "studio.site_published")
    result = stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert result["delivered"] is False
    assert sent == []


# -- audiences ----------------------------------------------------------------


def test_personal_event_reaches_only_owner(sent):
    _approve("pricewatch.price_drop")
    alice = _subscribe(ALICE, "pricewatch.price_drop")
    _subscribe(BOB, "pricewatch.price_drop")
    stonepi_notify.save_destinations({"ntfy": {"household_topic": "kitchen-tablet-topic"}})
    stonepi_notify.ingest_event(_event("pricewatch.price_drop", audience="personal", user=ALICE), now=NOON)
    assert _topics(sent) == {alice}


def test_personal_event_needs_owner_tick(sent):
    _approve("pricewatch.price_drop")
    _subscribe(ALICE)
    result = stonepi_notify.ingest_event(
        _event("pricewatch.price_drop", audience="personal", user=ALICE), now=NOON
    )
    assert result["message"] == "no recipients"
    assert sent == []


def test_personal_event_without_user_is_invalid(sent):
    _approve("pricewatch.price_drop")
    _subscribe(ALICE, "pricewatch.price_drop")
    result = stonepi_notify.ingest_event(_event("pricewatch.price_drop", audience="personal"), now=NOON)
    assert result["ok"] is False
    assert sent == []


def test_disabled_subscription_gets_nothing(sent):
    _approve("studio.site_published")
    _subscribe(ALICE, "studio.site_published", enabled=False)
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert sent == []


def test_household_event_fans_out_plus_household_channel(sent):
    _approve("studio.site_published")
    alice = _subscribe(ALICE, "studio.site_published")
    bob = _subscribe(BOB, "studio.site_published")
    _subscribe(ADMIN)  # not ticked
    stonepi_notify.save_destinations({"ntfy": {"household_topic": "kitchen-tablet-topic"}})
    result = stonepi_notify.ingest_event(_event("studio.site_published", audience="household"), now=NOON)
    assert _topics(sent) == {alice, bob, "kitchen-tablet-topic"}
    assert len(result["deliveries"]) == 3


def test_old_emitter_without_audience_is_household(sent):
    _approve("pricewatch.price_drop")
    alice = _subscribe(ALICE, "pricewatch.price_drop")
    bob = _subscribe(BOB, "pricewatch.price_drop")
    stonepi_notify.ingest_event(_event("pricewatch.price_drop"), now=NOON)
    assert _topics(sent) == {alice, bob}


def test_personal_event_never_goes_to_household_channel(sent):
    _approve("pricewatch.price_drop")
    stonepi_notify.save_destinations({"ntfy": {"household_topic": "kitchen-tablet-topic"}})
    stonepi_notify.ingest_event(_event("pricewatch.price_drop", audience="personal", user=ALICE), now=NOON)
    assert sent == []


def test_admin_event_reaches_only_admins(sent):
    _approve("system.disk_warning")
    admin = _subscribe(ADMIN, "system.disk_warning")
    _subscribe(ALICE, "system.disk_warning")
    stonepi_notify.save_destinations({"ntfy": {"household_topic": "kitchen-tablet-topic"}})
    # Catalog says admin even though the emitter doesn't send an audience yet.
    stonepi_notify.ingest_event(_event("system.disk_warning"), now=NOON)
    assert _topics(sent) == {admin}


def test_admin_only_approval_restricts_household_event(sent):
    _approve("studio.site_published", admin_only=True)
    admin = _subscribe(ADMIN, "studio.site_published")
    _subscribe(ALICE, "studio.site_published")
    stonepi_notify.ingest_event(_event("studio.site_published", audience="household"), now=NOON)
    assert _topics(sent) == {admin}


def test_revoked_phone_alerts_stop_delivery(sent, monkeypatch):
    _approve("studio.site_published")
    _subscribe(ALICE, "studio.site_published")
    bob = _subscribe(BOB, "studio.site_published")
    monkeypatch.setitem(PEOPLE, ALICE, {"is_admin": False, "phone_alerts": False})
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert _topics(sent) == {bob}


def test_disabled_user_gets_nothing(sent, monkeypatch):
    _approve("pricewatch.price_drop")
    _subscribe(ALICE, "pricewatch.price_drop")
    monkeypatch.delitem(PEOPLE, ALICE)  # disabled users are left out of the roster
    stonepi_notify.ingest_event(_event("pricewatch.price_drop", audience="personal", user=ALICE), now=NOON)
    assert sent == []


def test_roster_unavailable_falls_back_to_subscriptions(sent, tmp_path):
    stonepi_notify.configure(data_dir=tmp_path, people_fn=lambda: None)
    _approve("studio.site_published")
    alice = _subscribe(ALICE, "studio.site_published")
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert _topics(sent) == {alice}


def test_people_fn_error_falls_back_to_subscriptions(sent, tmp_path):
    def boom():
        raise RuntimeError("auth down")

    stonepi_notify.configure(data_dir=tmp_path, people_fn=boom)
    _approve("studio.site_published")
    alice = _subscribe(ALICE, "studio.site_published")
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert _topics(sent) == {alice}


def test_admin_snapshot_used_without_roster(sent, tmp_path):
    stonepi_notify.configure(data_dir=tmp_path)  # no people_fn
    _approve("system.disk_warning")
    admin = _subscribe(ADMIN, "system.disk_warning")
    stonepi_notify.save_subscription(ADMIN, {"is_admin": True})
    _subscribe(ALICE, "system.disk_warning")
    stonepi_notify.ingest_event(_event("system.disk_warning"), now=NOON)
    assert _topics(sent) == {admin}


def test_shared_topic_gets_one_message(sent):
    """Migration: household channel == first admin's topic -> deliver once."""
    _approve("studio.site_published")
    admin = _subscribe(ADMIN, "studio.site_published")
    stonepi_notify.save_destinations({"ntfy": {"household_topic": admin}})
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert [c["topic"] for c in sent] == [admin]


def test_dedupe_blocks_replay(sent):
    _approve("studio.site_published")
    _subscribe(ALICE, "studio.site_published")
    ev = _event("studio.site_published", dedupe_key="k1", timestamp="2026-09-27T10:00:00+00:00")
    stonepi_notify.ingest_event(ev, now=NOON)
    assert stonepi_notify.ingest_event(ev, now=NOON)["message"] == "deduped"
    assert len(sent) == 1


def test_history_records_once_with_redacted_topics(sent):
    _approve("studio.site_published")
    alice = _subscribe(ALICE, "studio.site_published")
    _subscribe(BOB, "studio.site_published")
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    history = stonepi_notify.load_history()
    assert len(history) == 1
    entry = history[0]
    assert len(entry["deliveries"]) == 2
    assert alice not in json.dumps(entry)
    assert all(d["topic"].endswith("…") for d in entry["deliveries"])


# -- quiet hours --------------------------------------------------------------

QUIET = {"enabled": True, "start": "22:00", "end": "07:00"}


def test_quiet_hours_delivers_silently(sent):
    _approve("studio.site_published")
    _subscribe(ALICE, "studio.site_published", quiet=QUIET)
    result = stonepi_notify.ingest_event(_event("studio.site_published"), now=NIGHT)
    assert sent[0]["priority"] == 1
    assert result["deliveries"][0]["silent"] is True


def test_quiet_hours_outside_window_rings(sent):
    _approve("studio.site_published")
    _subscribe(ALICE, "studio.site_published", quiet=QUIET)
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NOON)
    assert sent[0]["priority"] == 3


def test_quiet_hours_is_per_person(sent):
    _approve("studio.site_published")
    alice = _subscribe(ALICE, "studio.site_published", quiet=QUIET)
    bob = _subscribe(BOB, "studio.site_published")
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NIGHT)
    by_topic = {c["topic"]: c["priority"] for c in sent}
    assert by_topic == {alice: 1, bob: 3}


def test_urgent_admin_alert_breaks_through(sent):
    _approve("system.disk_warning", admin_only=True, urgent=True)
    _subscribe(ADMIN, "system.disk_warning", quiet=QUIET)
    stonepi_notify.ingest_event(_event("system.disk_warning", severity="critical"), now=NIGHT)
    assert sent[0]["priority"] == 5


def test_urgent_household_event_does_not_break_through(sent):
    _approve("studio.site_published", urgent=True)
    _subscribe(ALICE, "studio.site_published", quiet=QUIET)
    stonepi_notify.ingest_event(_event("studio.site_published"), now=NIGHT)
    assert sent[0]["priority"] == 1


@pytest.mark.parametrize(
    ("start", "end", "hhmm", "expected"),
    [
        ("22:00", "07:00", "22:00", True),  # start inclusive
        ("22:00", "07:00", "23:59", True),
        ("22:00", "07:00", "00:00", True),
        ("22:00", "07:00", "06:59", True),
        ("22:00", "07:00", "07:00", False),  # end exclusive
        ("22:00", "07:00", "21:59", False),
        ("13:00", "14:30", "13:00", True),  # same-day window
        ("13:00", "14:30", "14:29", True),
        ("13:00", "14:30", "14:30", False),
        ("13:00", "14:30", "12:59", False),
        ("08:00", "08:00", "08:00", False),  # empty window
    ],
)
def test_in_quiet_hours(start, end, hhmm, expected):
    hh, mm = map(int, hhmm.split(":"))
    now = datetime(2026, 9, 27, hh, mm)
    assert subs_mod.in_quiet_hours({"enabled": True, "start": start, "end": end}, now) is expected


def test_quiet_hours_off_or_bad_times():
    assert subs_mod.in_quiet_hours({"enabled": False, "start": "00:00", "end": "23:59"}, NOON) is False
    assert subs_mod.in_quiet_hours(None, NOON) is False
    # Garbage times fall back to 22:00-07:00.
    assert subs_mod.in_quiet_hours({"enabled": True, "start": "nope", "end": "25:00"}, NIGHT) is True


# -- subscriptions store ------------------------------------------------------


def test_topic_is_long_and_random(tmp_path):
    stonepi_notify.configure(data_dir=tmp_path)
    a = stonepi_notify.generate_topic("Adam Stone!")
    b = stonepi_notify.generate_topic("Adam Stone!")
    assert a != b
    assert a.startswith("stonepi-adamstone-")
    assert len(a.rsplit("-", 1)[1]) >= 16


def test_enable_rotate_disable(tmp_path):
    stonepi_notify.configure(data_dir=tmp_path)
    first = stonepi_notify.enable_subscription(ALICE, username="alice")
    assert first["enabled"] and first["topic"]
    again = stonepi_notify.enable_subscription(ALICE, username="alice")
    assert again["topic"] == first["topic"]
    rotated = stonepi_notify.rotate_topic(ALICE)
    assert rotated["topic"] != first["topic"]
    off = stonepi_notify.disable_subscription(ALICE)
    assert off["enabled"] is False
    assert off["topic"] == rotated["topic"]


def test_save_only_touches_own_record(tmp_path):
    stonepi_notify.configure(data_dir=tmp_path)
    stonepi_notify.enable_subscription(ALICE, username="alice")
    bob = stonepi_notify.enable_subscription(BOB, username="bob")
    stonepi_notify.save_subscription(ALICE, {"events": {"x.y": True}})
    assert stonepi_notify.get_subscription(BOB) == bob


def test_quiet_hours_merge_and_validate(tmp_path):
    stonepi_notify.configure(data_dir=tmp_path)
    stonepi_notify.save_subscription(ALICE, {"quiet_hours": {"enabled": True, "start": "7:5"}})
    sub = stonepi_notify.save_subscription(ALICE, {"quiet_hours": {"end": "6:30"}})
    assert sub["quiet_hours"] == {"enabled": True, "start": "22:00", "end": "06:30"}


def test_redact_topic():
    assert stonepi_notify.redact_topic("stonepi-adam-9f3k2q7wabcdefgh") == "stonepi-adam-9f…"
    assert stonepi_notify.redact_topic("stonepi") == "sto…"
    assert stonepi_notify.redact_topic("") == ""


# -- destinations: household channel migration --------------------------------


def test_legacy_topic_becomes_household_channel(tmp_path):
    (tmp_path / "destinations.json").write_text(json.dumps({"ntfy": {"enabled": True, "topic": "old-topic"}}))
    stonepi_notify.configure(data_dir=tmp_path)
    cfg = stonepi_notify.load_destinations()["ntfy"]
    assert cfg["household_topic"] == "old-topic"
    assert cfg["topic"] == "old-topic"


def test_saving_topic_alias_sets_household_channel(tmp_path):
    stonepi_notify.configure(data_dir=tmp_path)
    stonepi_notify.save_destinations({"ntfy": {"topic": "kitchen"}})
    assert stonepi_notify.load_destinations()["ntfy"]["household_topic"] == "kitchen"
    stonepi_notify.save_destinations({"ntfy": {"household_topic": ""}})
    assert stonepi_notify.load_destinations()["ntfy"]["household_topic"] == ""
