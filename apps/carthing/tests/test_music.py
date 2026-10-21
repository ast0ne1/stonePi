"""Music (F7): the Spotify provider (with recorded-style API responses) and the panel's music routes."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from stonepi_auth.internal import sign_internal
from stonepi_display import carthing
from stonepi_display.spotify import SpotifyAuthError

from app import music, routes, state

SECRET = "carthing-test-secret"
TOKEN = "device-token-123"
ART = "https://i.scdn.co/image/ab67616d00001e02aaaa"

PLAYER = {
    "device": {"id": "dev-kitchen", "is_active": True, "is_restricted": False, "name": "Kitchen", "type": "Speaker",
               "volume_percent": 40, "supports_volume": True},
    "shuffle_state": False,
    "repeat_state": "off",
    "progress_ms": 61000,
    "is_playing": True,
    "currently_playing_type": "track",
    "item": {
        "type": "track", "name": "Teardrop", "uri": "spotify:track:67Hna13dNDkZvBpTXRIaOJ", "duration_ms": 330000,
        "artists": [{"name": "Massive Attack"}],
        "album": {"name": "Mezzanine", "images": [
            {"url": "https://i.scdn.co/image/ab67616d0000b273aaaa", "width": 640, "height": 640},
            {"url": ART, "width": 300, "height": 300},
            {"url": "https://i.scdn.co/image/ab67616d00004851aaaa", "width": 64, "height": 64},
        ]},
    },
}
DEVICES = {"devices": [
    PLAYER["device"],
    {"id": "dev-living", "is_active": False, "name": "Living room", "type": "Speaker", "volume_percent": 25, "supports_volume": True},
    {"id": "dev-tv", "is_active": False, "name": "TV", "type": "TV", "volume_percent": None, "supports_volume": False},
]}


class FakeTokens:
    def __init__(self):
        self.fail: SpotifyAuthError | None = None
        self.resets = 0
        self.drops = 0
        self.is_connected = True

    def connected(self):
        return self.is_connected

    def access_token(self):
        if self.fail:
            raise self.fail
        return "a1"

    def drop_access(self):
        self.drops += 1

    def invalidate(self):
        self.resets += 1

    def status(self):
        return {"connected": self.is_connected, "token": self.fail is None, "error": str(self.fail or ""), "error_code": ""}


class Spotify:
    """A tiny fake of the Web API: records calls, answers from ``routes``."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict, object]] = []
        self.player: dict | None = json.loads(json.dumps(PLAYER))
        self.overrides: dict[tuple[str, str], httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.replace("/v1", "", 1)
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, dict(request.url.params), body))
        if request.url.host == "i.scdn.co":
            return httpx.Response(200, content=b"\xff\xd8jpeg")
        if (request.method, path) in self.overrides:
            return self.overrides[(request.method, path)]
        if (request.method, path) == ("GET", "/me/player"):
            return httpx.Response(200, json=self.player) if self.player else httpx.Response(204)
        if (request.method, path) == ("GET", "/me/player/devices"):
            return httpx.Response(200, json=DEVICES)
        if (request.method, path) == ("GET", "/me/playlists"):
            return httpx.Response(200, json={"items": [{"name": "Morning", "uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"}]})
        return httpx.Response(204)

    def sent(self, method: str, path: str) -> list:
        return [c for c in self.calls if c[0] == method and c[1] == path]


@pytest.fixture
def api(tmp_path):
    fake = Spotify()
    clock = {"t": 1000.0}
    tokens = FakeTokens()
    player = music.Player(tokens, client=httpx.Client(transport=httpx.MockTransport(fake)), art_dir=tmp_path / "art",
                          clock=lambda: clock["t"])
    return player, fake, tokens, clock


# ── provider ─────────────────────────────────────────────────────────────


def test_now_playing_parses_and_caches(api):
    player, fake, _, clock = api
    st = player.now_playing(3.0)
    assert st["track"] == "Teardrop" and st["artist"] == "Massive Attack" and st["album"] == "Mezzanine"
    assert st["playing"] and st["active"] and st["device"]["name"] == "Kitchen" and st["device"]["volume"] == 40
    assert st["art"].startswith("art/") and len(st["art"]) == 20
    assert st["progress_ms"] == 61000 and st["duration_ms"] == 330000
    player.now_playing(3.0)
    assert len(fake.sent("GET", "/me/player")) == 1
    clock["t"] += 4
    player.now_playing(3.0)
    assert len(fake.sent("GET", "/me/player")) == 2


def test_nothing_playing_and_not_connected(api):
    player, fake, tokens, _ = api
    fake.player = None
    st = player.now_playing(0)
    assert st["connected"] and not st["active"] and not st["track"]
    tokens.is_connected = False
    assert player.now_playing(0)["connected"] is False
    assert player.signature(10) == ""


def test_rate_limit_backs_off_and_keeps_stale_state(api):
    player, fake, _, clock = api
    player.now_playing(0)
    fake.overrides[("GET", "/me/player")] = httpx.Response(429, headers={"Retry-After": "20"})
    clock["t"] += 5
    st = player.now_playing(0)
    assert st["stale"] and st["track"] == "Teardrop"
    calls = len(fake.calls)
    clock["t"] += 5
    player.now_playing(0)
    assert len(fake.calls) == calls  # still backing off: no call
    with pytest.raises(music.MusicError) as err:
        player.skip(True)
    assert err.value.code == "rate"


def test_401_refreshes_once(api):
    player, fake, tokens, _ = api
    fake.overrides[("POST", "/me/player/next")] = httpx.Response(401)
    with pytest.raises(music.MusicError):
        player.skip(True)
    assert tokens.drops == 1 and len(fake.sent("POST", "/me/player/next")) == 2


def test_403_means_premium(api):
    player, fake, _, _ = api
    fake.overrides[("PUT", "/me/player/pause")] = httpx.Response(403)
    with pytest.raises(music.MusicError) as err:
        player.play_pause({})
    assert err.value.code == "premium" and "Premium" in str(err.value)


def test_play_pause_toggles_or_starts_on_default_speaker(api):
    player, fake, _, clock = api
    player.play_pause({})
    assert fake.sent("PUT", "/me/player/pause")
    fake.player = None
    clock["t"] += 10
    with pytest.raises(music.MusicError) as err:
        player.play_pause({"default_device": {"id": "", "name": ""}})
    assert err.value.code == "no_device"
    player.play_pause({"default_device": {"id": "old-id", "name": "living room"}})  # matched by name
    assert fake.sent("PUT", "/me/player")[-1][3] == {"device_ids": ["dev-living"], "play": True}


def test_volume_from_current_level(api):
    player, fake, _, _ = api
    assert player.volume(8) == 48
    params = fake.sent("PUT", "/me/player/volume")[0][2]
    assert params == {"volume_percent": "48", "device_id": "dev-kitchen"}
    assert player.volume(-100) == 0


def test_volume_refused_without_support(api):
    player, fake, _, _ = api
    fake.player["device"].update(supports_volume=False, volume_percent=None)
    with pytest.raises(music.MusicError) as err:
        player.volume(4)
    assert err.value.code == "no_volume"


def test_shuffle_repeat_save(api):
    player, fake, _, clock = api
    player.shuffle()
    assert fake.sent("PUT", "/me/player/shuffle")[0][2] == {"state": "true"}
    clock["t"] += 10
    player.repeat()
    assert fake.sent("PUT", "/me/player/repeat")[0][2] == {"state": "context"}
    assert player.save() == ("Teardrop", True)  # liked unknown: the tick saves
    assert fake.sent("PUT", "/me/library")[0][2] == {"uris": "spotify:track:67Hna13dNDkZvBpTXRIaOJ"}


def test_liked_state_and_tick_toggles(api):
    player, fake, _, clock = api
    fake.overrides[("GET", "/me/library/contains")] = httpx.Response(200, json=[True])
    st = player.now_playing(0)
    assert st["liked"] is True
    assert fake.sent("GET", "/me/library/contains")[0][2] == {"uris": "spotify:track:67Hna13dNDkZvBpTXRIaOJ"}
    # Asked once per song, not on every read.
    clock["t"] += 10
    player.now_playing(0)
    assert len(fake.sent("GET", "/me/library/contains")) == 1
    # Liked already: the tick removes it, and the state says so at once.
    assert player.save() == ("Teardrop", False)
    assert fake.sent("DELETE", "/me/library")[0][2] == {"uris": "spotify:track:67Hna13dNDkZvBpTXRIaOJ"}
    assert player.now_playing(60)["liked"] is False
    assert player.save() == ("Teardrop", True)


def test_liked_unknown_without_the_scope(api):
    player, fake, _, clock = api
    fake.overrides[("GET", "/me/library/contains")] = httpx.Response(403)
    assert player.now_playing(0)["liked"] is None
    clock["t"] += 10
    fake.player["item"]["uri"] = "spotify:track:0000000000000000000000"
    assert player.now_playing(0)["liked"] is None
    assert len(fake.sent("GET", "/me/library/contains")) == 1  # not asked again until Reconnect
    player.reset()
    clock["t"] += 10
    player.now_playing(0)
    assert len(fake.sent("GET", "/me/library/contains")) == 2


def test_seek_clamps_to_the_song(api):
    player, fake, _, _ = api
    player.seek(120000)
    assert fake.sent("PUT", "/me/player/seek")[-1][2] == {"position_ms": "120000"}
    player.seek(10**9)
    assert fake.sent("PUT", "/me/player/seek")[-1][2] == {"position_ms": str(330000 - 1000)}
    player.seek(-5)
    assert fake.sent("PUT", "/me/player/seek")[-1][2] == {"position_ms": "0"}


def test_preset_art_looked_up_once(api):
    player, fake, _, clock = api
    images = {"images": [{"url": "https://i.scdn.co/image/morning300", "width": 300}]}
    fake.overrides[("GET", "/playlists/37i9dQZF1DXcBWIGoYBM5M")] = httpx.Response(200, json=images)
    art = player.preset_art("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")
    assert art.startswith("art/") and player.art_path(art[4:]) is not None
    assert player.preset_art("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M") == art
    assert len(fake.sent("GET", "/playlists/37i9dQZF1DXcBWIGoYBM5M")) == 1
    # A song's artwork is its album's; a failed lookup is retried after 10 minutes.
    fake.overrides[("GET", "/tracks/67Hna13dNDkZvBpTXRIaOJ")] = httpx.Response(404)
    assert player.preset_art("spotify:track:67Hna13dNDkZvBpTXRIaOJ") == ""
    fake.overrides[("GET", "/tracks/67Hna13dNDkZvBpTXRIaOJ")] = httpx.Response(200, json={"album": images})
    assert player.preset_art("spotify:track:67Hna13dNDkZvBpTXRIaOJ") == ""
    clock["t"] += 601
    assert player.preset_art("spotify:track:67Hna13dNDkZvBpTXRIaOJ").startswith("art/")
    assert player.preset_art("not a uri") == ""


def test_preset_plays_context_on_its_speaker(api):
    player, fake, _, _ = api
    cfg = carthing.default_music()
    cfg["presets"][0] = {"name": "Morning", "uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "device": {"id": "dev-living", "name": "Living room"}}
    cfg["presets"][1] = {"name": "Song", "uri": "spotify:track:67Hna13dNDkZvBpTXRIaOJ", "device": {"id": "", "name": ""}}
    assert player.preset(1, cfg) == "Morning"
    call = fake.sent("PUT", "/me/player/play")[-1]
    assert call[2] == {"device_id": "dev-living"} and call[3] == {"context_uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"}
    player.preset(2, cfg)  # no speaker: where it's already playing
    call = fake.sent("PUT", "/me/player/play")[-1]
    assert call[2] == {} and call[3] == {"uris": ["spotify:track:67Hna13dNDkZvBpTXRIaOJ"]}
    with pytest.raises(music.MusicError) as err:
        player.preset(3, cfg)
    assert err.value.code == "no_preset"


def test_preset_on_sleeping_speaker(api):
    player, _, _, _ = api
    cfg = carthing.default_music()
    cfg["presets"][0] = {"name": "X", "uri": "spotify:album:4aawyAB9vmqN3uQ7FjRGTy", "device": {"id": "gone", "name": "Bedroom"}}
    with pytest.raises(music.MusicError) as err:
        player.preset(1, cfg)
    assert err.value.code == "speaker_missing" and "Bedroom" in str(err.value)


def test_artwork_cached_on_disk(api):
    player, fake, _, _ = api
    key = player.now_playing(0)["art"].split("/")[1]
    path = player.art_path(key)
    assert path is not None and path.read_bytes().startswith(b"\xff\xd8")
    art_calls = [c for c in fake.calls if c[1].startswith("/image")]
    player.art_path(key)
    assert len([c for c in fake.calls if c[1].startswith("/image")]) == len(art_calls)
    assert player.art_path("0123456789abcdef") is None  # never seen: no fetch
    assert player.art_path("../../etc/passwd") is None


def test_catalog_for_notify(api, monkeypatch):
    player, _, _, _ = api
    monkeypatch.setattr(music, "player", player)
    data = music.catalog()
    assert data["ok"] and [d["name"] for d in data["devices"]] == ["Kitchen", "Living room", "TV"]
    assert data["playlists"][0]["name"] == "Morning"


# ── panel routes ─────────────────────────────────────────────────────────


def _panel(config: dict | None = None) -> dict:
    record = {"enabled": False, "hash": "", "salt": "", "max_attempts": 3, "lockout_s": 60}
    return {"ok": True, "enabled": True, "token_hash": carthing.hash_token(TOKEN), "pin": record,
            "config": config or carthing.default_config(), "rev": "r1"}


@pytest.fixture
def env(api, monkeypatch):
    player, fake, tokens, clock = api
    panel = {"value": _panel()}
    monkeypatch.setattr(music, "player", player)
    monkeypatch.setattr(state, "session_secret", lambda: SECRET)
    monkeypatch.setattr(state, "panel_state", lambda draft=False: panel["value"])
    monkeypatch.setattr(state, "feed", lambda mini: {"ok": True, "card": {"headline": "x"}, "items": []})
    monkeypatch.setattr(state, "weather", lambda config: None)
    monkeypatch.setattr(state, "weather_cached", lambda: None)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    client.cookies.set(routes.TOKEN_COOKIE, TOKEN)
    return client, panel, fake, tokens, clock


def _signed(method: str, path: str) -> dict:
    return sign_internal(SECRET, method, path)


def test_internal_music_endpoints_need_signature(env):
    client, _, _, tokens, _ = env
    bare = TestClient(client.app)
    for method, path in (("POST", "/internal/music/check"), ("POST", "/internal/music/reset"), ("GET", "/internal/music/catalog")):
        assert bare.request(method, path).status_code == 403
    assert bare.post("/internal/music/check", headers=_signed("POST", "/internal/music/check")).json() == {"ok": True}
    tokens.fail = SpotifyAuthError("Spotify sign-in expired or was revoked. Connect again in Notify.", code="invalid_grant")
    data = bare.post("/internal/music/check", headers=_signed("POST", "/internal/music/check")).json()
    assert data["ok"] is False and data["code"] == "invalid_grant"
    assert bare.post("/internal/music/reset", headers=_signed("POST", "/internal/music/reset")).json()["ok"]
    assert tokens.resets == 1


def test_status_includes_music(env, monkeypatch):
    client, _, _, _, _ = env
    from app.connector import connector

    monkeypatch.setattr(connector, "status", lambda: {})
    data = TestClient(client.app).get("/internal/status", headers=_signed("GET", "/internal/status")).json()
    assert data["music"]["connected"] is True


def test_now_playing_screen(env):
    client, _, _, _, _ = env
    body = client.get("/v/music").text
    assert 'data-screen="music"' in body and "Teardrop" in body and "Massive Attack" in body and "Kitchen" in body
    assert 'data-music="play_pause"' in body and "is-playing" in body and 'data-music="save"' in body
    assert "v/app/music?list=speakers" in body
    assert client.get("/v/app/music").text == body  # opening the mini-app lands on Now Playing


def test_now_playing_not_connected(env):
    client, _, _, tokens, _ = env
    tokens.is_connected = False
    assert "Connect Spotify in Notify" in client.get("/v/music").text


def test_speakers_and_presets_lists(env):
    client, panel, _, _, _ = env
    body = client.get("/v/app/music?list=speakers").text
    assert 'data-device="dev-living"' in body and "Playing" in body
    assert "No music presets yet" in client.get("/v/app/music?list=presets").text
    cfg = carthing.default_config()
    cfg["music"]["presets"][2] = {"name": "Evening", "uri": "spotify:album:4aawyAB9vmqN3uQ7FjRGTy", "device": {"id": "", "name": ""}}
    panel["value"] = _panel(cfg)
    body = client.get("/v/app/music?list=presets").text
    assert 'data-preset="3"' in body and "Evening" in body


def test_np_json(env):
    client, _, _, _, _ = env
    data = client.get("/np").json()
    assert data["ok"] and data["track"] == "Teardrop" and data["device"]["id"] == "dev-kitchen"
    assert TestClient(client.app).get("/np").status_code == 403


def test_commands_follow_allowed_actions(env):
    client, panel, fake, _, _ = env
    assert client.post("/music/cmd", json={"cmd": "next"}).json()["ok"]
    assert fake.sent("POST", "/me/player/next")
    res = client.post("/music/cmd", json={"cmd": "vol", "arg": 8}).json()
    assert res["ok"] and res["message"] == "Volume 48%"
    cfg = carthing.default_config()
    cfg["allowed_actions"] = [a for a in cfg["allowed_actions"] if a not in ("music:next", "music:vol_down")]
    panel["value"] = _panel(cfg)
    assert client.post("/music/cmd", json={"cmd": "next"}).status_code == 403
    assert client.post("/music/cmd", json={"cmd": "vol", "arg": -4}).status_code == 403
    assert client.post("/music/cmd", json={"cmd": "bogus"}).status_code == 403


def test_command_error_is_a_toast(env):
    client, _, fake, _, _ = env
    fake.overrides[("PUT", "/me/player/pause")] = httpx.Response(403)
    res = client.post("/music/cmd", json={"cmd": "play_pause"}).json()
    assert res["ok"] is False and "Premium" in res["message"]


def test_artwork_route(env):
    client, _, _, _, _ = env
    art = client.get("/np").json()["art"]
    response = client.get(f"/{art}")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert client.get("/art/0123456789abcdef").status_code == 404


def _with_page(widgets: list[dict], **music_opts) -> dict:
    cfg = carthing.default_config()
    cfg["pages"] = [{"name": "Home", "widgets": widgets}]
    cfg["music"].update(music_opts)
    return cfg


@pytest.mark.parametrize("size, expect", [("small", "card-head"), ("half", "wm-art")])
def test_music_widget_opens_now_playing(env, size, expect):
    client, panel, _, _, _ = env
    panel["value"] = _panel(_with_page([{"id": "music", "size": size}]))
    body = client.get("/v/home").text
    assert "Teardrop" in body and expect in body and 'data-go="v/music"' in body


def test_full_music_widget_is_now_playing(env):
    client, panel, _, _, _ = env
    panel["value"] = _panel(_with_page([{"id": "music", "size": "full"}]))
    body = client.get("/v/home").text
    assert "data-np-full" in body and "Teardrop" in body and "Kitchen" in body
    assert 'data-go="v/music"' not in body  # no header to open it again: it is the full view
    for part in ('data-music="play_pause"', 'data-music="shuffle"', "list=speakers", "list=presets", "data-np-vol"):
        assert part in body


def test_tick_follows_the_track(env):
    client, panel, fake, _, clock = env
    panel["value"] = _panel(_with_page([{"id": "music", "size": "small"}]))
    first = client.get("/tick").json()["frev"]
    clock["t"] += 11
    fake.player["item"]["uri"] = "spotify:track:0000000000000000000000"
    assert client.get("/tick").json()["frev"] != first


def test_saver_shows_artwork_while_playing(env):
    client, panel, fake, _, clock = env
    body = client.get("/v/saver").text
    assert "data-saver-music" in body and "Teardrop" in body
    fake.player["is_playing"] = False
    clock["t"] += 31
    assert "data-saver-music" not in client.get("/v/saver").text
    cfg = carthing.default_config()
    cfg["music"]["saver_artwork"] = False
    cfg["idle"]["show"]["live_line"] = "music"
    fake.player["is_playing"] = True
    clock["t"] += 31
    panel["value"] = _panel(cfg)
    body = client.get("/v/saver").text
    assert "data-saver-music" not in body and "Teardrop — Massive Attack" in body


# ── Clock widget (clock on its own, no weather) ──────────────────────────


@pytest.mark.parametrize("size", ["small", "half", "full"])
def test_clock_only_widget(env, size, monkeypatch):
    client, panel, _, _, _ = env
    monkeypatch.setattr(state, "weather", lambda config: {"icon": "sun", "temp": 14, "unit": "°C", "high": 15, "low": 9, "label": "Clear"})
    cfg = _with_page([{"id": "time", "size": size}])
    cfg["clock"]["seconds"] = True
    panel["value"] = _panel(cfg)
    body = client.get("/v/home").text
    assert "w-time" in body and "secs" in body and "data-clock" in body and "data-date" in body
    assert "data-clock-small" not in body  # the strip shows the page name: the time is in the widget
    assert 'class="wx"' in body  # no weather on the page, so the strip keeps it


def test_clock_and_weather_widget_still_hides_strip_weather(env, monkeypatch):
    client, panel, _, _, _ = env
    monkeypatch.setattr(state, "weather", lambda config: {"icon": "sun", "temp": 14, "unit": "°C", "high": 15, "low": 9, "label": "Clear",
                                                         "hours": [], "days": [], "place": ""})
    panel["value"] = _panel(_with_page([{"id": "clock", "size": "small"}]))
    assert 'class="wx"' not in client.get("/v/home").text



def test_now_playing_look_follows_the_config(env):
    # After Mira: background, controls and accent from Notify; the default is Glow, always, the art's accent.
    client, panel, _, _, _ = env
    body = client.get("/v/music").text
    assert 'class="np2 np2-card bg-glow ctl-shown"' in body and 'data-controls="always"' in body and 'data-accent-mode="art"' in body
    assert "data-np-blur" in body and "data-np-seek" in body and "data-np-more" in body and "data-np-sheet" in body
    cfg = carthing.default_config()
    cfg["music"]["np"] = {"background": "blur", "controls": "touch", "accent": "theme", "edge": True}
    panel["value"] = _panel(cfg)
    body = client.get("/v/music").text
    # Touch: hidden while playing (panel.js shows them for 4 s after a touch, press or turn).
    assert 'class="np2 np2-card bg-blur edge"' in body and 'data-controls="touch"' in body and 'data-accent-mode="theme"' in body
    # Seeking follows play/pause's permission.
    cfg["allowed_actions"] = [a for a in cfg["allowed_actions"] if a != "music:play_pause"]
    panel["value"] = _panel(cfg)
    body = client.get("/v/music").text
    assert "data-np-seek" not in body and 'data-music="play_pause"' not in body


def test_seek_and_liked_commands(env):
    client, panel, fake, _, _ = env
    assert client.post("/music/cmd", json={"cmd": "seek", "arg": 90000}).json()["ok"]
    assert fake.sent("PUT", "/me/player/seek")[-1][2] == {"position_ms": "90000"}
    assert client.post("/music/cmd", json={"cmd": "seek", "arg": "soon"}).json()["ok"] is False
    assert client.post("/music/cmd", json={"cmd": "save"}).json()["message"] == "Saved Teardrop to Liked Songs."
    assert client.post("/music/cmd", json={"cmd": "save"}).json()["message"] == "Removed Teardrop from Liked Songs."
    assert client.get("/np").json()["liked"] is False
    cfg = carthing.default_config()
    cfg["allowed_actions"] = [a for a in cfg["allowed_actions"] if a != "music:play_pause"]
    panel["value"] = _panel(cfg)
    assert client.post("/music/cmd", json={"cmd": "seek", "arg": 1000}).status_code == 403


def test_nothing_playing_shows_preset_tiles(env):
    client, panel, fake, _, _ = env
    fake.player = None
    fake.overrides[("GET", "/albums/4aawyAB9vmqN3uQ7FjRGTy")] = httpx.Response(200, json={"images": [{"url": "https://i.scdn.co/image/evening", "width": 300}]})
    body = client.get("/v/music").text
    assert 'class="np2 np2-card bg-glow idle ctl-shown"' in body and "No music presets yet" in body
    cfg = carthing.default_config()
    cfg["music"]["presets"][2] = {"name": "Evening", "uri": "spotify:album:4aawyAB9vmqN3uQ7FjRGTy", "device": {"id": "", "name": ""}}
    cfg["music"]["default_device"] = {"id": "dev-living", "name": "Living room"}
    panel["value"] = _panel(cfg)
    body = client.get("/v/music").text
    assert 'data-preset="3"' in body and "Evening" in body and 'src="art/' in body and "start it on Living room" in body


def test_artwork_screensaver_takes_the_look(env):
    client, panel, _, _, _ = env
    cfg = carthing.default_config()
    cfg["music"]["np"]["background"] = "art"
    panel["value"] = _panel(cfg)
    body = client.get("/v/saver").text
    assert "data-saver-music" in body and "np2-saver bg-art" in body and 'data-controls="never"' in body


def test_artwork_screensaver_time_has_its_own_settings(env):
    client, panel, _, _, _ = env
    body = client.get("/v/saver").text
    # Default: shown top right, small, in the screensaver clock's face.
    assert 'class="sv-t tpos-top-right tsize-small' in body.split("data-saver-music", 1)[1]
    cfg = carthing.default_config()
    cfg["music"]["saver_time"] = {"show": True, "pos": "bottom-right", "size": "large", "backing": True}
    panel["value"] = _panel(cfg)
    assert 'class="sv-t tpos-bottom-right tsize-large sv-t-back' in client.get("/v/saver").text
    cfg["music"]["saver_time"]["show"] = False
    panel["value"] = _panel(cfg)
    body = client.get("/v/saver").text
    assert "data-saver-music" in body and 'class="sv-t' not in body


def test_music_screen_follows_the_header(env):
    # Same header as the pages (the config's default): Always draws it above a compact Now Playing.
    client, panel, _, _, _ = env
    body = client.get("/v/music").text
    assert 'class="strip"' in body and "strip-name" not in body and "data-clock-small" in body
    cfg = carthing.default_config()
    cfg["pages"].append({"name": "Second", "widgets": []})
    cfg["header"] = {"mode": "auto", "hide_after_s": 3, "time": True, "title": True, "weather": True, "dots": True}
    panel["value"] = _panel(cfg)
    body = client.get("/v/music").text
    # Auto-hide: full screen, the strip over its top; titled "Music", no page lit in the dots.
    assert 'class="screen music home-full"' in body and 'data-strip-auto="3"' in body and ">Music<" in body
    assert 'class="np2 bg-glow' in body and 'class="dot on"' not in body and 'data-page-go="2"' in body
    cfg["header"]["mode"] = "off"
    panel["value"] = _panel(cfg)
    assert "<header" not in client.get("/v/music").text


def test_full_page_now_playing_goes_edge_to_edge(env):
    client, panel, _, _, _ = env
    cfg = _with_page([{"id": "music", "size": "full"}])
    panel["value"] = _panel(cfg)
    body = client.get("/v/home").text
    assert "lay-full" in body and 'class="np2 np2-card' in body  # under an Always header: a card
    cfg["header"]["mode"] = "auto"
    panel["value"] = _panel(cfg)
    body = client.get("/v/home").text
    assert "lay-np" in body and 'class="np2 bg-glow' in body and "data-np-full" in body
