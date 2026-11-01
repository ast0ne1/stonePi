"""Where Radio plays (radio-outputs.md): outputs, DLNA speakers, stream resolving, the relay."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["RADIO_MOCK"] = "1"
os.environ["STONEPI_SESSION_SECRET"] = ""
os.environ["STONEPI_DATA_DIR"] = str(Path(__file__).resolve().parent / "_data")

SECRET = "test-secret-radio"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("STONEPI_DEV", "1")
    from app import config, db
    from app.services import dlna, odio_mock

    config.env.mock = True
    config.env.session_secret = ""
    config.DATA_DIR = data
    config.DB_PATH = data / "radio.sqlite"
    db.DB_PATH = config.DB_PATH
    db.DATA_DIR = data
    odio_mock.reset()
    dlna._mock_state.clear()
    db.init_db()
    dlna.refresh(force=True)  # the two pretend speakers
    from app.main import app

    return TestClient(app)


def _post(client, path, data):
    from stonepi_auth.session import CSRF_COOKIE

    client.cookies.set(CSRF_COOKIE, "tok")
    return client.post(path, data={"csrf_token": "tok", **data}, headers={"X-Requested-With": "fetch"})


def _settings(client, data):
    """Settings → General: a plain form post; the answer is a redirect with msg= or error=."""
    from stonepi_auth.session import CSRF_COOKIE

    client.cookies.set(CSRF_COOKIE, "tok")
    return client.post("/settings/general", data={"csrf_token": "tok", **data}, follow_redirects=False)


# -- Outputs and the picker -------------------------------------------------------------------------


def test_picker_lists_this_device_speakers_and_stonepi(client):
    data = client.get("/api/outputs").json()
    ids = [o["id"] for o in data["outputs"]]
    assert ids[0] == "browser" and ids[-1] == "stonepi"
    assert "dlna:uuid:mock-kitchen" in ids and "dlna:uuid:mock-living" in ids
    assert data["current"] == "browser"  # This device until you choose


def test_play_on_this_device_tells_the_page_what_to_load(client):
    res = _post(client, "/play", {"station_id": "fip"}).json()
    assert res["ok"] and res["output"] == "browser"
    assert res["browser"]["src"] == "https://icecast.radiofrance.fr/fip-hifi.aac"
    assert res["browser"]["name"] == "FIP" and res["browser"]["hls"] is False
    # BBC 6 Music is HLS: the page loads it with hls.js (or Safari natively).
    assert _post(client, "/play", {"station_id": "bbc-radio-6"}).json()["browser"]["hls"] is True


def test_play_on_a_speaker_and_control_it(client):
    from app.services import dlna

    assert _post(client, "/output", {"output": "dlna:uuid:mock-kitchen"}).json()["ok"]
    res = _post(client, "/play", {"station_id": "kexp"}).json()
    assert res["ok"] and res["message"] == "Playing KEXP 90.3 FM on Kitchen"
    assert dlna._mock_state["uuid:mock-kitchen"]["state"] == "PLAYING"
    assert _post(client, "/volume", {"level": "0.25"}).json()["volume"] == pytest.approx(0.25)
    assert _post(client, "/transport", {"action": "play_pause"}).json()["now"]["paused"] is True
    page = client.get("/now").content.decode()
    assert "Paused on Kitchen" in page and "KEXP 90.3 FM" in page
    # HLS goes to a speaker through the relay, joined into one AAC stream.
    from app.services import streams

    hls = _post(client, "/play", {"station_id": "bbc-radio-6"}).json()
    assert hls["ok"] and "on Kitchen" in hls["message"]
    sent = dlna._mock_state["uuid:mock-kitchen"]["url"]
    assert sent.startswith("/relay/")
    assert streams.relay_info(sent.rsplit("/", 1)[1])["hls"] is True


def test_switching_output_carries_the_station_over(client):
    from app.services import dlna

    _post(client, "/output", {"output": "dlna:uuid:mock-kitchen"})
    _post(client, "/play", {"station_id": "kexp"})
    res = _post(client, "/output", {"output": "dlna:uuid:mock-living"}).json()
    assert res["ok"] and "on Living room" in res["message"]
    assert dlna._mock_state["uuid:mock-kitchen"]["state"] == "STOPPED"
    assert dlna._mock_state["uuid:mock-living"]["state"] == "PLAYING"
    # To This device: the page gets the station to load.
    back = _post(client, "/output", {"output": "browser"}).json()
    assert back["browser"]["station_url"] == "https://kexp.streamguys1.com/kexp160.aac"
    assert dlna._mock_state["uuid:mock-living"]["state"] == "STOPPED"


def test_hls_station_on_this_device_moves_to_a_speaker(client):
    from app.services import dlna

    hls = {"station_url": "https://live.example.test/rtl/index.m3u8", "name": "RTL"}
    res = _post(client, "/output", {"output": "dlna:uuid:mock-kitchen", **hls}).json()
    assert res["ok"] and res["message"] == "Playing RTL on Kitchen"
    assert dlna._mock_state["uuid:mock-kitchen"]["state"] == "PLAYING"


def test_a_station_a_speaker_cant_reach_doesnt_switch(client, monkeypatch):
    from app.services import dlna, outputs, streams

    def nope(url):
        raise streams.NotAStream("The station isn't answering.")

    monkeypatch.setattr(streams, "resolve", nope)
    res = _post(client, "/output", {"output": "dlna:uuid:mock-kitchen", "station_url": "https://gone.example.test/x", "name": "Gone"})
    assert res.status_code == 400
    body = res.json()
    assert "Gone: The station isn't answering." in body["message"] and "Still playing on This device" in body["message"]
    assert "output" not in body  # nothing switched: the page keeps playing and closes the picker
    assert outputs.chosen("") == outputs.BROWSER
    assert dlna._mock_state.get("uuid:mock-kitchen", {}).get("state") != "PLAYING"


def test_unknown_output_is_refused(client):
    res = _post(client, "/output", {"output": "dlna:uuid:nope"})
    assert res.status_code == 400 and "isn't on the network" in res.json()["message"]


def test_car_thing_plays_on_its_speaker(client):
    from app.services import dlna

    res = client.post("/api/internal/play", json={"station_id": "fip", "output": "dlna:uuid:mock-living"}).json()
    assert res["ok"] and dlna._mock_state["uuid:mock-living"]["state"] == "PLAYING"
    now = client.get("/api/internal/now", params={"output": "dlna:uuid:mock-living"}).json()
    assert now["title"] == "FIP" and now["output_name"] == "Living room"
    assert "kbps" not in now  # only when asked
    info = client.get("/api/internal/now", params={"output": "dlna:uuid:mock-living", "info": "1"}).json()
    assert (info["kbps"], info["khz"]) == (128, 44)  # mock mode; on the Pi from the stream's headers
    # No output (an older panel) = StonePi; This device isn't a Car Thing output.
    assert client.post("/api/internal/play", json={"station_id": "fip", "output": "browser"}).json()["output"] == "stonepi"
    outs = client.get("/api/internal/outputs").json()["outputs"]
    assert "browser" not in [o["id"] for o in outs]


def test_speaker_settings_are_for_admins(client, monkeypatch):
    from types import SimpleNamespace

    from app import db, routes

    member = SimpleNamespace(user_id="u-m", username="m", display_name="M", is_admin=False, using_factory_admin=False,
                             can_access=lambda a: True)
    monkeypatch.setattr(routes, "_session_secret", lambda: SECRET)
    monkeypatch.setattr(routes, "_user", lambda request: member)
    from stonepi_auth.session import CSRF_COOKIE

    client.cookies.set(CSRF_COOKIE, "tok")
    client.post("/settings/speakers/uuid:mock-kitchen", data={"csrf_token": "tok", "label": "Hacked"}, follow_redirects=False)
    assert db.get_speaker("uuid:mock-kitchen")["label"] == ""
    member.is_admin = True
    # "Pass https stations through the Pi" is on by default; unticked, the speaker plays https itself.
    page = client.get("/settings?tab=speakers").text
    assert "Pass https stations through the Pi" in page and 'name="relay_https" value="1" aria-labelledby="https-1" checked' in page
    client.post("/settings/speakers/uuid:mock-kitchen", data={"csrf_token": "tok", "label": "Kitchen M5"}, follow_redirects=False)
    spk = db.get_speaker("uuid:mock-kitchen")
    assert spk["label"] == "Kitchen M5" and spk["direct_https"] is True and spk["use_linkplay"] is False
    client.post("/settings/speakers/uuid:mock-kitchen", data={"csrf_token": "tok", "label": "Kitchen M5", "relay_https": "1"}, follow_redirects=False)
    assert db.get_speaker("uuid:mock-kitchen")["direct_https"] is False


# -- DLNA against a fake renderer on loopback ------------------------------------------------------

DESC = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
<friendlyName>Office</friendlyName><manufacturer>iEAST</manufacturer><modelName>AudioCast M5</modelName>
<UDN>uuid:fake-office</UDN>
<serviceList>
<service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType><controlURL>/av</controlURL></service>
<service><serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType><controlURL>/rc</controlURL></service>
</serviceList></device></root>"""


class FakeRenderer(BaseHTTPRequestHandler):
    calls: list[tuple[str, str, bytes]] = []
    state = {"transport": "STOPPED", "volume": 30}

    def log_message(self, *args):  # quiet
        pass

    def _send(self, body: str, ctype="text/xml"):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/desc.xml":
            self._send(DESC)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        action = self.headers.get("SOAPACTION", "").strip('"').split("#")[-1]
        FakeRenderer.calls.append((self.path, action, body))
        out = ""
        if action == "Play":
            FakeRenderer.state["transport"] = "PLAYING"
        elif action == "Stop":
            FakeRenderer.state["transport"] = "STOPPED"
        elif action == "SetVolume":
            FakeRenderer.state["volume"] = int(body.split(b"<DesiredVolume>")[1].split(b"<")[0])
        elif action == "GetTransportInfo":
            out = f"<CurrentTransportState>{FakeRenderer.state['transport']}</CurrentTransportState>"
        elif action == "GetVolume":
            out = f"<CurrentVolume>{FakeRenderer.state['volume']}</CurrentVolume>"
        self._send(
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            f'<u:{action}Response xmlns:u="x">{out}</u:{action}Response></s:Body></s:Envelope>'
        )


@pytest.fixture()
def renderer(monkeypatch):
    from app.services import dlna

    FakeRenderer.calls = []
    FakeRenderer.state = {"transport": "STOPPED", "volume": 30}
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeRenderer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(dlna, "_private_host", lambda host: host == "127.0.0.1")  # loopback only for this test
    monkeypatch.setattr(dlna, "_is_linkplay", lambda host: False)
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_dlna_describe_play_volume_and_state(client, renderer):
    from app import db
    from app.services import dlna

    info = dlna.describe(renderer + "/desc.xml")
    assert info["udn"] == "uuid:fake-office" and info["name"] == "Office" and info["model"] == "iEAST AudioCast M5"
    assert info["av_control"] == renderer + "/av"
    db.upsert_speaker(info, time.time())
    spk = db.get_speaker("uuid:fake-office")

    dlna.play(spk, "http://ice.example.test/fip.mp3", "audio/mpeg", "FIP & friends")
    actions = [c[1] for c in FakeRenderer.calls]
    assert actions[-3:] == ["Stop", "SetAVTransportURI", "Play"] or actions[-2:] == ["SetAVTransportURI", "Play"]
    seturi = next(c for c in FakeRenderer.calls if c[1] == "SetAVTransportURI")[2].decode()
    assert "<CurrentURI>http://ice.example.test/fip.mp3</CurrentURI>" in seturi
    # DIDL-Lite goes in escaped, with the title escaped inside it.
    assert "&lt;dc:title&gt;FIP &amp;amp; friends&lt;/dc:title&gt;" in seturi
    assert "audioBroadcast" in seturi and "http-get:*:audio/mpeg:*" in seturi
    assert dlna.state(spk) == "PLAYING"
    dlna.set_volume(spk, 0.55)
    assert dlna.get_volume(spk) == pytest.approx(0.55)
    dlna.transport(spk, "stop")
    assert dlna.state(spk) == "STOPPED"


def test_dlna_refuses_unsafe_descriptions(renderer, monkeypatch):
    from app.services import dlna

    # Control URLs on another host than the description came from are refused.
    monkeypatch.setattr(dlna, "_private_host", lambda host: True)
    global DESC
    original = DESC
    try:
        DESC = original.replace("<controlURL>/av</controlURL>", "<controlURL>http://192.168.1.99:8011/x</controlURL>")
        assert dlna.describe(renderer + "/desc.xml") is None
    finally:
        DESC = original
    # The real check: loopback, public and non-literal hosts are never speakers.
    monkeypatch.undo()
    for host in ("127.0.0.1", "8.8.8.8", "stonepi.local", "0.0.0.0", "239.255.255.250"):
        assert dlna._private_host(host) is False
    for host in ("192.168.1.20", "10.0.0.5", "172.16.3.4", "169.254.1.1"):
        assert dlna._private_host(host) is True


# -- Streams and the relay --------------------------------------------------------------------------


def test_playlists_unwrap_to_their_stream():
    from app.services import streams

    assert streams._parse_playlist("[playlist]\nNumberOfEntries=1\nFile1=http://ice.test/a.mp3\n", "http://x/p.pls") == ("http://ice.test/a.mp3", False)
    assert streams._parse_playlist("#EXTM3U\n#EXTINF:-1,Radio\nhttp://ice.test/b.aac\n", "http://x/p.m3u") == ("http://ice.test/b.aac", False)
    assert streams._parse_playlist("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=96000\nlow.m3u8\n", "https://x/p.m3u8") == ("https://x/p.m3u8", True)
    with pytest.raises(streams.NotAStream):
        streams._parse_playlist("#EXTM3U\n", "http://x/p.m3u")


def test_relay_tokens_name_one_stream_and_expire(client):
    from app.services import streams

    tok = streams.relay_token("https://ice.example.test/live")
    assert streams.relay_target(tok) == "https://ice.example.test/live"
    payload, sig = tok.split(".")
    assert streams.relay_target(payload + "." + "0" * len(sig)) is None  # forged
    assert streams.relay_target(streams.relay_token("https://ice.example.test/live", ttl=-1)) is None  # expired
    assert streams.relay_target(streams.relay_token("http://127.0.0.1:8011/")) is None  # never the Pi's own services
    assert client.get("/relay/nonsense").status_code == 404


def test_https_on_a_speaker_goes_through_the_relay(client, monkeypatch):
    from app import db
    from app.services import dlna, outputs, streams

    sent = {}
    monkeypatch.setattr(dlna, "play", lambda spk, url, mime, title: sent.update(url=url))
    monkeypatch.setattr(dlna, "state", lambda spk: "PLAYING")
    monkeypatch.setattr(dlna, "get_volume", lambda spk: 0.4)
    monkeypatch.setattr(streams, "lan_address_for", lambda host: "192.168.1.50")
    db.update_speaker("uuid:mock-kitchen", direct_https=False)
    spk = db.get_speaker("uuid:mock-kitchen")
    db.upsert_speaker({**spk, "av_control": "http://192.0.2.10/av"}, time.time())  # a "real" speaker
    outputs.play("dlna:uuid:mock-kitchen", {"stream_url": "https://ice.example.test/live.mp3", "name": "Test"})
    assert sent["url"].startswith("http://192.168.1.50/relay/")
    assert streams.relay_target(sent["url"].rsplit("/", 1)[1]) == "https://ice.example.test/live.mp3"
    db.update_speaker("uuid:mock-kitchen", direct_https=True)
    outputs.play("dlna:uuid:mock-kitchen", {"stream_url": "https://ice.example.test/live.mp3", "name": "Test"})
    assert sent["url"] == "https://ice.example.test/live.mp3"
    # HLS always goes through the relay (joined), even to a speaker that does https itself.
    outputs.play("dlna:uuid:mock-kitchen", {"stream_url": "https://live.example.test/bbc/index.m3u8", "name": "BBC"})
    assert sent["url"].startswith("http://192.168.1.50/relay/")
    assert streams.relay_info(sent["url"].rsplit("/", 1)[1]) == {"url": "https://live.example.test/bbc/index.m3u8", "hls": True}


# -- HLS joined for speakers ------------------------------------------------------------------------

AUDIO_PID = 0x101


def _ts_packets(pid: int, payload: bytes, *, start: bool, cc: int = 0) -> bytes:
    """MPEG-TS packets carrying ``payload`` on ``pid``; the last one padded with an adaptation field."""
    out = bytearray()
    first = True
    while payload or first:
        chunk, payload = payload[:184], payload[184:]
        pusi = 0x40 if (start and first) else 0
        header = bytes([0x47, pusi | (pid >> 8), pid & 0xFF])
        if len(chunk) == 184:
            out += header + bytes([0x10 | (cc & 0xF)]) + chunk
        else:
            pad = 184 - len(chunk) - 1  # adaptation_field_length byte, then stuffing
            af = bytes([pad]) + (bytes([0x00]) + b"\xff" * (pad - 1) if pad else b"")
            out += header + bytes([0x30 | (cc & 0xF)]) + af + chunk
        cc += 1
        first = False
    return bytes(out)


def _pes(stream_id: int, data: bytes) -> bytes:
    pts = b"\x21\x00\x01\x00\x01"
    return b"\x00\x00\x01" + bytes([stream_id]) + (len(data) + 8).to_bytes(2, "big") + b"\x80\x80\x05" + pts + data


def _segment(audio: bytes) -> bytes:
    pat = _ts_packets(0, b"\x00\x00\xb0\x0d\x00\x01\xc1\x00\x00\x00\x01\xf0\x00\x00\x00\x00\x00", start=True)
    id3 = _ts_packets(0x102, _pes(0xBD, b"ID3 timed metadata"), start=True)  # not audio: left out
    return pat + id3 + _ts_packets(AUDIO_PID, _pes(0xC0, audio), start=True)


def test_ts_audio_takes_the_aac_out_of_mpeg_ts():
    from app.services import streams

    frames = (b"\xff\xf1\x50\x80" + bytes(range(200))) * 3  # ADTS-looking, longer than one packet
    seg = _segment(frames)
    demux = streams.TsAudio()
    assert demux.feed(seg[:500]) + demux.feed(seg[500:]) == frames  # split mid-packet is fine
    assert streams._strip_id3(b"ID3\x04\x00\x00\x00\x00\x00\x02ab\xff\xf1") == b"\xff\xf1"


def test_hls_playlists_parse_and_refuse_what_cant_be_joined():
    from app.services import streams

    media = streams.parse_media_playlist(
        "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:41\n#EXTINF:6.4,\na-41.ts\n#EXTINF:6.4,\na-42.ts\n",
        "https://live.example.test/x/index.m3u8",
    )
    assert media["segments"] == [(41, "https://live.example.test/x/a-41.ts"), (42, "https://live.example.test/x/a-42.ts")]
    assert media["target"] == 6 and media["ended"] is False
    master = streams.parse_media_playlist(
        "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=320000\nhi.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=96000\nlo.m3u8\n", "https://x.test/m.m3u8"
    )
    assert master["variants"] == [(320000, "https://x.test/hi.m3u8"), (96000, "https://x.test/lo.m3u8")]
    for text in ("#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"k\"\n", "#EXTM3U\n#EXT-X-MAP:URI=\"init.mp4\"\n"):
        with pytest.raises(streams.NotAStream):
            streams.parse_media_playlist(text, "https://x.test/p.m3u8")


class FakeHls(BaseHTTPRequestHandler):
    """A live HLS station: a master playlist, a sliding media playlist and TS segments."""

    seq = 10
    ended = False  # True: the playlist says #EXT-X-ENDLIST, so the joined stream finishes
    fetched: list[str] = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        FakeHls.fetched.append(self.path)
        if self.path == "/master.m3u8":
            body, ctype = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=96000\naudio.m3u8\n", "application/vnd.apple.mpegurl"
        elif self.path == "/audio.m3u8":
            lines = ["#EXTM3U", "#EXT-X-TARGETDURATION:2", f"#EXT-X-MEDIA-SEQUENCE:{FakeHls.seq}"]
            for n in range(FakeHls.seq, FakeHls.seq + 4):
                lines += ["#EXTINF:2.0,", f"seg-{n}.ts"]
            if FakeHls.ended:
                lines.append("#EXT-X-ENDLIST")
            FakeHls.seq += 1  # one new segment per playlist fetch
            body, ctype = ("\n".join(lines) + "\n").encode(), "application/vnd.apple.mpegurl"
        elif self.path.startswith("/seg-"):
            n = int(self.path[5:-3])
            body, ctype = _segment(b"\xff\xf1" + f"segment {n};".encode()), "video/mp2t"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_relay_joins_a_live_hls_station_for_speakers(client, monkeypatch):
    import asyncio

    import httpx

    from app.services import streams

    FakeHls.seq, FakeHls.ended, FakeHls.fetched = 10, False, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(streams, "_check", lambda url: url)  # the fake station is on loopback
    try:

        async def first_chunks():
            async with httpx.AsyncClient() as http:
                joined = streams.hls_audio(http, base + "/master.m3u8")
                got = [await joined.__anext__() for _ in range(4)]
                await joined.aclose()
                return got

        got = asyncio.run(first_chunks())
        # Starts two segments behind live (12, 13 of 10-13), then each new one as it appears, once.
        assert got == [b"\xff\xf1segment 12;", b"\xff\xf1segment 13;", b"\xff\xf1segment 14;", b"\xff\xf1segment 15;"]
        assert "/seg-10.ts" not in FakeHls.fetched

        # Through the relay route: an HLS token answers with one AAC stream. (The test client reads
        # the whole body, so this station's playlist ends; a live one streams until the speaker goes.)
        FakeHls.seq, FakeHls.ended = 20, True
        tok = streams.relay_token(base + "/master.m3u8", hls=True)
        resp = client.get(f"/relay/{tok}")
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("audio/aac")
        assert resp.content == b"\xff\xf1segment 22;\xff\xf1segment 23;"
    finally:
        server.shutdown()


# -- The player bar (Settings → Player bar) ---------------------------------------------------------


def test_player_bar_setting_is_per_person_and_api_now_follows_your_output(client):
    from app import db, routes

    assert 'data-player-bar-mode="device"' in client.get("/").text  # the default: This device only
    assert "tab=general" in client.get("/settings").text  # Player bar lives in General
    page = client.get("/settings?tab=general").text
    assert 'value="always"' in page and 'value="off"' in page
    assert "error=" in _settings(client, {"mode": "loud"}).headers["location"]
    assert "msg=" in _settings(client, {"mode": "always"}).headers["location"]
    assert db.get_person_pref("", routes.PLAYER_BAR_KEY) == "always"
    assert 'data-player-bar-mode="always"' in client.get("/discover").text

    now = client.get("/api/now").json()
    assert now["ok"] and now["output"] == "browser"
    _post(client, "/output", {"output": "dlna:uuid:mock-kitchen"})
    _post(client, "/play", {"station_id": "kexp"})
    now = client.get("/api/now").json()
    assert now["output"] == "dlna:uuid:mock-kitchen" and now["playing"] and now["output_name"] == "Kitchen"
    assert "KEXP" in now["title"]


def test_station_labels_split_long_radio_browser_names():
    from app.services.stations import station_label

    assert station_label("FIP") == ("FIP", "")
    assert station_label("SomaFM Groove Salad") == ("SomaFM Groove Salad", "")
    assert station_label("Radio Nostalgie - Les plus grands hits des années 80 - non-stop") == (
        "Radio Nostalgie", "Les plus grands hits des années 80 - non-stop")
    assert station_label("Jazz24 | KNKX Public Radio jazz from Seattle") == ("Jazz24", "KNKX Public Radio jazz from Seattle")
    assert station_label("A very long station name without any separator at all") == (
        "A very long station name without any separator at all", "")


def test_done_message_joins_a_page_that_already_has_a_query(client):
    from stonepi_auth.session import CSRF_COOKIE

    client.cookies.set(CSRF_COOKIE, "tok")
    resp = client.post("/favorites/add", data={"csrf_token": "tok", "station_id": "fip", "name": "FIP",
                                               "url": "https://icecast.radiofrance.fr/fip-hifi.aac",
                                               "next": "/discover?genre=jazz"}, follow_redirects=False)
    assert resp.headers["location"] == "/discover?genre=jazz&msg=Saved%20FIP"


def test_now_playing_shows_the_station_logo_for_speakers_and_this_device(client):
    from app import db
    from app.services import art

    logo = db.get_station("kexp")["favicon"]
    assert logo
    want = art.art_url(logo)
    # This device: the reply carries the logo as a signed /art link for the bar.
    here = _post(client, "/play", {"station_id": "kexp"}).json()
    assert here["browser"]["art"] == want
    # A speaker doesn't report artwork: Now Playing and the bar use the station's logo.
    _post(client, "/output", {"output": "dlna:uuid:mock-kitchen"})
    _post(client, "/play", {"station_id": "kexp"})
    assert client.get("/api/now").json()["art"] == want
    assert f'src="{want}"' in client.get("/now").text.replace("&amp;", "&")
    # Moving to another speaker keeps the logo with the station.
    _post(client, "/output", {"output": "dlna:uuid:mock-living"})
    assert client.get("/api/now").json()["art"] == want



def test_presets_are_the_latest_favourites_or_four_you_choose(client):
    from app import db

    db.add_favorite("", name="FIP", stream_url="https://icecast.radiofrance.fr/fip-hifi.aac", station_id="fip")
    db.add_favorite("", name="NTS Radio 1", stream_url="https://stream-relay-geo.ntslive.net/stream", station_id="nts")
    now = client.get("/now").text
    assert "Your four latest favourites" in now and now.index("NTS Radio 1") < now.index(">FIP<")  # newest first

    kexp = db.get_station("kexp")["stream_url"]  # a Home station, not a favourite
    fip = "https://icecast.radiofrance.fr/fip-hifi.aac"
    resp = _settings(client, {"presets_mode": "set", "preset_1": kexp, "preset_2": fip})
    assert "msg=" in resp.headers["location"]
    now = client.get("/now").text
    grid = now[now.index('class="preset-grid"'):]
    assert "Your chosen four" in now and grid.index("KEXP") < grid.index(">FIP<") and "NTS Radio 1" not in grid
    page = client.get("/settings?tab=general").text
    assert f'value="{kexp}" selected' in page

    # Only your favourites and Home stations can be presets.
    bad = _settings(client, {"presets_mode": "set", "preset_1": "https://evil.example.test/x"})
    assert "error=" in bad.headers["location"]
    # A chosen station that goes is skipped; with none left, back to the latest favourites.
    jazz = "https://live.example.test/jazz24.mp3"  # a favourite only, not a Home station
    db.add_favorite("", name="Jazz24", stream_url=jazz, station_id="jazz24")
    _settings(client, {"presets_mode": "set", "preset_1": jazz})
    assert "Jazz24" in client.get("/now").text
    db.remove_favorite("", "jazz24")
    now = client.get("/now").text
    grid = now[now.index('class="preset-grid"'):]
    assert "Jazz24" not in grid and "NTS Radio 1" in grid and "Your chosen four" in now


# -- The song on air (ICY metadata) ------------------------------------------------------------------


class FakeIcecast(BaseHTTPRequestHandler):
    """An Icecast-style station: 16 bytes of audio, then a metadata block, when asked."""

    title = "Cage the Elephant - Neon Pill"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        asked = self.headers.get("Icy-MetaData") == "1"
        if asked:
            self.send_header("icy-metaint", "16")
        self.end_headers()
        meta = f"StreamTitle='{FakeIcecast.title}';StreamUrl='';".encode()
        meta += b"\x00" * (-len(meta) % 16)
        block = b"\xff" * 16 + (bytes([len(meta) // 16]) + meta if asked else b"")
        try:
            for _ in range(4):
                self.wfile.write(block)
        except OSError:
            pass


def test_song_title_is_read_from_icy_metadata(monkeypatch):
    from app.services import streams

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeIcecast)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(streams, "_check", lambda url: url)  # the fake station is on loopback
    try:
        url = f"http://127.0.0.1:{server.server_port}/live"
        assert streams._read_icy_title(url) == "Cage the Elephant - Neon Pill"
    finally:
        server.shutdown()
    assert streams._parse_stream_title(b"StreamTitle='';StreamUrl='';\x00\x00") == ""  # KEXP sends it empty
    assert streams._parse_stream_title(b"StreamUrl='x';") == ""


def test_api_song_asks_about_your_station(client, monkeypatch):
    from app.services import streams

    asked = []
    monkeypatch.setattr(streams, "song_title", lambda url: asked.append(url) or "Rena Jones - What Once Was")
    # This device: the page says which station.
    res = client.get("/api/song", params={"url": "https://ice1.somafm.com/groovesalad-128-mp3"}).json()
    assert res == {"ok": True, "song": "Rena Jones - What Once Was"}
    # A speaker: the station last started there.
    _post(client, "/output", {"output": "dlna:uuid:mock-kitchen"})
    _post(client, "/play", {"station_id": "kexp"})
    client.get("/api/song")
    assert asked[-1] == "https://kexp.streamguys1.com/kexp160.aac"
    # Never the Pi's own services.
    assert client.get("/api/song", params={"url": "http://127.0.0.1:8011/"}).json()["song"] == ""


def test_admins_can_hide_the_add_station_button(client):
    assert 'class="sp-fab"' in client.get("/").text  # no sign-in here: everyone counts as an admin
    assert 'name="show_add"' in client.get("/settings?tab=general").text
    assert "msg=" in _settings(client, {"show_add_field": "1"}).headers["location"]  # unticked
    page = client.get("/").text
    assert 'class="sp-fab"' not in page and "Home stations" in page
    _settings(client, {"show_add_field": "1", "show_add": "1"})
    assert 'class="sp-fab"' in client.get("/").text



def test_admins_add_a_home_station_from_settings(client):
    from stonepi_auth.session import CSRF_COOKIE

    from app import db

    assert "Add a Home station" in client.get("/settings?tab=general").text
    client.cookies.set(CSRF_COOKIE, "tok")
    resp = client.post("/stations/add", data={"csrf_token": "tok", "name": "Local FM", "url": "https://ice.example.test/local",
                                              "next": "/settings?tab=general"}, follow_redirects=False)
    assert resp.headers["location"].startswith("/settings?tab=general&msg=")
    assert db.find_station_by_url("https://ice.example.test/local")


def test_player_bar_position_is_per_person(client):
    from app import db, routes

    assert 'data-player-pos="bottom-left"' in client.get("/").text  # the default
    page = client.get("/settings?tab=general").text
    assert 'name="bar_pos" value="top-right"' in page and "Player bar position" in page
    assert "msg=" in _settings(client, {"bar_pos": "top-right"}).headers["location"]
    assert db.get_person_pref("", routes.PLAYER_POS_KEY) == "top-right"
    assert 'data-player-pos="top-right"' in client.get("/discover").text
    assert "error=" in _settings(client, {"bar_pos": "middle"}).headers["location"]
    assert db.get_person_pref("", routes.PLAYER_POS_KEY) == "top-right"


def test_genre_results_are_that_genre():
    from app.services import discover

    stuffed = ",".join(["#charts", "#club", "dance", "deep house", "hiphop", "pop", "rock", "edm", "trance",
                        "techno", "chillout", "beach", "lounge", "90s"])
    rows = [
        {"name": "#TOP 100 CLUB CHARTS", "tags": stuffed, "stream_url": "a"},  # tag-stuffed: out
        {"name": "MajorFM", "tags": "80s,dj mixes,electro,hiphop,jazz", "stream_url": "b"},  # hiphop 4th
        {"name": "Smooth FM", "tags": "jazz,soul", "stream_url": "c"},  # no hiphop tag: out
        {"name": "HIP HOP RADIO", "tags": "urban,hiphop", "stream_url": "d"},  # named for it: first
        {"name": "The Beat", "tags": "hiphop,rnb", "stream_url": "e"},  # hiphop first
    ]
    assert [r["stream_url"] for r in discover.genre_fit(rows, "hiphop")] == ["d", "e", "b"]
    # A tag-stuffed station named for the genre stays in.
    named = [{"name": "Hip-Hop Charts", "tags": stuffed, "stream_url": "f"}]
    assert discover.genre_fit(named, "hiphop") == named


def test_genre_named_means_a_whole_word():
    from app.services.discover import _named

    assert _named("Hip Hop Radio", "hiphop", "hiphop")
    assert _named("HIPHOP 24/7", "hiphop", "hiphop")
    assert _named("BBC Radio Jazz", "jazz", "jazz")
    assert not _named("Bedrock FM", "rock", "rock")
    assert not _named("Radio Grapevine", "rap", "rap")
    assert not _named("Lighthouse FM", "house", "house")


def test_stream_info_from_headers():
    """kbps and kHz as Icecast and Shoutcast announce them; zeros when a station doesn't say."""
    from app.services.streams import parse_stream_info

    assert parse_stream_info({"icy-br": "128", "icy-sr": "44100"}) == {"kbps": 128, "khz": 44}
    assert parse_stream_info({"ice-audio-info": "ice-samplerate=48000;ice-bitrate=192;ice-channels=2"}) == {"kbps": 192, "khz": 48}
    assert parse_stream_info({"ice-audio-info": "samplerate=22050;bitrate=64"}) == {"kbps": 64, "khz": 22}
    assert parse_stream_info({"icy-br": "128, 128"}) == {"kbps": 128, "khz": 0}
    assert parse_stream_info({}) == {"kbps": 0, "khz": 0}
    assert parse_stream_info({"icy-br": "garbage"}) == {"kbps": 0, "khz": 0}


def test_frame_info_reads_mp3_and_adts_headers():
    """No sample rate in the headers: the first audio frames give it (two must agree)."""
    from app.services.streams import frame_info

    # MPEG-1 layer III, 128 kbps, 44.1 kHz: 417-byte frames.
    mp3 = bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(413)
    assert frame_info(b"junk" + mp3 + mp3) == {"kbps": 128, "khz": 44}
    # ADTS (AAC), 48 kHz (index 3), 300-byte frames → ~112 kbps from frame size.
    def adts(idx, length=300):
        return bytes([0xFF, 0xF1, 0x40 | (idx << 2), 0x80 | (length >> 11), (length >> 3) & 0xFF, ((length & 7) << 5) | 0x1F, 0xFC]) + bytes(length - 7)
    assert frame_info(adts(3) + adts(3)) == {"kbps": 112, "khz": 48}
    # 22.05 kHz ADTS is HE-AAC's core rate: shown as the 44 kHz it plays at; kbps uses the core rate.
    assert frame_info(adts(7) + adts(7)) == {"kbps": 52, "khz": 44}
    # One lone sync word isn't a frame.
    assert frame_info(bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(100)) == {"kbps": 0, "khz": 0}


def test_stream_info_reads_in_the_background(monkeypatch):
    """The first ask returns at once (zeros) and reads the stream in the background; a failed
    read is kept a minute, a good one ten."""
    import threading

    from app.config import env
    from app.services import streams

    monkeypatch.setattr(env, "mock", False)
    streams._info.clear()
    streams._info_reading.clear()
    gate = threading.Event()
    reads = []

    def slow_read(url):
        reads.append(url)
        gate.wait(5)
        return {"kbps": 128, "khz": 44}

    monkeypatch.setattr(streams, "resolve", lambda url: {"url": url, "hls": False})
    monkeypatch.setattr(streams, "_read_stream_info", slow_read)
    assert streams.stream_info("http://radio.example/a") == {"kbps": 0, "khz": 0}  # not waiting
    assert streams.stream_info("http://radio.example/a") == {"kbps": 0, "khz": 0}
    gate.set()
    for _ in range(50):
        if "http://radio.example/a" in streams._info:
            break
        threading.Event().wait(0.05)
    assert reads == ["http://radio.example/a"]  # one read at a time
    assert streams.stream_info("http://radio.example/a") == {"kbps": 128, "khz": 44}


def test_stream_info_wait_s_holds_for_the_background_read(monkeypatch):
    """info=1 on the Car Thing: hold briefly so the skin's first paint can show kbps/kHz."""
    import threading

    from app.config import env
    from app.services import streams

    monkeypatch.setattr(env, "mock", False)
    streams._info.clear()
    streams._info_reading.clear()

    def slow_read(url):
        threading.Event().wait(0.15)
        return {"kbps": 192, "khz": 48}

    monkeypatch.setattr(streams, "resolve", lambda url: {"url": url, "hls": False})
    monkeypatch.setattr(streams, "_read_stream_info", slow_read)
    assert streams.stream_info("http://radio.example/b", wait_s=1.0) == {"kbps": 192, "khz": 48}


def test_stream_info_reads_hls_bandwidth_and_segment_frames(monkeypatch):
    """HLS (BBC 6 Music etc.): master BANDWIDTH → kbps; a segment's AAC frames → kHz (and kbps if no master)."""
    from app.config import env
    from app.services import streams

    monkeypatch.setattr(env, "mock", False)
    streams._info.clear()
    streams._info_reading.clear()

    # Valid ADTS at 24 kHz core (HE-AAC → 48 kHz display), ~96 kbps from a 517-byte frame.
    length = 517
    adts = bytes([
        0xFF, 0xF1, 0x40 | (6 << 2), 0x80 | (length >> 11),
        (length >> 3) & 0xFF, ((length & 7) << 5) | 0x1F, 0xFC,
    ]) + bytes(length - 7)
    seg = _segment(adts + adts)

    class HlsInfo(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/master.m3u8":
                body = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=96000\naudio.m3u8\n"
            elif self.path == "/audio.m3u8":
                body = (
                    b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:1\n"
                    b"#EXTINF:6.0,\nbbc-audio=96000-a.ts\n#EXTINF:6.0,\nbbc-audio=96000-b.ts\n"
                )
            elif self.path.endswith(".ts"):
                body = seg
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), HlsInfo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(streams, "_check", lambda url: url)
    try:
        monkeypatch.setattr(streams, "resolve", lambda url: {"url": base + "/master.m3u8", "hls": True})
        assert streams.stream_info("https://station.example/6music", wait=True) == {"kbps": 96, "khz": 48}
        # Media playlist only (BBC's default URL is already the 96 kbit/s variant): kbps from the path.
        streams._info.clear()
        streams._info_reading.clear()
        monkeypatch.setattr(streams, "resolve", lambda url: {"url": base + "/audio.m3u8", "hls": True})
        assert streams.stream_info("https://station.example/6music-direct", wait=True) == {"kbps": 96, "khz": 48}
        assert streams._hls_url_kbps(
            "https://x/bbc_6music-audio%3d96000.norewind.m3u8"
        ) == 96
    finally:
        server.shutdown()
