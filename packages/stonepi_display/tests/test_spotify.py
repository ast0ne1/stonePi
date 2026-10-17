"""Spotify sign-in helpers and the panel's TokenSource (F7 S1)."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from stonepi_display import spotify

CLIENT_ID = "0123456789abcdef0123456789abcdef"


def test_valid_client_id():
    assert spotify.valid_client_id(CLIENT_ID)
    assert not spotify.valid_client_id("short")
    assert not spotify.valid_client_id("z" * 32)


def test_pkce_challenge_matches_verifier():
    verifier, challenge = spotify.new_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 128


def test_authorize_url_carries_pkce_and_scopes():
    url = spotify.authorize_url(CLIENT_ID, spotify.LOOPBACK_REDIRECT, "st", "ch")
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"] == ["ch"]
    assert query["redirect_uri"] == [spotify.LOOPBACK_REDIRECT] and query["state"] == ["st"]
    assert "user-modify-playback-state" in query["scope"][0] and "user-library-modify" in query["scope"][0]


@pytest.mark.parametrize(
    "text",
    ["http://127.0.0.1:8888/callback?code=abc&state=xyz", "  ?code=abc&state=xyz ", "code=abc&state=xyz"],
)
def test_parse_callback(text):
    assert spotify.parse_callback(text) == {"code": "abc", "state": "xyz"}


def test_parse_callback_error():
    assert spotify.parse_callback("http://127.0.0.1:8888/callback?error=access_denied&state=s")["error"] == "access_denied"


class Vault(dict):
    reloads = 0

    def read(self, key):
        return self.get(key, "")

    def write(self, key, value):
        self[key] = value

    def reload(self):
        Vault.reloads += 1


def _source(vault: Vault, handler, clock=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return spotify.TokenSource(
        read_secret=vault.read, write_secret=vault.write, reload_secrets=vault.reload, client=client,
        clock=clock or (lambda: 1000.0),
    )


def _connected() -> Vault:
    return Vault({spotify.CLIENT_ID_KEY: CLIENT_ID, spotify.REFRESH_TOKEN_KEY: "r1"})


def test_not_connected():
    source = _source(Vault(), lambda request: httpx.Response(500))
    assert source.status()["connected"] is False
    with pytest.raises(spotify.SpotifyAuthError) as err:
        source.access_token()
    assert err.value.code == "not_connected"


def test_refresh_caches_until_expiry_and_saves_rotated_token():
    calls = []
    now = {"t": 1000.0}

    def handler(request):
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": f"a{len(calls)}", "expires_in": 3600, "refresh_token": "r2"})

    vault = _connected()
    source = _source(vault, handler, clock=lambda: now["t"])
    assert source.access_token() == "a1"
    assert calls[0]["grant_type"] == ["refresh_token"] and calls[0]["client_id"] == [CLIENT_ID]
    assert "client_secret" not in calls[0]
    assert vault[spotify.REFRESH_TOKEN_KEY] == "r2"
    assert source.access_token() == "a1" and len(calls) == 1
    now["t"] += 3600
    assert source.access_token() == "a2" and calls[1]["refresh_token"] == ["r2"]
    assert source.status() == {"connected": True, "token": True, "error": "", "error_code": ""}


def test_revoked_refresh_token_is_not_retried_until_it_changes():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token revoked"})

    vault = _connected()
    source = _source(vault, handler)
    for _ in range(2):
        with pytest.raises(spotify.SpotifyAuthError) as err:
            source.access_token()
        assert err.value.code == "invalid_grant"
    assert len(calls) == 1
    assert "Connect again" in source.status()["error"]
    vault[spotify.REFRESH_TOKEN_KEY] = "fresh"
    with pytest.raises(spotify.SpotifyAuthError):
        source.access_token()
    assert len(calls) == 2


def test_invalidate_forgets_token_and_rereads_vault():
    seq = iter(["a1", "a2"])
    source = _source(_connected(), lambda request: httpx.Response(200, json={"access_token": next(seq), "expires_in": 3600}))
    before = Vault.reloads
    assert source.access_token() == "a1"
    source.invalidate()
    assert Vault.reloads == before + 1
    assert source.status()["token"] is False
    assert source.access_token() == "a2"


def test_network_error_is_reported():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    source = _source(_connected(), handler)
    with pytest.raises(spotify.SpotifyAuthError) as err:
        source.access_token()
    assert err.value.code == "network"


def test_fetch_profile_forbidden_explains_user_management():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
    with pytest.raises(spotify.SpotifyAuthError) as err:
        spotify.fetch_profile("tok", client=client)
    assert "User Management" in str(err.value)


def test_fetch_profile_name():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"id": "u1", "display_name": "Adam"}))
    )
    assert spotify.fetch_profile("tok", client=client) == {"id": "u1", "name": "Adam"}


@pytest.mark.parametrize(
    "text, uri",
    [
        ("spotify:album:4aawyAB9vmqN3uQ7FjRGTy", "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"),
        ("https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl?si=abc", "spotify:track:11dFghVXANMlKmJXsNCbNl"),
        ("https://open.spotify.com/intl-de/artist/0OdUWJ0sBjDrqHygGUXeCF", "spotify:artist:0OdUWJ0sBjDrqHygGUXeCF"),
        ("spotify:user:me:collection", ""),
        ("https://example.com/playlist/37i9dQZF1DXcBWIGoYBM5M", ""),
        ("", ""),
    ],
)
def test_normalize_uri(text, uri):
    assert spotify.normalize_uri(text) == uri
