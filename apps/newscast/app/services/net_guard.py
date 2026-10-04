"""Outbound fetch guard: feeds, article pages and favicons must not reach the Pi's own services.

A household user can add any feed URL, and remote pages can redirect anywhere. Without a
check, ``http://127.0.0.1:8014/`` (Kiwix, which skips Library sign-in) or other loopback
app ports would be fetched and their bodies shown back to the user.

The check runs as an httpx ``request`` event hook, which httpx calls for every hop of a
redirect chain, so a public URL that 302s to loopback is refused too. Loopback, link-local,
unspecified, multicast and reserved addresses are always refused. Private LAN ranges
(RFC 1918, ULA, CGNAT/Tailscale) are refused unless ``NEWSCAST_ALLOW_PRIVATE_FEEDS=1``.

Mirrors FileServe's ``fetch_proxy`` address checks. DNS is resolved here and again by
httpx, so a rebinding resolver could still race it; that is accepted for a LAN appliance.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

import httpx

_BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "metadata.google.internal"})


class BlockedAddress(httpx.RequestError):
    """Raised (as an httpx error, so existing fetch handlers treat it as a failure)."""


def allow_private() -> bool:
    return (os.environ.get("NEWSCAST_ALLOW_PRIVATE_FEEDS") or "").strip().lower() in {"1", "true", "yes", "on"}


def blocked_ip(address: str, *, private_ok: bool | None = None) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return True
    if ip.is_global:
        return False
    # Private, ULA, CGNAT (Tailscale 100.64/10), documentation ranges, …
    return not (allow_private() if private_ok is None else private_ok)


def check_url(url: str) -> None:
    """Raise BlockedAddress when ``url`` points at a disallowed host."""
    parts = urlsplit(str(url))
    if parts.scheme not in {"http", "https"}:
        raise BlockedAddress(f"Only http and https URLs can be fetched: {url}")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise BlockedAddress(f"URL is missing a host: {url}")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise BlockedAddress(f"Refusing to fetch local address: {host}")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError):
            # Unresolvable: let httpx fail the request normally.
            return
        addresses = [info[4][0] for info in infos]
    for address in addresses:
        if blocked_ip(address):
            raise BlockedAddress(f"Refusing to fetch {host}: it resolves to a private or local address")


def _guard_request(request: httpx.Request) -> None:
    try:
        check_url(str(request.url))
    except BlockedAddress as exc:
        raise BlockedAddress(str(exc), request=request) from None


# Pass as ``httpx.Client(event_hooks=EVENT_HOOKS, ...)``; runs on every request, redirects included.
EVENT_HOOKS = {"request": [_guard_request], "response": []}
