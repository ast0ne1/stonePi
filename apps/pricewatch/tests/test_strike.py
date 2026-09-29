from __future__ import annotations

from app import db
from app.config import env
from app.services import strike
from app.sources.mock import MockSource


def test_mock_search_and_offers():
    src = MockSource()
    hits = src.search("Sony")
    assert hits
    assert hits[0].product_id == "1000000001"
    offers = src.get_offers("1000000001")
    assert len(offers) >= 2
    assert offers[0].product_price > 0


def test_strike_flow_with_mock(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "mock", True)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.sqlite")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    # Reload path constants used by connect
    import app.config as cfg

    monkeypatch.setattr(cfg, "DB_PATH", tmp_path / "t.sqlite")
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    db.init_db()
    watch_id = db.create_watch(
        {
            "user_key": "local",
            "source_id": "mock",
            "product_id": "1000000001",
            "product_name": "Sony WH-1000XM6",
            "target_price": 2000,
            "condition": "new",
            "in_stock_required": True,
            "schedule_minutes": 360,
        }
    )
    result = strike.check_watch_now(watch_id)
    assert result["ok"]
    assert result["qualifying"] >= 1
    watch = db.get_watch(watch_id)
    assert watch["status"] == "strike_found"
    assert watch["strike"]
    # Second check should not crash; fingerprint suppresses noisy re-notify
    result2 = strike.check_watch_now(watch_id)
    assert result2["ok"]
