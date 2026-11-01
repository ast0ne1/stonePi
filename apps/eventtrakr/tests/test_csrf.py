"""Every browser POST needs the CSRF token: csrf_token in forms, X-StonePi-CSRF on background saves."""

from __future__ import annotations

from app import create_app
from app.db import SessionLocal
from app.models import User
from app.services.auth import create_session_token
from stonepi_auth.session import CSRF_COOKIE

TOKEN = "csrf-test-token"


def _client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_IN_TESTS"] = True
    client = app.test_client()
    with SessionLocal() as db:
        admin = db.query(User).filter_by(role="admin").first()
        client.set_cookie("eventtrakr_session", create_session_token(admin.id, admin.username, admin.role))
    return client


def test_pages_carry_the_token():
    client = _client()
    client.set_cookie(CSRF_COOKIE, TOKEN)
    html = client.get("/settings/").get_data(as_text=True) or client.get("/settings").get_data(as_text=True)
    assert f'<meta name="csrf-token" content="{TOKEN}">' in html
    assert f'name="csrf_token" value="{TOKEN}"' in html


def test_first_page_sets_the_cookie():
    client = _client()
    response = client.get("/agenda")
    assert CSRF_COOKIE in response.headers.get("Set-Cookie", "")


def test_form_post_without_token_is_refused_and_with_it_goes_through():
    client = _client()
    client.set_cookie(CSRF_COOKIE, TOKEN)
    refused = client.post("/settings/privacy", data={}, headers={"Referer": "/settings"})
    assert refused.status_code == 302 and refused.headers["Location"].endswith("/settings")
    allowed = client.post("/settings/privacy", data={"csrf_token": TOKEN})
    assert allowed.status_code != 403 and "expired" not in allowed.get_data(as_text=True)


def test_background_post_needs_the_header():
    client = _client()
    client.set_cookie(CSRF_COOKIE, TOKEN)
    refused = client.post("/api/sync/stop")
    assert refused.status_code == 403 and refused.get_json()["ok"] is False
    assert client.post("/api/sync/stop", headers={"X-StonePi-CSRF": TOKEN}).status_code == 200
    assert client.post("/api/sync/stop", headers={"X-StonePi-CSRF": "wrong"}).status_code == 403


def test_signed_agent_read_is_exempt():
    client = _client()
    # No CSRF token: the signed endpoint answers its own way (404 for a bad signature), not 403.
    assert client.post("/api/internal/agent", json={"tool": "x"}).status_code == 404


def test_log_out_from_the_shared_header_still_works():
    client = _client()
    response = client.post("/logout", headers={"Referer": "/settings"})  # the header's form sends no token
    # Refused, it would go back to /settings; signed out, it goes on to the login page.
    assert response.status_code in (302, 303) and not response.headers["Location"].endswith("/settings")


def test_cookie_renewed_on_every_page_and_refusals_stay_on_site():
    client = _client()
    client.set_cookie(CSRF_COOKIE, TOKEN)
    assert f"{CSRF_COOKIE}={TOKEN}" in client.get("/agenda").headers.get("Set-Cookie", "")  # renewed
    away = client.post("/settings/privacy", data={}, headers={"Referer": "https://evil.example/x"})
    assert away.status_code == 302 and "evil.example" not in away.headers["Location"]
