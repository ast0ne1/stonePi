"""Retailer trust scores: Trustpilot (via Bright Data) first, PriceRunner's shop
rating as an optional, clearly labelled fallback.

PriceWatch never contacts Trustpilot itself. Lookups run in the background
(scheduler tick or a settings button), never inside a watch check or page load.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from app import db
from app.config import (
    DEFAULT_TRUST_MIN_REVIEWS,
    DEFAULT_TRUST_REFRESH_DAYS,
    env,
)
from app.sources.base import normalise_domain

logger = logging.getLogger("pricewatch.trust")

KEY_NAME = "PRICEWATCH_BRIGHTDATA_API_KEY"
EVENTTRAKR_KEY_NAME = "BRIGHTDATA_API_KEY"
_DB_KEY_FALLBACK = "brightdata_api_key"

SOURCE_LABELS = {"trustpilot": "Trustpilot", "pricerunner": "PriceRunner"}

FAILURE_BACKOFF = timedelta(hours=1)
# Bright Data bills per record and doesn't strictly honour num_of_reviews_limit: 1
# (Elgiganten came back as 3 records), so each shop books 3 against the shared limit
# and the real count is settled after the call. A run looks up at most MAX_BATCH shops.
RECORDS_PER_SHOP_WORST = 3
MAX_BATCH = 50
QUOTA_USE = "pricewatch/trustpilot"

_refresh_lock = threading.Lock()
_last_run: dict[str, Any] = {}
_backoff_until: datetime | None = None


# -- API key (own vault slot; "Copy from EventTrakr" duplicates EventTrakr's) ------


def _vault_get(name: str) -> str:
    try:
        from stonepi_vault import get_secret

        return (get_secret(name, env_name=name, default="") or "").strip()
    except Exception:
        return ""


def get_api_key() -> str:
    """PriceWatch's own key only — never falls back to EventTrakr's."""
    return _vault_get(KEY_NAME) or db.get_setting(_DB_KEY_FALLBACK, "").strip()


def eventtrakr_key_available() -> bool:
    return bool(_vault_get(EVENTTRAKR_KEY_NAME))


def set_api_key(value: str) -> None:
    value = (value or "").strip()
    try:
        from stonepi_vault import get_vault, set_secret

        if value:
            set_secret(KEY_NAME, value)
        else:
            get_vault().delete(KEY_NAME)
        db.set_setting(_DB_KEY_FALLBACK, "")
        return
    except Exception:
        logger.warning("Vault unavailable; storing Bright Data key in PriceWatch settings")
    db.set_setting(_DB_KEY_FALLBACK, value)


def copy_key_from_eventtrakr() -> bool:
    value = _vault_get(EVENTTRAKR_KEY_NAME)
    if not value:
        return False
    set_api_key(value)
    return True


# -- settings ------------------------------------------------------------------------


def _int_setting(settings: dict[str, str], key: str, default: int, *, low: int = 0) -> int:
    try:
        return max(low, int(settings.get(key) or default))
    except (TypeError, ValueError):
        return default


def trust_settings(settings: dict[str, str] | None = None) -> dict[str, Any]:
    s = settings if settings is not None else db.list_settings()
    return {
        "brightdata_enabled": s.get("trust_brightdata_enabled") == "1",
        "pricerunner_fallback": s.get("trust_pricerunner_fallback") == "1",
        "min_reviews": _int_setting(s, "trust_min_reviews", DEFAULT_TRUST_MIN_REVIEWS),
        "refresh_days": _int_setting(s, "trust_refresh_days", DEFAULT_TRUST_REFRESH_DAYS, low=1),
    }


def _quota():
    from stonepi_vault.quota import get_quota

    return get_quota("brightdata")


def usage() -> dict[str, Any] | None:
    """This period's Bright Data usage, shared with EventTrakr (stonepi_vault.quota), or
    None when the Vault folder can't be read (lookups then stay paused)."""
    try:
        return _quota().status()
    except Exception:
        logger.warning("Bright Data usage unavailable", exc_info=True)
        return None


def set_limit(limit: int) -> dict[str, Any]:
    return _quota().set_limit(limit)


def set_reset_day(day: int) -> dict[str, Any]:
    return _quota().set_reset_day(day)


# -- scores --------------------------------------------------------------------------


def effective_score(merchant: dict[str, Any] | None, settings: dict[str, Any]) -> dict[str, Any] | None:
    """The score to show and use for one shop, or None ("no rating")."""
    if not merchant:
        return None
    min_reviews = settings["min_reviews"]
    if settings["brightdata_enabled"] and merchant.get("tp_score") is not None and merchant.get("tp_status") != "not_found":
        count = merchant.get("tp_review_count")
        return {
            "score": float(merchant["tp_score"]),
            "count": count,
            "source": "trustpilot",
            "url": merchant.get("tp_url"),
            "fetched_at": merchant.get("tp_fetched_at"),
            "counts": count is None or int(count) >= min_reviews,
        }
    if settings["pricerunner_fallback"] and merchant.get("pr_rating") is not None:
        count = merchant.get("pr_rating_count")
        return {
            "score": float(merchant["pr_rating"]),
            "count": count,
            "source": "pricerunner",
            "url": None,
            "fetched_at": None,
            "counts": count is not None and int(count) >= min_reviews,
        }
    return None


def _trust_for(merchant: dict[str, Any] | None, settings: dict[str, Any]) -> dict[str, Any]:
    score = effective_score(merchant, settings)
    if score:
        return {**score, "label": SOURCE_LABELS[score["source"]]}
    pending = bool(
        merchant
        and settings["brightdata_enabled"]
        and merchant.get("tp_status") == "pending"
        and merchant.get("effective_domain")
    )
    return {"score": None, "count": None, "source": None, "counts": False, "pending": pending}


def annotate(offers: list[dict[str, Any]], *, settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Copy of ``offers`` with a ``trust`` dict on each (one merchants query per source)."""
    settings = settings or trust_settings()
    by_source: dict[str, list[str]] = {}
    for offer in offers:
        mid = offer.get("merchant_id")
        if mid:
            by_source.setdefault(str(offer.get("source_id") or ""), []).append(str(mid))
    merchants = {sid: db.get_merchants(sid, ids) for sid, ids in by_source.items()}
    out = []
    for offer in offers:
        merchant = merchants.get(str(offer.get("source_id") or ""), {}).get(str(offer.get("merchant_id") or ""))
        out.append({**offer, "trust": _trust_for(merchant, settings)})
    return out


def is_trusted(trust: dict[str, Any] | None, watch: dict[str, Any]) -> bool:
    """Does this offer's shop pass the watch's minimum score?"""
    minimum = watch.get("min_trust_score")
    if minimum is None:
        return True
    trust = trust or {}
    if trust.get("score") is not None and trust.get("counts"):
        return float(trust["score"]) >= float(minimum)
    return not watch.get("require_rating")


def badge_text(trust: dict[str, Any] | None) -> str:
    """Plain-text badge for alerts: ``★4.3 Trustpilot``; empty when no rating."""
    if not trust or trust.get("score") is None:
        return ""
    return f"★{float(trust['score']):.1f} {SOURCE_LABELS.get(trust.get('source') or '', '')}".strip()


def website_mismatch(merchant: dict[str, Any]) -> bool:
    site = normalise_domain(merchant.get("tp_website"))
    domain = merchant.get("effective_domain")
    return bool(site and domain and site != domain)


# -- background refresh --------------------------------------------------------------


def _lookup(api_key: str, domains: list[str]):
    if env.mock:
        from app.sources.brightdata_trustpilot import TrustResult, profile_url
        from app.sources.mock import mock_trustpilot_scores

        fixture = mock_trustpilot_scores()
        results = {}
        for d in domains:
            item = fixture.get(d)
            results[d] = (
                TrustResult(status="ok", score=item["score"], review_count=item.get("count"),
                            url=profile_url(d), website=item.get("website"))
                if item
                else TrustResult(status="not_found")
            )
        return results, len(domains)
    from app.sources.brightdata_trustpilot import fetch_scores

    return fetch_scores(api_key, domains)


def status() -> dict[str, Any]:
    return {"running": _refresh_lock.locked(), **_last_run}


def refresh_scores(*, force: bool = False) -> dict[str, Any]:
    """Look up every shop that is pending or stale, within the shared Bright Data limit.

    After a failed Bright Data call, scheduled runs wait FAILURE_BACKOFF before
    trying again; ``force`` (the Settings button) ignores that wait.
    """
    if not _refresh_lock.acquire(blocking=False):
        return {"ok": False, "skipped": "already running"}
    try:
        return _refresh_locked(force=force)
    finally:
        _refresh_lock.release()


def _refresh_locked(*, force: bool) -> dict[str, Any]:
    global _backoff_until
    settings = trust_settings()
    if not settings["brightdata_enabled"]:
        return {"ok": True, "skipped": "disabled"}
    api_key = get_api_key()
    if not api_key and not env.mock:
        return {"ok": True, "skipped": "no key"}
    if not force and _backoff_until and datetime.now(timezone.utc) < _backoff_until:
        return {"ok": True, "skipped": "backing off"}
    due = db.merchants_due_for_score(refresh_days=settings["refresh_days"])
    if not due:
        return {"ok": True, "looked_up": 0}
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for merchant in due:
        by_domain.setdefault(merchant["effective_domain"], []).append(merchant)

    reservation = None
    if env.mock:
        domains = list(by_domain)[:MAX_BATCH]
    else:
        status = usage()
        if status is None:
            _last_run.update(at=db._iso(), note="Bright Data usage can't be read from the Vault folder — lookups paused")
            return {"ok": True, "skipped": "usage unavailable"}
        # Scheduled runs keep to the month's share so far; the Settings button can use
        # whatever is left of the limit.
        ceiling = status["limit"] if force else status["pace_allowance"]
        available = max(0, min(status["remaining"], ceiling - status["used"]))
        domains = list(by_domain)[: min(MAX_BATCH, available // RECORDS_PER_SHOP_WORST)]
        if not domains:
            reached = status["remaining"] < RECORDS_PER_SHOP_WORST
            _last_run.update(
                at=db._iso(),
                note=f"Bright Data limit reached — scores frozen until {status['resets_on']}"
                if reached
                else "Today's share of the Bright Data limit is used — more lookups tomorrow",
            )
            return {"ok": True, "skipped": "cap reached" if reached else "paced"}
        from stonepi_vault.quota import QuotaExceeded

        try:
            reservation = _quota().reserve(QUOTA_USE, len(domains) * RECORDS_PER_SHOP_WORST, paced=not force)
        except QuotaExceeded as exc:  # EventTrakr spent in the meantime
            _last_run.update(at=db._iso(), note=f"Bright Data: {exc}")
            return {"ok": True, "skipped": "cap reached" if exc.reason == "limit" else "paced"}
    try:
        results, records = _lookup(api_key, domains)
    except Exception as exc:
        # The call itself failed (network, key, Bright Data down): shops stay queued
        # with their last known scores, and scheduled runs pause before retrying.
        if reservation is not None:
            reservation.settle(len(domains) * RECORDS_PER_SHOP_WORST if getattr(exc, "billed", True) else 0)
        logger.warning("Trust score lookup failed: %s", exc)
        _backoff_until = datetime.now(timezone.utc) + FAILURE_BACKOFF
        _last_run.update(
            at=db._iso(),
            note=f"Lookup failed: {exc} — retrying after {_backoff_until.strftime('%H:%M')} UTC",
        )
        return {"ok": False, "error": str(exc)}
    _backoff_until = None
    if reservation is not None:
        reservation.settle(records)
    for d in domains:
        result = results.get(d)
        for m in by_domain[d]:
            if result is None:
                db.update_merchant_score(m["source_id"], m["merchant_id"], status="not_found")
            elif result.status == "error":
                db.update_merchant_score(m["source_id"], m["merchant_id"], status="error", error=result.error)
            else:
                db.update_merchant_score(
                    m["source_id"],
                    m["merchant_id"],
                    status=result.status,
                    score=result.score,
                    review_count=result.review_count,
                    url=result.url,
                    website=result.website,
                )
    found = sum(1 for r in results.values() if r.status == "ok")
    logger.info("Trust scores: looked up %s shops, %s scored, %s records", len(domains), found, records)
    _last_run.update(at=db._iso(), note=f"Looked up {len(domains)} shop{'s' if len(domains) != 1 else ''} · {found} scored · {records} records")
    return {"ok": True, "looked_up": len(domains), "found": found, "records": records}


def refresh_in_background(*, force: bool = False) -> bool:
    """Start a refresh thread (scheduler tick or Settings). False when one is already running."""
    if _refresh_lock.locked():
        return False
    threading.Thread(
        target=refresh_scores, kwargs={"force": force}, name="pricewatch-trust-refresh", daemon=True
    ).start()
    return True
