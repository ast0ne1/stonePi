"""Board list view (pinboard-canvas.md step 2): card types, pins, permissions, areas,
auto-remove and link previews."""

from __future__ import annotations

import socket
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import links, people, routes, store
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
ADAM = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
HOUSEHOLD = [{"id": ADAM, "name": "Adam"}, {"id": JO, "name": "Jo"}]
SECRET = "pin-secret"
ALL = {"can_post_notices": True, "can_assign_others": True}
FETCH = {"X-Requested-With": "fetch"}


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "STORE", tmp_path / "pinboard.json")
    monkeypatch.setattr(store, "LEGACY_STORE", tmp_path / "board.json")
    return tmp_path


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    import stonepi_contracts

    monkeypatch.setattr(stonepi_contracts, "emit_event", lambda ev: True)
    monkeypatch.setattr(links, "fetch_preview_later", lambda card_id: None)


def _client(monkeypatch, *, secret=SECRET, is_admin=False, perms=None, uid=JO, name="Jo") -> TestClient:
    monkeypatch.setattr(routes, "_session_secret", lambda: secret)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    monkeypatch.setattr(people, "household_people", lambda s: list(HOUSEHOLD) if s else [])
    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    c.cookies.set(CSRF_COOKIE, "csrf")
    if secret:
        c.cookies.set(
            COOKIE_NAME,
            encode_session(
                secret=secret, user_id=uid, username=name.lower(), display_name=name, is_admin=is_admin,
                apps=["pinboard"], session_id="s", permissions={"pinboard": perms or {}},
            ),
        )
    return c


def _add(c: TestClient, **fields) -> dict:
    res = c.post("/cards", data={"csrf_token": "csrf", **fields}, headers=FETCH)
    return res.json()


# -- adding -----------------------------------------------------------------------------


def test_add_each_type_and_board_page_shows_them(monkeypatch):
    c = _client(monkeypatch, perms=ALL)
    assert _add(c, type="note", text="Bins out", color="pink", pin_color="red", pin_style="tape")["ok"]
    assert _add(c, type="link", url="example.com/recipes", note="Sunday dinner")["ok"]
    assert _add(c, type="checklist", title="Shop", items="Milk\n- Bread\n\n")["ok"]
    assert _add(c, type="reminder", text="Call plumber", due=date.today().isoformat(), pin_color="")["ok"]

    page = c.get("/").text
    for text in ("Bins out", "Sunday dinner", "Milk", "Bread", "Call plumber", "note-pink", "bpin-tape pin-red"):
        assert text in page
    link = next(card for g in store.board_groups() for card in g["cards"] if card["type"] == "link")
    assert link["url"] == "https://example.com/recipes" and link["host"] == "example.com"
    checklist = next(card for g in store.board_groups() for card in g["cards"] if card["type"] == "checklist")
    assert [i["text"] for i in checklist["checks"]] == ["Milk", "Bread"]
    # The pin style is remembered for the next pin.
    assert store.user_pref(JO, "pin_style", store.PIN_STYLES) == "tape"


def test_add_refuses_bad_input(monkeypatch):
    c = _client(monkeypatch, perms=ALL)
    assert _add(c, type="note", text="  ")["ok"] is False
    assert _add(c, type="link", url="ftp://files.example")["ok"] is False
    assert _add(c, type="checklist", title="", items="")["ok"] is False
    assert _add(c, type="banana")["ok"] is False
    assert c.post("/cards", data={"csrf_token": "nope", "type": "note", "text": "x"}, headers=FETCH).status_code == 403


def test_without_post_notices_cards_are_unpinned(monkeypatch):
    c = _client(monkeypatch, perms={"can_post_notices": False, "can_assign_others": True})
    assert _add(c, type="note", text="Hello", pin_color="red")["ok"]
    card = store.board_groups()[0]["cards"][0]
    assert card["pin_color"] == ""
    res = c.post(f"/cards/{card['id']}/pin", data={"csrf_token": "csrf", "pin_color": "blue"}, headers=FETCH)
    assert res.status_code == 403


def test_reminder_from_board_respects_assign_others(monkeypatch):
    c = _client(monkeypatch, perms={"can_post_notices": True, "can_assign_others": False})
    assert _add(c, type="reminder", text="Dentist", assignee_user=ADAM)["ok"] is False
    assert _add(c, type="reminder", text="Dentist")["ok"]
    assert store.list_items()["reminders"][0]["assignee_user"] == JO


def test_signed_out_is_refused(monkeypatch):
    c = _client(monkeypatch, secret=SECRET)
    c.cookies.delete(COOKIE_NAME)
    assert c.post("/cards", data={"csrf_token": "csrf", "type": "note", "text": "x"}, headers=FETCH).status_code == 401


# -- editing, pins, moving, removing ----------------------------------------------------


def test_only_author_or_admin_edits_and_removes(monkeypatch):
    jo = _client(monkeypatch, perms=ALL)
    card_id = _add(jo, type="note", text="Jo's note")["id"]
    adam_member = _client(monkeypatch, perms=ALL, uid=ADAM, name="Adam")
    res = adam_member.post(f"/cards/{card_id}/edit", data={"csrf_token": "csrf", "text": "Hijack"}, headers=FETCH)
    assert res.status_code == 403
    assert adam_member.post(f"/cards/{card_id}/delete", data={"csrf_token": "csrf"}, headers=FETCH).status_code == 403
    # Anyone may move it.
    area = store.add_area("Kitchen")
    assert adam_member.post(f"/cards/{card_id}/area", data={"csrf_token": "csrf", "area_id": area["id"]}, headers=FETCH).json()["ok"]

    jo = _client(monkeypatch, perms=ALL)
    assert jo.post(f"/cards/{card_id}/edit", data={"csrf_token": "csrf", "text": "Edited", "color": "green"}, headers=FETCH).json()["ok"]
    card = store.get_card(card_id)
    assert card["data"]["text"] == "Edited" and card["color"] == "green" and card["area_id"] == area["id"]

    admin = _client(monkeypatch, is_admin=True, uid=ADAM, name="Adam")
    assert admin.post(f"/cards/{card_id}/delete", data={"csrf_token": "csrf"}, headers=FETCH).json()["ok"]
    assert store.get_card(card_id) is None


def test_pin_and_unpin_change_the_displays(monkeypatch):
    c = _client(monkeypatch, perms=ALL)
    card_id = _add(c, type="link", url="https://example.com/a")["id"]
    assert store.display_payload()["lines"] == []
    c.post(f"/cards/{card_id}/pin", data={"csrf_token": "csrf", "pin_color": "green", "pin_style": "map"}, headers=FETCH)
    store.set_link_preview(card_id, {"title": "Recipes", "site": "Example", "preview": "ok"})
    assert store.display_payload()["lines"] == ["Recipes · Example"]
    assert store.panel_feed(5)["items"][0]["sub"] == "Example"
    c.post(f"/cards/{card_id}/pin", data={"csrf_token": "csrf", "pin_color": ""}, headers=FETCH)
    assert store.display_payload()["lines"] == []


def test_link_url_change_resets_preview(monkeypatch):
    c = _client(monkeypatch, perms=ALL)
    card_id = _add(c, type="link", url="https://example.com/a")["id"]
    store.set_link_preview(card_id, {"title": "Old", "preview": "ok"})
    c.post(f"/cards/{card_id}/edit", data={"csrf_token": "csrf", "url": "https://example.org/b", "note": ""}, headers=FETCH)
    data = store.get_card(card_id)["data"]
    assert data["url"] == "https://example.org/b" and data["title"] == "" and data["preview"] == "pending"


def test_checklist_edit_keeps_ticks_and_moves_on(monkeypatch):
    c = _client(monkeypatch, perms=ALL)
    card_id = _add(c, type="checklist", title="Shop", items="Milk\nBread")["id"]
    assert c.post(f"/cards/{card_id}/check/0", data={"csrf_token": "csrf"}, headers=FETCH).json()["done"] is True
    c.post(f"/cards/{card_id}/edit", data={"csrf_token": "csrf", "title": "Shop", "items": "Milk\nEggs"}, headers=FETCH)
    items = store.get_card(card_id)["data"]["items"]
    assert items == [{"text": "Milk", "done": True}, {"text": "Eggs", "done": False}]
    assert c.post(f"/cards/{card_id}/check/9", data={"csrf_token": "csrf"}, headers=FETCH).status_code == 404


# -- done -------------------------------------------------------------------------------


def test_who_may_tick_a_reminder(monkeypatch):
    adam = _client(monkeypatch, perms=ALL, uid=ADAM, name="Adam")
    for_jo = _add(adam, type="reminder", text="Jo's dentist", assignee_user=JO)["id"]
    household = _add(adam, type="reminder", text="Bins")["id"]
    for_adam = _add(adam, type="reminder", text="Adam's MOT", assignee_user=ADAM)["id"]

    jo = _client(monkeypatch, perms=ALL)
    tick = lambda cid: jo.post(f"/cards/{cid}/done", data={"csrf_token": "csrf", "done": "1"}, headers=FETCH)  # noqa: E731
    assert tick(for_jo).json()["done"] is True
    assert tick(household).json()["ok"]
    assert tick(for_adam).status_code == 403
    assert store.get_card(for_jo)["data"]["done"] is True


# -- areas and settings -----------------------------------------------------------------


def test_areas_group_the_board_and_removing_one_keeps_its_cards(monkeypatch):
    kitchen = store.add_area("Kitchen")
    note = store.add_card("note", {"text": "Descale kettle"}, area_id=kitchen["id"])
    store.add_card("note", {"text": "Household note"})
    names = [g["area"]["name"] for g in store.board_groups() if g["area"]]
    assert names == ["Household", "Kitchen"]
    with pytest.raises(ValueError):
        store.delete_area(store.HOUSEHOLD_AREA)
    store.delete_area(kitchen["id"])
    groups = store.board_groups()
    assert groups[-1]["area"] is None and groups[-1]["cards"][0]["id"] == note["id"]


def test_area_grows_and_pushes_the_next_one_down():
    kitchen = store.add_area("Kitchen")
    before = next(a for a in store.areas() if a["id"] == kitchen["id"])["y"]
    for i in range(store.PER_ROW + 1):
        store.add_card("note", {"text": f"N{i}"})
    after = next(a for a in store.areas() if a["id"] == kitchen["id"])["y"]
    household = next(a for a in store.areas() if a["id"] == store.HOUSEHOLD_AREA)
    assert after > before and after >= household["y"] + household["h"]


def test_settings_board_admin_and_member(monkeypatch):
    member = _client(monkeypatch, perms=ALL)
    member.post("/settings/background", data={"csrf_token": "csrf", "background": "dotted"})
    assert store.user_pref(JO, "background", store.BACKGROUNDS) == "dotted"
    assert store.user_pref(ADAM, "background", store.BACKGROUNDS) == "cork"
    assert "bg-dotted" in member.get("/").text
    member.post("/settings/areas", data={"csrf_token": "csrf", "name": "Links"})
    member.post("/settings/auto-remove", data={"csrf_token": "csrf", "days": "7"})
    assert [a["name"] for a in store.areas()] == ["Household"] and store.auto_remove_days() == 0

    admin = _client(monkeypatch, is_admin=True, uid=ADAM, name="Adam")
    admin.post("/settings/areas", data={"csrf_token": "csrf", "name": "Links"})
    admin.post("/settings/auto-remove", data={"csrf_token": "csrf", "days": "7"})
    assert [a["name"] for a in store.areas()] == ["Household", "Links"] and store.auto_remove_days() == 7
    assert "Links" in admin.get("/settings", params={"tab": "board"}).text


def test_auto_remove_only_old_done_items():
    store.set_auto_remove_days(3)
    old = store.add_reminder("Old", pin=None)["id"]
    fresh = store.add_reminder("Fresh", pin=None)["id"]
    open_one = store.add_reminder("Open", pin=None)["id"]
    lst = store.add_card("checklist", {"title": "L", "items": "A"})["id"]
    note = store.add_card("note", {"text": "Keep"})["id"]
    store.set_reminder_done(old)
    store.set_reminder_done(fresh)
    store.toggle_check(lst, 0)
    later = datetime.now(timezone.utc) + timedelta(days=3, minutes=1)
    with store.connect() as conn:  # make "fresh" recent relative to ``later``
        conn.execute(
            "UPDATE cards SET data = json_set(data, '$.done_at', ?) WHERE id = ?",
            ((later - timedelta(days=1)).isoformat(), fresh),
        )
    assert store.remove_done_items(later) == 2
    assert {store.get_card(i) is None for i in (old, lst)} == {True}
    assert all(store.get_card(i) for i in (fresh, open_one, note))
    store.set_auto_remove_days(0)
    assert store.remove_done_items(later + timedelta(days=30)) == 0


# -- link previews ----------------------------------------------------------------------


def _resolve_to(monkeypatch, address: str):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]
    )


@pytest.mark.parametrize("address", ["192.168.1.10", "127.0.0.1", "100.101.102.103", "169.254.1.1", "10.0.0.5"])
def test_private_addresses_are_blocked(monkeypatch, address):
    _resolve_to(monkeypatch, address)
    assert links.public_host("sneaky.example") is False
    card_id = store.add_card("link", {"url": "https://sneaky.example/x"})["id"]
    assert links.fetch_preview(card_id) == "blocked"
    assert store.get_card(card_id)["data"]["preview"] == "blocked"


def test_public_address_and_other_ports(monkeypatch):
    _resolve_to(monkeypatch, "93.184.215.14")
    assert links.public_host("example.com") is True
    with pytest.raises(links.Blocked):
        links._check("https://example.com:8080/")


def test_parse_page_prefers_open_graph():
    html = """<html><head><title>Plain title</title>
    <meta property="og:title" content="  Nice   title ">
    <meta property="og:site_name" content="Example">
    <meta name="description" content="About it">
    <meta property="og:image" content="/img/card.png">
    <link rel="icon" href="/fav.png"><link rel="apple-touch-icon" href="/touch.png">
    </head><body></body></html>"""
    page = links.parse_page(html, "https://example.com/post/1")
    assert page["title"] == "Nice title" and page["site"] == "Example" and page["description"] == "About it"
    assert page["image"] == "https://example.com/img/card.png"
    assert page["icons"][:2] == ["https://example.com/touch.png", "https://example.com/fav.png"]


def test_preview_files_are_served_by_name_only(monkeypatch, fresh_store):
    c = _client(monkeypatch, perms=ALL)
    folder = fresh_store / store.PREVIEWS_DIR
    folder.mkdir()
    (folder / "abcdef012345-image.png").write_bytes(b"\x89PNG fake")
    res = c.get("/previews/abcdef012345-image.png")
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert c.get("/previews/..%2Fpinboard.sqlite").status_code == 404
    assert c.get("/previews/abcdef012345-image.svg").status_code == 404
