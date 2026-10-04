from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit


def normalise_domain(value: str | None) -> str | None:
    """`https://www.Elgiganten.dk/x` → `elgiganten.dk`."""
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if "//" not in raw:
        raw = f"//{raw}"
    try:
        host = (urlsplit(raw).hostname or "").strip(".")
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


@dataclass
class ProductHit:
    product_id: str
    name: str
    source_id: str
    manufacturer: str | None = None
    variant: str | None = None
    image_url: str | None = None
    product_url: str | None = None
    lowest_price: float | None = None
    currency: str = "DKK"
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductDetail:
    product_id: str
    name: str
    source_id: str
    manufacturer: str | None = None
    variant: str | None = None
    image_url: str | None = None
    product_url: str | None = None
    currency: str = "DKK"
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalisedOffer:
    product_id: str
    product_name: str
    source_id: str
    retailer: str
    product_price: float
    currency: str = "DKK"
    delivery_cost: float | None = None
    total_price: float | None = None
    availability: str | None = None  # in_stock | out_of_stock | unknown
    condition: str | None = None  # new | used | unknown
    product_url: str | None = None
    image_url: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    variant: str | None = None
    offer_id: str | None = None
    timestamp: str | None = None
    # Shop identity from the source (PriceRunner merchant id + web address) and
    # the source's own shop rating; None when the source doesn't give one.
    merchant_id: str | None = None
    merchant_domain: str | None = None
    merchant_rating: float | None = None
    merchant_rating_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data.get("total_price") is None:
            delivery = data.get("delivery_cost")
            price = data.get("product_price")
            if price is not None:
                data["total_price"] = float(price) + (float(delivery) if delivery is not None else 0.0)
        return data

    @property
    def effective_price(self) -> float:
        if self.total_price is not None:
            return float(self.total_price)
        delivery = float(self.delivery_cost or 0.0)
        return float(self.product_price) + delivery


class PriceSource(Protocol):
    id: str
    name: str

    def search(self, query: str, *, market: str = "DK") -> list[ProductHit]:
        ...

    def get_product(self, product_id: str, *, market: str = "DK") -> ProductDetail | None:
        ...

    def get_offers(self, product_id: str, *, market: str = "DK") -> list[NormalisedOffer]:
        ...
