from __future__ import annotations

import logging
from typing import Any

import httpx

from app.collectors.base import OfferRow
from app.config import env

logger = logging.getLogger("pricescout.salling")

API_BASE = "https://api.sallinggroup.com/v1/food-waste"


def fetch_food_waste(*, source_id: str, brand: str, zip_code: str) -> list[OfferRow]:
    token = (env.salling_api_token or "").strip()
    if not token:
        return []
    zip_code = (zip_code or "").strip()
    if not zip_code:
        return []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "StonePi-PriceScout/0.0.1",
    }
    params = {"zip": zip_code}
    with httpx.Client(timeout=30.0, headers=headers) as client:
        resp = client.get(API_BASE, params=params)
        resp.raise_for_status()
        stores = resp.json()
    if not isinstance(stores, list):
        return []
    rows: list[OfferRow] = []
    brand_l = brand.lower()
    for store in stores:
        store_brand = str(store.get("brand") or "").lower()
        if store_brand and store_brand != brand_l:
            continue
        if not store_brand:
            name_l = str(store.get("name") or "").lower()
            if brand_l not in name_l:
                continue
        for clear in store.get("clearances") or store.get("products") or []:
            product = clear.get("product") or clear
            title = (product.get("description") or product.get("name") or "").strip()
            offer = clear.get("offer") or clear
            price = offer.get("newPrice") or offer.get("price") or product.get("price")
            if not title or price is None:
                continue
            try:
                price_f = float(price)
            except (TypeError, ValueError):
                continue
            ext = str(clear.get("id") or product.get("ean") or f"{title}-{price_f}")
            rows.append(
                OfferRow(
                    external_id=f"fw-{ext}",
                    title=title,
                    price_dkk=price_f,
                    source_id=source_id,
                    unit_text=None,
                    valid_from=None,
                    valid_to=offer.get("endTime") or offer.get("validUntil"),
                    image_url=(product.get("image") or None),
                    category="Other",
                    is_hot=True,
                    raw=clear if isinstance(clear, dict) else {},
                )
            )
    return rows
