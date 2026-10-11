"""Background reader push: inside the push window, send the queue once the reader answers."""

from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, SyncTask, utcnow
from app.services import reader_config, reader_push, users
from app.services.briefing import enqueue_sync_file

HOST = "pat.local"


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _push_user(db: Session, *, push: bool = True) -> int:
    user = users.create_user(db, username="pat", password="pass1")
    reader_config.save_reader_settings(
        db,
        user,
        reader_device_value="xteink",
        reader_host_value=HOST,
        reader_upload_path_value="/News",
        reader_push_when_online=push,
    )
    return int(user.id)


def _queue(db: Session, uid: int, tmp_path: Path, name: str = "book.epub") -> SyncTask:
    path = tmp_path / name
    path.write_bytes(b"epub")
    return enqueue_sync_file(db, path, name, kind="crosspoint", save_path=f"/News/pat/{name}", user_id=uid)


def _reader(monkeypatch, *, online: bool, uploaded: list, probes: list, upload_error: Exception | None = None):
    def reachable(host, timeout=None, db=None, user_id=None):
        probes.append((host, timeout))
        return online

    def upload(host, path, dest, db=None, user_id=None):
        if upload_error is not None:
            raise upload_error
        uploaded.append(path.name)

    monkeypatch.setattr(reader_push, "reader_reachable", reachable)
    monkeypatch.setattr(reader_push, "detect_firmware", lambda host: None)
    monkeypatch.setattr(reader_push, "_http_file_names", lambda host, folder: set())
    monkeypatch.setattr(reader_push, "_http_delete", lambda host, device_path: True)
    monkeypatch.setattr(reader_push, "upload_file", upload)


def test_auto_push_sends_queue_when_reader_answers(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    task = _queue(db, uid, tmp_path)
    uploaded: list = []
    probes: list = []
    _reader(monkeypatch, online=True, uploaded=uploaded, probes=probes)

    results = reader_push.auto_push(db)

    assert uploaded == ["book.epub"]
    assert probes and probes[0] == (HOST, reader_push.AUTO_PROBE_TIMEOUT)  # cheap probe
    assert results[0]["uploaded"] == 1
    db.refresh(task)
    assert task.status == "complete" and task.attempts == 1 and task.last_attempt_at is not None
    assert reader_push.last_probe(HOST)["online"] is True
    items = reader_push.recent_items(db, user_id=uid)
    assert items[0]["state"] == "sent" and items[0]["status_label"].startswith("Sent ")


def test_auto_push_skips_asleep_reader_and_retries_on_scan_interval(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    task = _queue(db, uid, tmp_path)
    uploaded: list = []
    probes: list = []
    _reader(monkeypatch, online=False, uploaded=uploaded, probes=probes)

    reader_push.auto_push(db)

    db.refresh(task)
    assert task.status == "pending" and uploaded == []
    probe = reader_push.last_probe(HOST)
    assert probe["online"] is False and probe["at"] is not None
    item = reader_push.queue_items(db, user_id=uid)[0]
    assert item["state"] == "waiting"
    assert "Waiting for reader · last tried" in item["status_label"] and "(asleep)" in item["status_label"]
    assert "looking until" in item["status_label"]

    # Not due yet: the next tick leaves the reader alone.
    reader_push.auto_push(db)
    assert len(probes) == 1
    # After the scan interval (default 15 s) it looks again; the gap never stretches.
    reader_push.auto_push(db, now=utcnow() + timedelta(seconds=15))
    assert len(probes) == 2
    assert reader_push.last_probe(HOST)["next_try_at"] - reader_push.last_probe(HOST)["at"] == timedelta(seconds=15)


def test_scan_interval_is_configurable(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    from app.models import User

    reader_config.save_reader_settings(
        db, db.get(User, uid), reader_device_value="xteink", reader_host_value=HOST,
        reader_upload_path_value="/News", reader_push_when_online=True, reader_push_scan_value="30",
    )
    _queue(db, uid, tmp_path)
    probes: list = []
    _reader(monkeypatch, online=False, uploaded=[], probes=probes)

    reader_push.auto_push(db)
    reader_push.auto_push(db, now=utcnow() + timedelta(seconds=15))
    assert len(probes) == 1
    reader_push.auto_push(db, now=utcnow() + timedelta(seconds=30))
    assert len(probes) == 2


def _age(db: Session, task: SyncTask, minutes: int) -> None:
    task.created_at = utcnow() - timedelta(minutes=minutes)
    db.commit()


def _capture_alerts(monkeypatch) -> list:
    from app.services import ntfy

    alerts: list = []
    monkeypatch.setattr(ntfy, "notify", lambda db, **kw: alerts.append(kw) or True)
    return alerts


def test_window_closes_alerts_once_then_waits_for_push_now(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    task = _queue(db, uid, tmp_path)
    _age(db, task, 31)  # default window is 30 minutes
    probes: list = []
    _reader(monkeypatch, online=False, uploaded=[], probes=probes)
    alerts = _capture_alerts(monkeypatch)

    assert reader_push.auto_push(db) == []
    reader_push.auto_push(db)

    assert probes == []  # stopped looking
    assert len(alerts) == 1 and alerts[0]["kind"] == "missed" and alerts[0]["user_id"] == uid
    assert "Not delivered" in alerts[0]["body"]
    item = reader_push.queue_items(db, user_id=uid)[0]
    assert item["state"] == "missed" and "Push now" in item["status_label"]


def test_window_that_closed_long_ago_gets_no_alert(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    _age(db, _queue(db, uid, tmp_path), 180)  # e.g. still queued from before a restart
    _reader(monkeypatch, online=False, uploaded=[], probes=[])
    alerts = _capture_alerts(monkeypatch)

    reader_push.auto_push(db)
    assert alerts == []


def test_newly_queued_item_reopens_the_window(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    _age(db, _queue(db, uid, tmp_path, "old.epub"), 90)
    probes: list = []
    _reader(monkeypatch, online=True, uploaded=[], probes=probes)
    _capture_alerts(monkeypatch)

    reader_push.auto_push(db)
    assert probes == []
    _queue(db, uid, tmp_path, "new.epub")
    reader_push.auto_push(db)
    assert len(probes) == 1


def test_paper_only_trigger_ignores_send_files(tmp_path: Path, monkeypatch):
    from app.models import User

    db = _session()
    uid = _push_user(db)
    reader_config.save_reader_settings(
        db, db.get(User, uid), reader_device_value="xteink", reader_host_value=HOST,
        reader_upload_path_value="/News", reader_push_when_online=True, reader_push_trigger_value="paper",
    )
    library = tmp_path / "library"
    library.mkdir()
    _queue(db, uid, library, "book.epub")
    probes: list = []
    _reader(monkeypatch, online=True, uploaded=[], probes=probes)

    assert reader_push.auto_push(db) == []
    assert probes == []


def test_auto_push_does_not_double_run_while_push_in_progress(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    task = _queue(db, uid, tmp_path)
    uploaded: list = []
    probes: list = []
    _reader(monkeypatch, online=True, uploaded=uploaded, probes=probes)

    lock = reader_push._push_lock(HOST)
    lock.acquire()
    try:
        assert reader_push.push_in_progress(HOST)
        assert reader_push.auto_push(db) == []
        busy = reader_push.flush_pending(db, user_id=uid, wait=False)
        assert busy["busy"] is True and busy["ok"] is False
    finally:
        lock.release()
    assert probes == [] and uploaded == []
    db.refresh(task)
    assert task.status == "pending"

    reader_push.auto_push(db)
    assert uploaded == ["book.epub"]


def test_auto_push_respects_push_when_online_off(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db, push=False)
    _queue(db, uid, tmp_path)
    uploaded: list = []
    probes: list = []
    _reader(monkeypatch, online=True, uploaded=uploaded, probes=probes)

    assert reader_push.auto_push(db) == []
    assert probes == [] and uploaded == []
    item = reader_push.queue_items(db, user_id=uid)[0]
    assert "next try" not in item["status_label"]


def test_auto_push_skips_probe_with_nothing_queued(monkeypatch):
    db = _session()
    _push_user(db)
    probes: list = []
    _reader(monkeypatch, online=True, uploaded=[], probes=probes)
    assert reader_push.auto_push(db) == []
    assert probes == []


def test_failed_upload_stays_queued_and_retries(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    task = _queue(db, uid, tmp_path)
    uploaded: list = []
    _reader(monkeypatch, online=True, uploaded=uploaded, probes=[], upload_error=RuntimeError("disk full"))

    result = reader_push.flush_pending(db, user_id=uid)

    assert result["errors"] == ["disk full"]
    db.refresh(task)
    assert task.status == "pending" and task.attempts == 1 and task.error_message == "disk full"
    item = reader_push.queue_items(db, user_id=uid)[0]
    assert item["state"] == "retry" and item["status_label"].startswith("Failed: disk full · will retry")

    for _ in range(reader_push.MAX_UPLOAD_ATTEMPTS - 1):
        reader_push.flush_pending(db, user_id=uid)
    db.refresh(task)
    assert task.status == "failed" and task.attempts == reader_push.MAX_UPLOAD_ATTEMPTS
    assert reader_push.recent_items(db, user_id=uid)[0]["status_label"] == "Failed: disk full"


def test_reader_sleeping_mid_push_is_not_a_failed_attempt(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    first = _queue(db, uid, tmp_path, "a.epub")
    second = _queue(db, uid, tmp_path, "b.epub")
    answers = iter([True, False])  # awake for the push, gone by the re-check
    monkeypatch.setattr(reader_push, "reader_reachable", lambda *a, **k: next(answers))
    monkeypatch.setattr(reader_push, "detect_firmware", lambda host: None)
    monkeypatch.setattr(reader_push, "_http_file_names", lambda host, folder: set())
    attempted: list = []

    def upload(host, path, dest, db=None, user_id=None):
        attempted.append(path.name)
        raise RuntimeError("Could not reach pat.local")

    monkeypatch.setattr(reader_push, "upload_file", upload)

    reader_push.flush_pending(db, user_id=uid)

    assert attempted == ["a.epub"]  # stopped instead of timing out on every file
    db.refresh(first)
    db.refresh(second)
    assert first.status == second.status == "pending"
    assert (first.attempts or 0) == 0 and first.last_attempt_at is not None
    assert reader_push.last_probe(HOST)["online"] is False


def test_file_on_reader_from_elsewhere_fails_without_retry(tmp_path: Path, monkeypatch):
    db = _session()
    uid = _push_user(db)
    task = _queue(db, uid, tmp_path)
    conflict = reader_push.ReaderConflict("book.epub is already on the reader")
    _reader(monkeypatch, online=True, uploaded=[], probes=[], upload_error=conflict)

    reader_push.flush_pending(db, user_id=uid)

    db.refresh(task)
    assert task.status == "failed" and task.attempts == 1


def test_paper_only_send_files_say_how_they_go_out(tmp_path: Path, monkeypatch):
    from app.models import User

    db = _session()
    uid = _push_user(db)
    reader_config.save_reader_settings(
        db, db.get(User, uid), reader_device_value="xteink", reader_host_value=HOST,
        reader_upload_path_value="/News", reader_push_when_online=True, reader_push_trigger_value="paper",
    )
    library = tmp_path / "library"
    library.mkdir()
    _queue(db, uid, library, "book.epub")
    item = reader_push.queue_items(db, user_id=uid)[0]
    assert item["state"] == "waiting" and "next paper" in item["status_label"] and "Push now" in item["status_label"]


def test_push_window_settings_copy_to_new_accounts():
    from app.models import User

    db = _session()
    admin = users.create_user(db, username="admin", password="pass1", role="admin")
    reader_config.save_reader_settings(
        db, admin, reader_device_value="xteink", reader_host_value=HOST, reader_upload_path_value="/News",
        reader_push_when_online=True, reader_push_window_value="60", reader_push_scan_value="30",
        reader_push_trigger_value="paper",
    )
    member = users.create_user(db, username="sam", password="pass2")
    reader_config.copy_admin_reader_settings(db, db.get(User, member.id))
    assert reader_config.reader_push_window_minutes(db, member.id) == 60
    assert reader_config.reader_push_scan_seconds(db, member.id) == 30
    assert reader_config.reader_push_trigger(db, member.id) == "paper"
