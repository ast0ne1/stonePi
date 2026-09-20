from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class OfferRow:
    external_id: str
    title: str
    price_dkk: float
    source_id: str
    unit_text: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    image_url: str | None = None
    category: str = "Other"
    is_hot: bool = False
    offer_url: str | None = None
    catalog_url: str | None = None
    currency: str = "DKK"
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "title": self.title,
            "price_dkk": self.price_dkk,
            "source_id": self.source_id,
            "unit_text": self.unit_text,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "image_url": self.image_url,
            "category": self.category,
            "is_hot": self.is_hot,
            "offer_url": self.offer_url,
            "catalog_url": self.catalog_url,
            "currency": self.currency,
            "raw": self.raw,
        }


class Collector(Protocol):
    id: str
    name: str

    def fetch_offers(self) -> list[OfferRow]:
        ...
