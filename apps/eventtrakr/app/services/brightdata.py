"""Bright Data metering: the platform-wide monthly record limit, plus a small result cache.

Bright Data bills per record returned, and EventTrakr shares one free-tier account
with PriceWatch. Every call goes through ``metered()``: it books the call's worst case
against the shared quota in the Vault folder (``stonepi_vault.quota``) before calling,
then settles the real record count. When the limit (or, for background jobs, the
month's share so far) is used up, the call is skipped with BrightDataBudgetError and
the source keeps the events it already has.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Callable

from app import config

logger = logging.getLogger("eventtrakr.brightdata")

APP = "eventtrakr"


class BrightDataError(Exception):
    """A Bright Data call failed. ``billed`` is False only when the job never started
    (the request itself was refused), so its reserved records are handed back."""

    def __init__(self, message: str, *, billed: bool = True):
        super().__init__(message)
        self.billed = billed


class BrightDataBudgetError(BrightDataError):
    def __init__(self, message: str):
        super().__init__(message, billed=False)


def _quota():
    from stonepi_vault.quota import get_quota

    return get_quota("brightdata")


def usage() -> dict[str, Any] | None:
    """This month's shared usage, or None when the Vault folder can't be read."""
    try:
        return _quota().status()
    except Exception:
        logger.warning("Bright Data usage unavailable", exc_info=True)
        return None


def set_limit(limit: int) -> dict[str, Any]:
    return _quota().set_limit(limit)


def set_reset_day(day: int) -> dict[str, Any]:
    return _quota().set_reset_day(day)


# Instagram's own allowance inside the shared limit, so tracked accounts can't use up
# what Facebook and PriceWatch need. Kept in the shared quota file, where the default
# is applied once; after that the admin's choice (including "none") stands.
INSTAGRAM_USE = f"{APP}/instagram"
DEFAULT_INSTAGRAM_ALLOWANCE = 2000


def ensure_instagram_allowance() -> None:
    try:
        _quota().ensure_use_limit(INSTAGRAM_USE, DEFAULT_INSTAGRAM_ALLOWANCE)
    except Exception:
        logger.warning("Could not set the Instagram allowance", exc_info=True)


def set_instagram_allowance(limit: int | None) -> dict[str, Any]:
    """None: no separate allowance (Instagram shares the overall limit)."""
    return _quota().set_use_limit(INSTAGRAM_USE, limit)


def _cache_dir():
    return config.CACHE_DIR / "brightdata"


def _cache_path(key: str):
    return _cache_dir() / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}.json"


def _cache_get(key: str, max_age_hours: float) -> list | None:
    try:
        data = json.loads(_cache_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or time.time() - float(data.get("at") or 0) > max_age_hours * 3600:
        return None
    records = data.get("records")
    return records if isinstance(records, list) else None


def _cache_put(key: str, records: list) -> None:
    try:
        path = _cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": time.time(), "key": key, "records": records}), encoding="utf-8")
    except OSError:
        logger.warning("Could not cache Bright Data result", exc_info=True)


def metered(
    use: str,
    worst_case: int,
    call: Callable[[], list],
    *,
    paced: bool,
    count: Callable[[list], int] = len,
    cache_key: str | None = None,
    cache_hours: float = 0.0,
) -> list:
    """Run ``call`` (returns Bright Data's raw records) within the shared monthly limit.

    ``use`` labels the usage (``facebook``, ``instagram``…). ``worst_case`` is the most
    records the call can return; ``paced`` is for background jobs (see stonepi_vault.quota).
    With ``cache_key``, a result younger than ``cache_hours`` is reused at no cost.
    """
    if cache_key and cache_hours > 0:
        cached = _cache_get(cache_key, cache_hours)
        if cached is not None:
            logger.info("Bright Data %s: reusing cached result (%d records)", use, len(cached))
            return cached
    try:
        quota = _quota()
    except Exception as e:
        raise BrightDataBudgetError(f"Bright Data usage limit unavailable ({e}); skipping the call") from e
    from stonepi_vault.quota import QuotaExceeded

    try:
        reservation = quota.reserve(f"{APP}/{use}", worst_case, paced=paced)
    except QuotaExceeded as e:
        hint = f"until {e.status['resets_on']}" if e.reason == "limit" else "until tomorrow"
        raise BrightDataBudgetError(f"Bright Data: {e} — paused {hint}") from e
    try:
        records = call()
    except BrightDataError as e:
        reservation.settle(worst_case if e.billed else 0)
        raise
    except Exception:
        reservation.settle(worst_case)
        raise
    used = count(records) if isinstance(records, list) else 0
    reservation.settle(used)
    if used > worst_case:
        logger.warning("Bright Data %s returned %d records (expected at most %d)", use, used, worst_case)
    if cache_key and cache_hours > 0 and isinstance(records, list):
        _cache_put(cache_key, records)
    return records
