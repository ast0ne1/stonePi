from __future__ import annotations

import json
from pathlib import Path

from app.config import ROOT_DIR
from app.sources.base import NormalisedOffer, ProductDetail, ProductHit

_FIXTURE = ROOT_DIR / "fixtures" / "mock_products.json"


def _load() -> dict:
    if not _FIXTURE.exists():
        return {"products": []}
    try:
        return json.loads(_FIXTURE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"products": []}


class MockSource:
    id = "mock"
    name = "Mock source"

    def search(self, query: str, *, market: str = "DK") -> list[ProductHit]:
        q = (query or "").strip().lower()
        hits: list[ProductHit] = []
        for item in _load().get("products", []):
            hay = " ".join(
                str(item.get(k) or "") for k in ("name", "manufacturer", "variant", "product_id")
            ).lower()
            if q and q not in hay:
                continue
            hits.append(
                ProductHit(
                    product_id=str(item["product_id"]),
                    name=str(item["name"]),
                    source_id=self.id,
                    manufacturer=item.get("manufacturer"),
                    variant=item.get("variant"),
                    image_url=item.get("image_url"),
                    product_url=item.get("product_url"),
                    lowest_price=item.get("lowest_price"),
                    currency=item.get("currency") or "DKK",
                )
            )
        return hits

    def get_product(self, product_id: str, *, market: str = "DK") -> ProductDetail | None:
        for item in _load().get("products", []):
            if str(item.get("product_id")) != str(product_id):
                continue
            return ProductDetail(
                product_id=str(item["product_id"]),
                name=str(item["name"]),
                source_id=self.id,
                manufacturer=item.get("manufacturer"),
                variant=item.get("variant"),
                image_url=item.get("image_url"),
                product_url=item.get("product_url"),
                currency=item.get("currency") or "DKK",
            )
        return None

    def get_offers(self, product_id: str, *, market: str = "DK") -> list[NormalisedOffer]:
        for item in _load().get("products", []):
            if str(item.get("product_id")) != str(product_id):
                continue
            offers: list[NormalisedOffer] = []
            for offer in item.get("offers") or []:
                price = float(offer["product_price"])
                delivery = offer.get("delivery_cost")
                delivery_f = float(delivery) if delivery is not None else None
                total = price + (delivery_f or 0.0)
                offers.append(
                    NormalisedOffer(
                        product_id=str(product_id),
                        product_name=str(item["name"]),
                        source_id=self.id,
                        retailer=str(offer.get("retailer") or "Retailer"),
                        product_price=price,
                        currency=offer.get("currency") or item.get("currency") or "DKK",
                        delivery_cost=delivery_f,
                        total_price=total,
                        availability=offer.get("availability") or "in_stock",
                        condition=offer.get("condition") or "new",
                        product_url=offer.get("product_url") or item.get("product_url"),
                        image_url=item.get("image_url"),
                        manufacturer=item.get("manufacturer"),
                        variant=item.get("variant"),
                        offer_id=str(offer.get("offer_id") or ""),
                        merchant_id=offer.get("merchant_id"),
                        merchant_domain=offer.get("merchant_domain"),
                        merchant_rating=offer.get("merchant_rating"),
                        merchant_rating_count=offer.get("merchant_rating_count"),
                    )
                )
            return offers
        return []


def mock_trustpilot_scores() -> dict[str, dict]:
    """Fixture Trustpilot results keyed by domain (stands in for Bright Data when mocked)."""
    data = _load().get("trustpilot") or {}
    return data if isinstance(data, dict) else {}
