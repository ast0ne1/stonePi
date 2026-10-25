"""AgentLink reads (Pebble): signed internal endpoint; own + household reminders only."""

from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from stonepi_auth.internal import sign_internal_v2

from app import agent_routes, store

SECRET = "agent-test-secret"
PATH = "/api/internal/agent"
ME, EVA = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")
    monkeypatch.setattr(agent_routes, "_session_secret", lambda: SECRET)
    app = FastAPI()
    app.include_router(agent_routes.router)
    return TestClient(app)


def _post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post(PATH, content=body, headers={"Content-Type": "application/json", **sign_internal_v2(secret, "POST", PATH, body)})


def test_unsigned_is_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert client.post(PATH, json={"tool": "get_notices", "user": ME}).status_code == 404
    assert _post(client, {"tool": "get_notices", "user": ME}, secret="nope").status_code == 404


def test_reminders_are_mine_and_household_only(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    today = date.today()
    store.add_reminder("Pay nursery", due=(today - timedelta(days=1)).isoformat(), assignee="Me", assignee_user=ME)
    store.add_reminder("Bins out", due=today.isoformat())
    store.add_reminder("Eva's dentist", due=today.isoformat(), assignee="Eva", assignee_user=EVA)
    body = _post(client, {"tool": "get_reminders", "user": ME}).json()
    rems = body["payload"]["reminders"]
    assert [r["text"] for r in rems] == ["Pay nursery", "Bins out"]
    assert rems[0]["overdue"] and rems[0]["for"] == "you" and rems[0]["due_label"] == "yesterday"
    assert rems[1]["for"] == "household" and rems[1]["due_label"] == "today"


def test_notices_newest_first(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    store.add_notice("First")
    store.add_notice("Second")
    body = _post(client, {"tool": "get_notices", "user": ME}).json()
    assert body["payload"]["total"] == 2 and {n["text"] for n in body["payload"]["notices"]} == {"First", "Second"}


def test_unknown_tool(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    assert _post(client, {"tool": "add_reminder", "user": ME}).status_code == 400
