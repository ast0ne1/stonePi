"""What a station's address really is, and the relay that hands it on.

``resolve(url)`` follows redirects and unwraps .pls / .m3u playlists to the stream itself,
and says what it is: its media type, and whether it's HLS (a playlist of segments, which
browsers play but cheap DLNA speakers don't). Every hop must pass
``stations.check_stream_url`` (http(s), never this Pi's loopback services).

The relay (``/relay/<token>``) passes a stream's bytes on unchanged for listeners that
can't fetch it themselves: speakers that can't do https, and browsers on an https page
with an http stream. A token names one stream and expires; it's signed with a per-install
key, and is the only key (speakers can't sign in).

For speakers, the relay also joins HLS: it follows the live playlist, fetches each new
segment and passes on its AAC audio as one continuous ADTS stream (``hls_audio``), taking
it out of MPEG-TS when that's how the segments come (the BBC's do). Encrypted and fMP4
(``#EXT-X-MAP``) streams aren't joined.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, AsyncIterator
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app import db
from app.config import env

TIMEOUT = httpx.Timeout(6.0, connect=4.0)
MAX_HOPS = 4
PLAYLIST_MAX = 64 * 1024
RESOLVE_KEEP_S = 10 * 60
RELAY_TTL_S = 6 * 3600
USER_AGENT = "StonePi-Radio"
HLS_MIME = "audio/aac"  # what the joined stream is: ADTS AAC, like an Icecast .aac station
HLS_START_SEGMENTS = 2  # start this many segments behind live: a speaker fills its buffer at once
HLS_SEGMENT_MAX = 4 * 1024 * 1024

PLAYLIST_TYPES = {
    "audio/x-scpls",
    "application/pls+xml",
    "audio/x-mpegurl",
    "audio/mpegurl",
    "application/x-mpegurl",
    "application/vnd.apple.mpegurl",
    "application/vnd.apple.mpegurl.audio",
}
EXT_MIME = {
    ".mp3": "audio/mpeg",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
}

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_key_lock = threading.Lock()


class NotAStream(ValueError):
    """The address doesn't lead to something a player can play."""


def _check(url: str) -> str:
    from app.services.stations import check_stream_url

    return check_stream_url(url)


def _ext(url: str) -> str:
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    return path[dot:] if dot > path.rfind("/") else ""


def _guess(url: str) -> dict[str, Any]:
    ext = _ext(url)
    return {"url": url, "mime": EXT_MIME.get(ext, "audio/mpeg"), "hls": ext == ".m3u8"}


def _parse_playlist(text: str, base: str) -> tuple[str, bool]:
    """(next address, is HLS) from a .pls / .m3u body."""
    if "#EXT-X-" in text:
        return base, True  # HLS: the playlist itself is what players load
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:  # .pls: [playlist] / NumberOfEntries=… / File1=http://…
        key, sep, value = line.partition("=")
        if sep and key.lower().startswith("file") and value.strip():
            return urljoin(base, value.strip()), False
    for line in lines:  # .m3u: comments (#…), then one address per line
        if line.startswith(("#", "[")) or "=" in line.split("/", 1)[0]:
            continue
        return urljoin(base, line), False
    raise NotAStream("The playlist has no stream in it.")


def resolve(url: str) -> dict[str, Any]:
    """{url, mime, hls} for a station address, following redirects and playlists."""
    url = _check(url)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
    if hit and now - hit[0] < RESOLVE_KEEP_S:
        return dict(hit[1])
    if env.mock:
        return _guess(url)  # no network under RADIO_MOCK
    found = _resolve(url)
    with _cache_lock:
        _cache[url] = (now, found)
    return dict(found)


def _resolve(url: str) -> dict[str, Any]:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(MAX_HOPS * 2):
            url = _check(url)
            try:
                with client.stream("GET", url) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        url = urljoin(url, resp.headers["location"])
                        continue
                    if resp.status_code >= 400:
                        raise NotAStream(f"The station answered {resp.status_code}.")
                    ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                    ext = _ext(url)
                    if ctype in PLAYLIST_TYPES or ext in {".pls", ".m3u", ".m3u8"} or ctype.startswith("text/plain"):
                        body = bytearray()
                        for chunk in resp.iter_bytes():
                            body.extend(chunk)
                            if len(body) > PLAYLIST_MAX:
                                break
                        nxt, hls = _parse_playlist(body.decode("utf-8", "replace"), url)
                        if hls:
                            return {"url": url, "mime": "application/vnd.apple.mpegurl", "hls": True}
                        url = nxt
                        continue
                    if ctype.startswith("text/html"):
                        raise NotAStream("That address is a web page, not a stream.")
                    mime = ctype if (ctype.startswith("audio/") or ctype in {"application/ogg", "video/mp2t"}) else ""
                    return {"url": url, "mime": mime or EXT_MIME.get(ext, "audio/mpeg"), "hls": False}
            except httpx.HTTPError as exc:
                raise NotAStream("The station isn't answering.") from exc
    raise NotAStream("Too many redirects or playlists.")


# -- Relay ----------------------------------------------------------------------------------------


def _key() -> bytes:
    with _key_lock:
        key = db.get_pref("relay_key")
        if not key:
            key = secrets.token_hex(32)
            db.set_pref("relay_key", key)
    return key.encode()


def relay_token(url: str, *, ttl: int = RELAY_TTL_S, hls: bool = False) -> str:
    data: dict[str, Any] = {"u": url, "e": int(time.time()) + ttl}
    if hls:
        data["h"] = 1  # join the HLS playlist into one stream
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    sig = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{payload}.{sig}"


def relay_target(token: str) -> str | None:
    """The stream a relay token names, or None when it's forged or expired."""
    found = relay_info(token)
    return found["url"] if found else None


def relay_info(token: str) -> dict[str, Any] | None:
    """{url, hls} for a relay token, or None when it's forged or expired."""
    payload, _, sig = (token or "").partition(".")
    if not payload or not sig:
        return None
    want = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()[:40]
    if not hmac.compare_digest(sig, want):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, TypeError):
        return None
    if int(data.get("e") or 0) < time.time():
        return None
    try:
        return {"url": _check(str(data.get("u") or "")), "hls": bool(data.get("h"))}
    except ValueError:
        return None


def lan_address_for(host: str) -> str:
    """This Pi's address as seen from ``host`` on the home network (the interface it'd use)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((host, 9))  # UDP: no packet is sent, the OS just picks the route
            return sock.getsockname()[0]
    except OSError:
        return ""


def relay_path(url: str, *, hls: bool = False) -> str:
    """The relay's path on this app (prefix included), for a browser on the same origin."""
    prefix = (env.stonepi_prefix or "").rstrip("/")
    return f"{prefix}/relay/{relay_token(url, hls=hls)}"


def relay_url_for_speaker(url: str, speaker_host: str, *, hls: bool = False) -> str:
    """An address a speaker can fetch: plain HTTP to the Pi's LAN address, through nginx."""
    ip = lan_address_for(speaker_host)
    if not ip:
        raise NotAStream("Couldn't work out this Pi's address on the home network.")
    return f"http://{ip}{relay_path(url, hls=hls)}"


# -- What song is on (ICY metadata) ------------------------------------------------------------------
# Icecast / Shoutcast streams carry "StreamTitle='Artist - Title';" every icy-metaint bytes when
# asked (Icy-MetaData: 1). Radio reads up to the first block and lets go; nothing else is kept.

SONG_KEEP_S = 20
ICY_METAINT_MAX = 64 * 1024
_songs: dict[str, tuple[float, str]] = {}
_songs_lock = threading.Lock()


def _parse_stream_title(meta: bytes) -> str:
    text = meta.rstrip(b"\x00").decode("utf-8", "replace")
    start = text.find("StreamTitle='")
    if start < 0:
        return ""
    start += len("StreamTitle='")
    end = text.find("';", start)
    title = text[start:end if end >= 0 else None].strip()
    return " ".join(title.split())[:200]


def _read_icy_title(url: str) -> str:
    headers = {"User-Agent": USER_AGENT, "Icy-MetaData": "1"}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers=headers) as client:
        for _ in range(MAX_HOPS):
            url = _check(url)
            with client.stream("GET", url) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                try:
                    metaint = int(resp.headers.get("icy-metaint") or 0)
                except ValueError:
                    metaint = 0
                if resp.status_code >= 400 or not 0 < metaint <= ICY_METAINT_MAX:
                    return ""  # no song metadata from this station
                buf = bytearray()
                for chunk in resp.iter_raw():
                    buf.extend(chunk)
                    if len(buf) > metaint:
                        size = buf[metaint] * 16
                        if len(buf) >= metaint + 1 + size:
                            return _parse_stream_title(bytes(buf[metaint + 1:metaint + 1 + size]))
                    if len(buf) > metaint + 1 + 255 * 16:
                        return ""
                return ""
    return ""


def song_title(station_url: str) -> str:
    """'Artist - Title' the station is sending now, or "" (no ICY metadata, HLS, not answering)."""
    if not station_url or env.mock:
        return ""
    now = time.monotonic()
    with _songs_lock:
        hit = _songs.get(station_url)
    if hit and now - hit[0] < SONG_KEEP_S:
        return hit[1]
    try:
        found = resolve(station_url)
        title = "" if found["hls"] else _read_icy_title(found["url"])
    except (NotAStream, ValueError, httpx.HTTPError):
        title = ""
    with _songs_lock:
        _songs[station_url] = (now, title)
    return title


# -- Bitrate and sample rate (stream headers) ------------------------------------------------------
# Icecast/Shoutcast announce them when the stream opens: icy-br (kbps), icy-sr (Hz) or
# ice-audio-info ("ice-samplerate=44100;ice-bitrate=128;..."). Only the headers are read; a
# station's numbers don't change mid-stream, so they're kept 10 minutes. HLS has no ICY headers:
# the master playlist's BANDWIDTH (when present) and the first audio frames of a segment fill
# the same boxes (BBC 6 Music etc.). Stations that still don't say leave zeros.

INFO_KEEP_S = 600
INFO_FAILED_KEEP_S = 60
_info: dict[str, tuple[float, dict[str, int]]] = {}
_info_reading: set[str] = set()


def _first_int(value: str | None) -> int:
    for part in str(value or "").replace(";", ",").split(","):
        digits = "".join(ch for ch in part.strip() if ch.isdigit())
        if digits:
            return int(digits)
    return 0


def parse_stream_info(headers: Any) -> dict[str, int]:
    """{kbps, khz} from a stream's response headers; 0 when it doesn't say."""
    audio = {}
    for item in str(headers.get("ice-audio-info") or "").split(";"):
        key, _, value = item.partition("=")
        audio[key.strip().lower().removeprefix("ice-")] = value.strip()
    kbps = _first_int(headers.get("icy-br")) or _first_int(audio.get("bitrate"))
    rate = _first_int(headers.get("icy-sr")) or _first_int(audio.get("samplerate"))
    khz = round(rate / 1000) if rate >= 1000 else rate  # 44100 Hz -> 44; some say "44"
    return {"kbps": kbps if 0 < kbps < 10000 else 0, "khz": khz if 0 < khz < 400 else 0}


# When the headers don't say, the audio does: an MP3 frame header and an AAC ADTS header both
# carry the sample rate (and MP3 the bitrate). The first frame that checks out in the first
# 16 KB is used; two in a row must agree, so a stray 0xFF in the data isn't taken for one.
FRAME_SCAN = 16 * 1024
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_MP3_KBPS_L3 = {3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
                2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}
_ADTS_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def _frame_at(buf: bytes, i: int) -> tuple[int, int, int] | None:
    """(sample rate Hz, kbps or 0, frame length) of an MP3 (layer III) or ADTS frame at i."""
    if i + 7 > len(buf) or buf[i] != 0xFF:
        return None
    b1, b2 = buf[i + 1], buf[i + 2]
    if b1 & 0xF6 == 0xF0:  # ADTS: sync 0xFFF, layer 00
        idx = (b2 >> 2) & 0x0F
        if idx >= len(_ADTS_RATES):
            return None
        length = ((buf[i + 3] & 0x03) << 11) | (buf[i + 4] << 3) | (buf[i + 5] >> 5)
        if length <= 7:
            return None
        # HE-AAC (most radio AAC at 64 kbps and under) signals its core rate; the decoder plays
        # at twice that, which is what Winamp shows. Plain AAC this low is all but unheard of.
        # Bitrate from frame size uses the core rate (1024 samples); the display rate may be ×2.
        core = _ADTS_RATES[idx]
        rate = core * 2 if core <= 24000 else core
        kbps = max(1, round(length * 8 * core / 1024 / 1000))
        return (rate, kbps if kbps < 10000 else 0, length)
    if b1 & 0xE0 == 0xE0 and (b1 >> 1) & 0x03 == 0x01:  # MPEG audio layer III
        version = (b1 >> 3) & 0x03
        rate_idx, br_idx = (b2 >> 2) & 0x03, b2 >> 4
        if version == 1 or rate_idx == 3 or br_idx in (0, 15):
            return None
        rate = _MP3_RATES[version][rate_idx]
        kbps = _MP3_KBPS_L3[3 if version == 3 else 2][br_idx]
        length = (144 if version == 3 else 72) * kbps * 1000 // rate + ((b2 >> 1) & 1)
        return (rate, kbps, length) if length > 4 else None
    return None


def frame_info(buf: bytes) -> dict[str, int]:
    """{kbps, khz} from the first audio frame in ``buf`` that a second one confirms."""
    for i in range(max(0, len(buf) - 7)):
        first = _frame_at(buf, i)
        if first is None:
            continue
        second = _frame_at(buf, i + first[2])
        if second is not None and second[0] == first[0]:
            return {"kbps": first[1], "khz": round(first[0] / 1000)}
    return {"kbps": 0, "khz": 0}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Leave redirects to us so every hop still passes ``_check`` (SSRF)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, N802
        return None


_STREAM_OPENER = urllib.request.build_opener(_NoRedirect)


def _read_stream_info(url: str) -> dict[str, int]:
    # urllib, not httpx: Icecast/Shoutcast often answers with ``Transfer-Encoding: identity``,
    # which httpx/httpcore refuses ("Only Transfer-Encoding: chunked is supported"), so the
    # Car Thing's skins got 0/0 for stations like 90s90s even though icy-br was in the headers.
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    timeout = float(TIMEOUT.read or TIMEOUT.connect or 6.0)
    for _ in range(MAX_HOPS):
        url = _check(url)
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = _STREAM_OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                url = urljoin(url, exc.headers["Location"])
                continue
            return {"kbps": 0, "khz": 0}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return {"kbps": 0, "khz": 0}
        try:
            status = getattr(resp, "status", None) or resp.getcode()
            if 300 <= int(status) < 400 and resp.headers.get("Location"):
                url = urljoin(url, resp.headers["Location"])
                continue
            if int(status) >= 400:
                return {"kbps": 0, "khz": 0}
            info = parse_stream_info(resp.headers)
            if not info["khz"] or not info["kbps"]:
                found = frame_info(resp.read(FRAME_SCAN))
                info = {"kbps": info["kbps"] or found["kbps"], "khz": info["khz"] or found["khz"]}
            return info
        finally:
            try:
                resp.close()
            except Exception:
                pass
    return {"kbps": 0, "khz": 0}


def _fetch_limited(url: str, limit: int) -> bytes:
    """Body of ``url`` (≤ ``limit`` bytes), every redirect checked like the station address."""
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    timeout = float(TIMEOUT.read or TIMEOUT.connect or 6.0)
    for _ in range(MAX_HOPS):
        url = _check(url)
        req = urllib.request.Request(url, headers=headers)
        try:
            resp = _STREAM_OPENER.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get("Location"):
                url = urljoin(url, exc.headers["Location"])
                continue
            raise NotAStream(f"The station answered {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise NotAStream("Couldn't reach the station.") from exc
        try:
            status = getattr(resp, "status", None) or resp.getcode()
            if 300 <= int(status) < 400 and resp.headers.get("Location"):
                url = urljoin(url, resp.headers["Location"])
                continue
            if int(status) >= 400:
                raise NotAStream(f"The station answered {status}.")
            return resp.read(limit)
        finally:
            try:
                resp.close()
            except Exception:
                pass
    raise NotAStream("Too many redirects.")


def _pick_hls_variant(variants: list[tuple[int, str]]) -> tuple[int, str]:
    """Same choice as ``hls_audio``: best audio-sized variant under 256 kbit/s, else the smallest."""
    small = [v for v in variants if v[0] <= 256_000] or [min(variants)]
    return max(small)


def _hls_url_kbps(*urls: str) -> int:
    """Bitrate named in a USP/BBC-style HLS path (``…audio=96000…`` / ``audio%3d96000``)."""
    for url in urls:
        match = re.search(r"audio[=_](\d{4,6})\b", unquote(url or ""), re.I)
        if match:
            kbps = round(int(match.group(1)) / 1000) if int(match.group(1)) >= 1000 else int(match.group(1))
            if 8 <= kbps < 10000:
                return kbps
    return 0


def _read_hls_info(url: str) -> dict[str, int]:
    """{kbps, khz} from an HLS master/media playlist and one recent segment's audio frames."""
    body = _fetch_limited(url, PLAYLIST_MAX)
    found = parse_media_playlist(body.decode("utf-8", "replace"), url)
    kbps = _hls_url_kbps(url)
    if found["variants"]:
        bw, url = _pick_hls_variant(found["variants"])
        kbps = round(bw / 1000) if bw > 0 else kbps
        body = _fetch_limited(url, PLAYLIST_MAX)
        found = parse_media_playlist(body.decode("utf-8", "replace"), url)
        kbps = kbps or _hls_url_kbps(url)
    segments = found["segments"]
    if not segments:
        return {"kbps": kbps if 0 < kbps < 10000 else 0, "khz": 0}
    # One segment behind live so a just-listed file is already on the CDN.
    _, seg_url = segments[-2] if len(segments) > 1 else segments[-1]
    kbps = kbps or _hls_url_kbps(seg_url)
    data = _fetch_limited(seg_url, FRAME_SCAN)
    if data[:1] == b"\x47":
        audio = TsAudio().feed(data)
    else:
        audio = _strip_id3(data)
    found_frames = frame_info(audio[:FRAME_SCAN] if audio else b"")
    return {
        "kbps": (kbps if 0 < kbps < 10000 else 0) or found_frames["kbps"],
        "khz": found_frames["khz"],
    }


def _read_info_into_cache(station_url: str) -> None:
    empty = {"kbps": 0, "khz": 0}
    try:
        found = resolve(station_url)
        info = _read_hls_info(found["url"]) if found["hls"] else _read_stream_info(found["url"])
    except (NotAStream, ValueError, httpx.HTTPError):
        info = empty
    with _songs_lock:
        _info[station_url] = (time.monotonic(), info)
        _info_reading.discard(station_url)


def stream_info(station_url: str, *, wait: bool = False, wait_s: float = 0.0) -> dict[str, int]:
    """The station's bitrate (kbps) and sample rate (kHz), or zeros while they're being read.

    Reading opens the stream (headers, maybe its first frames), which can take seconds, so it
    runs in the background, one read per station at a time; callers get the last numbers or
    zeros straight away (the Car Thing asks again within seconds). ``wait`` reads inline.
    ``wait_s`` starts the background read then holds up to that many seconds for an answer —
    enough for most stations' headers so the Car Thing's first skin paint can show kbps/kHz.
    """
    empty = {"kbps": 0, "khz": 0}
    if not station_url:
        return empty
    if env.mock:
        return {"kbps": 128, "khz": 44}
    now = time.monotonic()
    with _songs_lock:
        hit = _info.get(station_url)
        keep = INFO_KEEP_S if hit and (hit[1]["kbps"] or hit[1]["khz"]) else INFO_FAILED_KEEP_S
        if hit and now - hit[0] < keep:
            return dict(hit[1])
        start = station_url not in _info_reading
        if start:
            _info_reading.add(station_url)
    if start:
        if wait:
            _read_info_into_cache(station_url)
        else:
            threading.Thread(target=_read_info_into_cache, args=(station_url,), name="radio-stream-info",
                             daemon=True).start()
    if wait or wait_s > 0:
        deadline = time.monotonic() + (0.0 if wait else wait_s)
        while True:
            with _songs_lock:
                hit = _info.get(station_url)
                reading = station_url in _info_reading
            if hit is not None and (wait or not reading):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
    with _songs_lock:
        hit = _info.get(station_url)
    return dict(hit[1]) if hit else empty


# -- HLS joined into one stream (for speakers) -------------------------------------------------------


class TsAudio:
    """The audio of an MPEG-TS stream: PES payloads of its first audio stream (0xC0-0xDF), in order.

    For AAC that's ADTS frames, which play as they are. Fed whole or partial 188-byte packets.
    """

    def __init__(self) -> None:
        self._pending = b""
        self._pid: int | None = None
        self._in_pes = False

    def feed(self, data: bytes) -> bytes:
        data = self._pending + data
        out = bytearray()
        i = 0
        while len(data) - i >= 188:
            if data[i] != 0x47:  # lost sync: find the next packet start
                nxt = data.find(b"\x47", i + 1)
                if nxt < 0:
                    i = len(data)
                    break
                i = nxt
                continue
            out += self._packet(data[i:i + 188])
            i += 188
        self._pending = bytes(data[i:])
        return bytes(out)

    def _packet(self, pkt: bytes) -> bytes:
        start = bool(pkt[1] & 0x40)
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        afc = (pkt[3] >> 4) & 0x3
        if not afc & 0x1:
            return b""  # adaptation field only, no payload
        pos = 4
        if afc & 0x2:
            pos += 1 + pkt[4]
        if pos >= 188:
            return b""
        payload = pkt[pos:]
        if self._pid is not None and pid != self._pid:
            return b""
        if start:
            if len(payload) < 9 or payload[:3] != b"\x00\x00\x01":
                return b""
            if not 0xC0 <= payload[3] <= 0xDF:
                return b""  # not audio (PAT/PMT sections never start with a PES start code)
            self._pid = pid
            self._in_pes = True
            return payload[9 + payload[8]:]
        return payload if self._in_pes and pid == self._pid else b""


def _strip_id3(data: bytes) -> bytes:
    """Packed-audio HLS segments open with an ID3 tag (the timestamp); the audio follows it."""
    if data[:3] == b"ID3" and len(data) >= 10:
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        return data[10 + size:]
    return data


def parse_media_playlist(text: str, base: str) -> dict[str, Any]:
    """{variants, segments [(seq, url)], target, ended} from an HLS playlist."""
    if "#EXT-X-KEY" in text and "METHOD=NONE" not in text:
        raise NotAStream("This station's stream is encrypted.")
    if "#EXT-X-MAP" in text:
        raise NotAStream("This station's stream can't be joined for speakers (fMP4).")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants: list[tuple[int, str]] = []
    segments: list[tuple[int, str]] = []
    seq = 0
    target = 6.0
    pending_variant: int | None = None
    for line in lines:
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1] or 0)
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target = float(line.split(":", 1)[1] or 6)
        elif line.startswith("#EXT-X-STREAM-INF:"):
            bw = 0
            for part in line.split(":", 1)[1].split(","):
                key, _, value = part.partition("=")
                if key.strip() == "BANDWIDTH" and value.strip().isdigit():
                    bw = int(value)
            pending_variant = bw
        elif line.startswith("#"):
            continue
        elif pending_variant is not None:
            variants.append((pending_variant, urljoin(base, line)))
            pending_variant = None
        else:
            segments.append((seq, urljoin(base, line)))
            seq += 1
    return {"variants": variants, "segments": segments, "target": max(1.0, min(target, 30.0)),
            "ended": "#EXT-X-ENDLIST" in text}


async def _fetch(client: httpx.AsyncClient, url: str, limit: int) -> tuple[str, bytes]:
    """(final url, body) with every redirect checked like the station address itself."""
    for _ in range(MAX_HOPS):
        url = _check(url)
        async with client.stream("GET", url) as resp:
            if resp.is_redirect and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            if resp.status_code >= 400:
                raise NotAStream(f"The station answered {resp.status_code}.")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise NotAStream("A segment of the stream was too large.")
            return url, bytes(body)
    raise NotAStream("Too many redirects.")


async def hls_audio(client: httpx.AsyncClient, url: str) -> AsyncIterator[bytes]:
    """A live HLS station as one continuous ADTS AAC stream, until the listener goes."""
    url, body = await _fetch(client, url, PLAYLIST_MAX)
    found = parse_media_playlist(body.decode("utf-8", "replace"), url)
    if found["variants"]:
        # A master playlist: the audio-only variants are the small ones; take the best of those
        # under 256 kbit/s, else the smallest.
        small = [v for v in found["variants"] if v[0] <= 256_000] or [min(found["variants"])]
        url, body = await _fetch(client, max(small)[1], PLAYLIST_MAX)
        found = parse_media_playlist(body.decode("utf-8", "replace"), url)
    demux = TsAudio()
    last = None
    while True:
        segments = found["segments"]
        if last is None:
            segments = segments[-HLS_START_SEGMENTS:]
        for seq, seg_url in segments:
            if last is not None and seq <= last:
                continue
            _, data = await _fetch(client, seg_url, HLS_SEGMENT_MAX)
            last = seq
            if data[:1] == b"\x47":
                audio = demux.feed(data)
            else:
                audio = _strip_id3(data)  # packed audio (.aac segments): already ADTS
            if audio:
                yield audio
        if found["ended"]:
            return
        await asyncio.sleep(found["target"] / 2)
        url, body = await _fetch(client, url, PLAYLIST_MAX)
        found = parse_media_playlist(body.decode("utf-8", "replace"), url)
