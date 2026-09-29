from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("stonepi.contracts.events")

SEVERITIES = ("info", "success", "warning", "error", "critical")

AUDIENCE_PERSONAL = "personal"
AUDIENCE_HOUSEHOLD = "household"
AUDIENCE_ADMIN = "admin"
AUDIENCES = (AUDIENCE_PERSONAL, AUDIENCE_HOUSEHOLD, AUDIENCE_ADMIN)

# Owner values that are never a real Auth user id (standalone/dev placeholders).
_NON_USERS = frozenset({"", "local", "none", "null", "0"})


def normalize_severity(raw: Any) -> str:
    value = str(raw or "info").strip().lower()
    return value if value in SEVERITIES else "info"


def normalize_audience(raw: Any) -> str:
    value = str(raw or AUDIENCE_HOUSEHOLD).strip().lower()
    return value if value in AUDIENCES else AUDIENCE_HOUSEHOLD


def normalize_user(raw: Any) -> str | None:
    """Auth user id, or None when missing / a local placeholder."""
    if isinstance(raw, bool) or isinstance(raw, int):
        return None  # integers are always local app user ids
    value = str(raw or "").strip()
    if value.lower() in _NON_USERS or value.isdigit():
        return None
    return value[:64]


@dataclass
class EventEnvelope:
    id: str
    source: str
    title: str
    summary: str = ""
    severity: str = "info"
    timestamp: str = ""
    dedupe_key: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    url: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    # Who the event is for. ``user`` is the platform Auth user id and is
    # required for personal events (never a local app user id).
    audience: str = AUDIENCE_HOUSEHOLD
    user: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["severity"] = normalize_severity(self.severity)
        payload["audience"] = normalize_audience(self.audience)
        payload["user"] = normalize_user(self.user)
        if not payload.get("timestamp"):
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        return payload


def validate_event(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    event_id = str(raw.get("id") or "").strip()
    source = str(raw.get("source") or "").strip()
    title = str(raw.get("title") or "").strip()
    if not event_id or not source or not title:
        return None
    audience = normalize_audience(raw.get("audience"))
    user = normalize_user(raw.get("user"))
    if audience == AUDIENCE_PERSONAL and not user:
        # Personal events never fall back to household.
        logger.warning("Dropping personal event %s from %s: no Auth user id", event_id, source)
        return None
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    actions = raw.get("actions") if isinstance(raw.get("actions"), list) else []
    return EventEnvelope(
        id=event_id,
        source=source,
        title=title[:200],
        summary=str(raw.get("summary") or "")[:500],
        severity=normalize_severity(raw.get("severity")),
        timestamp=str(raw.get("timestamp") or "").strip(),
        dedupe_key=str(raw.get("dedupe_key") or "").strip(),
        data=dict(data),
        url=(str(raw["url"]).strip() if raw.get("url") else None),
        actions=[a for a in actions if isinstance(a, dict)],
        audience=audience,
        user=user,
    ).to_dict()
