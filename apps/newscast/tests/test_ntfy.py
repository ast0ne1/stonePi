from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Story, User
from app.services import ntfy, passwords, reader_push, settings, user_settings
from app.services.briefing import enqueue_sync_file, publish_daily_briefing


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _patch_emit(monkeypatch, *, ok: bool = True) -> list[dict]:
    calls: list[dict] = []

    def _fake_emit(event, **kwargs):
        payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        calls.append(payload)
        return ok

    monkeypatch.setattr("stonepi_contracts.emit_event", _fake_emit)
    # Also patch the name used inside notify after import
    import stonepi_contracts

    monkeypatch.setattr(stonepi_contracts, "emit_event", _fake_emit)
    return calls


AUTH_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def _owner(db: Session, *, auth_user_id: str | None = AUTH_ID, **kw) -> User:
    """Local user 1 linked to a StonePi Auth account (the default publish owner)."""
    fields = {"username": "owner", "password": passwords.hash_password("oooo"), "role": "admin", "can_use_ntfy": True}
    fields.update(kw)
    user = User(id=1, auth_user_id=auth_user_id, **fields)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_notify_emits_and_is_idempotent(monkeypatch):
    db = _session()
    owner = _owner(db)
    calls = _patch_emit(monkeypatch)
    assert ntfy.notify(db, kind="publish", title="Home", body="Morning paper ready", user_id=owner.id) is True
    assert len(calls) == 1
    assert calls[0]["id"] == "newscast.publication_available"
    assert calls[0]["title"] == "Home"
    assert ntfy.notify(db, kind="publish", title="Home", body="again", user_id=owner.id) is False
    assert len(calls) == 1


def test_notify_skips_without_ntfy_permission(monkeypatch):
    db = _session()
    user = User(username="denied", password=passwords.hash_password("dddd"), role="user", can_use_ntfy=False)
    db.add(user)
    db.commit()
    db.refresh(user)
    calls = _patch_emit(monkeypatch)
    assert ntfy.notify(db, kind="publish", title="X", body="x", user_id=user.id) is False
    assert calls == []


def test_notify_allows_granted_user(monkeypatch):
    db = _session()
    user = _owner(db, username="ok", role="user")
    calls = _patch_emit(monkeypatch)
    assert ntfy.notify(db, kind="push", title="Pushed", body="On device", user_id=user.id) is True
    assert calls[0]["id"] == "newscast.push_available"


def test_notify_false_when_emit_fails(monkeypatch):
    db = _session()
    owner = _owner(db)
    _patch_emit(monkeypatch, ok=False)
    assert ntfy.notify(db, kind="publish", title="T", body="B", user_id=owner.id) is False


def test_publish_daily_briefing_notifies_once(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.services.briefing.BRIEFING_DIR", tmp_path)
    monkeypatch.setattr(
        "app.services.briefing.utcnow",
        lambda: datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc),
    )
    db = _session()
    _owner(db)
    when = datetime(2026, 9, 15, 5, 0, tzinfo=timezone.utc)
    db.add(
        Story(
            title="Headline",
            summary="Summary",
            source_name="BBC",
            canonical_url="https://example.com/a",
            content_hash="a",
            cluster_key="a",
            published_at=when,
            created_at=when,
            importance=3,
        )
    )
    db.commit()
    calls = _patch_emit(monkeypatch)
    now = datetime(2026, 9, 15, 7, 0)
    publish_daily_briefing(db, now=now, overwrite=True)
    publish_daily_briefing(db, now=now, overwrite=True)
    assert len(calls) == 1
    assert calls[0]["id"] == "newscast.publication_available"


def test_flush_pending_notifies_push_once(tmp_path: Path, monkeypatch):
    db = _session()
    _owner(db)
    day = date.today().isoformat()
    path = tmp_path / f"news-{day}.epub"
    path.write_bytes(b"epub")
    enqueue_sync_file(
        db,
        path,
        path.name,
        kind="crosspoint",
        save_path=f"/News/{path.name}",
    )
    calls = _patch_emit(monkeypatch)
    monkeypatch.setattr(reader_push, "reader_reachable", lambda _host, timeout=None, db=None, user_id=None: True)
    monkeypatch.setattr(reader_push, "upload_file", lambda host, file_path, dest, db=None, user_id=None: None)
    reader_push.flush_pending(db)
    assert len(calls) == 1
    assert calls[0]["id"] == "newscast.push_available"
    enqueue_sync_file(
        db,
        path,
        path.name,
        kind="crosspoint",
        save_path=f"/News/{path.name}",
    )
    reader_push.flush_pending(db)
    assert len(calls) == 1


def test_old_local_opt_out_no_longer_blocks(monkeypatch):
    """Per-person choices moved to the Notifications page; Notify filters, not NewsCast."""
    db = _session()
    user = _owner(db, username="prefs", role="user")
    user_settings.set_value(db, user.id, "ntfy_notify_on_publish", "0")
    calls = _patch_emit(monkeypatch)
    assert ntfy.notify(db, kind="publish", title="X", body="x", user_id=user.id) is True
    assert [c["id"] for c in calls] == ["newscast.publication_available"]
