"""DLNA / UPnP speakers on the home network (radio-outputs.md §2–§4).

Discovery is an SSDP search for MediaRenderers; each answer's description (friendly name,
model, the AVTransport and RenderingControl control URLs) is read and kept in the
``speakers`` table. Playing is ``SetAVTransportURI`` + ``Play``: the speaker fetches the
stream itself, Radio only sends commands. No UPnP event subscriptions (nothing has to call
back into the Pi); ``now()`` asks the speaker.

Safety: a speaker's description and control URLs must be on a private home-network
address (never loopback, never the internet), and the control URLs on the same host the
description came from, so an answer on the LAN can't make Radio post to the Pi's own
services. Short timeouts and size caps throughout.

LinkPlay speakers (iEAST AudioCast M5 and friends) also answer simple HTTP commands
(``/httpapi.asp?command=…``); a speaker switched to LinkPlay in Settings → Speakers is
driven that way instead.

Under ``RADIO_MOCK`` two pretend speakers stand in, kept in memory.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import httpx

from app import db
from app.config import env

logger = logging.getLogger("radio.dlna")

SSDP_ADDR = ("239.255.255.250", 1900)
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
RC = "urn:schemas-upnp-org:service:RenderingControl:1"
SEARCH_S = 3.0
DESC_MAX = 128 * 1024
TIMEOUT = httpx.Timeout(4.0, connect=2.5)
REFRESH_EVERY_S = 10 * 60
REFRESH_AT_MOST_S = 30
NS = {"d": "urn:schemas-upnp-org:device-1-0"}

_refresh_lock = threading.Lock()
_last_refresh = 0.0


class SpeakerError(RuntimeError):
    """A speaker didn't do what it was asked."""


# -- Addresses ------------------------------------------------------------------------------------


def _private_host(host: str) -> bool:
    """A literal private home-network address: RFC 1918, link-local or ULA. Not loopback."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_unspecified and not ip.is_multicast


def _same_host(url: str, host: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname == host


# -- Discovery ------------------------------------------------------------------------------------


def _ssdp_search(wait: float = SEARCH_S) -> list[str]:
    """LOCATION headers of the renderers that answer an M-SEARCH."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        f"ST: {RENDERER}\r\n\r\n"
    ).encode()
    found: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind(("", 0))  # an ephemeral port: the firewall's LAN-only 32768-60999 rule lets replies in
        sock.settimeout(0.5)
        sock.sendto(msg, SSDP_ADDR)
        end = time.monotonic() + wait
        while time.monotonic() < end:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            for line in data.decode("utf-8", "replace").split("\r\n"):
                if line.lower().startswith("location:"):
                    loc = line.split(":", 1)[1].strip()
                    if loc not in found:
                        found.append(loc)
    return found


def _text(el, path: str) -> str:
    node = el.find(path, NS)
    return (node.text or "").strip() if node is not None and node.text else ""


def describe(location: str) -> dict[str, Any] | None:
    """A renderer's details from its description XML, or None when it isn't one we'll use."""
    parts = urlsplit(location)
    host = parts.hostname or ""
    if parts.scheme != "http" or not _private_host(host):
        return None
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            with client.stream("GET", location) as resp:
                if resp.status_code >= 400:
                    return None
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > DESC_MAX:
                        return None
        root = ElementTree.fromstring(bytes(body))
    except (httpx.HTTPError, ElementTree.ParseError):
        return None
    base = _text(root, "d:URLBase") or location
    for device in root.iter("{urn:schemas-upnp-org:device-1-0}device"):
        if not _text(device, "d:deviceType").startswith("urn:schemas-upnp-org:device:MediaRenderer"):
            continue
        controls = {}
        for svc in device.findall("d:serviceList/d:service", NS):
            stype = _text(svc, "d:serviceType")
            url = urljoin(base, _text(svc, "d:controlURL"))
            if "AVTransport" in stype:
                controls["av"] = url
            elif "RenderingControl" in stype:
                controls["rc"] = url
        av = controls.get("av", "")
        rc = controls.get("rc", "")
        if not av or not _same_host(av, host) or (rc and not _same_host(rc, host)):
            return None
        udn = _text(device, "d:UDN")
        if not udn:
            return None
        return {
            "udn": udn,
            "name": _text(device, "d:friendlyName")[:80] or host,
            "model": " ".join(x for x in (_text(device, "d:manufacturer"), _text(device, "d:modelName")) if x)[:80],
            "host": host,
            "location": location,
            "av_control": av,
            "rc_control": rc,
            "linkplay": _is_linkplay(host),
        }
    return None


def _is_linkplay(host: str) -> bool:
    try:
        with httpx.Client(timeout=httpx.Timeout(2.0)) as client:
            resp = client.get(f"http://{host}/httpapi.asp", params={"command": "getStatusEx"})
        data = resp.json() if resp.status_code < 400 else {}
        return isinstance(data, dict) and bool(data.get("uuid") or data.get("DeviceName"))
    except Exception:  # noqa: BLE001 - not answering means not LinkPlay
        return False


# Pretend speakers for RADIO_MOCK (dev, tests): kept in memory, not on the network.
MOCK_SPEAKERS = (
    {"udn": "uuid:mock-kitchen", "name": "Kitchen", "model": "iEAST AudioCast M5 (pretend)", "host": "192.0.2.10"},
    {"udn": "uuid:mock-living", "name": "Living room", "model": "iEAST AudioCast M5 (pretend)", "host": "192.0.2.11"},
)
_mock_state: dict[str, dict[str, Any]] = {}


def refresh(*, force: bool = False) -> int:
    """Search the network and record what answers; at most every 30s unless ``force``."""
    global _last_refresh
    with _refresh_lock:
        now = time.time()
        if not force and now - _last_refresh < REFRESH_AT_MOST_S:
            return 0
        _last_refresh = now
    if env.mock:
        for spk in MOCK_SPEAKERS:
            db.upsert_speaker(
                {**spk, "location": f"http://{spk['host']}/desc.xml", "av_control": "mock", "rc_control": "mock", "linkplay": False},
                now,
            )
        return len(MOCK_SPEAKERS)
    count = 0
    try:
        locations = _ssdp_search()
    except OSError as exc:
        logger.info("SSDP search failed: %s", exc)
        return 0
    for location in locations:
        info = describe(location)
        if info:
            db.upsert_speaker(info, now)
            count += 1
    return count


def start_background_refresh() -> None:
    """Search at startup, then every 10 minutes, in a daemon thread."""

    def loop() -> None:
        while True:
            try:
                refresh(force=True)
            except Exception:  # noqa: BLE001 - never let discovery kill the thread
                logger.exception("speaker discovery failed")
            time.sleep(REFRESH_EVERY_S)

    threading.Thread(target=loop, name="radio-dlna-discovery", daemon=True).start()


# -- Control (UPnP SOAP) --------------------------------------------------------------------------


def _soap(url: str, service: str, action: str, args: dict[str, Any]) -> ElementTree.Element:
    inner = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items())
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{service}">{inner}</u:{action}>'
        "</s:Body></s:Envelope>"
    )
    headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{service}#{action}"'}
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            resp = client.post(url, content=body.encode("utf-8"), headers=headers)
    except httpx.HTTPError as exc:
        raise SpeakerError("The speaker isn't answering.") from exc
    if resp.status_code >= 400:
        raise SpeakerError(f"The speaker refused {action} ({resp.status_code}).")
    try:
        return ElementTree.fromstring(resp.content[:DESC_MAX])
    except ElementTree.ParseError:
        return ElementTree.Element("empty")


def _out(root: ElementTree.Element, name: str) -> str:
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == name:
            return (el.text or "").strip()
    return ""


def didl(title: str, url: str, mime: str) -> str:
    """DIDL-Lite metadata for a radio stream (some renderers refuse a URI without it)."""
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
        f"<dc:title>{escape(title or 'Radio')}</dc:title>"
        "<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>"
        f'<res protocolInfo="http-get:*:{escape(mime or "audio/mpeg")}:*">{escape(url)}</res>'
        "</item></DIDL-Lite>"
    )


def _linkplay(spk: dict, command: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.get(f"http://{spk['host']}/httpapi.asp", params={"command": command})
    except httpx.HTTPError as exc:
        raise SpeakerError("The speaker isn't answering.") from exc
    if resp.status_code >= 400:
        raise SpeakerError(f"The speaker refused the command ({resp.status_code}).")
    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def play(spk: dict, url: str, mime: str, title: str) -> None:
    if spk["av_control"] == "mock":
        _mock_state[spk["udn"]] = {**_mock_state.get(spk["udn"], {"volume": 40}), "state": "PLAYING", "url": url}
    elif spk.get("use_linkplay"):
        _linkplay(spk, f"setPlayerCmd:play:{quote(url, safe=':/?&=%')}")
    else:
        try:
            _soap(spk["av_control"], AVT, "Stop", {"InstanceID": 0})
        except SpeakerError:
            pass  # nothing playing
        _soap(spk["av_control"], AVT, "SetAVTransportURI", {"InstanceID": 0, "CurrentURI": url, "CurrentURIMetaData": didl(title, url, mime)})
        _soap(spk["av_control"], AVT, "Play", {"InstanceID": 0, "Speed": 1})
    db.update_speaker(spk["udn"], now_title=title, now_station=url)


def state(spk: dict) -> str:
    """PLAYING / PAUSED_PLAYBACK / STOPPED / TRANSITIONING / NO_MEDIA_PRESENT."""
    if spk["av_control"] == "mock":
        return _mock_state.get(spk["udn"], {}).get("state", "STOPPED")
    if spk.get("use_linkplay"):
        status = str(_linkplay(spk, "getPlayerStatus").get("status") or "").lower()
        return {"play": "PLAYING", "pause": "PAUSED_PLAYBACK", "load": "TRANSITIONING"}.get(status, "STOPPED")
    return _out(_soap(spk["av_control"], AVT, "GetTransportInfo", {"InstanceID": 0}), "CurrentTransportState") or "STOPPED"


def transport(spk: dict, action: str) -> None:
    if action == "play_pause":
        action = "pause" if state(spk) == "PLAYING" else "play"
    if action not in {"play", "pause", "stop"}:
        raise SpeakerError(f"Unknown transport action: {action}")
    if spk["av_control"] == "mock":
        cur = _mock_state.setdefault(spk["udn"], {"volume": 40, "state": "STOPPED"})
        cur["state"] = {"play": "PLAYING", "pause": "PAUSED_PLAYBACK", "stop": "STOPPED"}[action]
        return
    if spk.get("use_linkplay"):
        _linkplay(spk, {"play": "setPlayerCmd:resume", "pause": "setPlayerCmd:pause", "stop": "setPlayerCmd:stop"}[action])
        return
    args = {"InstanceID": 0, "Speed": 1} if action == "play" else {"InstanceID": 0}
    _soap(spk["av_control"], AVT, action.capitalize(), args)


def get_volume(spk: dict) -> float | None:
    if spk["av_control"] == "mock":
        return _mock_state.get(spk["udn"], {}).get("volume", 40) / 100
    if spk.get("use_linkplay"):
        try:
            return int(_linkplay(spk, "getPlayerStatus").get("vol") or 0) / 100
        except (TypeError, ValueError):
            return None
    if not spk.get("rc_control"):
        return None
    raw = _out(_soap(spk["rc_control"], RC, "GetVolume", {"InstanceID": 0, "Channel": "Master"}), "CurrentVolume")
    return int(raw) / 100 if raw.isdigit() else None


def set_volume(spk: dict, level: float) -> None:
    pct = max(0, min(100, round(level * 100)))
    if spk["av_control"] == "mock":
        _mock_state.setdefault(spk["udn"], {"state": "STOPPED"})["volume"] = pct
    elif spk.get("use_linkplay"):
        _linkplay(spk, f"setPlayerCmd:vol:{pct}")
    elif spk.get("rc_control"):
        _soap(spk["rc_control"], RC, "SetVolume", {"InstanceID": 0, "Channel": "Master", "DesiredVolume": pct})
    else:
        raise SpeakerError("This speaker doesn't let Radio set its volume.")
