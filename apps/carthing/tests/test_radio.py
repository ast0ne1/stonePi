from __future__ import annotations

from stonepi_display import carthing


def test_default_radio_theme_is_winamp():
    cfg = carthing.default_radio()
    assert cfg["theme"] == "winamp"
    assert len(cfg["presets"]) == carthing.RADIO_PRESETS


def test_normalize_radio_theme_and_presets():
    out = carthing._normalize_radio(
        {
            "theme": "plain",
            "dial_volume": False,
            "volume_step": 3,
            "presets": [{"name": "FIP", "station_id": "fip", "url": "http://example/fip"}],
        }
    )
    assert out["theme"] == "plain"
    assert out["dial_volume"] is False
    assert out["volume_step"] == 3
    assert out["presets"][0]["name"] == "FIP"
    assert out["presets"][0]["station_id"] == "fip"
    bad = carthing._normalize_radio({"theme": "neon"})
    assert bad["theme"] == "winamp"
    boy = carthing._normalize_radio({"theme": "ampboy"})
    assert boy["theme"] == "ampboy"
    # Omitron was removed; saved configs keep Plain.
    assert carthing._normalize_radio({"theme": "omitron"})["theme"] == "plain"


def test_radio_in_mini_apps_and_actions():
    assert any(a["id"] == "radio" for a in carthing.MINI_APPS)
    assert "radio:play_pause" in carthing.RADIO_ACTION_IDS
    assert "open:radio" in carthing.ADDED_ACTIONS
    cat = carthing.editor_catalog()
    ids = {t["id"] for t in cat["radio_themes"]}
    assert ids == {"winamp", "ampboy", "realplayer", "touchamp", "plain"}


def test_radio_client_empty_and_preset(monkeypatch):
    from app import radio

    monkeypatch.setattr(radio, "now_playing", lambda max_age=5.0, output="stonepi": radio.empty_state(connected=True, presets=[
        {"id": "a", "name": "Alpha", "stream_url": "http://x/a"},
    ]))
    calls = {}

    def fake_play(**kwargs):
        calls.update(kwargs)
        return {"ok": True, "title": kwargs.get("name") or ""}

    monkeypatch.setattr(radio, "play", fake_play)
    cfg = {"radio": carthing.default_radio()}
    out = radio.play_preset(cfg, 1)
    assert calls["station_id"] == "a"
    assert calls["output"] == "stonepi"  # the default: Odio on the Pi, as before
    assert out["ok"] is True
    cfg["radio"]["output"] = "dlna:uuid:kitchen"
    radio.play_preset(cfg, 1)
    assert calls["output"] == "dlna:uuid:kitchen"  # the panel's Play on speaker


def test_radio_output_in_config():
    norm = carthing.normalize_config({"radio": {"output": "dlna:uuid:kitchen"}})
    assert norm["radio"]["output"] == "dlna:uuid:kitchen"
    assert carthing.normalize_config({"radio": {"output": "http://evil"}})["radio"]["output"] == "stonepi"
    assert carthing.normalize_config({})["radio"]["output"] == "stonepi"


def test_radio_now_is_signed_the_way_radio_checks_it(monkeypatch):
    """Radio verifies over request.url.path (no query): signing "?output=…" too was a 403 on the Pi."""
    import httpx
    from stonepi_auth.internal import verify_internal_v2

    from app import radio, state

    monkeypatch.setattr(state, "session_secret", lambda: "s3cret-for-the-test")
    seen = []

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/internal/"):
            ok = verify_internal_v2("s3cret-for-the-test", request.method, request.url.path, request.headers)
            seen.append((request.url.path, request.url.params.get("output"), ok))
            if not ok:
                return httpx.Response(403, json={"ok": False, "error": "forbidden"})
            return httpx.Response(200, json={"ok": True, "playing": True, "title": "BBC Radio 6 Music"})
        return httpx.Response(200, json={"ok": True, "presets": []})

    real_client = httpx.Client
    monkeypatch.setattr(radio.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))
    radio.invalidate()
    now = radio.now_playing(0, "dlna:uuid:kitchen")
    assert seen == [("/api/internal/now", "dlna:uuid:kitchen", True)]
    assert now["connected"] and now["playing"] and now["title"] == "BBC Radio 6 Music"


def test_full_skin_has_pl_toggle_with_playlist_closed():
    from app.routes import templates

    tpl = templates.env.from_string('{% import "_radio.html" as r %}{{ r.player(now, opts, size) }}')
    now = {"connected": True, "playing": True, "paused": False, "title": "P6 Beat", "artist": "", "volume": 0.5, "source": "odio", "error": ""}
    opts = {
        "theme": "winamp",
        "presets": [{"number": 1, "name": "P6 Beat", "set": True, "allowed": True}],
        "allowed": {"play_pause": True, "stop": True, "vol_up": True, "vol_down": True},
    }
    full = tpl.render(now=now, opts=opts, size="full")
    # The PL button toggles the playlist on the panel (not a Radio command); it starts closed.
    assert 'class="sk-plb" data-focus data-skin-pl' in full
    assert "sk-has-pl" in full and "pl-open" not in full
    assert 'class="pl-row is-current" data-focus data-radio-cmd="preset" data-radio-arg="1"' in full
    # Widget size: main window only, PL is just the picture.
    half = tpl.render(now=now, opts=opts, size="half")
    assert "data-skin-pl" not in half and "class=\"sk-pl\"" not in half


def test_plain_idle_shows_favourite_cards_and_on_air_uses_icons():
    from app.routes import templates

    tpl = templates.env.from_string('{% import "_radio.html" as r %}{{ r.player(now, opts, size) }}')
    opts = {
        "theme": "plain",
        "presets": [
            {"number": 1, "name": "FIP", "set": True, "allowed": True, "art": "radio/art?u=https%3A%2F%2Fx%2Ffip.png"},
            {"number": 2, "name": "KEXP", "set": True, "allowed": True, "art": ""},
        ],
        "allowed": {"play_pause": True, "stop": True, "vol_up": True, "vol_down": True},
    }
    idle = tpl.render(
        now={"connected": True, "playing": False, "paused": False, "title": "", "artist": "", "source": "stonepi", "output_name": "Kitchen", "error": ""},
        opts=opts,
        size="full",
    )
    assert "radio-plain is-idle" in idle and "rp-tiles" in idle
    assert 'data-radio-cmd="preset" data-radio-arg="1"' in idle and "FIP" in idle
    assert "rp-tile-empty" in idle  # KEXP has no logo yet

    on_air = tpl.render(
        now={
            "connected": True, "playing": True, "paused": False, "title": "FIP", "artist": "On air",
            "station_logo": "https://img.example.test/fip.png", "art_url": "", "source": "stonepi",
            "output_name": "Kitchen", "playback_status": "Playing", "error": "",
        },
        opts=opts,
        size="full",
    )
    assert "rp-now" in on_air and "rp-tiles" not in on_air
    assert "radio/art?u=https%3A//img.example.test/fip.png" in on_air
    assert 'data-radio-cmd="play_pause"' in on_air and "i-play" in on_air and "i-pause" in on_air
    assert 'data-radio-cmd="stop"' in on_air and ">Pause<" not in on_air and ">Stop<" not in on_air


def test_skin_shows_kbps_and_khz_while_playing():
    """The stream's bitrate and sample rate in the skin's own font; blank when unknown or stopped."""
    from app.routes import templates

    tpl = templates.env.from_string('{% import "_radio.html" as r %}{{ r.player(now, opts, "widget") }}')
    opts = {"theme": "winamp", "presets": [], "allowed": {"play_pause": True, "stop": True, "vol_up": True, "vol_down": True}}
    base = {"connected": True, "paused": False, "title": "FIP", "artist": "", "volume": 0.5, "source": "odio", "error": ""}
    on = tpl.render(now={**base, "playing": True, "kbps": 128, "khz": 44}, opts=opts)
    assert 'data-skin-radio' in on and 'data-skin-radio-key="FIP|1|128|44"' in on
    kbps = on.split('class="sk-kbps"', 1)[1].split("</span></span>", 1)[0]
    khz = on.split('class="sk-khz"', 1)[1].split("</span></span>", 1)[0]
    # "128": 1, 2, 8 on TEXT.BMP's digit row (y = 6), 5 px apart; "44": 4, 4.
    assert "-5px -6px" in kbps and "-10px -6px" in kbps and "-40px -6px" in kbps
    assert khz.count("-20px -6px") == 2
    unknown = tpl.render(now={**base, "playing": True}, opts=opts)
    assert 'data-skin-radio-key="FIP|1|0|0"' in unknown
    stopped = tpl.render(now={**base, "playing": False, "kbps": 128, "khz": 44}, opts=opts)
    for html in (unknown, stopped):
        assert '<span class="sk-kbps" aria-hidden="true"></span>' in html
        assert '<span class="sk-khz" aria-hidden="true"></span>' in html


def test_skin_title_always_scrolls_like_winamp():
    """Short and long titles both marquee (two equal \"title  ***  \" copies), not only when too wide."""
    from app.routes import templates

    tpl = templates.env.from_string('{% import "_radio.html" as r %}{{ r.player(now, opts, "widget") }}')
    opts = {"theme": "winamp", "presets": [], "allowed": {"play_pause": True, "stop": True, "vol_up": True, "vol_down": True}}
    base = {"connected": True, "playing": True, "paused": False, "artist": "", "volume": 0.5, "source": "odio", "error": "", "kbps": 0, "khz": 0}
    short = tpl.render(now={**base, "title": "FIP"}, opts=opts)
    marq = short.split('class="sk-marquee"', 1)[1].split("</span></span>", 1)[0]
    assert "is-scroll" in marq and "steps(10)" in marq  # "FIP  ***  " is 10 glyphs, doubled for the loop
    # Space glyph isn't in TEXT.BMP (defaults to x=150); "***" and letters still paint.
    assert marq.count("-150px -0px") >= 4
    long = tpl.render(now={**base, "title": "Somafire Groove Salad - A Nicely Chilled Plate Of Ambient"}, opts=opts)
    assert "is-scroll" in long and "steps(" in long.split('class="sk-marquee"', 1)[1]


def test_now_playing_asks_radio_for_stream_info(monkeypatch):
    from app import radio as radio_mod

    asked = []

    def fake_get(path, timeout=4.0):
        asked.append(path)
        if path.startswith("/api/internal/now"):
            return {"playing": True, "title": "FIP", "kbps": "128", "khz": 44}
        return {"presets": []}

    monkeypatch.setattr(radio_mod, "_get", fake_get)
    radio_mod._NOW.clear()
    now = radio_mod.now_playing(0, "stonepi")
    assert any("info=1" in p for p in asked) and now["kbps"] == 128 and now["khz"] == 44


def test_now_playing_soft_cache_while_stream_info_pending(monkeypatch):
    """Blank kbps/kHz while playing only lasts ~1 s, so the skin can pick them up on the next look."""
    import time

    from app import radio as radio_mod

    answers = iter([
        {"playing": True, "title": "FIP", "kbps": 0, "khz": 0},
        {"playing": True, "title": "FIP", "kbps": 128, "khz": 44},
    ])

    def fake_get(path, timeout=4.0):
        if path.startswith("/api/internal/now"):
            return next(answers)
        return {"presets": []}

    monkeypatch.setattr(radio_mod, "_get", fake_get)
    radio_mod._NOW.clear()
    first = radio_mod.now_playing(5.0, "stonepi")
    assert first["kbps"] == 0
    # Still inside the normal 5 s TTL, but past the soft 1 s for incomplete info.
    with radio_mod._CACHE_LOCK:
        when, state = radio_mod._NOW["stonepi"]
        radio_mod._NOW["stonepi"] = (time.monotonic() - 1.1, state)
    second = radio_mod.now_playing(5.0, "stonepi")
    assert second["kbps"] == 128 and second["khz"] == 44
