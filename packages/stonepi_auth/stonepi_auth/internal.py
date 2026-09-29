"""Signed service-to-service calls between StonePi platform apps.

Internal endpoints (e.g. Auth ``/api/internal/people``) have no user session.
Callers sign ``METHOD``, ``path`` and a unix timestamp with the platform
session secret; receivers reject unsigned or stale requests. Any holder of the
session secret can already mint an admin session, so this adds no new trust.
nginx additionally blocks ``/<app>/api/internal/`` at the edge.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping

TS_HEADER = "X-StonePi-Internal-Ts"
SIG_HEADER = "X-StonePi-Internal-Sig"
MAX_SKEW_SECONDS = 60
_DOMAIN = b"stonepi-internal-v1"


def _digest(secret: str, method: str, path: str, ts: str) -> str:
    message = b"\n".join([_DOMAIN, method.upper().encode(), path.encode(), ts.encode()])
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_internal(secret: str, method: str, path: str, *, now: float | None = None) -> dict[str, str]:
    """Headers for one internal request. ``path`` excludes any query string."""
    if not secret:
        raise ValueError("session secret required for internal calls")
    ts = str(int(now if now is not None else time.time()))
    return {TS_HEADER: ts, SIG_HEADER: _digest(secret, method, path, ts)}


def verify_internal(
    secret: str,
    method: str,
    path: str,
    headers: Mapping[str, str],
    *,
    now: float | None = None,
    max_skew: int = MAX_SKEW_SECONDS,
) -> bool:
    if not secret:
        return False
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    ts = lowered.get(TS_HEADER.lower(), "").strip()
    sig = lowered.get(SIG_HEADER.lower(), "").strip()
    if not ts.isdigit() or not sig:
        return False
    current = now if now is not None else time.time()
    if abs(current - int(ts)) > max_skew:
        return False
    return hmac.compare_digest(sig, _digest(secret, method, path, ts))


def get_internal_json(base_url: str, secret: str, path: str, *, timeout: float = 2.0) -> dict | None:
    """Signed GET to another platform service (stdlib only). None on any failure."""
    import json
    import urllib.request

    if not secret:
        return None
    url = f"{str(base_url or '').rstrip('/')}{path}"
    request = urllib.request.Request(url, headers=sign_internal(secret, "GET", path), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (loopback URL)
            if response.status != 200:
                return None
            data = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None
