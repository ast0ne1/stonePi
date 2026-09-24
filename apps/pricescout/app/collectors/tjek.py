from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.collectors.base import OfferRow
from app.config import CACHE_DIR, env

logger = logging.getLogger("pricescout.tjek")

API_BASE = "https://api.etilbudsavis.dk/v2"
_UA = "StonePi-PriceScout/0.0.1 (household LAN; +https://github.com/ast0ne1/stonePi)"

_CATEGORY_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Dairy",
        (
            r"\bmælk\b",
            r"\byoghurt\b",
            r"\byoggi\b",
            r"\bsmør\b",
            r"\bsmørbar\b",
            r"\bæg\b",
            r"\bfløde\b",
            r"\bfraiche\b",
            r"\bskyr\b",
            r"\bost\b",
            r"(?:fløde|smøre|hytte|creme|revet)\s*ost\b",
            r"\bphiladelphia\b",
            r"\bbuko\b",
            r"\bmilbona\b.*(?:ost|mælk|yoghurt|fløde)",
        ),
    ),
    (
        "Meat & fish",
        (
            r"\bkylling\b",
            r"\bokse\b",
            r"\bsvin\b",
            r"\blaks\b",
            r"\bfisk\b",
            r"\bkød\b",
            r"\bpølse\b",
            r"\bbacon\b",
            r"\bpålæg",
            r"\bkalkun\b",
            r"\breje\b",
            r"\btorsk\b",
            r"\bhakkebøf\b",
            r"\bskinke\b",
        ),
    ),
    (
        "Fruit & veg",
        (
            r"\bæble\b",
            r"\bbanan\b",
            r"\btomat\b",
            r"\bsalat\b(?!\s*pålæg)",
            r"\bfrugt\b",
            r"\bgrønt(?:sager)?\b",
            r"\bagurk\b",
            r"\bbroccoli\b",
            r"\bblomkål\b",
            r"\bspinat\b",
            r"\bærter\b",
            r"\bkartofl",
        ),
    ),
    (
        "Bread & bakery",
        (
            r"\bbrød\b",
            r"\bboller\b",
            r"\brugbrød\b",
            r"\bkage\b",
            r"\brundstyk",
            r"\bhåndværker",
            r"\bkiks\b",
        ),
    ),
    (
        "Drinks",
        (
            r"\bcola\b",
            r"\bøl\b",
            r"\bvin\b",
            r"\bjuice\b",
            r"\bvand\b",
            r"\bkaffe\b",
            r"\bthe\b",
            r"\bte\b",
            r"\bsodavand\b",
            r"\bsaft\b",
            r"\bdrik\b",
        ),
    ),
    (
        "Frozen",
        (
            r"\bfrost\b",
            r"\bfrossen\b",
            r"\bfrosne\b",
            r"\bisvafler\b",
            r"\bis\b(?!\w)",
        ),
    ),
    (
        "Household",
        (
            r"\bvask\b",
            r"\bopvask\b",
            r"\btoilet\b",
            r"\bpapir\b",
            r"\bshampoo\b",
            r"\btrimmer\b",
            r"\bjakke\b",
            r"\btøj\b",
        ),
    ),
    (
        "Pantry",
        (
            r"\bpasta\b",
            r"\bris\b",
            r"\bmel\b",
            r"\bsukker\b",
            r"\bolie\b",
            r"\bkonserves\b",
            r"\bcornflakes\b",
            r"\bhavre\b",
        ),
    ),
]


def guess_category(title: str) -> str:
    t = (title or "").lower()
    if not t:
        return "Other"
    for cat, patterns in _CATEGORY_PATTERNS:
        for pat in patterns:
            if re.search(pat, t, flags=re.IGNORECASE):
                return cat
    return "Other"


def _unit_text(offer: dict[str, Any]) -> str | None:
    qty = offer.get("quantity") or {}
    unit = qty.get("unit") or qty.get("si") or {}
    symbol = ""
    if isinstance(unit, dict):
        symbol = str(unit.get("symbol") or "").strip()
    elif isinstance(unit, str):
        symbol = unit.strip()

    size = qty.get("size")
    pieces = qty.get("pieces")
    amount = None
    if isinstance(size, dict):
        amount = size.get("from") or size.get("to")
    elif size is not None:
        amount = size
    elif isinstance(pieces, dict):
        amount = pieces.get("from") or pieces.get("to")
        if not symbol:
            symbol = "pcs"
    elif pieces is not None:
        amount = pieces
        if not symbol:
            symbol = "pcs"

    if amount is not None and symbol:
        return f"{amount} {symbol}"
    if amount is not None:
        return str(amount)
    if symbol:
        return symbol

    desc = (offer.get("description") or "").strip()
    if desc:
        m = re.search(r"(\d+[.,]?\d*\s*(?:g|kg|ml|cl|l|stk|pak))", desc, re.I)
        if m:
            return m.group(1)
    return None


class TjekClient:
    def __init__(self, *, api_key: str | None = None, max_offers: int | None = None) -> None:
        self.api_key = (api_key or env.tjek_api_key or "").strip()
        self.max_offers = max_offers or env.max_offers_per_source

    def fetch_dealer_offers(self, *, source_id: str, dealer_id: str) -> list[OfferRow]:
        rows: list[OfferRow] = []
        seen: set[str] = set()
        offset = 0
        page_size = 100
        while len(rows) < self.max_offers:
            batch = self._get_offers(dealer_id, offset=offset, limit=page_size)
            if not batch:
                break
            for item in batch:
                row = self._map_offer(source_id, item)
                if not row:
                    continue
                if row.external_id in seen:
                    continue
                seen.add(row.external_id)
                rows.append(row)
                if len(rows) >= self.max_offers:
                    break
            if len(batch) < page_size:
                break
            offset += page_size
            time.sleep(0.15)
        self._cache(source_id, rows)
        return rows

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": _UA, "Accept": "application/json"}
        if self.api_key:
            headers["X-Token"] = self.api_key
        return headers

    def _get_offers(self, dealer_id: str, *, offset: int, limit: int) -> list[dict[str, Any]]:
        params = {
            "dealer_ids": dealer_id,
            "r_locale": "da_DK",
            "offset": offset,
            "limit": limit,
        }
        url = f"{API_BASE}/offers"
        with httpx.Client(timeout=30.0, headers=self._headers()) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []

    def _map_offer(self, source_id: str, item: dict[str, Any]) -> OfferRow | None:
        pricing = item.get("pricing") or {}
        price = pricing.get("price")
        if price is None:
            return None
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            return None
        title = (item.get("heading") or item.get("name") or "").strip()
        if not title:
            return None
        images = item.get("images") or {}
        image = images.get("view") or images.get("zoom") or images.get("thumb")
        from app.money import offer_public_urls

        urls = offer_public_urls(str(item.get("id") or ""), item)
        currency = str(pricing.get("currency") or "DKK").upper() or "DKK"
        external_id = str(item.get("id") or "").strip()
        if not external_id:
            return None
        return OfferRow(
            external_id=external_id,
            title=title,
            price_dkk=price_f,
            source_id=source_id,
            unit_text=_unit_text(item),
            valid_from=item.get("run_from"),
            valid_to=item.get("run_till"),
            image_url=image,
            category=guess_category(title),
            is_hot=bool(pricing.get("pre_price")),
            offer_url=urls.get("offer_url"),
            catalog_url=urls.get("catalog_url"),
            currency=currency,
            raw=item,
        )

    def _cache(self, source_id: str, rows: list[OfferRow]) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path = Path(CACHE_DIR) / f"{source_id}.json"
            path.write_text(
                json.dumps([r.as_dict() for r in rows], ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("cache write failed: %s", exc)
