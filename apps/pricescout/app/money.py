from __future__ import annotations

from app.config import CURRENCIES, DKK_TO

_CURRENCY_BY_ID = {c["id"]: c for c in CURRENCIES}


def currency_meta(code: str | None) -> dict[str, str]:
    code = (code or "DKK").upper()
    return _CURRENCY_BY_ID.get(code) or _CURRENCY_BY_ID["DKK"]


def convert_from_dkk(amount: float, currency: str | None) -> float:
    rate = DKK_TO.get((currency or "DKK").upper(), 1.0)
    return float(amount) * rate


def format_price(amount_dkk: float, currency: str | None = "DKK") -> str:
    meta = currency_meta(currency)
    value = convert_from_dkk(amount_dkk, meta["id"])
    if meta["id"] == "DKK":
        return f"{value:.2f} {meta['symbol']}"
    if meta["id"] in {"EUR", "GBP", "USD"}:
        return f"{meta['symbol']}{value:.2f}"
    return f"{value:.2f} {meta['symbol']} {meta['id']}"


def offer_public_urls(external_id: str | None, raw: dict | None = None) -> dict[str, str | None]:
    """Build public eTilbudsavis links for an offer / its leaflet."""
    raw = raw or {}
    oid = (external_id or raw.get("id") or "").strip()
    catalog_id = str(raw.get("catalog_id") or "").strip()
    page = raw.get("catalog_page")
    offer_url = f"https://etilbudsavis.dk/?offer={oid}" if oid else None
    catalog_url = None
    if catalog_id:
        catalog_url = f"https://etilbudsavis.dk/?publication={catalog_id}"
        if page not in (None, "", 0):
            catalog_url = f"{catalog_url}&page={page}"
    return {"offer_url": offer_url, "catalog_url": catalog_url}
