from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app import db
from app.config import env
from app.services import ntfy as ntfy_service
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


def offer_qualifies(offer: dict[str, Any], watch: dict[str, Any]) -> bool:
    price = offer.get("product_price")
    if price is None:
        return False
    if float(price) > float(watch["target_price"]):
        return False
    if watch.get("in_stock_required") and offer.get("availability") == "out_of_stock":
        return False
    wanted = (watch.get("condition") or "new").lower()
    cond = (offer.get("condition") or "new").lower()
    if wanted == "new" and cond == "used":
        return False
    if wanted == "used" and cond == "new":
        return False
    return True


def fetch_offers_for_watch(watch: dict[str, Any], *, use_cache: bool = True) -> list[dict[str, Any]]:
    source_id = watch["source_id"]
    product_id = watch["product_id"]
    if use_cache:
        cached = db.get_scan_cache(source_id, product_id)
        if cached is not None:
            return cached
    # Prefer mock when PRICEWATCH_MOCK
    lookup_id = "mock" if env.mock else source_id
    source = get_source(lookup_id) or get_source(source_id)
    if source is None:
        raise RuntimeError(f"Unknown source: {source_id}")
    offers = [o.as_dict() for o in source.get_offers(product_id, market=watch.get("market") or "DK")]
    db.set_scan_cache(source_id, product_id, offers)
    db.update_source_status(source_id if not env.mock else "mock", ok=True)
    return offers


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

    qualifying = [o for o in offers if offer_qualifies(o, watch)]
    qualifying.sort(key=lambda o: float(o.get("product_price") or 1e18))
    lowest = float(qualifying[0]["product_price"]) if qualifying else (
        min((float(o["product_price"]) for o in offers if o.get("product_price") is not None), default=None)
        if offers
        else None
    )
    # Current lowest among offers that pass condition/stock (even above target)
    filtered = []
    for o in offers:
        wanted = (watch.get("condition") or "new").lower()
        cond = (o.get("condition") or "new").lower()
        if wanted == "new" and cond == "used":
            continue
        if wanted == "used" and cond == "new":
            continue
        if watch.get("in_stock_required") and o.get("availability") == "out_of_stock":
            continue
        filtered.append(o)
    filtered.sort(key=lambda o: float(o.get("product_price") or 1e18))
    display_lowest = float(filtered[0]["product_price"]) if filtered else lowest

    db.add_observation(
        watch_id,
        lowest_price=display_lowest,
        offer_count=len(offers),
        qualifying_count=len(qualifying),
        offers=filtered[:20],
        note=None if qualifying else ("No offers" if not offers else "No strike"),
    )

    schedule = int(watch.get("schedule_minutes") or 360)
    next_check = _iso(_utc_now() + timedelta(minutes=schedule))
    fields: dict[str, Any] = {
        "current_lowest": display_lowest,
        "last_checked_at": _iso(),
        "next_check_at": next_check,
        "last_error": None,
    }

    notified = False
    if qualifying:
        best = qualifying[0]
        fp = offer_fingerprint(best)
        prev_fp = watch.get("strike_fingerprint")
        prev_price = None
        if watch.get("strike"):
            prev_price = watch["strike"].get("product_price")
        is_new = not prev_fp
        is_better = prev_price is not None and float(best["product_price"]) < float(prev_price) - 0.009
        is_changed = prev_fp and prev_fp != fp and (
            prev_price is None or float(best["product_price"]) <= float(prev_price)
        )
        snapshot = {
            **best,
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
            notified = ntfy_service.notify_strike(watch, snapshot, kind=kind)
    elif not offers:
        fields["status"] = "no_results"
        # Keep previous strike snapshot if any
        if watch.get("status") == "strike_found":
            fields["status"] = "strike_found"
    else:
        # Offers exist but none qualify — clear active strike display status back to watching
        # unless user still has a strike snapshot they haven't re-armed (keep strike_found)
        if watch.get("status") == "strike_found" and watch.get("strike_fingerprint"):
            fields["status"] = "strike_found"
        else:
            fields["status"] = "watching"

    db.update_watch_fields(watch_id, **fields)
    return {
        "ok": True,
        "watch_id": watch_id,
        "offers": len(offers),
        "qualifying": len(qualifying),
        "lowest": display_lowest,
        "notified": notified,
    }


def check_due_watches() -> dict[str, Any]:
    due = db.due_watches()
    if not due:
        return {"checked": 0, "results": []}
    # First watch per (source, product) forces a fetch; siblings reuse scan_cache.
    warmed: set[tuple[str, str]] = set()
    results = []
    for watch in due:
        key = (watch["source_id"], watch["product_id"])
        force = key not in warmed
        warmed.add(key)
        results.append(evaluate_watch(watch, force=force))
    db.prune_observations()
    return {"checked": len(results), "results": results}


def check_watch_now(watch_id: int) -> dict[str, Any]:
    watch = db.get_watch(watch_id)
    if not watch:
        return {"ok": False, "error": "Watch not found"}
    if watch.get("status") == "paused":
        return {"ok": False, "error": "Watch is paused"}
    return evaluate_watch(watch, force=True)
