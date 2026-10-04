"""Sign-in returns people to the page they asked for, even a Dashboard page behind /auth."""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="auth-next-test-")
os.environ.setdefault("STONEPI_SESSION_SECRET", "test-secret")
os.environ.setdefault("STONEPI_DATA_DIR", _TMP)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/users.sqlite")
os.environ.setdefault("STONEPI_VAULT_DIR", os.path.join(_TMP, "vault"))

from starlette.requests import Request  # noqa: E402

import pytest  # noqa: E402

from app import routes  # noqa: E402
from app.routes import _resolve_next  # noqa: E402
from stonepi_auth.prefix import rewrite_location  # noqa: E402


@pytest.fixture(autouse=True)
def _pi_install(monkeypatch):
    # Behind nginx on the Pi: no loopback PUBLIC_ORIGIN, the browser's host wins.
    monkeypatch.setattr(routes.env, "public_origin", "")


def _request(host: str = "stonepi.home") -> Request:
    return Request({"type": "http", "method": "GET", "path": "/login", "scheme": "http", "headers": [(b"host", host.encode())], "query_string": b""})


def test_dashboard_page_next_is_absolute_so_the_auth_prefix_leaves_it():
    nxt = _resolve_next("/notifications", _request())
    assert nxt == "http://stonepi.home/notifications"
    assert rewrite_location(nxt, "/auth") == nxt


def test_other_app_and_query_paths_keep_their_host():
    assert _resolve_next("/news/?tab=1", _request()) == "http://stonepi.home/news/?tab=1"


def test_offsite_next_is_still_refused():
    assert _resolve_next("https://evil.example/x", _request()).rstrip("/") == "http://stonepi.home"
