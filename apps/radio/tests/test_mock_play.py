from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Force mock Odio before app import.
os.environ["RADIO_MOCK"] = "1"
os.environ["STONEPI_SESSION_SECRET"] = ""
os.environ["STONEPI_DATA_DIR"] = str(Path(__file__).resolve().parent / "_data")

SECRET = "test-secret-radio"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("RADIO_MOCK", "1")
    monkeypatch.setenv("STONEPI_SESSION_SECRET", "")
    monkeypatch.setenv("STONEPI_DATA_DIR", str(data))
    monkeypatch.setenv("STONEPI_DEV", "1")  # unsigned internal calls only without a secret
    from app import config, db
    from app.services import odio_mock

    config.env.mock = True
    config.env.session_secret = ""
    config.env.data_dir = data
    config.DATA_DIR = data
    config.DB_PATH = data / "radio.sqlite"
    db.DB_PATH = config.DB_PATH
    db.DATA_DIR = data
    odio_mock.reset()
    db.init_db()
    from app.main import app

    return TestClient(app)


def _signed_client(client, monkeypatch):
    """Radio with a platform session secret, as on the Pi."""
    from app import routes

    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    return client


def _post_signed(client, path: str, body: dict):
    from stonepi_auth.internal import sign_internal_v2

    raw = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", **sign_internal_v2(SECRET, "POST", path, raw)}
    return client.post(path, content=raw, headers=headers)


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "radio"
    assert body["mock"] is True


def test_favorites_sqlite_and_play(client):
    from app import db

    item = db.add_favorite("", name="FIP", stream_url="http://example.test/fip.mp3", country="France")
    assert item["id"]
    assert db.get_favorite("", item["id"])["name"] == "FIP"

    play = client.post("/api/internal/play", json={"station_id": item["id"]})
    assert play.status_code == 200
    body = play.json()
    assert body["ok"] is True
    assert body["playing"] is True
    assert "FIP" in (body.get("title") or "")

    now = client.get("/api/internal/now").json()
    assert now["playing"] is True

    display = client.get("/api/display").json()
    assert display["ok"] is True
    assert display["playing"] is True
    assert any(p["id"] == item["id"] for p in display["presets"])


def test_favorites_are_per_person(client):
    from app import db

    db.add_favorite("u-ann", name="FIP", stream_url="http://example.test/fip.mp3", station_id="fip")
    db.add_favorite("u-bob", name="FIP", stream_url="http://example.test/fip.mp3", station_id="fip")
    db.add_favorite("u-bob", name="KEXP", stream_url="http://example.test/kexp.mp3", station_id="kexp")

    assert [f["id"] for f in db.list_favorites("u-ann")] == ["fip"]
    assert {f["id"] for f in db.list_favorites("u-bob")} == {"fip", "kexp"}
    assert db.remove_favorite("u-ann", "fip") is True
    assert db.get_favorite("u-bob", "fip") is not None

    # Displays (no one signed in) show the household's most-saved stations once each.
    db.add_favorite("u-ann", name="KEXP", stream_url="http://example.test/kexp.mp3", station_id="kexp")
    household = db.household_favorites(4)
    assert [f["id"] for f in household] == ["kexp", "fip"]


def test_internal_api_needs_a_signature(client, monkeypatch):
    _signed_client(client, monkeypatch)
    assert client.get("/api/internal/now").status_code == 403
    assert client.post("/api/internal/play", json={"url": "http://example.test/a.mp3"}).status_code == 403
    assert client.post("/api/internal/volume", json={"delta": 0.1}).status_code == 403

    resp = _post_signed(client, "/api/internal/play", {"url": "http://example.test/a.mp3", "name": "A"})
    assert resp.status_code == 200
    assert resp.json()["playing"] is True

    # A signature for one body doesn't cover another.
    from stonepi_auth.internal import sign_internal_v2

    headers = {"Content-Type": "application/json", **sign_internal_v2(SECRET, "POST", "/api/internal/volume", b'{"delta": 0.1}')}
    assert client.post("/api/internal/volume", content=b'{"level": 1.0}', headers=headers).status_code == 403


def test_streams_must_be_http_and_not_loopback(client):
    from app.services import stations

    assert stations.check_stream_url("https://ice.example.test/live") == "https://ice.example.test/live"
    assert stations.check_stream_url("http://192.168.1.20:8000/stream")  # a LAN Icecast is fine
    for bad in ("file:///etc/passwd", "/var/lib/mpd/x.mp3", "http://127.0.0.1:8011/", "http://localhost/x", "http://[::1]/x"):
        with pytest.raises(ValueError):
            stations.check_stream_url(bad)

    resp = client.post("/api/internal/play", json={"url": "http://127.0.0.1:8018/server"})
    assert resp.status_code == 400


def _form(client, path: str, data: dict):
    from stonepi_auth.session import CSRF_COOKIE

    client.cookies.set(CSRF_COOKIE, "tok")
    return client.post(path, data={"csrf_token": "tok", **data}, follow_redirects=False)


def test_forms_pass_csrf_and_save_for_this_person(client):
    from app import db

    resp = _form(client, "/favorites/add", {"name": "FIP", "url": "http://example.test/fip.mp3", "next": "/favorites"})
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/favorites?msg=")
    assert [f["name"] for f in db.list_favorites("")] == ["FIP"]

    bad = _form(client, "/favorites/add", {"name": "Local", "url": "file:///etc/passwd"})
    assert "error=" in bad.headers["location"]
    assert len(db.list_favorites("")) == 1


def test_next_cannot_leave_radio(client):
    for hostile in ("//evil.example/", "https://evil.example/", "/\\evil.example/"):
        resp = _form(client, "/transport", {"action": "play_pause", "next": hostile})
        assert resp.status_code == 303
        location = resp.headers["location"]
        assert location.startswith("/") and not location.startswith("//") and "evil" not in location


def _as(monkeypatch, *, admin: bool, apps=("radio",)):
    """Radio with sign-in on, and this person signed in."""
    from types import SimpleNamespace

    from app import routes

    person = SimpleNamespace(
        user_id="u-admin" if admin else "u-member", username="x", display_name="X", is_admin=admin,
        using_factory_admin=False, can_access=lambda app_id: app_id in apps,
    )
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "_user", lambda request: person)


def test_default_volume_is_admin_only(client, monkeypatch):
    from app import db

    _as(monkeypatch, admin=False)
    # A member's General post saves their own display options and never the household's.
    resp = _form(client, "/settings/general", {"default_volume": "0.9"})
    assert "msg=" in resp.headers["location"]
    assert db.get_pref("default_volume", "") == ""
    # Members see General (their display options only); Speakers and Odio stay hidden and bounce.
    hub = client.get("/settings").content
    assert b'href="/settings?tab=general"' in hub and b'href="/settings?tab=odio"' not in hub
    assert b'href="/settings?tab=about"' in hub
    general = client.get("/settings?tab=general").text
    assert "Player bar" in general and 'name="default_volume"' not in general and "Add a Home station" not in general
    assert client.get("/settings?tab=odio", follow_redirects=False).headers["location"].endswith("/settings")

    _as(monkeypatch, admin=True)
    resp = _form(client, "/settings/general", {"default_volume": "0.9"})
    assert "msg=" in resp.headers["location"]
    assert db.get_pref("default_volume", "") == "0.90"


def test_no_access_page(client, monkeypatch):
    _as(monkeypatch, admin=False, apps=())
    resp = client.get("/favorites")
    assert resp.status_code == 403
    assert b"No access to Radio" in resp.content
    assert b'aria-label="Main"' not in resp.content


def test_household_stations_seed_once_and_stay_removed(client):
    from app import db

    ids = [s["id"] for s in db.list_stations()]
    assert ids[:2] == ["bbc-radio-6", "fip"] and len(ids) == 8
    assert db.remove_station("fip") is True
    db.init_db()  # a restart doesn't bring a removed built-in station back
    assert "fip" not in {s["id"] for s in db.list_stations()}


def test_listen_has_no_catalog_label_and_offers_list_actions(client):
    page = client.get("/").content
    assert b"Catalog:" not in page
    assert b"Add a station" in page
    assert b"/stations/remove" in page  # no sign-in in this test, so everyone may remove


def test_stations_add_and_remove_permissions(client, monkeypatch):
    from app import db

    # Members star; Home stations are the admins'. The server says no, not just the page.
    _as(monkeypatch, admin=False)
    resp = _form(client, "/stations/add", {"name": "Local FM", "url": "https://ice.example.test/local", "next": "/"})
    assert "error=" in resp.headers["location"]
    assert db.find_station_by_url("https://ice.example.test/local") is None
    assert "error=" in _form(client, "/stations/remove", {"station_id": "kexp"}).headers["location"]
    assert db.get_station("kexp")
    for path in ("/", "/discover?q=kexp", "/?add=1"):
        page = client.get(path).content
        assert b"/stations/add" not in page and b"/stations/remove" not in page, path

    _as(monkeypatch, admin=True)
    resp = _form(client, "/stations/add", {"name": "Local FM", "url": "https://ice.example.test/local", "next": "/"})
    assert "msg=" in resp.headers["location"]
    mine = db.find_station_by_url("https://ice.example.test/local")
    assert mine and mine["added_by"] == "u-admin"
    bad = _form(client, "/stations/add", {"name": "x", "url": "file:///etc/passwd"})
    assert "error=" in bad.headers["location"]
    page = client.get("/").content
    assert b"Add a station" in page and b"/stations/remove" in page
    # Discover marks the household's stations instead of offering the bin; removing is on My Radio.
    discover_page = client.get("/discover").content
    assert b"In Home stations" in discover_page and b"/stations/remove" not in discover_page
    assert "msg=" in _form(client, "/stations/remove", {"station_id": "kexp"}).headers["location"]
    assert db.get_station("kexp") is None


def test_logos_go_through_signed_art_links(client, monkeypatch, tmp_path):
    from app.services import art

    src = "https://cdn.example.test/logo.png"
    link = art.art_url(src)
    assert link.startswith("/art/") and "u=https%3A%2F%2Fcdn.example.test%2Flogo.png" in link
    assert art.art_url("javascript:alert(1)") == ""

    sig = link.split("/")[2].split("?")[0]
    assert client.get(f"/art/{sig}?u=https%3A%2F%2Fother.example.test%2Fx.png").status_code == 404

    png = tmp_path / "logo.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(art, "get", lambda u: (png, "image/png") if u == src else None)
    resp = client.get(link)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_art_never_fetches_from_the_pi_or_the_lan():
    from app.services import art

    for url in ("http://127.0.0.1:8018/x.png", "http://192.168.1.5/x.png", "http://localhost/x.png", "https://example.com:8443/x.png"):
        with pytest.raises(art.Blocked):
            art._check(url)
    assert art.get("http://127.0.0.1:8018/logo.png") is None


def test_webradiodb_pictures_use_the_public_site():
    from app.services import mympd

    assert mympd._image_url("Stream1_liferadio_at.webp") == mympd.WEBRADIODB_PICS + "Stream1_liferadio_at.webp"
    assert mympd._image_url("https://x.test/a.png") == "https://x.test/a.png"
    assert mympd._image_url("/browse/webradiodb/pics/a.webp") == ""


def test_now_volume_is_live_and_silent(client):
    from stonepi_auth.session import CSRF_COOKIE

    from app import db

    db.set_person_pref("", "output", "stonepi")  # the server-side volume is StonePi's / a speaker's
    client.cookies.set(CSRF_COOKIE, "tok")
    resp = client.post("/volume", data={"csrf_token": "tok", "level": "0.3"}, headers={"X-Requested-With": "fetch"})
    assert resp.json() == {"ok": True, "message": "", "volume": pytest.approx(0.3)}
    resp = client.post("/volume", data={"csrf_token": "tok", "delta": "0.05"}, headers={"X-Requested-With": "fetch"})
    assert resp.json()["volume"] == pytest.approx(0.35)
    # Without JS: back to Now with no "Volume updated" message.
    resp = client.post("/volume", data={"csrf_token": "tok", "delta": "-0.05", "level": "0.9"}, follow_redirects=False)
    assert resp.status_code == 303 and "msg=" not in resp.headers["location"]
    page = client.get("/now").content
    assert b"data-volume" in page and b"Set volume" not in page and b"30%" in page


def test_nav_has_no_home_and_messages_are_toasts(client):
    page = client.get("/?msg=Saved%20FIP").content.decode()
    start = page.index('aria-label="Main"')
    nav = page[start:page.index("</nav>", start)]
    assert ">Home<" not in nav and nav.count("<a ") == 4
    assert 'data-toast-message="Saved FIP"' in page and 'banner banner-ok' not in page


def test_saving_a_moved_station_updates_the_favorite(client):
    from app import db

    db.add_favorite("u-ann", name="FIP", stream_url="http://old.example.test/fip.aac", station_id="fip")
    db.add_favorite("u-ann", name="FIP", stream_url="https://new.example.test/fip.aac", station_id="fip")
    favs = db.list_favorites("u-ann")
    assert [(f["id"], f["stream_url"]) for f in favs] == [("fip", "https://new.example.test/fip.aac")]


def test_my_radio_shows_home_stations_and_favourites_together(client):
    from app import db

    db.add_favorite("", name="FIP", stream_url="https://icecast.radiofrance.fr/fip-hifi.aac", station_id="fip")
    db.add_favorite("", name="Jazz24", stream_url="https://live.example.test/jazz24.mp3", station_id="jazz24")
    page = client.get("/").content.decode()
    home, _, favs = page.partition('id="favourites"')
    assert "Home stations" in home and "KEXP" in home and "Jazz24" not in home
    assert "FIP" in favs and "Jazz24" in favs and "KEXP" not in favs
    # In My favourites a Home station says so; its bin is only under Home stations.
    assert "In Home stations" in favs and "/stations/remove" not in favs and "/stations/remove" in home
    # The old favourites view and /favorites land on the section.
    assert client.get("/?show=favorites", follow_redirects=False).headers["location"].endswith("/#favourites")
    assert client.get("/favorites", follow_redirects=False).headers["location"].endswith("/#favourites")
    listen = page
    assert 'data-sheet="add-station"' in listen and "sp-fab" in listen
    assert 'data-sheet="add-station" hidden' not in listen.replace('class="sheet" hidden data-sheet', "")  # sanity
    assert 'class="sheet is-open"' in client.get("/?add=1").content.decode()
    # A search from the header lands on Discover.
    assert client.get("/?q=jazz", follow_redirects=False).headers["location"].endswith("/discover?q=jazz")


def test_discover_sections_genres_and_search(client):
    from app import db

    db.add_favorite("", name="KEXP", stream_url="https://kexp.streamguys1.com/kexp160.aac", tags="alternative,indie", station_id="kexp")
    page = client.get("/discover").content.decode()
    assert "Trending now" in page and "More like your favourites" in page
    assert 'href="/discover?genre=jazz"' in page
    genre = client.get("/discover?genre=jazz").content.decode()
    assert "Jazz stations" in genre and "FIP" in genre
    found = client.get("/discover?q=soma").content.decode()
    assert "SomaFM Groove Salad" in found and "BBC Radio 6" not in found
    nav = found[found.index('aria-label="Main"'):]
    assert 'href="/discover" class="is-active"' in nav


def test_country_setting(client, monkeypatch):
    from app import db
    from app.services import discover

    monkeypatch.setattr(discover, "country_from_timezone", lambda: "DK")
    assert discover.household_country() == "DK"
    _form(client, "/settings/general", {"default_volume": "0.6", "country": "gb"})
    assert db.get_pref("country") == "GB" and discover.household_country() == "GB"
    _form(client, "/settings/general", {"default_volume": "0.6", "country": "-"})
    assert discover.household_country() == ""
    _form(client, "/settings/general", {"default_volume": "0.6", "country": "nope"})
    assert db.get_pref("country") == "-"  # junk is ignored


def test_my_radio_order_is_each_persons_choice(client):
    from app import db, routes

    db.add_favorite("", name="FIP", stream_url="https://icecast.radiofrance.fr/fip-hifi.aac", station_id="fip")
    page = client.get("/").text
    assert page.index('id="home-stations"') < page.index('id="favourites"')  # the default
    settings = client.get("/settings?tab=general").text
    assert 'name="first" value="favourites"' in settings
    hub = client.get("/settings").text
    assert "tab=display" not in hub  # all in General now
    assert client.get("/settings?tab=myradio", follow_redirects=False).headers["location"].endswith("/settings?tab=general")

    resp = _form(client, "/settings/general", {"first": "favourites"})
    assert "msg=" in resp.headers["location"]
    assert db.get_person_pref("", routes.MY_RADIO_FIRST_KEY) == "favourites"
    page = client.get("/").text
    assert page.index('id="favourites"') < page.index('id="home-stations"')
    # Favourites first doesn't leak its "In Home stations" note into Home stations: they keep the bin.
    home = page[page.index('id="home-stations"'):]
    assert "/stations/remove" in home and "In Home stations" not in home
    assert "error=" in _form(client, "/settings/general", {"first": "sideways"}).headers["location"]
