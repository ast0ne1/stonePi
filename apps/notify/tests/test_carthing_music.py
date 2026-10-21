"""Displays → Car Thing → Music: Spotify client ID, PKCE connect (HTTPS callback or paste-back), disconnect."""

from __future__ import annotations

import os
import tempfile
import uuid
from urllib.parse import parse_qs, urlparse

import pytest

os.environ.setdefault("STONEPI_SESSION_SECRET", "test-session-secret")
os.environ.setdefault("STONEPI_DATA_DIR", tempfile.mkdtemp(prefix="notify-test-"))
os.environ.setdefault("STONEPI_EMIT_RETRY_PATH", os.path.join(os.environ["STONEPI_DATA_DIR"], "emit-retry.jsonl"))

import stonepi_vault  # noqa: E402
import stonepi_vault.store  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from stonepi_auth.session import COOKIE_NAME, CSRF_COOKIE, encode_session  # noqa: E402
from stonepi_display import carthing, spotify  # noqa: E402

from app import carthing_routes  # noqa: E402
from app.main import app  # noqa: E402

SECRET = os.environ["STONEPI_SESSION_SECRET"]
CSRF = "csrf-token-123"
CLIENT_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    carthing.configure_carthing(tmp_path)
    vault = stonepi_vault.Vault(tmp_path / "vault")
    monkeypatch.setattr(stonepi_vault.store, "_vault", vault)
    monkeypatch.setattr(carthing_routes, "_secret", lambda: SECRET)
    panel_calls: list[str] = []
    monkeypatch.setattr(carthing_routes, "_panel_signed", lambda method, path, **kw: panel_calls.append(path))
    with carthing_routes._pending_lock:
        carthing_routes._pending.clear()
    return {"vault": vault, "panel": panel_calls}


def admin_client(**kwargs) -> TestClient:
    client = TestClient(app, follow_redirects=False, **kwargs)
    client.cookies.set(CSRF_COOKIE, CSRF)
    client.cookies.set(
        COOKIE_NAME,
        encode_session(
            secret=SECRET, user_id=str(uuid.uuid4()), username="a", display_name="A", is_admin=True,
            apps=["notify"], session_id="s1",
        ),
    )
    return client


def post(client, **form):
    return client.post("/displays/carthing/music", data={"csrf_token": CSRF, **form})


def fake_spotify(monkeypatch, *, refresh="r1"):
    seen = {}

    def exchange(client_id, code, redirect_uri, verifier, *, client=None):
        seen.update(client_id=client_id, code=code, redirect_uri=redirect_uri, verifier=verifier)
        return {"access_token": "a1", "refresh_token": refresh, "expires_in": 3600}

    monkeypatch.setattr(spotify, "exchange_code", exchange)
    monkeypatch.setattr(spotify, "fetch_profile", lambda token, client=None: {"id": "u1", "name": "Adam"})
    return seen


def test_client_id_is_validated_and_saved(store):
    client = admin_client()
    assert "err=" in post(client, action="client", client_id="nope").headers["location"]
    response = post(client, action="client", client_id=CLIENT_ID)
    assert "msg=" in response.headers["location"]
    assert store["vault"].get(spotify.CLIENT_ID_KEY) == CLIENT_ID
    assert "/internal/music/reset" in store["panel"]


def test_connect_needs_client_id():
    assert "err=" in post(admin_client(), action="connect", mode="https").headers["location"]


def test_http_page_uses_loopback_and_paste_finishes(store, monkeypatch):
    store["vault"].set(spotify.CLIENT_ID_KEY, CLIENT_ID)
    seen = fake_spotify(monkeypatch)
    client = admin_client()
    page = client.get("/displays/carthing/setup").text
    assert "Connect Spotify" in page and "Address from the error page" in page
    response = post(client, action="connect", mode="https")
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "accounts.spotify.com"
    assert query["redirect_uri"] == [spotify.LOOPBACK_REDIRECT]
    state = query["state"][0]
    pasted = f"{spotify.LOOPBACK_REDIRECT}?code=the-code&state={state}"
    done = post(client, action="paste", callback_url=pasted)
    assert "msg=" in done.headers["location"] and "Adam" in done.headers["location"]
    assert seen["code"] == "the-code" and seen["redirect_uri"] == spotify.LOOPBACK_REDIRECT and seen["verifier"]
    assert store["vault"].get(spotify.REFRESH_TOKEN_KEY) == "r1"
    assert carthing.music_account()["name"] == "Adam"
    # The state is single-use.
    assert "err=" in post(client, action="paste", callback_url=pasted).headers["location"]
    assert "Connected as" in client.get("/displays/carthing/setup").text


def test_https_page_uses_its_own_callback(store, monkeypatch):
    store["vault"].set(spotify.CLIENT_ID_KEY, CLIENT_ID)
    seen = fake_spotify(monkeypatch)
    client = admin_client(base_url="https://stonepi.tail1234.ts.net")
    response = post(client, action="connect", mode="https")
    query = parse_qs(urlparse(response.headers["location"]).query)
    expected = f"https://stonepi.tail1234.ts.net{spotify.CALLBACK_PATH}"
    assert query["redirect_uri"] == [expected]
    back = client.get(spotify.CALLBACK_PATH, params={"code": "c2", "state": query["state"][0]})
    assert back.status_code == 303 and "msg=" in back.headers["location"]
    assert seen["redirect_uri"] == expected
    assert store["vault"].get(spotify.REFRESH_TOKEN_KEY) == "r1"


def test_callback_with_unknown_state_or_cancel(store):
    client = admin_client()
    assert "err=" in client.get(spotify.CALLBACK_PATH, params={"code": "c", "state": "forged"}).headers["location"]
    assert "cancelled" in client.get(spotify.CALLBACK_PATH, params={"error": "access_denied", "state": "x"}).headers["location"]


def test_paste_rejects_junk():
    assert "err=" in post(admin_client(), action="paste", callback_url="hello").headers["location"]


def test_disconnect_clears_token_and_account(store):
    store["vault"].set(spotify.CLIENT_ID_KEY, CLIENT_ID)
    store["vault"].set(spotify.REFRESH_TOKEN_KEY, "r1")
    carthing.save_music_account("Adam", "u1")
    response = post(admin_client(), action="disconnect")
    assert "msg=" in response.headers["location"]
    assert store["vault"].get(spotify.REFRESH_TOKEN_KEY) == ""
    assert store["vault"].get(spotify.CLIENT_ID_KEY) == CLIENT_ID
    assert carthing.music_account()["name"] == ""
    assert "/internal/music/reset" in store["panel"]


def test_non_admin_cannot_post():
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(CSRF_COOKIE, CSRF)
    client.cookies.set(
        COOKIE_NAME,
        encode_session(secret=SECRET, user_id="u", username="m", display_name="M", is_admin=False, apps=["notify"], session_id="s"),
    )
    response = post(client, action="client", client_id=CLIENT_ID)
    assert response.status_code in (303, 403) and "msg=" not in response.headers.get("location", "")


def test_music_catalog_comes_from_the_panel(store, monkeypatch):
    client = admin_client()
    assert client.get("/api/carthing/music/catalog").json()["ok"] is False  # panel down
    assert "/internal/music/catalog" in store["panel"]

    class Reply:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True, "devices": [{"id": "d1", "name": "Kitchen"}], "playlists": []}

    monkeypatch.setattr(carthing_routes, "_panel_signed", lambda method, path, **kw: Reply())
    assert client.get("/api/carthing/music/catalog").json()["devices"][0]["name"] == "Kitchen"


def test_editor_has_music_settings():
    # Music settings sit under Look (and open on a Music widget's row); there's no Music tab.
    page = admin_client().get("/displays/carthing").text
    assert 'data-ct-box="music"' in page and "data-ct-music-pick" in page and "Play music preset 1" in page
    assert 'data-ct-tab="music"' not in page


def test_card_shows_expiry_and_admin_can_change_it(store):
    store["vault"].set(spotify.CLIENT_ID_KEY, CLIENT_ID)
    store["vault"].set(spotify.REFRESH_TOKEN_KEY, "r1")
    carthing.save_music_account("Adam", "u1")
    client = admin_client()
    expected = carthing.music_expiry()["expires_on"]
    page = client.get("/displays/carthing/setup").text
    assert "Sign-in lasts until about" in page and expected in page
    # The editor's Spotify pill shows the same date and links to Setup.
    assert f"until {expected}" in client.get("/displays/carthing").text
    assert "err=" in post(client, action="expiry", expires_on="nope").headers["location"]
    assert "msg=" in post(client, action="expiry", expires_on="2027-01-02").headers["location"]
    assert carthing.music_account()["expires_on"] == "2027-01-02"
    post(client, action="expiry", reset="1")
    assert carthing.music_account()["expires_on"] is None


def test_expiry_check_alerts_once_per_date(store, monkeypatch):
    from datetime import date

    import stonepi_notify

    sent = []
    monkeypatch.setattr(stonepi_notify, "ingest_event", lambda event: sent.append(event) or {"ok": True})
    store["vault"].set(spotify.REFRESH_TOKEN_KEY, "r1")
    carthing.save_music_account("Adam", "u1")
    carthing.set_music_expiry("2027-04-17")
    assert carthing_routes.check_music_expiry(today=date(2027, 3, 1)) == ""
    assert carthing_routes.check_music_expiry(today=date(2027, 4, 10)) == "soon"
    assert carthing_routes.check_music_expiry(today=date(2027, 4, 11)) == ""
    assert carthing_routes.check_music_expiry(today=date(2027, 4, 17)) == "expired"
    assert carthing_routes.check_music_expiry(today=date(2027, 4, 18)) == ""
    assert [e["id"] for e in sent] == ["carthing.spotify_expiring", "carthing.spotify_expired"]
    assert all(e["audience"] == "admin" for e in sent)
    carthing.set_music_expiry("2027-06-01")  # a new date warns again
    assert carthing_routes.check_music_expiry(today=date(2027, 5, 25)) == "soon"


def test_expiry_check_skips_when_disconnected(store):
    from datetime import date

    carthing.save_music_account("Adam", "u1")
    assert carthing_routes.check_music_expiry(today=date(2030, 1, 1)) == ""


def test_daily_expiry_check_runs_in_the_morning_and_retries_hourly(store, monkeypatch, caplog):
    import logging
    from datetime import datetime, timedelta

    runs = []
    outcomes = iter([RuntimeError("vault"), RuntimeError("vault"), RuntimeError("vault"), "", ""])

    def fake_check(today=None):
        runs.append(today)
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(carthing_routes, "check_music_expiry", fake_check)
    monkeypatch.setattr(carthing_routes, "_expiry_checked_on", "")
    monkeypatch.setattr(carthing_routes, "_expiry_retry_at", None)
    monkeypatch.setattr(carthing_routes, "_expiry_failures", 0)
    caplog.set_level(logging.INFO, logger="notify.carthing")
    night = datetime(2027, 4, 10, 1, 0).astimezone()
    carthing_routes.check_music_expiry_daily(night)
    assert runs == []  # before 09:00 local
    morning = night.replace(hour=9)
    for minute in range(0, 60, 1):  # fails at 09:00, then waits an hour
        carthing_routes.check_music_expiry_daily(morning + timedelta(minutes=minute))
    assert len(runs) == 1
    carthing_routes.check_music_expiry_daily(morning + timedelta(hours=1))  # fails again
    carthing_routes.check_music_expiry_daily(morning + timedelta(hours=2))  # and again
    carthing_routes.check_music_expiry_daily(morning + timedelta(hours=3))  # works
    carthing_routes.check_music_expiry_daily(morning + timedelta(hours=6))  # done for today
    assert len(runs) == 4
    # The traceback once, then one line per failure, then a note that it recovered.
    records = [r for r in caplog.records if "expiry check" in r.getMessage()]
    assert [r.exc_info is not None for r in records] == [True, False, False, False]
    assert "2 in a row" in records[1].getMessage() and "working again" in records[3].getMessage()
    carthing_routes.check_music_expiry_daily(morning + timedelta(days=1))
    assert len(runs) == 5


def test_card_render_doesnt_ask_the_panel_service(store, monkeypatch):
    store["vault"].set(spotify.CLIENT_ID_KEY, CLIENT_ID)
    store["vault"].set(spotify.REFRESH_TOKEN_KEY, "r1")
    carthing.save_music_account("Adam", "u1")
    store["panel"].clear()
    page = admin_client().get("/displays/carthing/setup").text
    assert "data-ct-expiry-revoked" in page and "/internal/status" not in store["panel"]


def test_editor_has_now_playing_look():
    page = admin_client().get("/displays/carthing").text
    for bind in ("music.np.background", "music.np.controls", "music.np.accent", "music.np.edge"):
        assert f'data-bind="{bind}"' in page
    assert "Glow behind the artwork" in page and "Blurred artwork" in page
