"""AgentLink reads (Pebble): signed internal endpoint, per platform user."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from stonepi_auth.internal import sign_internal_v2

from app import create_app
from app.db import SessionLocal
from app.models import Event, User
from app.services import auth

SECRET = "agent-test-secret"
PATH = "/api/internal/agent"


def _post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", **sign_internal_v2(secret, "POST", PATH, body)}
    return client.post(PATH, data=body, headers=headers)


def _setup(monkeypatch):
    monkeypatch.setattr(auth, "_platform_session_secret", lambda: SECRET)
    app = create_app()
    app.config["TESTING"] = True
    platform_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        me = User(username=f"agent-{platform_id[:8]}", role="user", auth_user_id=platform_id)
        other = User(username=f"other-{other_id[:8]}", role="user", auth_user_id=other_id)
        db.add_all([me, other])
        db.flush()
        tag = platform_id[:6]
        db.add_all([
            Event(user_id=me.id, title=f"Jazz in the park {tag}", start_time=now + timedelta(days=1),
                  location="Fælledparken", category="Music", cost="Free", fingerprint=f"a{tag}"),
            Event(user_id=me.id, title=f"Starred talk {tag}", start_time=now + timedelta(days=3),
                  is_favourited=True, fingerprint=f"b{tag}"),
            Event(user_id=me.id, title=f"Cancelled {tag}", start_time=now + timedelta(days=1),
                  is_cancelled=True, fingerprint=f"c{tag}"),
            Event(user_id=me.id, title=f"Far away {tag}", start_time=now + timedelta(days=20), fingerprint=f"d{tag}"),
            Event(user_id=other.id, title=f"Not mine {tag}", start_time=now + timedelta(days=1), fingerprint=f"e{tag}"),
        ])
        db.commit()
    return app.test_client(), platform_id, tag


def test_unsigned_or_wrongly_signed_is_404(monkeypatch):
    client, uid, _tag = _setup(monkeypatch)
    assert client.post(PATH, json={"tool": "get_events", "user": uid}).status_code == 404
    assert _post(client, {"tool": "get_events", "user": uid}, secret="wrong").status_code == 404


def test_get_events_is_scoped_to_the_user(monkeypatch):
    client, uid, tag = _setup(monkeypatch)
    payload = _post(client, {"tool": "get_events", "user": uid, "params": {"days": 7}}).get_json()["payload"]
    titles = [e["title"] for e in payload["events"]]
    assert titles == [f"Jazz in the park {tag}", f"Starred talk {tag}"]
    jazz = payload["events"][0]
    assert jazz["location"] == "Fælledparken" and jazz["price"] == "Free" and not jazz["favourite"]
    assert payload["events"][1]["favourite"] is True
    wide = _post(client, {"tool": "get_events", "user": uid, "params": {"days": 30}}).get_json()["payload"]
    assert f"Far away {tag}" in [e["title"] for e in wide["events"]]


def test_search_events(monkeypatch):
    client, uid, tag = _setup(monkeypatch)
    payload = _post(client, {"tool": "search_events", "user": uid, "params": {"query": "jazz"}}).get_json()["payload"]
    assert [e["title"] for e in payload["events"]] == [f"Jazz in the park {tag}"]
    assert _post(client, {"tool": "search_events", "user": uid, "params": {"query": "not mine"}}).get_json()["payload"]["events"] == []


def test_unknown_user_and_tool(monkeypatch):
    client, _uid, _tag = _setup(monkeypatch)
    unknown = _post(client, {"tool": "get_events", "user": str(uuid.uuid4())}).get_json()
    assert unknown["ok"] and unknown["payload"]["events"] == []
    assert _post(client, {"tool": "delete_everything", "user": "x"}).status_code == 400


def test_get_events_by_window_and_category(monkeypatch):
    client, uid, tag = _setup(monkeypatch)

    def ask(**params):
        return _post(client, {"tool": "get_events", "user": uid, "params": params}).get_json()["payload"]

    tomorrow = ask(when="tomorrow")
    assert tomorrow["when"] == "tomorrow" and [e["title"] for e in tomorrow["events"]] == [f"Jazz in the park {tag}"]
    week = ask(days=7)
    assert {"name": "Music", "events": 1} in week["categories"]
    music = ask(days=7, category="music")
    assert music["category"] == "Music" and [e["title"] for e in music["events"]] == [f"Jazz in the park {tag}"]
    missing = ask(days=7, category="community")
    assert missing["category_not_found"] == "community" and missing["total"] == 2  # nothing hidden
    assert ask(when="someday")["when_not_understood"] == "someday"


def test_named_windows_in_local_time():
    from app.routes import agent

    # A Wednesday afternoon (local time).
    now = datetime(2026, 10, 21, 15, 0).astimezone()
    def local(when):
        start, end, _label = agent._window(when, now.astimezone(timezone.utc))
        return start.astimezone(now.tzinfo), end.astimezone(now.tzinfo)

    start, end = local("tonight")
    assert (start.hour, start.day, end.day, end.hour) == (16, 21, 22, 4)
    start, end = local("tomorrow")
    assert (start.day, start.hour, end.day) == (22, 0, 23)
    start, end = local("this_weekend")
    assert (start.day, start.weekday(), end.day) == (24, 5, 26)
    start, end = local("next_week")
    assert (start.day, start.weekday(), end.day) == (26, 0, 2)
    assert agent._match_category("communities", ["Music", "Community & Culture"]) == "Community & Culture"
    assert agent._match_category("kids", ["Music"]) is None
