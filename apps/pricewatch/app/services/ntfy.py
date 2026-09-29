from __future__ import annotations

import logging
from typing import Any

from app import db

logger = logging.getLogger("pricewatch.ntfy")


def notify_strike(
    watch: dict[str, Any],
    offer: dict[str, Any],
    *,
    kind: str = "target_reached",
) -> bool:
    """Emit a personal strike alert to the watch's owner. Never raises into callers.

    Watches store the owner's Auth user id in ``user_key``; standalone
    (``"local"``) watches have no platform owner and send nothing.
    """
    from stonepi_auth.alerts import auth_user_id

    owner = auth_user_id(watch.get("user_key"))
    if owner is None:
        logger.debug("strike for watch %s has no platform owner; not sent", watch.get("id"))
        return False
    name = watch.get("product_name") or "Product"
    price = offer.get("product_price")
    retailer = offer.get("retailer") or "retailer"
    target = watch.get("target_price")
    currency = watch.get("currency") or "DKK"
    event_kind = "price_drop" if kind == "price_drop" else "target_reached"
    event_id = f"pricewatch.{event_kind}"
    title = f"PriceWatch: {name}"
    if event_kind == "price_drop":
        body = f"{_fmt(price, currency)} at {retailer} — lower than your last strike (target {_fmt(target, currency)})."
    else:
        body = f"{_fmt(price, currency)} at {retailer} — below your {_fmt(target, currency)} target."
    watch_id = watch.get("id")
    dedupe = f"pricewatch:{watch_id}:{event_kind}:{_offer_key(offer)}"
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        return emit_event(
            EventEnvelope(
                id=event_id,
                source="pricewatch",
                title=title[:200],
                summary=body[:500],
                severity="success",
                audience="personal",
                user=owner,
                dedupe_key=dedupe,
                url=offer.get("product_url"),
                data={
                    "watch_id": watch_id,
                    "product_name": name,
                    "price": price,
                    "retailer": retailer,
                    "target_price": target,
                    "currency": currency,
                    "kind": event_kind,
                },
            )
        )
    except Exception:
        logger.debug("emit_event strike failed", exc_info=True)
        return False


def _offer_key(offer: dict[str, Any]) -> str:
    return f"{offer.get('retailer')}:{offer.get('product_price')}:{offer.get('product_url') or offer.get('offer_id')}"


def _fmt(value: Any, currency: str) -> str:
    try:
        return db.format_price(float(value), currency)
    except (TypeError, ValueError):
        return "—"
