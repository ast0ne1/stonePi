"""Spotify sign-in and access tokens for the Car Thing Music mini-app (F7, ideation/carthing-spotify.md).

Notify runs the sign-in (authorization code + PKCE, so there is no client secret) and saves the
refresh token in the vault. The panel service holds a ``TokenSource``: access tokens live only in
its memory, and when Spotify rotates the refresh token it writes the new one back to the vault.
One Spotify account for the house.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

CLIENT_ID_KEY = "SPOTIFY_CLIENT_ID"
REFRESH_TOKEN_KEY = "SPOTIFY_REFRESH_TOKEN"

SCOPES = (
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "user-library-modify",
)
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"

# Spotify allows plain http only for loopback IP literals. Without HTTPS the browser lands on
# an error page at this address and the admin pastes it back into Notify.
LOOPBACK_REDIRECT = "http://127.0.0.1:8888/callback"
CALLBACK_PATH = "/displays/carthing/spotify/callback"

_CLIENT_ID_CHARS = frozenset("0123456789abcdefABCDEF")
# Renew a little before Spotify's expiry so a call never goes out with a dying token.
_EARLY_S = 60.0


class SpotifyAuthError(Exception):
    """Sign-in or token problem. ``str()`` is safe to show an admin."""

    def __init__(self, message: str, *, code: str = "error"):
        super().__init__(message)
        self.code = code


def valid_client_id(value: str) -> bool:
    value = (value or "").strip()
    return len(value) == 32 and set(value) <= _CLIENT_ID_CHARS


# What a preset can start. Tracks play on their own (``uris``); the rest are contexts.
URI_KINDS = ("playlist", "album", "artist", "show", "track", "episode")
_URI = re.compile(r"^spotify:(playlist|album|artist|show|track|episode):([A-Za-z0-9]{22})$")
_LINK = re.compile(r"^https?://open\.spotify\.com/(?:intl-[a-z-]+/)?(playlist|album|artist|show|track|episode)/([A-Za-z0-9]{22})")


def normalize_uri(text: Any) -> str:
    """``spotify:playlist:…`` from a URI or an open.spotify.com link; "" when it's neither."""
    raw = str(text or "").strip()
    match = _URI.match(raw) or _LINK.match(raw)
    return f"spotify:{match.group(1)}:{match.group(2)}" if match else ""


def uri_kind(uri: str) -> str:
    parts = str(uri or "").split(":")
    return parts[1] if len(parts) == 3 else ""


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
        }
    )


def parse_callback(text: str) -> dict[str, str]:
    """``code`` / ``state`` / ``error`` from a pasted callback address (or just its query)."""
    raw = (text or "").strip()
    query = urlparse(raw).query if "://" in raw else raw.lstrip("?")
    values = parse_qs(query)
    return {key: values[key][0] for key in ("code", "state", "error") if values.get(key)}


def _token_call(data: dict[str, str], client: httpx.Client | None) -> dict[str, Any]:
    try:
        if client is not None:
            response = client.post(TOKEN_URL, data=data, timeout=10.0)
        else:
            response = httpx.post(TOKEN_URL, data=data, timeout=10.0)
    except httpx.HTTPError as exc:
        raise SpotifyAuthError("Spotify isn't reachable right now.", code="network") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 200 and body.get("access_token"):
        return body
    error = str(body.get("error") or response.status_code)
    if error == "invalid_grant":
        raise SpotifyAuthError("Spotify sign-in expired or was revoked. Connect again in Notify.", code="invalid_grant")
    if error == "invalid_client":
        raise SpotifyAuthError("Spotify doesn't recognise that client ID.", code="invalid_client")
    detail = str(body.get("error_description") or error)[:120]
    raise SpotifyAuthError(f"Spotify refused the sign-in: {detail}", code=error)


def exchange_code(
    client_id: str, code: str, redirect_uri: str, verifier: str, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    return _token_call(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        client,
    )


def refresh_access(client_id: str, refresh_token: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    return _token_call(
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}, client
    )


def fetch_profile(access_token: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """``/me``: the account name shown in Notify. Spotify no longer returns ``product`` here."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        if client is not None:
            response = client.get(f"{API_URL}/me", headers=headers, timeout=10.0)
        else:
            response = httpx.get(f"{API_URL}/me", headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        raise SpotifyAuthError("Spotify isn't reachable right now.", code="network") from exc
    if response.status_code == 403:
        # Development mode: the account isn't on the app's User Management list.
        raise SpotifyAuthError(
            "Spotify blocked this account. Add it under User Management in your Spotify developer app.",
            code="forbidden",
        )
    if response.status_code != 200:
        raise SpotifyAuthError(f"Spotify didn't return the account ({response.status_code}).", code="profile")
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {"id": str(body.get("id") or ""), "name": str(body.get("display_name") or body.get("id") or "")}


def _vault_get(key: str) -> str:
    try:
        from stonepi_vault import get_secret

        return (get_secret(key, env_name=key, default="") or "").strip()
    except Exception:  # noqa: BLE001 - vault unreadable: treat as not connected
        return ""


def _vault_set(key: str, value: str) -> None:
    from stonepi_vault import set_secret

    set_secret(key, value)


def _vault_reload() -> None:
    try:
        from stonepi_vault import get_vault

        get_vault().reload()
    except Exception:  # noqa: BLE001
        pass


class TokenSource:
    """Access tokens for the panel service: refreshed on demand, never written to disk."""

    def __init__(
        self,
        *,
        read_secret: Callable[[str], str] = _vault_get,
        write_secret: Callable[[str, str], None] = _vault_set,
        reload_secrets: Callable[[], None] = _vault_reload,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._read = read_secret
        self._write = write_secret
        self._reload = reload_secrets
        self._client = client
        self._clock = clock
        self._lock = threading.Lock()
        self._access = ""
        self._expires_at = 0.0
        self._error = ""
        self._error_code = ""
        # The refresh token that last failed with invalid_grant: don't retry it until the
        # vault holds a different one (a new Connect in Notify).
        self._dead_refresh = ""

    def connected(self) -> bool:
        return bool(self._read(CLIENT_ID_KEY) and self._read(REFRESH_TOKEN_KEY))

    def access_token(self) -> str:
        with self._lock:
            if self._access and self._clock() < self._expires_at:
                return self._access
            client_id = self._read(CLIENT_ID_KEY)
            refresh = self._read(REFRESH_TOKEN_KEY)
            if not client_id or not refresh:
                self._access = ""
                raise SpotifyAuthError("Spotify isn't connected. Connect it in Notify → Displays → Car Thing.", code="not_connected")
            if refresh == self._dead_refresh:
                raise SpotifyAuthError(self._error, code=self._error_code)
            try:
                body = refresh_access(client_id, refresh, client=self._client)
            except SpotifyAuthError as exc:
                self._access = ""
                self._error, self._error_code = str(exc), exc.code
                if exc.code in {"invalid_grant", "invalid_client"}:
                    self._dead_refresh = refresh
                raise
            self._access = str(body["access_token"])
            self._expires_at = self._clock() + max(60.0, float(body.get("expires_in") or 3600) - _EARLY_S)
            self._error = self._error_code = ""
            rotated = str(body.get("refresh_token") or "")
            if rotated and rotated != refresh:
                try:
                    self._write(REFRESH_TOKEN_KEY, rotated)
                except Exception as exc:  # noqa: BLE001 - keep the access token; say why the next restart may fail
                    self._error = f"Couldn't save Spotify's new refresh token: {exc}"[:160]
                    self._error_code = "save_failed"
            return self._access

    def drop_access(self) -> None:
        """Spotify said 401 to a live token: get a new one next time, same refresh token."""
        with self._lock:
            self._access = ""
            self._expires_at = 0.0

    def invalidate(self) -> None:
        """Drop the access token (after a 401, or when Notify connects or disconnects)."""
        with self._lock:
            self._access = ""
            self._expires_at = 0.0
            self._dead_refresh = ""
            self._error = self._error_code = ""
        self._reload()

    def status(self) -> dict[str, Any]:
        with self._lock:
            live = bool(self._access) and self._clock() < self._expires_at
            return {
                "connected": self.connected(),
                "token": live,
                "error": self._error,
                "error_code": self._error_code,
            }
