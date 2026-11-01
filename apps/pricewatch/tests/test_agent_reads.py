"""AgentLink reads (Pebble): signed internal endpoint; the person's own watches only."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from stonepi_auth.internal import sign_internal_v2

from app import agent_routes, db

SECRET = "agent-test-secret"
PATH = "/api/internal/agent"
ME, EVA = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app.config as cfg

    for mod in (db, cfg):
        monkeypatch.setattr(mod, "DB_PATH", tmp_path / "t.sqlite")
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    db.init_db()
    monkeypatch.setattr(agent_routes, "_session_secret", lambda: SECRET)
    app = FastAPI()
    app.include_router(agent_routes.router)
    return TestClient(app)


def _post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post(PATH, content=body, headers={"Content-Type": "application/json", **sign_internal_v2(secret, "POST", PATH, body)})


def _watch(user, name, target, lowest=None, status="watching", strike=None):
    wid = db.create_watch({"user_key": user, "source_id": "mock", "product_id": name, "product_name": name, "target_price": target})
    fields = {"current_lowest": lowest, "status": status, "last_checked_at": "2026-10-28T09:00:00Z"}
    if strike:
        fields["strike_snapshot_json"] = json.dumps(strike)
    db.update_watch_fields(wid, **fields)
    return wid


def test_unsigned_is_404(client):
    assert client.post(PATH, json={"tool": "get_price_alerts", "user": ME}).status_code == 404
    assert _post(client, {"tool": "get_price_alerts", "user": ME}, secret="nope").status_code == 404


def test_deals_first_then_closest_to_target_mine_only(client):
    _watch(ME, "Far TV", 1000, lowest=2000)
    _watch(ME, "Near TV", 1000, lowest=1100)
    _watch(ME, "Headphones", 900, lowest=850, status="strike_found",
           strike={"watch_price": 850, "retailer": "Shop", "found_at": "2026-10-27T10:00:00Z"})
    _watch(ME, "Old kettle", 200, status="paused")
    _watch(EVA, "Bike", 3000, lowest=2500, status="strike_found", strike={"watch_price": 2500})
    body = _post(client, {"tool": "get_price_alerts", "user": ME}).json()["payload"]
    assert body["total"] == 4 and body["deals"] == 1 and body["paused"] == 1
    assert [w["product"] for w in body["watches"]] == ["Headphones", "Near TV", "Far TV"]
    deal = body["watches"][0]["deal"]
    assert deal["price"] == "850 DKK" and deal["shop"] == "Shop" and deal["found"]
    assert body["watches"][1]["above_target_pct"] == 10.0 and body["watches"][1]["lowest_now"] == "1.100 DKK"
