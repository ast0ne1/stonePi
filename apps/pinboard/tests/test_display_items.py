"""/api/display?items=N (Car Thing panel): plain call unchanged, lists only when asked."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes, store


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_plain_display_is_unchanged(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    store.add_notice("Bins out Thursday")
    plain = client.get("/api/display").json()
    assert set(plain) == {"ok", "lines", "total", "reminders"}
    assert plain == store.display_payload()


def test_items_adds_card_and_list(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    store.add_notice("Bins out Thursday\nGreen bin too")
    store.add_reminder("Pay nursery", due=(date.today() - timedelta(days=1)).isoformat(), assignee="Sam")
    body = client.get("/api/display", params={"items": 10}).json()
    assert body["lines"]  # existing fields still there
    titles = [i["title"] for i in body["items"]]
    assert "Bins out Thursday" in titles and "Pay nursery" in titles
    overdue = next(i for i in body["items"] if i["title"] == "Pay nursery")
    assert overdue["badge"] == "Due" and "Sam" in overdue["sub"]
    assert body["card"]["headline"] == "Pay nursery"  # something due leads the card
    notice = next(i for i in body["items"] if i["title"] == "Bins out Thursday")
    assert "Green bin too" in notice["detail"]
