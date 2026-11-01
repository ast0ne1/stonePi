"""Canvas (pinboard-canvas.md step 3): free placement of cards and areas, adding at a point,
and the state the open board polls."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import links, people, routes, store
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
SECRET = "pin-secret"
FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")
    monkeypatch.setattr(links, "fetch_preview_later", lambda card_id: None)


def _client(monkeypatch, *, signed_in=True) -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    monkeypatch.setattr(people, "household_people", lambda s: [])
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    if signed_in:
        c.cookies.set(
            COOKIE_NAME,
            encode_session(
                secret=SECRET, user_id=JO, username="jo", display_name="Jo", is_admin=False,
                apps=["pinboard"], session_id="s", permissions={"pinboard": {"can_post_notices": True}},
            ),
        )
    return c


def _area(area_id: str) -> dict:
    return next(a for a in store.areas() if a["id"] == area_id)


def test_place_card_moves_resizes_and_finds_its_area():
    kitchen = store.add_area("Kitchen")
    card = store.add_card("note", {"text": "Kettle"})
    assert store.get_card(card["id"])["area_id"] == store.HOUSEHOLD_AREA
    inside = store.place_card(card["id"], kitchen["x"] + 40, kitchen["y"] + 60, 300, 200)
    assert inside["area_id"] == kitchen["id"] and (inside["w"], inside["h"]) == (300, 200)
    outside = store.place_card(card["id"], -5000, -5000)
    assert outside["area_id"] == ""
    assert store.board_groups()[-1]["area"] is None  # listed under "Board"
    # Too small or too big is clamped; it comes to the front.
    tiny = store.place_card(card["id"], 0, 0, 10, 99999)
    assert (tiny["w"], tiny["h"]) == (store.CARD_MIN[0], store.CARD_MAX[1])
    other = store.add_card("note", {"text": "Other"})
    assert store.place_card(other["id"], 0, 0)["z"] > tiny["z"]
    assert store.place_card("missing", 0, 0) is None


def test_place_area_carries_its_cards():
    card = store.add_card("note", {"text": "Bins"})
    before = store.get_card(card["id"])
    area = _area(store.HOUSEHOLD_AREA)
    assert store.place_area(store.HOUSEHOLD_AREA, area["x"] + 100, area["y"] - 50, 2000, 10)
    moved = store.get_card(card["id"])
    assert (moved["x"], moved["y"]) == (before["x"] + 100, before["y"] - 50)
    assert _area(store.HOUSEHOLD_AREA)["h"] == store.AREA_MIN[1]
    assert store.place_area("missing", 0, 0) is False


def test_moving_a_card_does_not_change_its_rev():
    card = store.add_card("note", {"text": "Bins"})
    rev = {c["id"]: c["rev"] for c in store.canvas_state()["cards"]}[card["id"]]
    store.place_card(card["id"], 500, 500)
    assert {c["id"]: c["rev"] for c in store.canvas_state()["cards"]}[card["id"]] == rev
    store.update_card(card["id"], {"text": "Bins and recycling"})
    assert {c["id"]: c["rev"] for c in store.canvas_state()["cards"]}[card["id"]] != rev


def test_add_at_a_canvas_point(monkeypatch):
    c = _client(monkeypatch)
    kitchen = store.add_area("Kitchen")
    x, y = kitchen["x"] + 30, kitchen["y"] + 50
    res = c.post("/cards", data={"csrf_token": "csrf", "type": "note", "text": "Here", "x": x, "y": y, "area_id": "household"}, headers=FETCH)
    card = store.get_card(res.json()["id"])
    assert (card["x"], card["y"], card["area_id"]) == (x, y, kitchen["id"])
    # Without a point it goes into the picked area's next spot, as in the list.
    res = c.post("/cards", data={"csrf_token": "csrf", "type": "note", "text": "Listed", "x": "", "y": ""}, headers=FETCH)
    assert store.get_card(res.json()["id"])["area_id"] == store.HOUSEHOLD_AREA


def test_place_routes_and_polling_api(monkeypatch):
    c = _client(monkeypatch)
    card = store.add_card("note", {"text": "Bins"})
    res = c.post(f"/cards/{card['id']}/place", data={"csrf_token": "csrf", "x": "-800", "y": "12.5", "w": "260", "h": "180"}, headers=FETCH)
    assert res.json()["ok"] and res.json()["area_id"] == ""
    assert c.post(f"/cards/{card['id']}/place", data={"csrf_token": "csrf", "x": "nan", "y": "1"}, headers=FETCH).status_code == 400
    assert c.post("/areas/household/place", data={"csrf_token": "csrf", "x": "10", "y": "10"}, headers=FETCH).json()["ok"]
    state = c.get("/api/cards").json()
    placed = next(x for x in state["cards"] if x["id"] == card["id"])
    assert (placed["x"], placed["y"], placed["w"], placed["h"]) == (-800, 12.5, 260, 180)
    assert [a["name"] for a in state["areas"]] == ["Household"]
    page = c.get("/").text
    assert 'data-board-api href="/api/cards"' in page and f'data-x="-800.0"' in page

    signed_out = _client(monkeypatch, signed_in=False)
    assert signed_out.get("/api/cards").status_code == 401
    assert signed_out.post("/areas/household/place", data={"csrf_token": "csrf", "x": "0", "y": "0"}, headers=FETCH).status_code == 401
