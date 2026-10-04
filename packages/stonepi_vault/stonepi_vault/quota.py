"""Shared allowance for a metered provider (Bright Data), counted across apps per billing period.

Several apps can spend from the same account (EventTrakr's Facebook/Instagram,
PriceWatch's Trustpilot), so the count lives in one file in the Vault folder,
which every service user can write (setgid stonepi-vault, 2770). Same lock pattern
as the Vault itself.

Callers reserve their worst case before a call and settle the real record count
afterwards, so two apps calling at once can't both slip under the limit:

    quota = get_quota("brightdata")
    reservation = quota.reserve("eventtrakr/facebook", 30, paced=True)  # may raise QuotaExceeded
    ...call Bright Data...
    reservation.settle(len(records))

The period runs from ``reset_day`` (1-31, default 1; clamped to short months' last day)
to the same day next month, so it can match the provider's billing date. One use (say
``eventtrakr/instagram``) can also have its own allowance inside the limit.

``paced`` (background jobs) also keeps spending to the period's share so far, so a busy
first week can't use up the whole period; manual actions can use what's left.
"""

from __future__ import annotations

import calendar
import contextlib
import json
import math
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:  # POSIX (the Pi); Windows dev has a single writer.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

DEFAULT_LIMITS = {"brightdata": 5000}
DEFAULT_RESET_DAY = 1

_thread_lock = threading.Lock()


class QuotaExceeded(Exception):
    def __init__(self, message: str, *, reason: str, status: dict[str, Any]):
        super().__init__(message)
        self.reason = reason  # "limit" | "pace"
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reset_date(year: int, month: int, reset_day: int) -> date:
    return date(year, month, min(reset_day, calendar.monthrange(year, month)[1]))


def period_bounds(today: date, reset_day: int = DEFAULT_RESET_DAY) -> tuple[date, date]:
    """(first day of the current period, the day the next one starts)."""
    start = _reset_date(today.year, today.month, reset_day)
    if today < start:
        y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        start = _reset_date(y, m, reset_day)
    y, m = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, _reset_date(y, m, reset_day)


def pace_allowance(limit: int, today: date, reset_day: int = DEFAULT_RESET_DAY) -> int:
    """Records the period may have used by the end of today if spread evenly."""
    start, end = period_bounds(today, reset_day)
    elapsed = (today - start).days + 1
    return min(limit, math.ceil(limit * elapsed / max(1, (end - start).days)))


def _clamp_day(day: int) -> int:
    return min(31, max(1, int(day)))


class Reservation:
    def __init__(self, quota: "Quota", use: str, records: int):
        self.quota = quota
        self.use = use
        self.records = records
        self._settled = False

    def settle(self, actual: int) -> None:
        """Replace the reserved worst case with what the call actually used."""
        if self._settled:
            return
        self._settled = True
        delta = max(0, int(actual)) - self.records
        if delta:
            self.quota._adjust(self.use, delta)


class Quota:
    def __init__(self, provider: str, root: Path, default_limit: int | None = None):
        self.provider = provider
        self.root = Path(root)
        self.default_limit = default_limit if default_limit is not None else DEFAULT_LIMITS.get(provider, 0)
        self.path = self.root / f"quota-{provider}.json"

    # -- file access ---------------------------------------------------------------

    def _secure_file(self, path: Path) -> None:
        try:
            os.chmod(path, 0o660)
        except OSError:
            pass
        if hasattr(os, "chown"):
            try:
                os.chown(path, -1, self.root.stat().st_gid)
            except OSError:
                pass

    @contextlib.contextmanager
    def _lock(self):
        with _thread_lock:
            if fcntl is None:
                yield
                return
            self.root.mkdir(parents=True, exist_ok=True)
            lock_path = self.root / f".quota-{self.provider}.lock"
            handle = open(lock_path, "a+")
            try:
                self._secure_file(lock_path)
                fcntl.flock(handle, fcntl.LOCK_EX)
                yield
            finally:
                try:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                finally:
                    handle.close()

    def _read(self, now: datetime) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        try:
            limit = max(0, int(data.get("limit", self.default_limit)))
        except (TypeError, ValueError):
            limit = self.default_limit
        try:
            reset_day = _clamp_day(data.get("reset_day", DEFAULT_RESET_DAY))
        except (TypeError, ValueError):
            reset_day = DEFAULT_RESET_DAY
        raw_limits = data.get("use_limits") if isinstance(data.get("use_limits"), dict) else {}
        use_limits: dict[str, int | None] = {}  # None: allowance switched off on purpose
        for use, value in raw_limits.items():
            try:
                use_limits[str(use)] = None if value is None else max(0, int(value))
            except (TypeError, ValueError):
                continue
        start, _ = period_bounds(now.date(), reset_day)
        period = start.isoformat()
        # Files from before reset days kept a calendar "month" (YYYY-MM).
        same_period = data.get("period") == period or (
            "period" not in data and data.get("month") == period[:7] and start.day == 1
        )
        base = {"period": period, "limit": limit, "reset_day": reset_day, "use_limits": use_limits}
        if not same_period:
            # New period: counts reset; the limit, reset day and allowances carry over.
            return {**base, "used": 0, "by_use": {}}
        by_use = data.get("by_use") if isinstance(data.get("by_use"), dict) else {}
        return {
            **base,
            "used": max(0, int(data.get("used") or 0)),
            "by_use": {str(k): max(0, int(v or 0)) for k, v in by_use.items()},
        }

    def _write(self, data: dict[str, Any], now: datetime) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        data = {**data, "updated_at": now.isoformat(timespec="seconds")}
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._secure_file(tmp)
        os.replace(tmp, self.path)

    def _status(self, data: dict[str, Any], now: datetime) -> dict[str, Any]:
        limit, used, reset_day = data["limit"], data["used"], data["reset_day"]
        start, end = period_bounds(now.date(), reset_day)
        by_app: dict[str, int] = {}
        for use, count in data["by_use"].items():
            app = use.split("/", 1)[0]
            by_app[app] = by_app.get(app, 0) + count
        uses = {}
        for use, use_limit in sorted(data["use_limits"].items()):
            if use_limit is None:
                continue
            spent = data["by_use"].get(use, 0)
            uses[use] = {
                "limit": use_limit,
                "used": spent,
                "remaining": max(0, use_limit - spent),
                "pace_allowance": pace_allowance(use_limit, now.date(), reset_day),
            }
        return {
            "provider": self.provider,
            "limit": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "pace_allowance": pace_allowance(limit, now.date(), reset_day),
            "reset_day": reset_day,
            "period_start": start.isoformat(),
            "resets_on": end.isoformat(),
            "period_days": (end - start).days,
            "by_use": dict(sorted(data["by_use"].items())),
            "by_app": dict(sorted(by_app.items())),
            "use_limits": uses,
        }

    def _update(self, change) -> dict[str, Any]:
        now = _now()
        with self._lock():
            data = self._read(now)
            change(data, now)
            self._write(data, now)
            return self._status(data, now)

    # -- public API ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        now = _now()
        return self._status(self._read(now), now)

    def set_limit(self, limit: int) -> dict[str, Any]:
        return self._update(lambda data, now: data.update(limit=max(0, int(limit))))

    def set_reset_day(self, day: int) -> dict[str, Any]:
        """Move the billing day. What's been used so far stays counted in the new period."""

        def change(data, now):
            data["reset_day"] = _clamp_day(day)
            data["period"] = period_bounds(now.date(), data["reset_day"])[0].isoformat()

        return self._update(change)

    def set_use_limit(self, use: str, limit: int | None) -> dict[str, Any]:
        """Give one use its own allowance inside the limit (None: no separate allowance)."""

        def change(data, now):
            data["use_limits"][use] = None if limit is None else max(0, int(limit))

        return self._update(change)

    def ensure_use_limit(self, use: str, default: int) -> dict[str, Any]:
        """Apply ``default`` once; a limit set (or removed) since then is left alone."""
        now = _now()
        with self._lock():
            data = self._read(now)
            if use not in data["use_limits"]:
                data["use_limits"][use] = max(0, int(default))
                self._write(data, now)
            return self._status(data, now)

    def reserve(self, use: str, records: int, *, paced: bool = False) -> Reservation:
        """Book ``records`` (the call's worst case) or raise QuotaExceeded."""
        records = max(1, int(records))
        now = _now()
        with self._lock():
            data = self._read(now)
            status = self._status(data, now)
            if data["used"] + records > data["limit"]:
                raise QuotaExceeded(
                    f"Limit reached ({data['used']}/{data['limit']} records used this period)",
                    reason="limit",
                    status=status,
                )
            if paced and data["used"] + records > status["pace_allowance"]:
                raise QuotaExceeded(
                    f"Today's share of the limit is used ({data['used']}/{status['pace_allowance']} so far this period)",
                    reason="pace",
                    status=status,
                )
            own = status["use_limits"].get(use)
            if own is not None:
                if own["used"] + records > own["limit"]:
                    raise QuotaExceeded(
                        f"This feature's allowance is used ({own['used']}/{own['limit']} records this period)",
                        reason="limit",
                        status=status,
                    )
                if paced and own["used"] + records > own["pace_allowance"]:
                    raise QuotaExceeded(
                        f"Today's share of this feature's allowance is used ({own['used']}/{own['pace_allowance']} so far)",
                        reason="pace",
                        status=status,
                    )
            data["used"] += records
            data["by_use"][use] = data["by_use"].get(use, 0) + records
            self._write(data, now)
        return Reservation(self, use, records)

    def _adjust(self, use: str, delta: int) -> None:
        def change(data, now):
            data["used"] = max(0, data["used"] + delta)
            data["by_use"][use] = max(0, data["by_use"].get(use, 0) + delta)

        self._update(change)


def get_quota(provider: str = "brightdata", root: Path | None = None) -> Quota:
    """The shared quota in the Vault folder (or ``root`` for an app without a Vault)."""
    if root is None:
        from .store import get_vault

        root = get_vault().root
    return Quota(provider, root)
