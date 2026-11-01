"""Outbound Display webhook URL validation (SSRF hardening)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

_BLOCKED_HOSTS = frozenset({"localhost", "metadata.google.internal"})
_LOOPBACK_LITERALS = frozenset({"127.0.0.1", "::1", "0:0:0:0:0:0:0:1"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _normalize_host(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved or ip.is_private:
        return True
    if ip.is_loopback or ip.is_link_local:
        return True
    if ip.version == 4 and ip in _CGNAT:
        return True
    if ip == ipaddress.ip_address("169.254.169.254"):
        return True
    return False


def resolve_webhook(raw: str) -> tuple[str, str]:
    """Return ``(url, ip)`` after the SSRF checks. Connect to ``ip``, not the name."""
    value = (raw or "").strip()
    if not value:
        raise ValueError("Add a Display webhook URL first.")
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError("Display webhook URL must use https.")
    if parsed.username or parsed.password:
        raise ValueError("Webhook URL must not include credentials.")
    if not parsed.hostname:
        raise ValueError("That URL is missing a host.")
    host = _normalize_host(parsed.hostname)
    if host in _BLOCKED_HOSTS or host in _LOOPBACK_LITERALS:
        raise ValueError("That host is not allowed.")
    try:
        if _blocked_ip(ipaddress.ip_address(host)):
            raise ValueError("That host is not allowed.")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
    port = parsed.port or 443
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("Could not resolve that host.") from exc
    pinned = ""
    for info in infos:
        address = info[4][0]
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError:
            continue
        if _blocked_ip(resolved):
            raise ValueError("That host resolves to a blocked address.")
        if not pinned:
            pinned = address
    if not pinned:
        raise ValueError("Could not resolve that host.")
    return value, pinned


def validate_webhook_url(raw: str) -> str:
    url, _ip = resolve_webhook(raw)
    return url


class PinnedTransport(httpx.BaseTransport):
    """Connect to the address ``resolve_webhook`` just checked, with the original name for TLS."""

    def __init__(self, ip: str, inner: httpx.BaseTransport | None = None):
        self._ip = ip
        self._inner = inner if inner is not None else httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        headers = request.headers.copy()
        headers["host"] = host
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=self._ip),
            headers=headers,
            content=request.content,
            extensions={**dict(request.extensions), "sni_hostname": host},
        )
        return self._inner.handle_request(pinned)

    def close(self) -> None:
        self._inner.close()
