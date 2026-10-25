"""Ripple Phase 4: the family display page, its one-off capture keys and the nudge to Notify."""

from __future__ import annotations

import base64
import io
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import db, display, notify, photos, routes
from stonepi_auth.internal import sign_internal_v2
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session

JO = "16fd2706-8baf-433b-82eb-8c7fada847da"
ADAM = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SECRET = "ripple-secret"
KEY_PATH = "/api/internal/capture-key"


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    routes._uploads.clear()
    display._keys.clear()
    display._photo_uri.cache_clear()


@pytest.fixture()
def nudges(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(notify, "display_changed", lambda secret: calls.append(secret))
    return calls


def _app(monkeypatch) -> FastAPI:
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "bell_context", lambda user, **kw: {"show": False})
    app = FastAPI()
    app.include_router(routes.router)
    return app


def _member(monkeypatch, *, uid=JO, name="Jo", is_admin=False, granted=True) -> TestClient:
    c = TestClient(_app(monkeypatch))
    c.cookies.set(CSRF_COOKIE, "csrf")
    c.cookies.set(
        COOKIE_NAME,
        encode_session(secret=SECRET, user_id=uid, username=name.lower(), display_name=name, is_admin=is_admin,
                       apps=["ripple"] if granted else [], session_id="s", permissions={}),
    )
    return c


def _pi(monkeypatch, host="127.0.0.1") -> TestClient:
    """Notify's capture browser: not signed in, on the Pi itself."""
    return TestClient(_app(monkeypatch), client=(host, 50000))


def _photo(color="steelblue", size=(1200, 900)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _post(c: TestClient, body: str, *, show=True, photo: bytes | None = None, caption=""):
    data = {"body": body, "csrf_token": "csrf", "captions": [caption] if photo else []}
    if show:
        data["show_on_display"] = "1"
    files = [("photos", ("p.jpg", photo, "image/jpeg"))] if photo else None
    return c.post("/posts", data=data, files=files, follow_redirects=False)


def _key(c: TestClient) -> str:
    response = c.post(KEY_PATH, headers=sign_internal_v2(SECRET, "POST", KEY_PATH, b""))
    assert response.status_code == 200
    return response.json()["key"]


def _data_uris(html: str) -> list[str]:
    return re.findall(r'src="(data:image/[a-z]+;base64,[^"]+)"', html)


def _decode(uri: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))


# -- who can open it -------------------------------------------------------------------


def test_preview_needs_ripple_like_the_feed(monkeypatch):
    signed_out = TestClient(_app(monkeypatch))
    assert signed_out.get("/display", follow_redirects=False).status_code == 303
    assert _member(monkeypatch, granted=False).get("/display").status_code == 403
    assert _member(monkeypatch).get("/display").status_code == 200


def test_capture_key_endpoint_is_signed(monkeypatch):
    c = _pi(monkeypatch)
    assert c.post(KEY_PATH).status_code == 404
    assert c.post(KEY_PATH, headers=sign_internal_v2("wrong", "POST", KEY_PATH, b"")).status_code == 404
    body = c.post(KEY_PATH, headers=sign_internal_v2(SECRET, "POST", KEY_PATH, b"")).json()
    assert body["ok"] and len(body["key"]) >= 32 and body["expires_in"] == display.KEY_TTL_S


def test_a_key_opens_the_page_once(monkeypatch):
    _post(_member(monkeypatch), "Hello from Copenhagen")
    c = _pi(monkeypatch)
    key = _key(c)
    first = c.get(f"/display?k={key}")
    assert first.status_code == 200 and "Hello from Copenhagen" in first.text
    assert first.headers["cache-control"] == "no-store"
    again = c.get(f"/display?k={key}")
    assert again.status_code == 403 and "Hello" not in again.text


def test_made_up_or_expired_keys_are_refused(monkeypatch):
    c = _pi(monkeypatch)
    assert c.get("/display?k=made-up").status_code == 403
    key = display.issue_key(now=1000.0)
    assert display.redeem_key(key, now=1000.0 + display.KEY_TTL_S + 1) is False


def test_keys_only_work_straight_from_the_pi(monkeypatch):
    _post(_member(monkeypatch), "Private")
    lan = _pi(monkeypatch, host="192.168.1.50")
    assert lan.get(f"/display?k={_key(lan)}").status_code == 403
    # Through nginx the request is loopback too, but carries forwarding headers.
    proxied = _pi(monkeypatch)
    key = _key(proxied)
    response = proxied.get(f"/display?k={key}", headers={"X-Forwarded-For": "100.64.0.7"})
    assert response.status_code == 403 and "Private" not in response.text


# -- what it shows ---------------------------------------------------------------------


def test_only_posts_marked_for_the_display_and_the_newest_few(monkeypatch):
    c = _member(monkeypatch)
    for n in range(5):
        _post(c, f"Shown {n}")
    _post(c, "Not for the screen", show=False)
    html = c.get("/display").text
    assert "Not for the screen" not in html
    assert [n for n in range(5) if f"Shown {n}" in html] == [2, 3, 4]  # three by default, newest
    assert '<span class="board">Family</span>' in html


def test_empty_board(monkeypatch):
    assert "No updates yet" in _member(monkeypatch).get("/display").text


def test_hero_photo_is_dithered_to_the_panel(monkeypatch):
    c = _member(monkeypatch)
    _post(c, "Harbour", photo=_photo(), caption="Nyhavn at 2pm")
    html = c.get("/display").text  # the preview: OG, 1-bit
    uris = _data_uris(html)
    assert len(uris) == 1 and uris[0].startswith("data:image/png")
    hero = _decode(uris[0])
    assert hero.mode == "L" and set(hero.getdata()) <= {0, 255}
    assert hero.size == (376, 480 - display.HEADER_CSS)
    assert "Nyhavn at 2pm" in html
    assert "/media/" not in html  # nothing for the capture to fetch


def test_panel_size_and_depth_come_from_the_address(monkeypatch):
    _post(_member(monkeypatch), "Harbour", photo=_photo())
    c = _pi(monkeypatch)
    html = c.get(f"/display?k={_key(c)}&w=1040&h=780&scale=1.8&bits=4").text
    hero = _decode(_data_uris(html)[0])
    # TRMNL X: 1040×780 CSS px at 1.8×. The hero is 485×735 CSS px (snapped and with a 45 px
    # header, so both land on whole panel pixels): exactly 873×1323, in 16 greys at most.
    assert hero.size == (873, 1323) and len(set(hero.getdata())) <= 16
    assert 'content="width=1040"' in html and "height: 735px" in html


def test_colour_screens_get_colour_photos(monkeypatch):
    _post(_member(monkeypatch), "Harbour", photo=_photo())
    c = _pi(monkeypatch)
    uri = _data_uris(c.get(f"/display?k={_key(c)}&bits=0").text)[0]
    assert uri.startswith("data:image/jpeg") and _decode(uri).mode == "RGB"


def test_list_layout_uses_small_photos(monkeypatch):
    db.set_setting("display_layout", "list")
    c = _member(monkeypatch)
    _post(c, "With photo", photo=_photo())
    _post(c, "Text only")
    uris = _data_uris(c.get("/display").text)
    assert len(uris) == 1 and _decode(uris[0]).size == (120, 120)


def test_missing_photo_falls_back_to_text(monkeypatch):
    c = _member(monkeypatch)
    _post(c, "Photo gone", photo=_photo())
    media = db.list_posts()[0][0]["media"][0]
    photos.delete_files(media["storage_key"], media["ext"])
    html = c.get("/display").text
    assert "Photo gone" in html and _data_uris(html) == []


def test_snap_lands_on_whole_panel_pixels():
    assert display.snap(376, 1.0) == 376
    assert (display.snap(489, 1.8) * 1.8) % 1 == 0


@pytest.mark.parametrize("h, scale", [(480, 1.0), (780, 1.8), (780, 1.44), (600, 1.25), (480, 0.8)])
def test_main_area_is_whole_panel_pixels_at_any_zoom(monkeypatch, h, scale):
    panel = {"w": 800, "h": h, "scale": scale, "bits": 1}
    data = display.page_data(panel)
    pixels = data["main_h"] * scale
    assert abs(pixels - round(pixels)) < 1e-6 and data["header_h"] >= display.HEADER_CSS


def test_dithered_photo_uses_notifys_grey_levels(monkeypatch):
    c = _member(monkeypatch)
    _post(c, "Harbour", photo=_photo("#8899aa"))
    k = _pi(monkeypatch)
    hero = _decode(_data_uris(k.get(f"/display?k={_key(k)}&bits=2").text)[0])
    assert set(hero.getdata()) <= {0, 85, 170, 255}


def test_members_dont_see_notify_details(monkeypatch):
    def must_not_ask(secret):
        raise AssertionError("members shouldn't trigger a Notify lookup")

    monkeypatch.setattr(notify, "display_status", must_not_ask)
    html = _member(monkeypatch).get("/settings?tab=display").text
    assert "can see how it&#39;s doing" in html or "can see how it's doing" in html
    assert "Last update" not in html and "Open in Notify" not in html


# -- settings and the nudge ------------------------------------------------------------


def test_display_settings_are_admin_only(monkeypatch, nudges):
    jo = _member(monkeypatch)
    r = jo.post("/settings/display", data={"layout": "list", "posts": "4", "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in r.headers["location"] and display.layout() == "hero"
    admin = _member(monkeypatch, uid=ADAM, name="Adam", is_admin=True)
    admin.post("/settings/display", data={"layout": "list", "posts": "4", "csrf_token": "csrf"})
    assert (display.layout(), display.post_count()) == ("list", 4)
    bad = admin.post("/settings/display", data={"layout": "grid", "posts": "4", "csrf_token": "csrf"}, follow_redirects=False)
    assert "err=" in bad.headers["location"]
    assert nudges == [SECRET]


def test_setup_button_opens_notify_with_ripple_picked(monkeypatch):
    monkeypatch.setattr(notify, "display_status", lambda secret: [])
    admin = _member(monkeypatch, uid=ADAM, name="Adam", is_admin=True)
    html = admin.get("/settings?tab=display").text
    assert "No TRMNL shows the family board yet" in html
    assert 'href="http://127.0.0.1:8012/displays?add=ripple#add-display"' in html
    assert "Set up the family display" in html and "Webhook Image" in html
    member_html = _member(monkeypatch).get("/settings?tab=display").text
    assert "add=ripple" not in member_html and "An admin sets up" in member_html


def test_settings_show_how_the_family_display_is_doing(monkeypatch):
    displays = [
        {"id": "a1", "name": "Mum and Dad", "device": "TRMNL OG", "pushing": True,
         "last_push_at": "2026-10-24T16:40:00+00:00", "last_push_ok": True, "last_push_message": ""},
        {"id": "b2", "name": "Spare", "device": "TRMNL X", "pushing": True,
         "last_push_at": "2026-10-24T16:41:00+00:00", "last_push_ok": False, "last_push_message": "TRMNL rate limit"},
        {"id": "c3", "name": "Half done", "device": "TRMNL OG", "pushing": False,
         "last_push_at": "", "last_push_ok": False, "last_push_message": ""},
    ]
    monkeypatch.setattr(notify, "display_status", lambda secret: [dict(d) for d in displays])
    html = _member(monkeypatch, uid=ADAM, name="Adam", is_admin=True).get("/settings?tab=display").text
    assert "Mum and Dad" in html and "Last updated" in html
    assert "Last update failed: TRMNL rate limit" in html
    assert "Not pushing: open it in Notify" in html
    assert 'href="http://127.0.0.1:8012/displays/a1"' in html and "Set up another TRMNL" in html


def test_settings_say_when_notify_cannot_be_asked(monkeypatch):
    monkeypatch.setattr(notify, "display_status", lambda secret: None)
    html = _member(monkeypatch, uid=ADAM, name="Adam", is_admin=True).get("/settings?tab=display").text
    assert "Couldn't check Notify" in html and "Set up the family display" in html


def test_display_status_reads_notify_with_a_signed_call(monkeypatch):
    from stonepi_auth import internal

    seen: dict = {}

    def fake_get(base, secret, path, timeout):
        seen.update(base=base, secret=secret, path=path)
        return {"ok": True, "displays": [{"id": "a1"}, "junk"]}

    monkeypatch.setattr(internal, "get_internal_json", fake_get)
    assert notify.display_status(SECRET) == [{"id": "a1"}]
    assert seen["path"] == "/api/internal/web/app-displays/ripple" and seen["secret"] == SECRET
    monkeypatch.setattr(internal, "get_internal_json", lambda *a, **k: None)
    assert notify.display_status(SECRET) is None and notify.display_status("") is None


def test_every_change_the_display_shows_nudges_notify(monkeypatch, nudges):
    c = _member(monkeypatch)
    _post(c, "One")
    pid = db.list_posts()[0][0]["id"]
    c.post(f"/posts/{pid}", data={"body": "One, edited", "show_on_display": "1", "csrf_token": "csrf"})
    c.post(f"/posts/{pid}/delete", data={"csrf_token": "csrf"})
    admin = _member(monkeypatch, uid=ADAM, name="Adam", is_admin=True)
    admin.post("/settings/board", data={"board_name": "The Stones", "csrf_token": "csrf"})
    assert len(nudges) == 4
    _post(c, "", show=True)  # refused (empty): no nudge
    assert len(nudges) == 4


def test_feed_only_changes_dont_nudge(monkeypatch, nudges):
    c = _member(monkeypatch)
    _post(c, "Just the feed", show=False)
    pid = db.list_posts()[0][0]["id"]
    c.post(f"/posts/{pid}", data={"body": "Still just the feed", "csrf_token": "csrf"})
    assert nudges == []
    # Switched on: now it's on the display. Switched off again: it leaves the display.
    c.post(f"/posts/{pid}", data={"body": "Now shown", "show_on_display": "1", "csrf_token": "csrf"})
    c.post(f"/posts/{pid}", data={"body": "Hidden again", "csrf_token": "csrf"})
    assert len(nudges) == 2
    c.post(f"/posts/{pid}/delete", data={"csrf_token": "csrf"})
    assert len(nudges) == 2


def test_hero_layout_keeps_newest_first(monkeypatch):
    c = _member(monkeypatch)
    _post(c, "Older photo post", photo=_photo(), caption="Monday's walk")
    _post(c, "Newer text post")
    html = c.get("/display").text
    assert len(_data_uris(html)) == 1  # the photo is still the hero
    assert html.index("Newer text post") < html.index("Older photo post") < html.index("Monday&#39;s walk")


def test_preview_cannot_pick_an_arbitrary_size(monkeypatch):
    c = _member(monkeypatch)
    _post(c, "Harbour", photo=_photo())
    hero = _decode(_data_uris(c.get("/display?w=2000&h=2000&scale=3&bits=4").text)[0])
    assert hero.size == (376, 480 - display.HEADER_CSS) and set(hero.getdata()) <= {0, 255}
    x = _decode(_data_uris(c.get("/display?device=v2").text)[0])
    assert x.size == (873, 1323)


def test_nudge_posts_a_signed_note_to_notify(monkeypatch):
    sent: dict = {}

    def fake_post(url, content, headers, timeout):
        sent.update(url=url, content=content, headers=headers)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify._post(SECRET)
    assert sent["url"].endswith("/api/internal/web/changed") and sent["content"] == b'{"app": "ripple"}'
    from stonepi_auth.internal import verify_internal_v2

    assert verify_internal_v2(SECRET, "POST", "/api/internal/web/changed", sent["headers"], sent["content"])


def test_no_nudge_without_a_session_secret(monkeypatch):
    started: list = []
    monkeypatch.setattr(notify.threading, "Thread", lambda *a, **k: started.append(k))
    notify.display_changed("")
    assert started == []



# -- several photos, and dates the family can read ------------------------------------


def _age(body: str, days: float) -> None:
    from datetime import datetime, timedelta, timezone

    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE posts SET created_at = ? WHERE body = ?", (stamp, body))


def test_photo_boxes_fill_the_area_exactly():
    W, H, g = 376, 436, 4
    assert display.photo_boxes(1, W, H, g) == [(0, 0, W, H)]
    two = display.photo_boxes(2, W, H, g)
    assert [b[3] for b in two] == [216, 216] and two[1][1] == 220
    three = display.photo_boxes(3, W, H, g)
    assert three[0] == (0, 0, W, 216) and three[1][2] + g + three[2][2] == W
    four = display.photo_boxes(4, W, H, g)
    assert len(four) == 4 and four[3] == (190, 220, 186, 216)
    for x, y, w, h in four:
        assert x + w <= W and y + h <= H


def test_recent_photos_show_together_up_to_four(monkeypatch):
    c = _member(monkeypatch)
    _post(c, "Last week", photo=_photo("black"), caption="Old")
    _age("Last week", 8)
    for n in range(5):
        _post(c, f"Photo {n}", photo=_photo(), caption=f"Caption {n}")
    html = c.get("/display?device=og").text
    sizes = [_decode(u).size for u in _data_uris(html)]
    # Four newest photos (OG, 1-bit): 376 × 436 split two by two with a 4 px gap.
    assert sizes == [(186, 216), (186, 216), (186, 216), (186, 216)]
    # Photo 1's post isn't listed (three posts by default), but its photo is in the grid.
    assert "Caption 4" in html and "Caption 1" not in html and "Old" not in html


def test_an_older_photo_still_shows_when_it_is_the_only_one(monkeypatch):
    c = _member(monkeypatch)
    _post(c, "A while ago", photo=_photo(), caption="Spring")
    _age("A while ago", 30)
    _post(c, "Since then, words only")
    sizes = [_decode(u).size for u in _data_uris(c.get("/display").text)]
    assert sizes == [(376, 480 - display.HEADER_CSS)]


def test_display_dates_name_the_day_with_am_pm(monkeypatch):
    from datetime import datetime

    morning = datetime(2026, 10, 25, 8, 31).astimezone().isoformat()
    night = datetime(2026, 10, 25, 1, 2).astimezone().isoformat()
    noon = datetime(2026, 10, 26, 12, 5).astimezone().isoformat()
    assert routes._display_when(morning) == "Sun 25/10 8:31 AM"
    assert routes._display_when(night) == "Sun 25/10 1:02 AM"
    assert routes._display_when(noon) == "Mon 26/10 12:05 PM"
    c = _member(monkeypatch)
    _post(c, "Rain today")
    html = c.get("/display").text
    assert "Today" not in html and re.search(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2}/\d{1,2} \d{1,2}:\d\d (AM|PM)", html)
