"""Reader (was Device): Status is read-only, Library manages files, Send gets things onto the reader."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import auth
from app.config import env
from app.db import get_db
from app.models import Base, LibraryFile, SyncTask, User
from app.routers import ui
from app.services import library, passwords, reader_push
from stonepi_auth.session import COOKIE_NAME, encode_session

OWNER = "5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
SECRET = "nc-test-secret"


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    user = User(auth_user_id=OWNER, username="adam", password=passwords.hash_password("test"), role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(auth, "_platform_session_secret", lambda: SECRET)
    monkeypatch.setattr(env, "stonepi_session_secret", SECRET)
    monkeypatch.setattr(ui, "bell_context", lambda user, **kw: {"show": False, "dot": False, "url": ""})
    app = FastAPI()
    app.include_router(ui.router)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=OWNER, username="adam", display_name="Adam", is_admin=True,
            apps=["newscast"], session_id="s",
        ),
    )
    return client, db, user, tmp_path


def _library_file(db: Session, user: User, folder, name: str = "book.epub") -> LibraryFile:
    (folder / str(user.id)).mkdir(exist_ok=True)
    (folder / str(user.id) / name).write_bytes(b"epub")
    item = LibraryFile(user_id=user.id, title="Book", original_name=name, stored_name=f"{user.id}/{name}", size=4)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_tabs_and_cards(ctx):
    client, _db, _user, _tmp = ctx
    status = client.get("/device?tab=status").text
    assert "<h1>Reader</h1>" in status
    assert 'href="/device?tab=library"' in status and 'href="/device?tab=send"' in status
    assert "Push to reader" not in status and 'action="/reader/publish"' not in status  # read-only now

    library_page = client.get("/device?tab=library").text
    assert "Add a file" in library_page and "Your files" in library_page
    assert "Also send to reader" in library_page

    send = client.get("/device?tab=send").text
    for heading in ("Reader</h2>", "Today’s paper</h2>", "Queue</h2>"):
        assert heading in send
    assert "Queue library" not in send and "Queue for later" not in send
    assert "Nothing waiting." in send
    assert "Add a file" not in send

    assert client.get("/library").headers["location"] == "/device?tab=library"


def test_library_marks_files_waiting_to_send(ctx):
    client, db, user, tmp = ctx
    item = _library_file(db, user, tmp)
    assert "Send to reader" in client.get("/device?tab=library").text
    library.enqueue_library_file(db, item)
    page = client.get("/device?tab=library").text
    assert "Waiting to send" in page
    assert f'action="/library/{item.id}/push"' not in page


def test_paper_send_to_reader_queues_only_the_paper(ctx, monkeypatch):
    client, db, user, tmp = ctx
    _library_file(db, user, tmp)
    calls: list[dict] = []

    def fake_enqueue(db, user_id=None, *, include_briefing=True, include_library=True):
        calls.append({"briefing": include_briefing, "library": include_library})
        return []

    monkeypatch.setattr(reader_push, "enqueue_briefing_and_library", fake_enqueue)
    response = client.post(
        "/reader/queue",
        data={"next": "/device?tab=send", "include_paper": "1", "include_library": "0"},
        headers={"Accept": "application/json"},
    )
    assert response.json()["ok"] is True
    assert calls == [{"briefing": True, "library": False}]
    assert "Generate it first" in response.json()["message"]  # nothing published in this test
    assert db.query(SyncTask).count() == 0


def test_search_page_outlet_picker(ctx):
    from datetime import datetime, timezone

    from app.models import Story

    client, db, user, _tmp = ctx
    now = datetime.now(timezone.utc)
    for key, title, source in (("1", "Ferry strike ends", "BBC World"), ("2", "Chip unveiled", "Hackaday")):
        db.add(Story(
            user_id=user.id, title=title, summary=title, source_name=source,
            canonical_url=f"https://example.com/{key}", content_hash=key, cluster_key=key,
            published_at=now, created_at=now,
        ))
    db.commit()

    blank = client.get("/search").text
    assert '<option value="BBC World"' in blank and "Hackaday (1)" in blank
    assert "Ferry strike ends" not in blank  # nothing searched yet

    picked = client.get("/search?source=Hackaday").text
    assert "Chip unveiled" in picked and "Ferry strike ends" not in picked
    assert 'value="Hackaday" selected' in picked
    assert "from <strong>Hackaday</strong>" in picked

    unknown = client.get("/search?source=Nope").text  # not one of your outlets
    assert "Chip unveiled" not in unknown and "from <strong>" not in unknown


def _fake_flush(online: bool, *, status: str = "complete", skipped: int = 0, error: str = ""):
    def flush(db, user_id=None):
        uploaded = 0
        if online:
            for task in db.query(SyncTask).filter(SyncTask.status == "pending").all():
                task.status = status
                task.error_message = error or None
                uploaded += 0 if (skipped or status != "complete") else 1
            db.commit()
        return {"ok": online, "online": online, "uploaded": uploaded, "skipped": skipped, "pending": 0}

    return flush


@pytest.mark.parametrize(
    ("online", "status", "skipped", "expected"),
    [
        (True, "complete", 0, "Book sent to the reader."),
        (True, "complete", 1, "Book is already on the reader."),
        (True, "failed", 0, "Couldn’t send book: disk full"),
        (False, "pending", 0, "Book is queued. It will send when the reader is on Wi-Fi."),
    ],
)
def test_library_send_to_reader_sends_straight_away(ctx, monkeypatch, online, status, skipped, expected):
    client, db, user, tmp = ctx
    item = _library_file(db, user, tmp)
    monkeypatch.setattr(reader_push, "flush_pending", _fake_flush(online, status=status, skipped=skipped, error="disk full"))
    response = client.post(f"/library/{item.id}/push", headers={"Accept": "application/json"})
    assert response.json()["message"] == expected


def test_paper_button_offers_send_again_once_on_reader(ctx, monkeypatch):
    client, _db, _user, _tmp = ctx
    monkeypatch.setattr(
        ui, "paper_status",
        lambda db, user_id=None: {"published": True, "empty": False, "story_count": 20, "message": "Ready."},
    )
    from app.services import delivery

    monkeypatch.setattr(delivery, "briefing_pushed_today", lambda db, day, user_id=None: False)
    assert "Send to reader</span>" in client.get("/device?tab=send").text
    monkeypatch.setattr(delivery, "briefing_pushed_today", lambda db, day, user_id=None: True)
    page = client.get("/device?tab=send").text
    assert "Send again</span>" in page and "It is on the reader." in page


def test_library_shows_file_state(ctx):
    from app.models import utcnow

    client, db, user, tmp = ctx
    item = _library_file(db, user, tmp)
    page = client.get("/device?tab=library").text
    assert "Send to reader</span>" in page and "On reader" not in page

    task = library.enqueue_library_file(db, item)
    page = client.get("/device?tab=library").text
    assert "Waiting to send" in page

    task.status = "complete"
    task.completed_at = utcnow()
    db.commit()
    page = client.get("/device?tab=library").text
    assert "On reader · sent" in page and "Send again</span>" in page

    task.removed_at = utcnow()  # pruned from the reader later
    db.commit()
    assert "Send to reader</span>" in client.get("/device?tab=library").text


def test_source_text_cleanup_setting_saves_and_shows(ctx):
    from app.models import Feed

    client, db, user, _tmp = ctx
    feed = Feed(user_id=user.id, name="The Age", url="https://www.theage.com.au/", type="webpage",
                homepage_url="https://www.theage.com.au/", enabled=True)
    db.add(feed)
    db.commit()
    page = client.get("/sources?tab=feeds").text
    assert 'name="text_cleanup"' in page and "· Strict cleanup ·" not in page

    response = client.post(
        f"/feeds/{feed.id}/schedule",
        data={"feed_type": "webpage", "homepage_url": "https://www.theage.com.au/", "text_cleanup": "strict"},
        headers={"Accept": "application/json"},
    )
    assert response.json()["ok"] is True
    db.expire_all()
    assert db.get(Feed, feed.id).text_cleanup == "strict"
    assert "· Strict cleanup ·" in client.get("/sources?tab=feeds").text


def test_send_queue_shows_item_status_and_recent(ctx):
    from app.models import utcnow

    client, db, user, tmp = ctx
    waiting = library.enqueue_library_file(db, _library_file(db, user, tmp, "waiting.epub"))
    retry = library.enqueue_library_file(db, _library_file(db, user, tmp, "retry.epub"))
    retry.attempts = 1
    retry.error_message = "disk full"
    retry.last_attempt_at = utcnow()
    sent = library.enqueue_library_file(db, _library_file(db, user, tmp, "sent.epub"))
    sent.status = "complete"
    sent.completed_at = utcnow()
    db.commit()
    reader_push.remember_probe(reader_push.reader_host(db, user_id=user.id), False)

    page = client.get("/device?tab=send").text

    assert f'data-queue-status="{waiting.task_id}"' in page
    assert "Waiting for reader · last tried" in page and "(asleep)" in page
    assert "Failed: disk full · Push now to retry" in page  # push-when-online is off
    assert "Recently</h3>" in page and "Sent " in page

    activity = client.get("/api/activity").json()["reader"]
    states = {item["task_id"]: item["state"] for item in activity["queue"]}
    assert states == {waiting.task_id: "waiting", retry.task_id: "retry"}
    assert activity["recent"][0]["task_id"] == sent.task_id and activity["sending"] is False


def test_check_reader_sends_queue_when_push_when_online_is_on(ctx, monkeypatch):
    from app.services import user_settings

    client, db, user, tmp = ctx
    library.enqueue_library_file(db, _library_file(db, user, tmp))
    monkeypatch.setattr(reader_push, "reader_reachable", lambda *a, **k: True)
    monkeypatch.setattr(reader_push, "flush_pending", _fake_flush(True))
    headers = {"Accept": "application/json"}

    message = client.post("/reader/poll", headers=headers).json()["message"]
    assert "Sent" not in message and reader_push.pending_crosspoint(db, user_id=user.id)

    user_settings.set_value(db, user.id, "reader_push_when_online", "1")
    message = client.post("/reader/poll", headers=headers).json()["message"]
    assert message.endswith("is online. Sent 1 file from the queue.")
    assert reader_push.pending_crosspoint(db, user_id=user.id) == []
