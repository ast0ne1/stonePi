from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import httpx

from app.config import env
from app.sources.base import NormalisedOffer, ProductDetail, ProductHit

logger = logging.getLogger("pricewatch.pricerunner")

BASE = "https://www.pricerunner.dk"
CDN = "https://owp.klarna.com"
# Klarna/PriceRunner edge routes (locale segment + service name).
SEARCH_SUGGEST = f"{BASE}/dk/api/instant-search-edge-rest/public/search/suggest/DK"
SEARCH_PAGE = f"{BASE}/dk/api/search-page-edge-rest/public/search/page/DK"
OFFERS_URL = f"{BASE}/dk/api/product-detail-edge-rest/public/product-detail/v0/offers/DK"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Referer": f"{BASE}/",
    "Origin": BASE,
}


class PriceRunnerDkSource:
    id = "pricerunner_dk"
    name = "PriceRunner Denmark"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_request = 0.0

    def _throttle(self) -> None:
        with self._lock:
            gap = env.min_request_interval
            elapsed = time.monotonic() - self._last_request
            if elapsed < gap:
                time.sleep(gap - elapsed)
            self._last_request = time.monotonic()

    def _get(self, url: str, *, params: dict[str, Any] | None = None) -> Any:
        self._throttle()
        timeout = httpx.Timeout(env.request_timeout, connect=min(10.0, env.request_timeout))
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=timeout, headers=HEADERS, follow_redirects=True) as client:
                    resp = client.get(url, params=params)
                    if resp.status_code == 429:
                        time.sleep(2 ** attempt)
                        continue
                    if resp.status_code == 404:
                        raise RuntimeError(f"404 for {url}")
                    resp.raise_for_status()
                    if not resp.content:
                        return {}
                    return resp.json()
            except Exception as exc:
                last_err = exc
                logger.warning("PriceRunner request failed (%s): %s", attempt + 1, exc)
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"PriceRunner unavailable: {last_err}")

    def search(self, query: str, *, market: str = "DK") -> list[ProductHit]:
        q = (query or "").strip()
        if not q:
            return []

        hits: list[ProductHit] = []
        seen: set[str] = set()

        # Instant suggest works reliably from server-side clients.
        try:
            data = self._get(SEARCH_SUGGEST, params={"q": q[:100]})
            for item in data.get("products") or []:
                hit = self._map_hit(item)
                if hit and hit.product_id not in seen:
                    seen.add(hit.product_id)
                    hits.append(hit)
        except Exception:
            logger.exception("PriceRunner suggest search failed")

        # Full search page when it returns cards (browser sessions often do).
        try:
            data = self._get(
                SEARCH_PAGE,
                params={"q": q, "size": 24, "device": "desktop"},
            )
            for item in data.get("productCards") or []:
                hit = self._map_hit(item)
                if hit and hit.product_id not in seen:
                    seen.add(hit.product_id)
                    hits.append(hit)
        except Exception:
            logger.warning("PriceRunner page search failed", exc_info=True)

        return hits

    def get_product(self, product_id: str, *, market: str = "DK") -> ProductDetail | None:
        pid = str(product_id).strip()
        if not pid:
            return None
        # Offers payload carries product metadata; searching by numeric id alone is unreliable.
        try:
            data = self._get(f"{OFFERS_URL}/{pid}")
            name = (
                data.get("productName")
                or (data.get("product") or {}).get("name")
                or (data.get("name") if isinstance(data.get("name"), str) else None)
            )
            image = _image_url(data.get("product") or data)
            path = (data.get("product") or {}).get("path") or data.get("path")
            product_url = None
            if isinstance(path, str) and path:
                product_url = path if path.startswith("http") else f"{BASE}{path}"
            if name:
                return ProductDetail(
                    product_id=pid,
                    name=str(name),
                    source_id=self.id,
                    image_url=image,
                    product_url=product_url or f"{BASE}/pl/0-{pid}",
                    currency="DKK",
                    raw=data if isinstance(data, dict) else {},
                )
        except Exception:
            logger.warning("PriceRunner get_product via offers failed", exc_info=True)
        return ProductDetail(
            product_id=pid,
            name=f"Product {pid}",
            source_id=self.id,
            product_url=f"{BASE}/pl/0-{pid}",
            currency="DKK",
        )

    def get_offers(self, product_id: str, *, market: str = "DK") -> list[NormalisedOffer]:
        pid = str(product_id).strip()
        data = self._get(f"{OFFERS_URL}/{pid}")
        offers_raw = data.get("offers") or []
        merchants = data.get("merchants") or {}
        product_name = (
            data.get("productName")
            or (data.get("product") or {}).get("name")
            or f"Product {pid}"
        )
        image = _image_url(data.get("product") or data)
        out: list[NormalisedOffer] = []
        for raw in offers_raw:
            mapped = self._map_offer(pid, str(product_name), raw, merchants, image)
            if mapped:
                out.append(mapped)
        return out

    def _map_hit(self, item: dict[str, Any]) -> ProductHit | None:
        pid = item.get("id") or item.get("productId") or item.get("product_id")
        if pid is None and item.get("url"):
            pid = _id_from_url(str(item["url"]))
        if pid is None and item.get("path"):
            pid = _id_from_url(str(item["path"]))
        if pid is None:
            return None
        name = item.get("name") or item.get("productName") or item.get("title")
        if not name:
            return None
        brand = item.get("brand")
        manufacturer = None
        if isinstance(brand, dict):
            manufacturer = brand.get("name")
        elif isinstance(brand, str):
            manufacturer = brand
        path = item.get("path") or item.get("url")
        product_url = None
        if isinstance(path, str) and path:
            product_url = path if path.startswith("http") else f"{BASE}{path}"
        return ProductHit(
            product_id=str(pid),
            name=str(name),
            source_id=self.id,
            manufacturer=manufacturer,
            variant=item.get("description") if isinstance(item.get("description"), str) else None,
            image_url=_image_url(item),
            product_url=product_url or f"{BASE}/pl/0-{pid}",
            lowest_price=_price_amount(item.get("lowestPrice") or item.get("price")),
            currency="DKK",
            raw=item,
        )

    def _map_offer(
        self,
        product_id: str,
        product_name: str,
        raw: dict[str, Any],
        merchants: dict[str, Any],
        image: str | None,
    ) -> NormalisedOffer | None:
        price = _price_amount(raw.get("price") or raw.get("campaignPrice"))
        if price is None:
            return None
        delivery = _price_amount(raw.get("shippingCost"))
        merchant_id = str(raw.get("merchantId") or "")
        merchant = merchants.get(merchant_id) if isinstance(merchants, dict) else None
        retailer = "Retailer"
        if isinstance(merchant, dict):
            retailer = str(merchant.get("name") or retailer)
        elif raw.get("merchantName"):
            retailer = str(raw["merchantName"])
        url = raw.get("url") or raw.get("productRawUrl")
        if isinstance(url, str) and url.startswith("/"):
            url = f"{BASE}{url}"
        return NormalisedOffer(
            product_id=product_id,
            product_name=product_name,
            source_id=self.id,
            retailer=retailer,
            product_price=price,
            currency="DKK",
            delivery_cost=delivery,
            total_price=price + (delivery or 0.0),
            availability=_availability(raw),
            condition=_condition(raw),
            product_url=url if isinstance(url, str) else None,
            image_url=image,
            offer_id=str(raw.get("id") or ""),
            raw=raw,
        )


def _price_amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "inclVat", "excludingShipping"):
            if key in value:
                return _price_amount(value[key])
        return None
    if isinstance(value, str):
        cleaned = value.replace("kr", "").replace("DKK", "").strip()
        # Danish thousands: 3.079,00 or plain 3079.00
        if re.search(r",\d{2}$", cleaned):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _image_url(obj: Any) -> str | None:
    if not isinstance(obj, dict):
        return None
    for key in ("imageUrl", "image", "thumbnailUrl", "primaryImage"):
        val = obj.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
        if isinstance(val, dict):
            url = val.get("url")
            if isinstance(url, str) and url.startswith("http"):
                return url
            path = val.get("path")
            if isinstance(path, str) and path.startswith("/"):
                return f"{CDN}{path}"
    images = obj.get("images")
    if isinstance(images, list) and images:
        return _image_url({"image": images[0]})
    return None


def _id_from_url(url: str) -> str | None:
    # /pl/94-3410100348/... → 3410100348
    match = re.search(r"/pl/\d+-(\d+)/", url)
    if match:
        return match.group(1)
    match = re.search(r"/pl/(\d+)/", url)
    if match:
        return match.group(1)
    parts = url.strip("/").split("/")
    for part in parts:
        if "-" in part:
            tail = part.split("-")[-1]
            if tail.isdigit() and len(tail) >= 6:
                return tail
        if part.isdigit() and len(part) >= 6:
            return part
    return None


def _availability(raw: dict[str, Any]) -> str:
    stock = str(raw.get("stockStatus") or "").upper()
    avail = str(raw.get("availability") or "").upper()
    if stock in {"IN_STOCK", "LOW_STOCK"} or avail in {"AVAILABLE", "IN_STOCK"}:
        return "in_stock"
    if stock in {"OUT_OF_STOCK", "NOT_IN_STOCK"} or avail in {"UNAVAILABLE", "OUT_OF_STOCK"}:
        return "out_of_stock"
    if raw.get("inStock") is True:
        return "in_stock"
    if raw.get("inStock") is False:
        return "out_of_stock"
    return "unknown"


def _condition(raw: dict[str, Any]) -> str:
    cond = str(raw.get("condition") or raw.get("productCondition") or "new").lower()
    if "used" in cond or "brugt" in cond or "refurb" in cond:
        return "used"
    return "new"
