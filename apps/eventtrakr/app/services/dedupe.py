from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

STRIP_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
        "source",
    }
)


def canonicalize_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/")
    if not path:
        path = "/"
    query_tuples = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=False):
        if k.lower() not in STRIP_PARAMS:
            query_tuples.append((k, v))
    query_tuples.sort()
    query = urlencode(query_tuples)
    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_string(text: str | None) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def compute_event_fingerprint(
    user_id: int,
    title: str,
    start_time: datetime,
    location: str | None = None,
) -> str:
    norm_title = normalize_string(title)
    # Group by calendar date (YYYY-MM-DD)
    date_str = start_time.strftime("%Y-%m-%d")
    norm_loc = normalize_string(location or "")
    payload = f"{user_id}|{norm_title}|{date_str}|{norm_loc}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
