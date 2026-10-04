"""Car Thing panel: pairing, fragments, PIN-gated restart, preview proxy auth."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from stonepi_auth.internal import sign_internal
from stonepi_display import carthing

from app import routes, state

SECRET = "carthing-test-secret"
TOKEN = "device-token-123"


def _panel(*, enabled: bool = True, pin: str | None = None, config: dict | None = None) -> dict:
    record = {"enabled": False, "hash": "", "salt": "", "max_attempts": 3, "lockout_s": 60}
    if pin:
        salt = "00" * 16
        record.update(enabled=True, salt=salt, hash=carthing._pin_digest(pin, salt))
    cfg = config or carthing.default_config()
    return {"ok": True, "enabled": enabled, "token_hash": carthing.hash_token(TOKEN), "pin": record, "config": cfg, "rev": "r1"}


FEEDS = {
    "system": {
        "ok": True,
        "card": {"headline": "52°C · CPU 9% · Disk 41%", "sub": "11/11 services up"},
        "items": [
            {"id": "newscast", "title": "NewsCast", "sub": "Running", "badge": "Up", "level": "up",
             "unit": "stonepi-newscast", "detail": "Status: running"},
        ],
    },
    "newscast": {"ok": True, "card": {"headline": "Big story"}, "items": [{"id": "7", "title": "Big story", "detail": "Line one\nLine two"}]},
}


@pytest.fixture
def env(monkeypatch):
    panel = {"value": _panel()}
    restarts: list[str] = []
    monkeypatch.setattr(state, "session_secret", lambda: SECRET)
    monkeypatch.setattr(state, "panel_state", lambda draft=False: panel["value"])
    monkeypatch.setattr(state, "feed", lambda mini: FEEDS.get(mini, {"ok": True, "card": {}, "items": []}))
    monkeypatch.setattr(state, "weather", lambda config: None)
    monkeypatch.setattr(state, "weather_cached", lambda: None)

    def _restart(unit):
        restarts.append(unit)
        return {"ok": True, "message": ""}

    monkeypatch.setattr(state, "restart_unit", _restart)
    state.pin_guard.failures = 0
    state.pin_guard.locked_until = 0.0
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), panel, restarts


def test_unpaired_without_token(env):
    client, _, _ = env
    response = client.get("/")
    assert response.status_code == 403
    assert "Not paired" in response.text


def test_token_sets_cookie_and_renders_home(env):
    client, _, _ = env
    response = client.get(f"/?t={TOKEN}")
    assert response.status_code == 200
    assert "Big story" in response.text  # NewsCast card on Home
    assert routes.TOKEN_COOKIE in response.cookies
    # Fragments then work on the cookie alone
    assert client.get("/v/app/newscast").status_code == 200


def test_switched_off_panel(env):
    client, panel, _ = env
    panel["value"] = _panel(enabled=False)
    response = client.get(f"/?t={TOKEN}")
    assert "switched off" in response.text


def test_detail_and_unknown_item(env):
    client, _, _ = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    body = client.get("/v/item/newscast?id=7").text
    assert "Line one" in body and "Line two" in body
    assert "gone" in client.get("/v/item/newscast?id=nope").text


def test_restart_without_pin_uses_confirm(env):
    client, _, restarts = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    assert "Restart NewsCast?" in client.get("/v/restart?unit=stonepi-newscast").text
    result = client.post("/restart", json={"unit": "stonepi-newscast"}).json()
    assert result["ok"] and restarts == ["stonepi-newscast"]


def test_restart_rejects_unknown_unit_and_disallowed(env):
    client, panel, restarts = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    assert client.post("/restart", json={"unit": "ssh"}).status_code == 400
    cfg = carthing.normalize_config({"allowed_actions": ["home", "back"]})
    panel["value"] = _panel(config=cfg)
    assert client.post("/restart", json={"unit": "stonepi-newscast"}).status_code == 400
    assert restarts == []


def test_pin_and_lockout(env):
    client, panel, restarts = env
    panel["value"] = _panel(pin="4821")
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    assert "data-pin" in client.get("/v/restart?unit=stonepi-newscast").text
    for _ in range(3):
        result = client.post("/restart", json={"unit": "stonepi-newscast", "pin": "0000"}).json()
        assert not result["ok"]
    assert result["locked"] > 0
    # Even the right PIN waits out the lockout
    assert not client.post("/restart", json={"unit": "stonepi-newscast", "pin": "4821"}).json()["ok"]
    state.pin_guard.locked_until = 0.0
    assert client.post("/restart", json={"unit": "stonepi-newscast", "pin": "4821"}).json()["ok"]
    assert restarts == ["stonepi-newscast"]


def test_preview_needs_signature_and_never_locks_device(env):
    client, panel, restarts = env
    panel["value"] = _panel(pin="4821")
    assert client.get("/", headers={"x-carthing-preview": "1"}).status_code == 403
    headers = {**sign_internal(SECRET, "GET", "/"), "x-carthing-preview": "1", "x-carthing-base": "/carthing/panel/"}
    page = client.get("/", headers=headers)
    assert page.status_code == 200 and '<base href="/carthing/panel/">' in page.text
    post = {**sign_internal(SECRET, "POST", "/restart"), "x-carthing-preview": "1"}
    for _ in range(5):
        assert not client.post("/restart", json={"unit": "stonepi-newscast", "pin": "1111"}, headers=post).json()["ok"]
    assert state.pin_guard.locked_for() == 0
    result = client.post("/restart", json={"unit": "stonepi-newscast", "pin": "4821"}, headers=post).json()
    assert result["ok"] and "Preview" in result["message"] and restarts == []


def test_tick_reports_pi_clock(env):
    client, _, _ = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    tick = client.get("/tick").json()
    assert tick["rev"] == "r1" and tick["now_ms"] > 0 and "tz_min" in tick and tick["backlight"] is False


def test_isready_allows_cross_origin(env):
    client, _, _ = env
    response = client.get("/isready")
    assert response.headers["access-control-allow-origin"] == "*"


# ── Pages + large widgets ────────────────────────────────────────────────────

PAGED = carthing.normalize_config(
    {
        "pages": [
            {"name": "Home", "widgets": ["newscast", {"id": "system", "size": "half"}]},
            {"name": "Clock", "widgets": [{"id": "clock", "size": "full"}]},
            {"name": "System", "widgets": [{"id": "system", "size": "full"}]},
        ],
        "rotation": {"every_s": 30, "resume_after_s": 20},
        "weather": {"lat": 55.68, "lon": 12.57, "label": "Copenhagen"},
    }
)
WEATHER = {
    "temp": 14, "high": 16, "low": 9, "icon": "partly", "label": "Partly cloudy", "unit": "°C", "place": "Copenhagen",
    "hours": [{"hour": 15, "temp": 15, "icon": "sun"}, {"hour": 18, "temp": 13, "icon": "cloud"}],
    "days": [{"day": "Sat", "high": 17, "low": 8, "icon": "rain", "label": "Rain"}],
}
FEEDS["system"]["stats"] = {"cpu": 37, "mem": 41, "temp": 52, "disk": 63, "uptime": "3d 4h", "hostname": "stonepi",
                            "up": 10, "total": 11, "backup": "6h ago", "backup_ok": True}


def _shell_config(html: str) -> dict:
    import json
    import re

    return json.loads(re.search(r'<script type="application/json" id="cfg">(.*?)</script>', html, re.S).group(1))


def test_pages_render_with_dots_and_client_config(env, monkeypatch):
    client, panel, _ = env
    panel["value"] = _panel(config=PAGED)
    monkeypatch.setattr(state, "weather", lambda config: WEATHER)
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    shell = client.get("/").text
    cfg = _shell_config(shell)
    assert cfg["pages"] == ["Home", "Clock", "System"] and cfg["page"] == 0
    assert cfg["rotation"] == {"every_s": 30, "resume_after_s": 20}
    assert cfg["apps"] == ["newscast", "system"]  # mini-apps in page order; the clock isn't one
    assert shell.count('<span class="dot') == 3 and 'data-page-go="2"' in shell
    assert "lay-split" in shell and "Big story" in shell and "CPU" in shell
    # ?page= opens another page (the editor preview uses it); out of range falls back to page 1
    assert _shell_config(client.get("/?page=3").text)["page"] == 2
    assert _shell_config(client.get("/?page=9").text)["page"] == 0
    clock = client.get("/v/home?page=2").text
    assert "data-clock" in clock and "Partly cloudy" in clock and "Sat" in clock and "lay-full" in clock
    assert 'data-url="v/home?page=2"' in clock


def test_large_system_widget_shows_numbers(env):
    client, panel, _ = env
    panel["value"] = _panel(config=PAGED)
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    body = client.get("/v/home?page=3").text
    assert "w-system" in body and "w-full" in body
    for text in (">37<", ">41<", ">52<", ">63<", "Up 3d 4h", "10/11 services up", "NewsCast"):
        assert text in body


def test_single_page_has_no_dots_and_disallowed_dots_are_inert(env):
    client, panel, _ = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    assert 'class="dot' not in client.get("/v/home").text  # one page: no dots
    cfg = carthing.normalize_config({**PAGED, "allowed_actions": ["home", "back"]})
    panel["value"] = _panel(config=cfg)
    body = client.get("/v/home").text
    assert body.count('<span class="dot') == 3 and "data-page-go" not in body


def test_tick_watches_feeds_on_every_page(env, monkeypatch):
    client, panel, _ = env
    panel["value"] = _panel(config=PAGED)
    seen: list[list[str]] = []
    monkeypatch.setattr(state, "feeds_rev", lambda shown: seen.append(shown) or "f1")
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    assert client.get("/tick").json()["frev"] == "f1"
    assert seen == [["newscast", "system", "eventtrakr"]]  # + the screensaver's live line


def test_forecast_from_open_meteo():
    data = {
        "hourly": {
            "time": [f"2026-10-03T{h:02d}:00" for h in range(12, 24)] + ["2026-10-04T00:00"] * 4,
            "temperature_2m": [10.4 + i for i in range(16)],
            "weather_code": [0] * 16,
            "is_day": [1] * 6 + [0] * 10,
        },
        "daily": {
            "time": ["2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06"],
            "weather_code": [3, 61, 0, 71],
            "temperature_2m_max": [16.2, 14.6, None, 9.4],
            "temperature_2m_min": [8.1, 7.5, 6.0, 1.2],
        },
    }
    hours, days = state._forecast(data)
    assert [h["hour"] for h in hours] == [15, 18, 21, 0, 0]
    assert hours[0] == {"hour": 15, "temp": 13, "icon": "sun"} and hours[1]["icon"] == "moon"
    assert days == [
        {"day": "Sun", "high": 15, "low": 8, "icon": "rain", "label": "Light rain"},
        {"day": "Tue", "high": 9, "low": 1, "icon": "snow", "label": "Light snow"},
    ]
