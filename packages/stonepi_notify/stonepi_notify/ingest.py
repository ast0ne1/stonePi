from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stonepi_contracts import (
    AUDIENCE_ADMIN,
    AUDIENCE_HOUSEHOLD,
    AUDIENCE_PERSONAL,
    event_type,
    normalize_severity,
    validate_event,
)

from .destinations import load_destinations
from .history import append_history
from .ntfy import publish_ntfy
from .subscriptions import in_quiet_hours, load_subscriptions, redact_topic

logger = logging.getLogger("stonepi.notify.ingest")

_data_dir: Path | None = None
# Returns ``{auth_user_id: {"is_admin": bool, "phone_alerts": bool}}`` for
# enabled users, or None when the roster is unavailable.
PeopleFn = Callable[[], Mapping[str, Mapping[str, Any]] | None]
_people_fn: PeopleFn | None = None
_lock = threading.Lock()
_dedupe: dict[str, str] = {}

QUIET_PRIORITY = 1
MAX_REVIEW = 100


def configure_ingest(data_dir: Path, *, people_fn: PeopleFn | None = None) -> None:
    global _data_dir, _people_fn
    _data_dir = Path(data_dir)
    _people_fn = people_fn


def _file(name: str) -> Path:
    if _data_dir is None:
        raise RuntimeError("stonepi_notify not configured")
    return _data_dir / name


def _prefs_path() -> Path:
    return _file("prefs.json")


def _dedupe_path() -> Path:
    return _file("dedupe.json")


def _review_path() -> Path:
    return _file("review.json")


# ── Admin approvals (prefs.json) ────────────────────────────────────────────


def normalize_approval(raw: Any) -> dict[str, bool]:
    """``{"approved", "admin_only", "urgent"}``; legacy booleans mean ``approved``."""
    if isinstance(raw, dict):
        return {
            "approved": bool(raw.get("approved")),
            "admin_only": bool(raw.get("admin_only")),
            "urgent": bool(raw.get("urgent")),
        }
    return {"approved": bool(raw), "admin_only": False, "urgent": False}


def load_prefs() -> dict[str, Any]:
    path = _prefs_path()
    prefs: dict[str, Any] = {"events": {}, "outputs_required": False}
    if not path.exists():
        return prefs
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return prefs
    if isinstance(data, dict):
        events = data.get("events") if isinstance(data.get("events"), dict) else {}
        prefs["events"] = {str(k): normalize_approval(v) for k, v in events.items()}
        prefs["outputs_required"] = bool(data.get("outputs_required"))
    return prefs


def save_prefs(prefs: dict[str, Any]) -> dict[str, Any]:
    current = load_prefs()
    if isinstance(prefs.get("events"), dict):
        events = {str(k): normalize_approval(v) for k, v in prefs["events"].items()}
    else:
        events = current["events"]
    if "outputs_required" in prefs:
        outputs_required = bool(prefs.get("outputs_required"))
    else:
        outputs_required = bool(current.get("outputs_required"))
    payload = {"events": events, "outputs_required": outputs_required}
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _clear_review(k for k in events)
    return payload


def event_approval(event_id: str, prefs: dict[str, Any] | None = None) -> dict[str, bool]:
    """Unknown events are not approved."""
    events = (prefs or load_prefs()).get("events") or {}
    return normalize_approval(events.get(str(event_id or "").strip(), False))


# ── "Needs review": unapproved events that arrived with no admin decision ────


def load_needs_review() -> dict[str, dict[str, Any]]:
    path = _review_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _note_needs_review(event: dict[str, Any]) -> None:
    with _lock:
        items = load_needs_review()
        entry = items.get(event["id"]) or {"count": 0}
        entry.update(
            {
                "source": event["source"],
                "title": event["title"][:120],
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "count": int(entry.get("count") or 0) + 1,
            }
        )
        items[event["id"]] = entry
        if len(items) > MAX_REVIEW:
            for old in sorted(items, key=lambda k: items[k].get("last_seen") or "")[: len(items) - MAX_REVIEW]:
                items.pop(old, None)
        path = _review_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _clear_review(event_ids: Iterable[str]) -> None:
    decided = set(event_ids)
    with _lock:
        items = load_needs_review()
        remaining = {k: v for k, v in items.items() if k not in decided}
        if len(remaining) != len(items):
            _review_path().write_text(json.dumps(remaining, indent=2), encoding="utf-8")


# ── Dedupe ──────────────────────────────────────────────────────────────────


def _load_dedupe() -> dict[str, str]:
    global _dedupe
    path = _dedupe_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _dedupe = {str(k): str(v) for k, v in data.items()}
        except (OSError, json.JSONDecodeError):
            pass
    return _dedupe


def _save_dedupe() -> None:
    path = _dedupe_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_dedupe, indent=2), encoding="utf-8")


def _is_duplicate(event: dict[str, Any]) -> bool:
    """Record the event's fingerprint; True when it was already seen."""
    dedupe_key = str(event.get("dedupe_key") or "").strip()
    if not dedupe_key:
        return False
    fingerprint = str(event.get("timestamp") or "") or f"{event.get('title')}|{event.get('summary')}"
    with _lock:
        known = _load_dedupe()
        if known.get(dedupe_key) == fingerprint:
            return True
        known[dedupe_key] = fingerprint
        if len(known) > 500:
            for old in list(known.keys())[:100]:
                known.pop(old, None)
        _save_dedupe()
    return False


# ── Routing ─────────────────────────────────────────────────────────────────


def _severity_priority(severity: str) -> int:
    return {
        "info": 2,
        "success": 3,
        "warning": 4,
        "error": 4,
        "critical": 5,
    }.get(normalize_severity(severity), 3)


def _tags(event: dict[str, Any]) -> str:
    if event.get("severity") == "critical":
        return "rotating_light"
    if event.get("severity") == "warning":
        return "warning"
    if event.get("source") == "pricewatch":
        return "shopping_cart,moneybag"
    if event.get("source") == "newscast":
        return "newspaper"
    return ""


def roster(subs: dict[str, dict[str, Any]]) -> tuple[set[str] | None, set[str]]:
    """``(allowed, admins)`` from Auth; ``allowed`` is None when Auth is unknown.

    Without a roster, anyone subscribed may receive and admins come from the
    snapshot saved on each subscription by the person's own session.
    """
    people = None
    if _people_fn is not None:
        try:
            people = _people_fn()
        except Exception:  # noqa: BLE001
            logger.warning("people_fn failed; using subscription snapshots", exc_info=True)
    if people is None:
        return None, {uid for uid, sub in subs.items() if sub.get("is_admin")}
    allowed = {str(uid) for uid, p in people.items() if p.get("phone_alerts")}
    admins = {str(uid) for uid, p in people.items() if p.get("is_admin")}
    return allowed, admins


def effective_audience(event: dict[str, Any], approval: dict[str, bool]) -> str:
    """Who an event may reach, combining the envelope, catalog and approval.

    Personal stays personal. Otherwise admin wins if the catalog, envelope or
    the admin's approval says so; everything else (including old emitters
    that don't send ``audience``) is household.
    """
    if event.get("audience") == AUDIENCE_PERSONAL:
        return AUDIENCE_PERSONAL
    catalog = event_type(event.get("id") or "")
    if (
        approval.get("admin_only")
        or event.get("audience") == AUDIENCE_ADMIN
        or (catalog is not None and catalog.audience == AUDIENCE_ADMIN)
    ):
        return AUDIENCE_ADMIN
    return AUDIENCE_HOUSEHOLD


def plan_recipients(
    event: dict[str, Any],
    approval: dict[str, bool],
    subs: dict[str, dict[str, Any]],
    *,
    admin_ids: set[str],
    allowed: set[str] | None = None,
    household_topic: str = "",
) -> list[dict[str, Any]]:
    """``[{"kind": "user"|"household", "user", "topic", "quiet_hours"}]``, one per topic.

    ``allowed`` (enabled users with Phone alerts) filters people when known.
    """
    audience = effective_audience(event, approval)
    event_id = event.get("id") or ""

    def wants(uid: str) -> bool:
        if allowed is not None and uid not in allowed:
            return False
        sub = subs.get(uid) or {}
        return bool(sub.get("enabled") and sub.get("topic") and (sub.get("events") or {}).get(event_id))

    if audience == AUDIENCE_PERSONAL:
        owner = str(event.get("user") or "")
        uids = [owner] if owner and wants(owner) else []
        if approval.get("admin_only"):
            uids = [u for u in uids if u in admin_ids]
    elif audience == AUDIENCE_ADMIN:
        uids = [u for u in subs if u in admin_ids and wants(u)]
    else:
        uids = [u for u in subs if wants(u)]

    recipients: list[dict[str, Any]] = []
    seen: set[str] = set()
    for uid in uids:
        sub = subs[uid]
        if sub["topic"] in seen:
            continue
        seen.add(sub["topic"])
        recipients.append(
            {"kind": "user", "user": uid, "topic": sub["topic"], "quiet_hours": sub.get("quiet_hours")}
        )
    if audience == AUDIENCE_HOUSEHOLD and household_topic and household_topic not in seen:
        recipients.append({"kind": "household", "user": None, "topic": household_topic, "quiet_hours": None})
    return recipients


def recipient_priority(
    base: int,
    recipient: dict[str, Any],
    *,
    audience: str,
    approval: dict[str, bool],
    now: datetime | None = None,
) -> tuple[int, bool]:
    """``(priority, silenced)`` — quiet hours drop to priority 1 unless admin + urgent."""
    if not in_quiet_hours(recipient.get("quiet_hours"), now):
        return base, False
    if audience == AUDIENCE_ADMIN and approval.get("urgent"):
        return base, False
    return QUIET_PRIORITY, True


def ingest_event(raw: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Validate, filter by approval, fan out to subscribed topics. Never raises."""
    event = validate_event(raw)
    if not event:
        return {"ok": False, "message": "invalid event"}
    dest = load_destinations().get("ntfy") or {}
    if not dest.get("enabled"):
        append_history(
            channel="ntfy",
            ok=False,
            title=event["title"],
            message="ntfy disabled — event accepted but not delivered",
            source=event["source"],
            event_id=event["id"],
        )
        return {"ok": True, "delivered": False, "message": "accepted (ntfy off)"}

    prefs = load_prefs()
    approval = event_approval(event["id"], prefs)
    if not approval["approved"]:
        if event["id"] not in (prefs.get("events") or {}):
            _note_needs_review(event)
        return {"ok": True, "delivered": False, "message": "not approved"}

    if _is_duplicate(event):
        return {"ok": True, "delivered": False, "message": "deduped"}

    subs = load_subscriptions()
    audience = effective_audience(event, approval)
    allowed, admins = roster(subs)
    recipients = plan_recipients(
        event,
        approval,
        subs,
        admin_ids=admins,
        allowed=allowed,
        household_topic=str(dest.get("household_topic") or ""),
    )
    if not recipients:
        append_history(
            channel="ntfy",
            ok=False,
            title=event["title"],
            message="No one is subscribed to this alert.",
            source=event["source"],
            event_id=event["id"],
            deliveries=[],
        )
        return {"ok": True, "delivered": False, "message": "no recipients", "deliveries": []}

    base = _severity_priority(event.get("severity") or "info")
    tags = _tags(event)
    deliveries: list[dict[str, Any]] = []
    for recipient in recipients:
        priority, silenced = recipient_priority(base, recipient, audience=audience, approval=approval, now=now)
        result = publish_ntfy(
            topic=recipient["topic"],
            title=event["title"],
            body=event.get("summary") or event["title"],
            priority=priority,
            tags=tags,
            click_url=event.get("url"),
            cfg=dest,
        )
        deliveries.append(
            {
                "kind": recipient["kind"],
                "user": recipient["user"],
                "topic": redact_topic(recipient["topic"]),
                "ok": bool(result.get("ok")),
                "silent": silenced,
                "message": str(result.get("message") or "")[:120],
            }
        )
    sent = sum(1 for d in deliveries if d["ok"])
    append_history(
        channel="ntfy",
        ok=sent > 0,
        title=event["title"],
        message=f"Sent to {sent} of {len(deliveries)} recipient(s).",
        source=event["source"],
        event_id=event["id"],
        deliveries=deliveries,
    )
    return {"ok": True, "delivered": sent > 0, "audience": audience, "deliveries": deliveries}
