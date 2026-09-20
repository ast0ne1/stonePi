from __future__ import annotations

from dataclasses import dataclass

from app.collectors.base import OfferRow
from app.collectors import salling_foodwaste, tjek
from app.config import STORES, env


@dataclass
class StoreCollector:
    id: str
    name: str
    dealer_id: str

    def fetch_offers(self) -> list[OfferRow]:
        client = tjek.TjekClient()
        return client.fetch_dealer_offers(source_id=self.id, dealer_id=self.dealer_id)


@dataclass
class FoodWasteCollector:
    id: str
    name: str
    brand: str
    zip_code: str

    def fetch_offers(self) -> list[OfferRow]:
        return salling_foodwaste.fetch_food_waste(
            source_id=self.id,
            brand=self.brand,
            zip_code=self.zip_code,
        )


def tjek_collectors() -> list[StoreCollector]:
    return [StoreCollector(id=s["id"], name=s["name"], dealer_id=s["dealer_id"]) for s in STORES]


def foodwaste_collectors(zip_code: str) -> list[FoodWasteCollector]:
    if not (env.salling_api_token or "").strip() or not (zip_code or "").strip():
        return []
    return [
        FoodWasteCollector(id="netto_fw", name="Netto madspild", brand="netto", zip_code=zip_code),
        FoodWasteCollector(id="foetex_fw", name="føtex madspild", brand="foetex", zip_code=zip_code),
    ]
