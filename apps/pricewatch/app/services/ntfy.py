from __future__ import annotations

import logging
from typing import Any

from app import db
from app.services import access, trust

logger = logging.getLogger("pricewatch.ntfy")


def notify_strike(
    watch: dict[str, Any],
    offer: dict[str, Any],
    *,
    kind: str = "target_reached",
    cheaper_low_rated: dict[str, Any] | None = None,
) -> bool:
    """Emit a personal strike alert to the watch's owner. Never raises into callers.

    Watches store the owner's Auth user id in ``user_key``; standalone
    (``"local"``) watches have no platform owner and send nothing. When a
    low-rated shop is cheaper (and the watch is in warn mode) it gets one extra
    line instead of a separate alert.
    """
    currency = watch.get("currency") or "DKK"
    target = watch.get("target_price")
    event_kind = "price_drop" if kind == "price_drop" else "target_reached"
    at = _at(offer)
    if event_kind == "price_drop":
        body = f"{_fmt(_price(offer), currency)} at {at} — lower than your last strike (target {_fmt(target, currency)})."
    else:
        body = f"{_fmt(_price(offer), currency)} at {at} — below your {_fmt(target, currency)} target."
    if cheaper_low_rated:
        badge = trust.badge_text(cheaper_low_rated.get("trust")) or "no rating"
        body += (
            f" Cheaper at {cheaper_low_rated.get('retailer') or 'another shop'}"
            f" {_fmt(_price(cheaper_low_rated), currency)} ({badge} — below your minimum)."
        )
    return _emit(watch, offer, event_kind=event_kind, severity="success", body=body)


def notify_low_rated(watch: dict[str, Any], offer: dict[str, Any]) -> bool:
    """Warning: a shop below the watch's minimum rating is under target. Not a strike."""
    currency = watch.get("currency") or "DKK"
    shop = offer.get("retailer") or "a shop"
    t = offer.get("trust") or {}
    minimum = watch.get("min_trust_score")
    if t.get("score") is not None and t.get("counts"):
        why = f"{trust.badge_text(t)}, below your {float(minimum):.1f} minimum"
    elif t.get("score") is not None:
        why = f"{trust.badge_text(t)}, too few reviews to count"
    else:
        why = "no rating"
    body = (
        f"⚠ {_fmt(_price(offer), currency)} at {shop} ({why}) — under your"
        f" {_fmt(watch.get('target_price'), currency)} target. Not counted as a strike."
    )
    return _emit(watch, offer, event_kind="low_rated_offer", severity="warning", body=body)


def _emit(watch: dict[str, Any], offer: dict[str, Any], *, event_kind: str, severity: str, body: str) -> bool:
    from stonepi_auth.alerts import auth_user_id

    owner = auth_user_id(watch.get("user_key"))
    if owner is None:
        logger.debug("%s for watch %s has no platform owner; not sent", event_kind, watch.get("id"))
        return False
    # Strike alerts are a capability: skip when Auth's roster says the owner lost
    # PriceWatch or "Strike alerts" (unknown roster → send as before).
    reason = access.denied_reason(access.owner_access(owner), access.CAN_USE_ALERTS)
    if reason:
        logger.info("%s for watch %s not sent: %s", event_kind, watch.get("id"), reason)
        return False
    name = watch.get("product_name") or "Product"
    currency = watch.get("currency") or "DKK"
    watch_id = watch.get("id")
    t = offer.get("trust") or {}
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        return emit_event(
            EventEnvelope(
                id=f"pricewatch.{event_kind}",
                source="pricewatch",
                title=f"PriceWatch: {name}"[:200],
                summary=body[:500],
                severity=severity,
                audience="personal",
                user=owner,
                dedupe_key=f"pricewatch:{watch_id}:{event_kind}:{_offer_key(offer)}",
                url=offer.get("product_url"),
                data={
                    "watch_id": watch_id,
                    "product_name": name,
                    "price": _price(offer),
                    "retailer": offer.get("retailer") or "retailer",
                    "target_price": watch.get("target_price"),
                    "currency": currency,
                    "kind": event_kind,
                    "trust_score": t.get("score"),
                    "trust_source": t.get("source"),
                },
            )
        )
    except Exception:
        logger.debug("emit_event %s failed", event_kind, exc_info=True)
        return False


def _price(offer: dict[str, Any]) -> Any:
    """The price the watch compared (incl. delivery when set), else product price."""
    value = offer.get("watch_price")
    return value if value is not None else offer.get("product_price")


def _at(offer: dict[str, Any]) -> str:
    retailer = offer.get("retailer") or "retailer"
    badge = trust.badge_text(offer.get("trust"))
    return f"{retailer} ({badge})" if badge else retailer


def _offer_key(offer: dict[str, Any]) -> str:
    return f"{offer.get('retailer')}:{offer.get('product_price')}:{offer.get('product_url') or offer.get('offer_id')}"


def _fmt(value: Any, currency: str) -> str:
    try:
        return db.format_price(float(value), currency)
    except (TypeError, ValueError):
        return "—"
