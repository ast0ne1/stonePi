"""SQLite cards store (pinboard-canvas.md step 1): JSON import, pins on the displays, done."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

import pytest

from app import store


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")
    return tmp_path


def _cards(tmp_path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(tmp_path / store.DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM cards ORDER BY y, x").fetchall()
    finally:
        conn.close()


def test_json_board_is_imported_once_and_kept(fresh_store):
    today = date.today().isoformat()
    old = {
        "notices": [{"id": "n2", "text": "Newer", "pinned": True, "created_at": "2026-10-02T09:00:00+00:00"}],
        "reminders": [
            {
                "id": "r1",
                "text": "Bins",
                "due": today,
                "assignee": "Sam",
                "assignee_user": "u-sam",
                "created_at": "2026-10-01T09:00:00+00:00",
            }
        ],
        "settings": {"reminder_time": "07:30"},
        "alerts_sent": {"r1": today},
    }
    (fresh_store / "pinboard.json").write_text(json.dumps(old), encoding="utf-8")

    items = store.list_items()
    assert [n["id"] for n in items["notices"]] == ["n2"]
    rem = items["reminders"][0]
    assert (rem["id"], rem["assignee"], rem["assignee_user"], rem["done"]) == ("r1", "Sam", "u-sam", False)
    assert store.reminder_time() == "07:30"
    assert store.reminders_due_for_alert(date.today()) == []  # already alerted before the move
    assert not (fresh_store / "pinboard.json").exists()
    assert (fresh_store / "pinboard.json.migrated").exists()

    rows = _cards(fresh_store)
    # Newest first fills the Household grid left to right; everything keeps a pin.
    assert [r["id"] for r in rows] == ["n2", "r1"]
    assert {r["area_id"] for r in rows} == {store.HOUSEHOLD_AREA}
    assert rows[0]["x"] < rows[1]["x"] and rows[0]["y"] == rows[1]["y"]
    assert all(r["pin_color"] == "blue" and r["pin_style"] == "push" for r in rows)

    # A second start doesn't import again, even if an old file reappears.
    (fresh_store / "pinboard.json").write_text(json.dumps(old), encoding="utf-8")
    assert len(store.list_items()["notices"]) == 1


def test_legacy_board_json_is_imported(fresh_store):
    (fresh_store / "board.json").write_text(json.dumps({"notices": [{"id": "x", "text": "Old"}]}), encoding="utf-8")
    assert store.list_items()["notices"][0]["text"] == "Old"
    assert (fresh_store / "board.json.migrated").exists()


def test_unreadable_json_starts_empty_and_is_kept(fresh_store):
    (fresh_store / "pinboard.json").write_text("{not json", encoding="utf-8")
    assert store.list_items()["notices"] == []
    assert (fresh_store / "pinboard.json.migrated").read_text(encoding="utf-8") == "{not json"


def test_cards_fill_rows_and_grow_the_area(fresh_store):
    for i in range(store.PER_ROW + 1):
        store.add_notice(f"N{i}")
    rows = _cards(fresh_store)
    assert len({r["y"] for r in rows}) == 2
    conn = sqlite3.connect(fresh_store / store.DB_FILE)
    try:
        height = conn.execute("SELECT h FROM areas WHERE id = ?", (store.HOUSEHOLD_AREA,)).fetchone()[0]
    finally:
        conn.close()
    assert height >= max(r["y"] for r in rows) + store.CARD_H


def test_unpinned_note_stays_off_the_displays(fresh_store):
    store.add_notice("On the fridge")
    store.add_notice("Board only", pinned=False)
    assert store.display_payload()["lines"] == ["On the fridge"]
    titles = [i["title"] for i in store.panel_feed(10)["items"]]
    assert titles == ["On the fridge"]
    assert store.list_items()["notices"][0]["pinned"] is False  # still on the board


def test_due_reminder_shows_even_without_a_pin(fresh_store):
    store.add_reminder("Pay nursery", due=date.today().isoformat())
    with store.connect() as conn:
        conn.execute("UPDATE cards SET pin_color = '', pin_style = ''")
    store.add_reminder("Later", due=(date.today() + timedelta(days=3)).isoformat())
    with store.connect() as conn:
        conn.execute("UPDATE cards SET pin_color = '' WHERE json_extract(data, '$.text') = 'Later'")
    titles = [r["title"] for r in store.display_payload()["reminders"]]
    assert titles == ["Pay nursery"]


def test_red_pins_lead(fresh_store):
    store.add_notice("Red one")
    store.add_notice("Newest blue")
    with store.connect() as conn:
        conn.execute("UPDATE cards SET pin_color = 'red' WHERE json_extract(data, '$.text') = 'Red one'")
    assert store.display_payload()["lines"] == ["Red one", "Newest blue"]
    first = store.panel_feed(10)["items"][0]
    assert (first["title"], first["badge"]) == ("Red one", "Important")


def test_done_reminder_leaves_displays_alerts_and_count(fresh_store):
    today = date.today().isoformat()
    rem = store.add_reminder("Bins", due=today)
    store.add_notice("Hello")
    assert store.set_reminder_done(rem["id"]) is True
    payload = store.display_payload()
    assert payload["reminders"] == [] and payload["total"] == 1
    assert store.reminders_due_for_alert(date.today()) == []
    assert store.list_items()["reminders"][0]["done"] is True
    assert store.set_reminder_done("missing") is False


def test_delete_clears_alert_record(fresh_store):
    rem = store.add_reminder("Bins", due=date.today().isoformat())
    store.mark_reminder_alerted(rem["id"], rem["due"])
    assert store.delete_item("reminder", rem["id"]) is True
    assert store.list_items()["alerts_sent"] == {}
    assert store.delete_item("notice", rem["id"]) is False
