from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed
from app.services import settings
from app.services.system_stats import _db_file_size, _sqlite_path_from_url, status_health


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_status_health_rollup():
    db = _session()
    db.add(Feed(name="Ok", url="https://example.com/ok", enabled=True, last_error=None))
    db.add(Feed(name="Bad", url="https://example.com/bad", enabled=True, last_error="timeout"))
    db.add(Feed(name="Off", url="https://example.com/off", enabled=False, last_error="ignored"))
    db.commit()
    settings.set_value(db, "last_ai_error", "Summarise failed: boom")
    settings.set_value(db, "last_ai_error_at", "2026-09-14T10:00:00+00:00")
    health = status_health(db)
    assert health["failing_feeds"] == 1
    assert "1 enabled feed" in health["failing_label"]
    assert health["failing_items"][0]["name"] == "Bad"
    assert health["failing_items"][0]["error"] == "timeout"
    assert "boom" in health["ai_label"]
    assert health["disk_total"] > 0
    assert "free of" in health["disk_label"]


def test_sqlite_path_from_url():
    assert _sqlite_path_from_url("sqlite:////var/lib/stonepi/newscast/newscast.sqlite") == Path(
        "/var/lib/stonepi/newscast/newscast.sqlite"
    )
    resolved = _sqlite_path_from_url("sqlite:///./data/newscast.db")
    assert resolved is not None
    assert resolved.name == "newscast.db"


def test_db_file_size_reads_sqlite_name(tmp_path, monkeypatch):
    from app.services import system_stats

    db_file = tmp_path / "newscast.sqlite"
    db_file.write_bytes(b"x" * 2048)
    monkeypatch.setattr(system_stats.env, "database_url", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr(system_stats, "DATA_DIR", tmp_path)
    assert _db_file_size() >= 2048
