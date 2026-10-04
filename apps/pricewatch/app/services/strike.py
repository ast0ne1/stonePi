from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app import db
from app.config import env
from app.services import access
from app.services import ntfy as ntfy_service
from app.services import trust
from app.sources import get_source
from app.sources.base import NormalisedOffer

logger = logging.getLogger("pricewatch.strike")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    value = dt or _utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def offer_fingerprint(offer: dict[str, Any]) -> str:
    key = "|".join(
        [
            str(offer.get("source_id") or ""),
            str(offer.get("retailer") or ""),
            str(offer.get("product_url") or offer.get("offer_id") or ""),
            f"{float(offer.get('product_price') or 0):.2f}",
            f"{float(offer.get('total_price') or offer.get('product_price') or 0):.2f}",
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def watch_price(offer: dict[str, Any], watch: dict[str, Any]) -> float | None:
    """The price compared with the target: total incl. delivery, or product price."""
    price = offer.get("product_price")
    if price is None:
        return None
    if watch.get("include_delivery"):
        total = offer.get("total_price")
        if total is None:
            total = float(price) + float(offer.get("delivery_cost") or 0.0)
        return float(total)
    return float(price)


def offer_matches(offer: dict[str, Any], watch: dict[str, Any]) -> bool:
    """Stock and condition filters (independent of price and trust)."""
    if watch.get("in_stock_required") and offer.get("availability") == "out_of_stock":
        return False
    wanted = (watch.get("condition") or "new").lower()
    cond = (offer.get("condition") or "new").lower()
    if wanted == "new" and cond == "used":
        return False
    if wanted == "used" and cond == "new":
        return False
    return True


def price_ok(offer: dict[str, Any], watch: dict[str, Any]) -> bool:
    price = watch_price(offer, watch)
    return price is not None and price <= float(watch["target_price"]) and offer_matches(offer, watch)


def offer_qualifies(offer: dict[str, Any], watch: dict[str, Any]) -> bool:
    """Under target, matching, and from a shop that passes the watch's minimum score."""
    return price_ok(offer, watch) and trust.is_trusted(offer.get("trust"), watch)


def _sort_key(watch: dict[str, Any]):
    return lambda o: watch_price(o, watch) if watch_price(o, watch) is not None else 1e18


def fetch_offers(
    source_id: str, product_id: str, *, market: str = "DK", use_cache: bool = True
) -> list[dict[str, Any]]:
    if use_cache:
        cached = db.get_scan_cache(source_id, product_id)
        if cached is not None:
            return cached
    # Prefer mock when PRICEWATCH_MOCK
    lookup_id = "mock" if env.mock else source_id
    source = get_source(lookup_id) or get_source(source_id)
    if source is None:
        raise RuntimeError(f"Unknown source: {source_id}")
    offers = [o.as_dict() for o in source.get_offers(product_id, market=market)]
    # Offers are stored under the watch's source id so merchants line up with watches.
    for offer in offers:
        offer["source_id"] = source_id
    db.set_scan_cache(source_id, product_id, offers)
    db.upsert_merchants(source_id, offers)
    db.update_source_status(source_id if not env.mock else "mock", ok=True)
    return offers


def fetch_offers_for_watch(watch: dict[str, Any], *, use_cache: bool = True) -> list[dict[str, Any]]:
    return fetch_offers(
        watch["source_id"], watch["product_id"], market=watch.get("market") or "DK", use_cache=use_cache
    )


def evaluate_watch(watch: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """Run one check for a watch. Returns a summary dict."""
    watch_id = int(watch["id"])
    try:
        offers = fetch_offers_for_watch(watch, use_cache=not force)
    except Exception as exc:
        logger.exception("Check failed for watch %s", watch_id)
        db.update_source_status(watch["source_id"], ok=False, error=str(exc))
        schedule = int(watch.get("schedule_minutes") or 360)
        db.update_watch_fields(
            watch_id,
            status="error",
            last_error=str(exc)[:500],
            last_checked_at=_iso(),
            next_check_at=_iso(_utc_now() + timedelta(minutes=schedule)),
        )
        return {"ok": False, "error": str(exc), "watch_id": watch_id}

    offers = trust.annotate(offers)
    by_price = _sort_key(watch)
    under_target = sorted((o for o in offers if price_ok(o, watch)), key=by_price)
    qualifying = [o for o in under_target if trust.is_trusted(o.get("trust"), watch)]
    low_rated = [o for o in under_target if not trust.is_trusted(o.get("trust"), watch)]
    # Current lowest among offers that pass condition/stock (even above target)
    filtered = sorted((o for o in offers if offer_matches(o, watch) and watch_price(o, watch) is not None), key=by_price)
    if filtered:
        display_lowest = watch_price(filtered[0], watch)
    else:
        # Nothing passes stock/condition: still show the cheapest listing (as 0.0.6 did).
        priced = [p for p in (watch_price(o, watch) for o in offers) if p is not None]
        display_lowest = min(priced) if priced else None

    db.add_observation(
        watch_id,
        lowest_price=display_lowest,
        offer_count=len(offers),
        qualifying_count=len(qualifying),
        offers=filtered[:20],
        note=None if qualifying else (
            "No offers" if not offers else ("Only low-rated shops at target" if low_rated else "No strike")
        ),
    )

    schedule = int(watch.get("schedule_minutes") or 360)
    next_check = _iso(_utc_now() + timedelta(minutes=schedule))
    fields: dict[str, Any] = {
        "current_lowest": display_lowest,
        "last_checked_at": _iso(),
        "next_check_at": next_check,
        "last_error": None,
    }

    # A low-rated shop under target, cheaper than any trusted one: never a strike,
    # but remembered (detail card) and, in "warn" mode, alerted once.
    cheaper_low = None
    if low_rated and (not qualifying or by_price(low_rated[0]) < by_price(qualifying[0])):
        cheaper_low = low_rated[0]
    low_should_warn = False
    if cheaper_low:
        low_fp = offer_fingerprint(cheaper_low)
        prev_low = watch.get("low_rated") or {}
        same_shop = bool(prev_low) and _shop_key(prev_low) == _shop_key(cheaper_low)
        # The price we last warned at, not the last price seen: a shop that goes
        # 900 → 950 → 900 was already warned about at 900.
        warned_price = prev_low.get("warned_price") if same_shop else None
        if warned_price is None and same_shop:
            warned_price = _snapshot_price(prev_low, watch)
        # Warn once per shop; again only if it gets cheaper or another low-rated shop leads.
        low_should_warn = low_fp != watch.get("low_rated_fingerprint") and (
            not watch.get("low_rated_fingerprint")
            or not same_shop
            or (warned_price is not None and by_price(cheaper_low) < float(warned_price) - 0.009)
        )
        low_snapshot = {
            **cheaper_low,
            "watch_price": by_price(cheaper_low),
            "warned_price": by_price(cheaper_low) if low_should_warn or warned_price is None else warned_price,
            "found_at": _iso(),
        }
        fields.update(
            {
                "low_rated_snapshot_json": json.dumps(low_snapshot, ensure_ascii=False),
                "low_rated_fingerprint": low_fp,
                "low_rated_at": low_snapshot["found_at"] if low_should_warn else (watch.get("low_rated_at") or low_snapshot["found_at"]),
            }
        )
    elif watch.get("low_rated_fingerprint"):
        fields.update({"low_rated_snapshot_json": None, "low_rated_fingerprint": None, "low_rated_at": None})
    warn = cheaper_low is not None and watch.get("low_rated_mode") == "warn"

    notified = False
    if qualifying:
        best = qualifying[0]
        best_price = by_price(best)
        fp = offer_fingerprint(best)
        prev_fp = watch.get("strike_fingerprint")
        prev_price = _snapshot_price(watch.get("strike"), watch)
        is_new = not prev_fp
        is_better = prev_price is not None and best_price < prev_price - 0.009
        is_changed = prev_fp and prev_fp != fp and (prev_price is None or best_price <= prev_price)
        snapshot = {
            **best,
            "watch_price": best_price,
            "found_at": _iso(),
            "target_price": watch["target_price"],
        }
        fields.update(
            {
                "status": "strike_found",
                "strike_snapshot_json": json.dumps(snapshot, ensure_ascii=False),
                "strike_fingerprint": fp,
                "strike_at": snapshot["found_at"] if (is_new or is_better or watch.get("status") != "strike_found") else watch.get("strike_at"),
            }
        )
        should_notify = is_new or is_better or (watch.get("status") != "strike_found" and is_changed)
        if should_notify:
            kind = "price_drop" if is_better else "target_reached"
            notified = ntfy_service.notify_strike(
                watch, snapshot, kind=kind, cheaper_low_rated=cheaper_low if warn else None
            )
            low_should_warn = False  # folded into the strike alert
    if warn and low_should_warn:
        ntfy_service.notify_low_rated(watch, {**cheaper_low, "watch_price": by_price(cheaper_low)})
    if not qualifying:
        if not offers:
            fields["status"] = "no_results"
            # Keep previous strike snapshot if any
            if watch.get("status") == "strike_found":
                fields["status"] = "strike_found"
        # Offers exist but none qualify — clear active strike display status back to watching
        # unless user still has a strike snapshot they haven't re-armed (keep strike_found)
        elif watch.get("status") == "strike_found" and watch.get("strike_fingerprint"):
            fields["status"] = "strike_found"
        else:
            fields["status"] = "watching"

    db.update_watch_fields(watch_id, **fields)
    return {
        "ok": True,
        "watch_id": watch_id,
        "offers": len(offers),
        "qualifying": len(qualifying),
        "low_rated": len(low_rated),
        "lowest": display_lowest,
        "notified": notified,
    }


def _shop_key(offer: dict[str, Any]) -> str:
    return str(offer.get("merchant_id") or offer.get("retailer") or "")


def _snapshot_price(snapshot: dict[str, Any] | None, watch: dict[str, Any]) -> float | None:
    """Price a stored strike/low-rated snapshot was found at (pre-0.0.7 ones lack watch_price)."""
    if not snapshot:
        return None
    if snapshot.get("watch_price") is not None:
        return float(snapshot["watch_price"])
    return watch_price(snapshot, watch)


def checkable_watches(watches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Watches whose owners may still have them checked.

    Auth's roster decides: an owner who lost PriceWatch or "Manage watches" has
    their watches skipped (kept, not deleted; checks resume if access returns).
    Local watches, admins and an unknown roster keep every watch.
    """
    allowed, skipped = access.filter_watches(watches, access.CAN_MANAGE_WATCHES)
    for watch, reason in skipped:
        logger.debug("Skipping watch %s: %s", watch.get("id"), reason)
    return allowed


def check_due_watches() -> dict[str, Any]:
    due_all = db.due_watches()
    due = checkable_watches(due_all)
    skipped = len(due_all) - len(due)
    if not due:
        return {"checked": 0, "skipped": skipped, "results": []}
    # First watch per (source, product) forces a fetch; siblings reuse scan_cache.
    warmed: set[tuple[str, str]] = set()
    results = []
    for watch in due:
        key = (watch["source_id"], watch["product_id"])
        force = key not in warmed
        warmed.add(key)
        results.append(evaluate_watch(watch, force=force))
    db.prune_observations()
    return {"checked": len(results), "skipped": skipped, "results": results}


def check_watch_now(watch_id: int) -> dict[str, Any]:
    watch = db.get_watch(watch_id)
    if not watch:
        return {"ok": False, "error": "Watch not found"}
    if watch.get("status") == "paused":
        return {"ok": False, "error": "Watch is paused"}
    return evaluate_watch(watch, force=True)
