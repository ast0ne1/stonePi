"""Tests for PriceScout publication notifications."""

from __future__ import annotations

from app import db
from app.services import notify as notify_service


def test_note_source_catalogs_seeds_then_emits(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ps.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "CACHE_DIR", tmp_path / "cache")
    db.init_db()
    db.set_pref("local", notify_service.SEEN_KEY, "{}")

    emitted: list[object] = []

    def _fake_emit(envelope):
        emitted.append(envelope)
        return True

    monkeypatch.setattr("stonepi_contracts.emit_event", _fake_emit)

    rows = [
        {
            "title": "Milk",
            "catalog_url": "https://etilbudsavis.dk/?publication=cat-1",
            "raw": {"catalog_id": "cat-1"},
        },
        {
            "title": "Bread",
            "catalog_url": "https://etilbudsavis.dk/?publication=cat-1",
            "raw": {"catalog_id": "cat-1"},
        },
    ]
    first = notify_service.note_source_catalogs("netto", rows, store_name="Netto")
    assert first["seeded"] is True
    assert first["notified"] == 0
    assert emitted == []

    rows2 = rows + [
        {
            "title": "Cheese",
            "catalog_url": "https://etilbudsavis.dk/?publication=cat-2",
            "raw": {"catalog_id": "cat-2"},
        }
    ]
    second = notify_service.note_source_catalogs("netto", rows2, store_name="Netto")
    assert second["new"] == 1
    assert second["notified"] == 1
    assert len(emitted) == 1
    assert emitted[0].id == "pricescout.publication_released"
    assert "Netto" in emitted[0].title
