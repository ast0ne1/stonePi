from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import dateparser
from bs4 import BeautifulSoup

from app.services.scrapers.base import BaseScraper, ScrapedEvent

logger = logging.getLogger("eventtrakr.scrapers.schema_org")


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = dateparser.parse(str(raw))
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _extract_location(loc_obj: Any) -> str:
    if not loc_obj:
        return "Unspecified"
    if isinstance(loc_obj, str):
        return loc_obj.strip()
    if isinstance(loc_obj, dict):
        name = loc_obj.get("name", "")
        addr = loc_obj.get("address", "")
        if isinstance(addr, dict):
            parts = [
                addr.get("streetAddress"),
                addr.get("addressLocality"),
                addr.get("postalCode"),
            ]
            addr_str = ", ".join([p for p in parts if p])
            return f"{name} ({addr_str})" if name and addr_str else (name or addr_str or "Unspecified")
        return f"{name} ({addr})" if name and addr else (name or str(addr) or "Unspecified")
    return "Unspecified"


def _extract_cost(offers: Any) -> str:
    if not offers:
        return "Free / Unspecified"
    if isinstance(offers, dict):
        currency = offers.get("priceCurrency", "")
        price = offers.get("price")
        if price is not None:
            try:
                p_num = float(price)
                if p_num == 0:
                    return "Free"
                return f"{currency} {p_num:.2f}".strip()
            except (ValueError, TypeError):
                return str(price).strip()
        # AggregateOffer shape (a price range across ticket tiers) has no
        # single "price", just low/high bounds.
        low = offers.get("lowPrice")
        high = offers.get("highPrice")
        if low is not None:
            try:
                low_num = float(low)
                if low_num == 0 and (high is None or float(high) == 0):
                    return "Free"
                if high is not None and float(high) != low_num:
                    return f"From {currency} {low_num:.2f}".strip()
                return f"{currency} {low_num:.2f}".strip()
            except (ValueError, TypeError):
                return str(low).strip()
    elif isinstance(offers, list) and offers:
        return _extract_cost(offers[0])
    return "Free / Unspecified"


class SchemaOrgExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        soup = BeautifulSoup(content, "html.parser")

        # 1. JSON-LD scripts
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
            except Exception:
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Handle @graph wrappers and ItemList listing pages (each
                # itemListElement is a ListItem whose actual payload is nested
                # under "item").
                if "@graph" in item:
                    graphs = item.get("@graph", [item])
                elif str(item.get("@type", "")) == "ItemList":
                    graphs = [
                        li.get("item", li)
                        for li in item.get("itemListElement", [])
                        if isinstance(li, dict)
                    ]
                else:
                    graphs = [item]
                for node in graphs:
                    if not isinstance(node, dict):
                        continue
                    typ = str(node.get("@type", ""))
                    if "event" in typ.lower():
                        title = node.get("name") or node.get("headline")
                        start_time = _parse_dt(node.get("startDate"))
                        if not title or not start_time:
                            continue
                        end_time = _parse_dt(node.get("endDate"))
                        desc = node.get("description", "") or ""
                        loc = _extract_location(node.get("location"))
                        cost = _extract_cost(node.get("offers"))
                        ev_url = node.get("url") or base_url
                        ev_url = urljoin(base_url, ev_url)
                        img = node.get("image")
                        img_url = img if isinstance(img, str) else (img[0] if isinstance(img, list) and img else None)

                        events.append(
                            ScrapedEvent(
                                title=str(title).strip(),
                                description=str(desc).strip()[:1000],
                                start_time=start_time,
                                end_time=end_time,
                                location=loc[:250],
                                cost=cost[:90],
                                category=typ.replace("Event", "") or "General",
                                url=ev_url,
                                image_url=img_url,
                            )
                        )
        return events
