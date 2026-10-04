from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.services.scrapers.base import BaseScraper, ScrapedEvent

# Jungle (jungle.am, Copenhagen food & drink) is a SvelteKit app: the events
# aren't in the HTML as cards but in the page data, which SvelteKit also serves
# at <page>/__data.json -- newline-delimited JSON in "devalue" form (a flat
# value table where objects and arrays hold indices into it), with the event
# shelves streamed as later "chunk" lines. Reading that data is far steadier
# than scraping the rendered page, and needs no headless browser.

BASE = "https://jungle.am"


def is_jungle_url(url: str) -> bool:
    return urlsplit(url).netloc.lower().removeprefix("www.") == "jungle.am"


def data_url(url: str) -> str:
    """The SvelteKit data endpoint for a Jungle page (``/events`` → ``/events/__data.json``)."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or ""
    if not path.endswith("/__data.json"):
        path = f"{path}/__data.json"
    return urlunsplit((parts.scheme or "https", parts.netloc, path, parts.query, ""))


def _unflatten(values: list) -> Any:
    """Decode one devalue table (index 0 is the root)."""
    done: dict[int, Any] = {}

    def get(i: Any) -> Any:
        if not isinstance(i, int) or i < 0:
            return None  # undefined / holes / NaN / ±Infinity
        if i in done:
            return done[i]
        v = values[i]
        if isinstance(v, dict):
            out: dict = {}
            done[i] = out
            for k, ref in v.items():
                out[k] = get(ref)
            return out
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                # Typed values: ["Date", iso], ["Promise", id], ["Set", ...] ...
                tag = v[0]
                done[i] = v[1] if tag == "Date" and len(v) > 1 else None
                return done[i]
            out_list: list = []
            done[i] = out_list
            out_list.extend(get(ref) for ref in v)
            return out_list
        done[i] = v
        return v

    return get(0)


def decode(text: str) -> list[Any]:
    """Every decoded node and streamed chunk in a SvelteKit __data.json body."""
    roots: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "data":
            for node in msg.get("nodes") or []:
                if isinstance(node, dict) and isinstance(node.get("data"), list):
                    roots.append(_unflatten(node["data"]))
        elif msg.get("type") == "chunk" and isinstance(msg.get("data"), list):
            roots.append(_unflatten(msg["data"]))
    return roots


def _walk(value: Any, seen: set[int]):
    if isinstance(value, dict):
        if id(value) in seen:
            return
        seen.add(id(value))
        if value.get("slug") and value.get("title") and (value.get("displayDate") or value.get("event_date")):
            yield value
        for v in value.values():
            yield from _walk(v, seen)
    elif isinstance(value, list):
        if id(value) in seen:
            return
        seen.add(id(value))
        for v in value:
            yield from _walk(v, seen)


def _dt(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _text(html: Any) -> str:
    if not isinstance(html, str):
        return ""
    text = re.sub(r"<br\s*/?>|</p>", "\n", html, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _cost(item: dict) -> str:
    price = item.get("min_ticket_type_price")
    if price is None:
        price = item.get("ticket_price")
    if price is None:
        return "Unspecified"
    try:
        amount = float(price)
    except (TypeError, ValueError):
        return "Unspecified"
    if amount == 0:
        return "Free"
    meta = item.get("metadata") or {}
    prefix = "From " if meta.get("price_is_from") else ""
    return f"{prefix}{amount:g} {item.get('currency') or 'DKK'}"


class JungleExtractor(BaseScraper):
    """Events from a Jungle page's ``__data.json`` (see module notes)."""

    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        keys: set[str] = set()
        seen: set[int] = set()
        for root in decode(content):
            for item in _walk(root, seen):
                if item.get("status") not in (None, "published"):
                    continue
                start = _dt(item.get("displayDate") or item.get("next_occurrence_date") or item.get("event_date"))
                if not start:
                    continue
                key = str(item.get("entryKey") or f"{item.get('id')}-{start.isoformat()}")
                if key in keys:
                    continue  # the same event sits on several shelves
                keys.add(key)
                # end_date belongs to the first date; shift it onto this occurrence.
                end = None
                first, last = _dt(item.get("event_date")), _dt(item.get("end_date"))
                if first and last and last > first:
                    end = start + (last - first)
                where = [str(x).strip() for x in (item.get("host"), item.get("custom_address") or item.get("area")) if x and str(x).strip()]
                meta = item.get("metadata") or {}
                title = str(item["title"]).strip()
                label = str(item.get("occurrence_label") or "").strip()
                description = _text(item.get("description"))
                if label:
                    description = f"{label}\n\n{description}".strip()
                events.append(
                    ScrapedEvent(
                        title=title,
                        description=description,
                        start_time=start,
                        end_time=end,
                        location=" · ".join(where) or "Copenhagen",
                        cost=_cost(item),
                        url=f"{BASE}/events/{item['slug']}",
                        image_url=meta.get("poster_url") or item.get("poster_url"),
                    )
                )
        events.sort(key=lambda e: e.start_time)
        return events
