"""Car Thing panel: pairing, fragments, PIN-gated restart, preview proxy auth."""

from __future__ import annotations

from pathlib import Path

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
    "feels": 12, "wind": 18, "wind_unit": "km/h",
    "hours": [{"hour": 15, "temp": 15, "icon": "sun"}, {"hour": 18, "temp": 13, "icon": "cloud"}],
    "days": [{"day": "Sat", "high": 17, "low": 8, "icon": "rain", "label": "Rain"}],
    "hourly": [{"hour": 10 + i, "temp": 15 - i, "icon": "cloud", "rain": 10 * i} for i in range(12)],
    "week": [{"day": "Today", "high": 16, "low": 9, "icon": "partly", "label": "Partly cloudy", "rain": 20}]
    + [{"day": d, "high": 17, "low": 8, "icon": "rain", "label": "Rain", "rain": 70} for d in ("Sat", "Sun", "Mon", "Tue", "Wed", "Thu")],
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
    assert hours[0] == {"hour": 15, "temp": 13, "icon": "sun", "rain": None} and hours[1]["icon"] == "moon"
    assert days == [
        {"day": "Sun", "high": 15, "low": 8, "icon": "rain", "label": "Light rain", "rain": None},
        {"day": "Tue", "high": 9, "low": 1, "icon": "snow", "label": "Light snow", "rain": None},
    ]


def test_hourly_and_week_for_the_weather_screen():
    data = {
        "hourly": {
            "time": [f"2026-10-03T{h:02d}:00" for h in range(10, 24)],
            "temperature_2m": [10.0 + i for i in range(14)],
            "weather_code": [61] * 14,
            "is_day": [1] * 14,
            "precipitation_probability": [i * 5 for i in range(14)],
        },
        "daily": {
            "time": ["2026-10-03", "2026-10-04"],
            "weather_code": [3, 0],
            "temperature_2m_max": [16.2, 14.6],
            "temperature_2m_min": [8.1, 7.5],
            "precipitation_probability_max": [80, None],
        },
    }
    hourly = state._hour_rows(data, 1, state.HOURLY_HOURS)
    assert len(hourly) == 12 and hourly[0] == {"hour": 11, "temp": 11, "icon": "rain", "rain": 5}
    week = state._day_rows(data)
    assert [d["day"] for d in week] == ["Today", "Sun"] and week[0]["rain"] == 80 and week[1]["rain"] is None


def test_screensaver_styles_render(env, monkeypatch):
    client, panel, _ = env
    monkeypatch.setattr(state, "weather", lambda config: WEATHER)
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    clock = client.get("/v/saver").text
    assert 'data-face="digital_bold"' in clock and "data-saver" not in clock and "saver-date" in clock

    def saver(style, **opts):
        cfg = carthing.normalize_config({"idle": {"saver": {"style": style, style: opts}}})
        panel["value"] = _panel(config=cfg)
        return client.get("/v/saver").text

    logo = saver("logo", time=True)
    assert 'data-saver="logo"' in logo and "data-lg-mark" in logo and 'class="sv-t ' in logo
    assert "saver-date" not in logo and "Partly cloudy" not in logo  # date and weather are clock-only
    assert 'class="sv-t ' not in saver("logo")  # time off by default
    matrix = saver("matrix")
    assert 'data-saver="matrix"' in matrix and "data-mx" in matrix and "data-clock" in matrix  # time on by default
    assert "data-clock" not in saver("matrix", time=False)
    shell = _shell_config(client.get("/").text)
    assert shell["idle"]["saver"]["style"] == "matrix"  # panel.js reads the options from here
    # Windows 98 styles: one canvas panel.js draws whole, plus the corner time when it's on.
    for style in ("starfield", "mystify", "flowerbox", "maze"):
        html = saver(style)
        assert f'data-saver="{style}"' in html and "data-sv" in html and 'class="sv-t ' not in html
        assert "saver-date" not in html and "Partly cloudy" not in html
        assert 'class="sv-t ' in saver(style, time=True)



def test_pipes_and_separate_screensaver_clock(env, monkeypatch):
    client, panel, _ = env
    monkeypatch.setattr(state, "weather", lambda config: WEATHER)
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    cfg = carthing.normalize_config({"idle": {"saver": {"style": "pipes", "pipes": {"time": True}}}})
    panel["value"] = _panel(config=cfg)
    pipes = client.get("/v/saver").text
    assert 'data-saver="pipes"' in pipes and "data-pp" in pipes and 'class="sv-t ' in pipes
    assert "Partly cloudy" not in pipes  # no feed data for the animations
    # U13: the Clock screensaver uses its own face once set apart; widgets keep theirs.
    cfg = carthing.normalize_config(
        {
            "pages": [{"widgets": [{"id": "clock", "size": "full"}]}],
            "clock": {"face": "flip", "saver": {"separate": True, "face": "analogue"}},
        }
    )
    panel["value"] = _panel(config=cfg)
    assert 'data-face="analogue"' in client.get("/v/saver").text
    assert 'data-face="flip"' in client.get("/v/home").text
    shell = _shell_config(client.get("/").text)
    assert shell["clock"]["saver"]["separate"] is True  # panel.js picks the colours from here
    # Matrix's time follows the screensaver clock: its own face once it's set apart, else the widgets'.
    matrix = {"style": "matrix", "matrix": {"time": True}}
    cfg = carthing.normalize_config({"idle": {"saver": matrix}, "clock": {"saver": {"separate": True, "face": "segment"}}})
    panel["value"] = _panel(config=cfg)
    assert 'tsize-xl sv-t-back face-segment"' in client.get("/v/saver").text
    panel["value"] = _panel(config=carthing.normalize_config({"idle": {"saver": matrix}, "clock": {"face": "flip"}}))
    assert 'tsize-xl sv-t-back face-flip"' in client.get("/v/saver").text
    # Flying Through Space at Large: the widgets' face too (it was always bold digital).
    stars = {"style": "starfield", "starfield": {"time": True}, "time_size": "large"}
    panel["value"] = _panel(config=carthing.normalize_config({"idle": {"saver": stars}, "clock": {"face": "segment"}}))
    assert 'tsize-large face-segment"' in client.get("/v/saver").text


def test_weather_widget_and_screen(env, monkeypatch):
    client, panel, _ = env
    monkeypatch.setattr(state, "weather", lambda config: WEATHER)
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    pages = [
        {"name": "Small", "widgets": [{"id": "weather", "size": "small"}, "newscast"]},
        {"name": "Half", "widgets": [{"id": "weather", "size": "half"}]},
        {"name": "Full", "widgets": [{"id": "weather", "size": "full"}]},
    ]
    panel["value"] = _panel(config=carthing.normalize_config({"pages": pages}))
    small = client.get("/v/home?page=1").text
    assert "w-weather" in small and 'data-go="v/weather"' in small and "14<small>°C" in small
    assert 'class="wx"' not in small  # no second temperature in the strip
    assert "Sat" in client.get("/v/home?page=2").text  # half: hours + next days
    full = client.get("/v/home?page=3").text
    assert "Thu" in full and full.count("<li>") >= 16  # full: 10 hours + 6 days
    screen = client.get("/v/weather").text
    assert 'data-screen="weather"' in screen and "Feels 12°" in screen and "Wind 18 km/h" in screen
    assert screen.count('class="wv-when"') == 12 + 7 and "is-today" in screen and "70%" in screen
    monkeypatch.setattr(state, "weather", lambda config: None)
    assert "No weather yet" in client.get("/v/weather").text


def test_clock_widget_opens_weather_only_when_set(env):
    client, panel, _ = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    pages = [{"widgets": [{"id": "clock", "size": "half"}]}]
    panel["value"] = _panel(config=carthing.normalize_config({"pages": pages}))
    plain = client.get("/v/home").text
    assert "w-clock" in plain and 'data-go="v/weather"' not in plain
    panel["value"] = _panel(config=carthing.normalize_config({"pages": pages, "clock": {"tap": "weather"}}))
    tap = client.get("/v/home").text
    assert '<button class="card w w-half w-clock" data-focus data-go="v/weather">' in tap


def test_web_page_widget_saver_and_snapshot(env, monkeypatch):
    """F14: a web page fills a page under the strip, or the screensaver; snapshots come from Notify."""
    client, panel, _ = env
    asked: list[tuple] = []

    def snapshot(page_id, width, height, max_age):
        asked.append((page_id, width, height, max_age))
        return b"\x89PNG-fake"

    monkeypatch.setattr(state, "web_snapshot", snapshot)
    cfg = carthing.normalize_config(
        {
            "pages": [{"name": "Home", "widgets": [{"id": "web:homepage", "size": "full"}]},
                      {"name": "Gone", "widgets": [{"id": "web:removed", "size": "full"}]}],
            "idle": {"saver": {"style": "web", "web": {"page_id": "homepage", "refresh_min": 3, "time": True}}},
        }
    )
    panel["value"] = {**_panel(config=cfg), "web_pages": [{"id": "homepage", "name": "Homepage"}]}
    home = client.get(f"/?t={TOKEN}").text
    assert "lay-web" in home and 'src="web/homepage.png?s=widget"' in home and 'data-web-every="60"' in home
    assert "was removed in Notify" in client.get("/v/home?page=2").text
    saver = client.get("/v/saver").text
    assert 'src="web/homepage.png?s=saver"' in saver and 'data-web-every="180"' in saver and 'class="sv-t ' in saver

    image = client.get("/web/homepage.png?s=widget")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert client.get("/web/homepage.png?s=saver").status_code == 200
    assert asked == [("homepage", 800, 412, 55), ("homepage", 800, 480, 175)]
    # Only pages this config shows can be asked for.
    assert client.get("/web/other.png").status_code == 404
    assert client.get("/web/..%2Fx.png").status_code == 404


def test_page_header_modes_and_contents(env, monkeypatch):
    client, panel, _ = env
    monkeypatch.setattr(state, "weather", lambda config: WEATHER)
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)

    def home(**cfg):
        panel["value"] = _panel(config=carthing.normalize_config({"pages": [
            {"name": "Desk", "widgets": [{"id": "newscast", "size": "small"}]},
            {"name": "Two", "widgets": [{"id": "pinboard", "size": "small"}]},
        ], **cfg}))
        return client.get("/v/home?page=1").text

    plain = home()  # today's strip: time, date, dots, weather; no title
    assert 'class="strip"' in plain and "data-clock-small" in plain and 'class="dots"' in plain
    assert "strip-name" not in plain and "home-full" not in plain and "data-strip-auto" not in plain
    titled = home(header={"title": True, "dots": False, "weather": False})
    assert "strip-name" in titled and "Desk" in titled and "data-clock-small" in titled
    assert 'class="dots"' not in titled and 'class="wx"' not in titled
    auto = home(header={"mode": "auto", "hide_after_s": 3})
    assert 'data-strip-auto="3"' in auto and "strip-auto" in auto and "home-full" in auto
    off = home(header={"mode": "off"})
    assert "<header" not in off and "home-full" in off
    # A page's own header wins over the default.
    panel["value"] = _panel(config=carthing.normalize_config({
        "header": {"mode": "off"},
        "pages": [{"name": "Desk", "widgets": [], "header": {"mode": "always", "title": True}}],
    }))
    own = client.get("/v/home?page=1").text
    assert "<header" in own and "strip-name" in own and "home-full" not in own


def test_screensaver_time_placement_renders(env, monkeypatch):
    client, panel, _ = env
    monkeypatch.setattr(state, "weather", lambda config: WEATHER)
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)

    def saver(**raw):
        panel["value"] = _panel(config=carthing.normalize_config({"idle": {"saver": raw}}))
        return client.get("/v/saver").text

    small = saver(style="pipes", pipes={"time": True})
    assert "sv-t tpos-bottom-right tsize-small" in small and 'data-clock data-face="digital_bold"' in small and "data-clock-small" not in small
    big = saver(style="starfield", starfield={"time": True}, time_pos="center", time_size="xl", time_backing=True)
    assert "tpos-center tsize-xl sv-t-back" in big and "data-clock " in big and "data-clock-small" not in big
    matrix = saver(style="matrix", matrix={"time_pos": "top-left", "time_size": "medium"})
    assert "sv-t tpos-top-left tsize-medium sv-t-back" in matrix


def test_panel_js_keeps_dial_press_on_now_playing():
    # With Now Playing's controls set to Never nothing can take focus: the dial press plays/pauses directly.
    js = (Path(routes.__file__).parent / "static" / "js" / "panel.js").read_text(encoding="utf-8")
    assert 'id === "dial_press" && npShown() && !view.querySelector(".np2.ctl-shown")' in js


def test_panel_js_keeps_the_strip_away_on_now_playing():
    # Taps and dial turns bring an auto-hidden strip back, except on Now Playing and full web pages.
    js = (Path(routes.__file__).parent / "static" / "js" / "panel.js").read_text(encoding="utf-8")
    assert """if (view.querySelector('[data-screen="music"], [data-np-full], .lay-web')) return;""" in js
    assert js.count("stripOnInput();") == 2  # onInput and pointerdown; load and wake still call showStrip


SYSTEM_FEED = {
    "ok": True,
    "card": {},
    "stats": {"cpu": 9, "mem": 82, "temp": 80, "disk": 41, "uptime": "3d 4h", "hostname": "stonepi", "backup": "2 days ago", "backup_ok": False},
    "alerts": [],
    "items": [
        {"id": "newscast", "title": "NewsCast", "sub": "4 feeds", "level": "up", "unit": "stonepi-newscast", "running": True, "line": "4 feeds", "note": "Summaries on the Pi"},
        {"id": "pinboard", "title": "Pinboard", "sub": "Stopped", "level": "down", "unit": "stonepi-pinboard", "running": False, "line": "", "note": ""},
        {"id": "mystery", "title": "Mystery", "sub": "Running", "level": "up", "unit": "stonepi-mystery", "running": True, "line": "", "note": ""},
    ],
}


def test_system_screen_is_cards(env, monkeypatch):
    client, _, _ = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    monkeypatch.setattr(state, "feed", lambda mini: SYSTEM_FEED if mini == "system" else {"ok": True, "items": []})
    body = client.get("/v/app/system").text
    assert 'class="screen sys"' in body and "2 of 3 running" in body
    # Meters: memory amber, temperature red; the stopped service leads the cards.
    assert 'class="lv-warn" style="width:82%"' in body and 'class="lv-bad" style="width:80%"' in body
    assert body.index("Pinboard") < body.index("NewsCast") and "sys-svc is-down" in body
    assert "static/brand/icons/apps/newscast.svg" in body and "sys-ico-none" in body  # no icon: a plain tile
    # Check: what needs attention, from the feed when Notify sends none.
    assert "Pinboard isn&#39;t running" in body and "Temp 80°C" in body and "Backup 2 days ago" in body


def test_service_page_shows_details(env, monkeypatch):
    client, _, _ = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    import time

    monkeypatch.setattr(state, "feed", lambda mini: SYSTEM_FEED if mini == "system" else {"ok": True, "items": []})
    monkeypatch.setattr(state, "feed_item", lambda mini, id: next(i for i in SYSTEM_FEED["items"] if i["id"] == id))
    details = {"ok": True, "state": "active", "sub": "running", "since": int(time.time()) - 3 * 3600 - 300, "memory_bytes": 88 * 1048576,
               "restarts": 2, "result": "success", "logs": [{"time": "11:08", "text": "Fetched 4 feeds"}]}
    monkeypatch.setattr(state, "unit_details", lambda unit: details)
    body = client.get("/v/item/system?id=newscast").text
    assert 'class="screen svc-page"' in body and ">Running<" in body and "stonepi-newscast" in body
    assert "3 h 5 min" in body and "88 MB" in body and ">2<" in body and "Fetched 4 feeds" in body and "Summaries on the Pi" in body
    assert 'data-act="restart"' in body and "btn-primary" in body and 'data-go="v/app/newscast"' in body
    # Stopped (systemd says so even if the feed is behind): red Restart, a banner.
    monkeypatch.setattr(state, "unit_details", lambda unit: {**details, "state": "failed", "result": "exit-code"})
    body = client.get("/v/item/system?id=newscast").text
    assert "Not running" in body and "btn-danger" in body and "NewsCast isn't running. Restarting" in body
    # Dashboard not answering: the feed's facts, and a note that more shows on the Pi.
    monkeypatch.setattr(state, "unit_details", lambda unit: {"ok": False})
    body = client.get("/v/item/system?id=mystery").text
    assert "More detail appears here on the Pi" in body and 'data-go="v/app/' not in body


def test_restart_progress_and_unit_state(env, monkeypatch):
    client, _, _ = env
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    monkeypatch.setattr(state, "feed", lambda mini: SYSTEM_FEED if mini == "system" else {"ok": True, "items": []})
    monkeypatch.setattr(state, "unit_details", lambda unit: {"ok": True, "state": "activating", "sub": "start"})
    body = client.get("/v/restarting?unit=stonepi-newscast").text
    assert 'data-restarting="stonepi-newscast"' in body and "Restarting NewsCast" in body
    assert client.get("/unit-state?unit=stonepi-newscast").json() == {"ok": True, "state": "activating", "sub": "start"}
    assert client.get("/unit-state?unit=ssh").status_code == 404  # only System's services
    assert client.get("/v/restarting?unit=ssh").status_code == 404
