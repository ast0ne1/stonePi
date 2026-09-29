from __future__ import annotations

from app.config import CURRENCIES, DKK_TO, STORES

_CURRENCY_BY_ID = {c["id"]: c for c in CURRENCIES}
_STORE_SLUG_BY_ID = {s["id"]: s.get("slug") or s["id"] for s in STORES}
# Food-waste variants share the parent dealer page.
_STORE_SLUG_BY_ID["netto_fw"] = _STORE_SLUG_BY_ID.get("netto", "netto")
_STORE_SLUG_BY_ID["foetex_fw"] = _STORE_SLUG_BY_ID.get("foetex", "fotex")


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


def dealer_page_url(source_id: str | None) -> str | None:
    """Public eTilbudsavis store page (current leaflets), e.g. /fotex."""
    sid = (source_id or "").strip()
    slug = _STORE_SLUG_BY_ID.get(sid)
    if not slug:
        return None
    return f"https://etilbudsavis.dk/{slug}"


def offer_public_urls(
    external_id: str | None,
    raw: dict | None = None,
    *,
    source_id: str | None = None,
) -> dict[str, str | None]:
    """Build public eTilbudsavis links for an offer / its store leaflets."""
    raw = raw or {}
    oid = (external_id or raw.get("id") or "").strip()
    offer_url = f"https://etilbudsavis.dk/?offer={oid}" if oid else None
    # Prefer the dealer homepage (latest leaflets) over a single publication deep-link.
    catalog_url = dealer_page_url(source_id)
    if not catalog_url:
        catalog_id = str(raw.get("catalog_id") or "").strip()
        if catalog_id:
            catalog_url = f"https://etilbudsavis.dk/?publication={catalog_id}"
            page = raw.get("catalog_page")
            if page not in (None, "", 0):
                catalog_url = f"{catalog_url}&page={page}"
    return {"offer_url": offer_url, "catalog_url": catalog_url}
