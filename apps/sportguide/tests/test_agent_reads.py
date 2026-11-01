"""AgentLink reads (Pebble): signed internal endpoint; what's on and the person's own teams."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from stonepi_auth.internal import sign_internal_v2

from app import agent_routes, db

SECRET = "agent-test-secret"
PATH = "/api/internal/agent"
ME, JO = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
TZ = "Europe/London"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sport.sqlite")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    db.set_pref(ME, "timezone", TZ)
    monkeypatch.setattr(agent_routes, "_session_secret", lambda: SECRET)
    app = FastAPI()
    app.include_router(agent_routes.router)
    return TestClient(app)


def _post(client, tool, user=ME, params=None, secret=SECRET):
    body = json.dumps({"tool": tool, "user": user, "params": params or {}}).encode()
    return client.post(PATH, content=body, headers={"Content-Type": "application/json", **sign_internal_v2(secret, "POST", PATH, body)})


def _row(ext, title, hours, sport="football", league="Premier League", channels=("Sky Sports",)):
    starts = datetime.now(timezone.utc) + timedelta(hours=hours)
    return {"external_id": ext, "sport": sport, "league": league, "title": title,
            "starts_at": starts.strftime("%Y-%m-%dT%H:%M:%SZ"), "channels": list(channels)}


def test_unsigned_is_404(client):
    assert client.post(PATH, json={"tool": "get_sport", "user": ME}).status_code == 404
    assert _post(client, "get_sport", secret="nope").status_code == 404


def test_whats_on_puts_my_teams_first(client):
    db.set_watched_teams(ME, [{"key": "arsenal", "name": "Arsenal", "sport": "football"}])
    db.replace_source_listings("wheresthematch", [
        _row("1", "Spurs vs Villa", 2),
        _row("2", "Arsenal vs Chelsea", 5),
        _row("3", "Collingwood v Carlton", 1, sport="afl", league="", channels=("Kayo",)),
        _row("9", "Next week", 24 * 8),
    ])
    body = _post(client, "get_sport").json()["payload"]
    assert body["when"] == "now" and body["timezone"] == TZ and body["total"] == 3
    assert body["sports"] == {"Football": 2, "AFL": 1}
    first = body["listings"][0]
    assert first["title"] == "Arsenal vs Chelsea" and first["your_team"] == "Arsenal" and first["channels"] == ["Sky Sports"]
    assert first["start"].endswith(("+00:00", "+01:00")) and first["day"] and first["time"]
    assert [i["title"] for i in body["listings"][1:]] == ["Collingwood v Carlton", "Spurs vs Villa"]


def test_sport_or_league_filter_and_unknown_sport(client):
    db.replace_source_listings("wheresthematch", [
        _row("1", "Spurs vs Villa", 2),
        _row("2", "Real Madrid vs Barcelona", 3, league="La Liga"),
        _row("3", "Collingwood v Carlton", 1, sport="afl", league=""),
    ])
    football = _post(client, "get_sport", params={"sport": "Football"}).json()["payload"]
    assert football["sport"] == "Football" and football["total"] == 2
    liga = _post(client, "get_sport", params={"sport": "la liga"}).json()["payload"]
    assert liga["sport"] == "La Liga" and [i["title"] for i in liga["listings"]] == ["Real Madrid vs Barcelona"]
    tennis = _post(client, "get_sport", params={"sport": "tennis"}).json()["payload"]
    assert tennis["sport_not_found"] == "tennis" and tennis["listings"] == [] and tennis["sports"]["AFL"] == 1


@pytest.mark.parametrize("now, when, start, end", [
    (datetime(2026, 10, 28, 12, 0), "tonight", datetime(2026, 10, 28, 17, 0), datetime(2026, 10, 29, 2, 0)),
    (datetime(2026, 10, 28, 12, 0), "tomorrow", datetime(2026, 10, 29), datetime(2026, 10, 30)),
    (datetime(2026, 10, 28, 12, 0), "this_weekend", datetime(2026, 10, 31), datetime(2026, 11, 2)),  # a Wednesday
    (datetime(2026, 11, 1, 12, 0), "this_weekend", datetime(2026, 11, 1, 9, 0), datetime(2026, 11, 2)),  # a Sunday
    (datetime(2026, 10, 28, 12, 0), "today", datetime(2026, 10, 28, 9, 0), datetime(2026, 10, 29)),
])
def test_windows(now, when, start, end):
    assert agent_routes._window(when, now) == (start, end)


def test_my_teams_next_match_and_none_yet(client):
    db.set_watched_teams(ME, [{"key": "arsenal", "name": "Arsenal", "sport": "football"}, "Leeds"])
    db.set_watched_teams(JO, ["Spurs"])
    db.replace_source_listings("wheresthematch", [
        _row("1", "Arsenal vs Chelsea", 30),
        _row("2", "Brighton vs Arsenal", 200),
        _row("3", "Spurs vs Villa", 2),
    ])
    teams = _post(client, "get_my_teams").json()["payload"]["teams"]
    assert [t["team"] for t in teams] == ["Arsenal", "Leeds"]
    assert teams[0]["next"]["title"] == "Arsenal vs Chelsea" and teams[0]["sport"] == "Football"
    assert teams[1]["next"] is None
    assert _post(client, "get_my_teams", user=JO).json()["payload"]["teams"][0]["next"]["title"] == "Spurs vs Villa"


def test_no_teams_yet(client):
    assert _post(client, "get_my_teams").json()["payload"] == {"teams": [], "note": "No Teams to Watch picked in SportGuide yet"}
