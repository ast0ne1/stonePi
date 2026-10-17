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
    page = client.get("/displays/carthing").text
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
    assert "Connected as" in client.get("/displays/carthing").text


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


def test_editor_has_music_tab():
    page = admin_client().get("/displays/carthing").text
    assert 'data-ct-tab="music"' in page and "data-ct-music-pick" in page and "Play music preset 1" in page
