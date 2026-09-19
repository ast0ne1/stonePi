"""Server-side URL fetch for the URL→zip admin tool (avoids browser CORS)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from stonepi_auth import is_public_exposure

TIMEOUT = httpx.Timeout(45.0, connect=10.0)
MAX_BYTES = 15 * 1024 * 1024
HEADERS = {
    "User-Agent": "FileServe-URLPack/0.0.0.5 (+local household tool)",
    "Accept": "*/*",
}

_METADATA_HOSTS = frozenset({"localhost", "metadata.google.internal"})
_LOOPBACK_LITERALS = frozenset({"127.0.0.1", "::1", "0:0:0:0:0:0:0:1"})


def _normalize_host(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h


def _blocked_hostname(host: str) -> bool:
    h = _normalize_host(host)
    if h in _METADATA_HOSTS or h == "127.0.0.1":
        return True
    if is_public_exposure():
        if h in _LOOPBACK_LITERALS or h == "localhost":
            return True
    return False


def _blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return True
    if ip == ipaddress.ip_address("169.254.169.254"):
        return True
    if is_public_exposure():
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return True
    return False


def validate_fetch_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("Enter a URL.")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http and https URLs are allowed.")
    if not parsed.hostname:
        raise ValueError("That URL is missing a host.")
    host = parsed.hostname
    if _blocked_hostname(host):
        raise ValueError("That host is not allowed.")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve that host.") from exc
    for info in infos:
        address = info[4][0]
        if _blocked_ip(address):
            raise ValueError("That host resolves to a blocked address.")
    return value


def fetch_url(raw: str) -> tuple[bytes, str]:
    url = validate_fetch_url(raw)
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=HEADERS) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("That file is larger than 15 MB.")
                chunks.append(chunk)
            body = b"".join(chunks)
            content_type = response.headers.get("content-type") or "application/octet-stream"
            return body, content_type.split(";")[0].strip()
