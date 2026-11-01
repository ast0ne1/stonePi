"""Signed-in test clients pass the CSRF check every NewsCast POST makes (require_csrf)."""

from __future__ import annotations

from stonepi_auth.session import CSRF_COOKIE

TOKEN = "test-csrf-token"


def with_csrf(client):
    """Give ``client`` the CSRF cookie and send the matching X-StonePi-CSRF header on every request."""
    client.cookies.set(CSRF_COOKIE, TOKEN)
    client.headers["X-StonePi-CSRF"] = TOKEN
    return client
