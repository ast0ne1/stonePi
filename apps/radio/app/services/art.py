"""Station logos, fetched and kept by Radio so pages never load third-party images.

Pages link ``/art/<sig>?u=<logo url>``; the signature (a per-install key in prefs) means
only logos Radio itself put on a page are fetched, so this isn't an open proxy. Safety as
Pinboard's link previews: http(s) on ports 80/443, every hop resolving to public addresses
only, raster images only (no SVG), a size cap and short timeouts. Kept in
``<data>/art/`` for a week; a failure is remembered for a day, and the page then shows the
station's letter instead.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app import __github__, db

TIMEOUT = httpx.Timeout(6.0, connect=4.0)
MAX_REDIRECTS = 4
IMAGE_MAX = 1024 * 1024
KEEP_S = 7 * 24 * 3600
FAILED_S = 24 * 3600
USER_AGENT = f"Mozilla/5.0 (compatible; StonePi-Radio/1.0; +{__github__})"
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
}
MEDIA_TYPES = {ext: mime for mime, ext in IMAGE_TYPES.items()}
# At most a few fetches at once: a search page can list 40 stations.
_slots = threading.BoundedSemaphore(4)
_key_lock = threading.Lock()


class Blocked(Exception):
    """Not an address Radio fetches logos from."""


def _key() -> bytes:
    with _key_lock:
        key = db.get_pref("art_key")
        if not key:
            key = secrets.token_hex(32)
            db.set_pref("art_key", key)
    return key.encode()


def _sig(src: str) -> str:
    return hmac.new(_key(), src.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def art_url(src: str | None) -> str:
    """The page's address for a logo, or "" when there's none worth trying."""
    src = (src or "").strip()
    if not src.lower().startswith(("http://", "https://")) or len(src) > 1000:
        return ""
    return f"/art/{_sig(src)}?u={quote(src, safe='')}"


def valid(sig: str, src: str) -> bool:
    return bool(src) and hmac.compare_digest(sig, _sig(src))


def _folder() -> Path:
    return Path(db.DATA_DIR) / "art"  # read at call time (tests point db at a temp folder)


def public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return False
    return True


def _check(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise Blocked(url)
    if parts.port not in (None, 80, 443):
        raise Blocked(url)
    if not public_host(parts.hostname):
        raise Blocked(url)


def _download(url: str) -> tuple[str, bytes]:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _check(url)
            with client.stream("GET", url, headers={"Accept": "image/*"}) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if ctype not in IMAGE_TYPES:
                    raise ValueError(f"not a raster image: {ctype}")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > IMAGE_MAX:
                        raise ValueError("too large")
                return ctype, bytes(body)
    raise ValueError("too many redirects")


def get(src: str) -> tuple[Path, str] | None:
    """(file, media type) for a logo, fetching it when not kept yet; None when it can't be had."""
    name = hashlib.sha256(src.encode("utf-8")).hexdigest()[:40]
    folder = _folder()
    now = time.time()
    for path in folder.glob(f"{name}.*"):
        age = now - path.stat().st_mtime
        if path.suffix == ".failed":
            if age < FAILED_S:
                return None
            continue
        if age < KEEP_S:
            return path, MEDIA_TYPES.get(path.suffix[1:], "application/octet-stream")
    with _slots:
        try:
            ctype, body = _download(src)
        except Exception:  # noqa: BLE001 - any failure shows the station's letter
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob(f"{name}.*"):
                old.unlink(missing_ok=True)
            (folder / f"{name}.failed").touch()
            return None
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{name}.*"):
        old.unlink(missing_ok=True)
    path = folder / f"{name}.{IMAGE_TYPES[ctype]}"
    path.write_bytes(body)
    return path, ctype
