"""Create / confirm / ignore event discoveries from social posts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventDiscovery,
    EventSocialLink,
    SocialAccount,
    SocialPost,
    utcnow,
)
from app.services.dedupe import compute_event_fingerprint
from app.services.social import config as social_config
from app.services.social.types import DetectionResult

logger = logging.getLogger("eventtrakr.social.discover")


def _reasons_json(detection: DetectionResult) -> str:
    return json.dumps(list(detection.signals or []), ensure_ascii=False)


def create_discovery(
    db: Session,
    *,
    user_id: int,
    account: SocialAccount,
    post: SocialPost,
    detection: DetectionResult,
    status: str,
) -> EventDiscovery:
    row = EventDiscovery(
        user_id=user_id,
        social_post_id=post.id,
        social_account_id=account.id,
        title=detection.title or "Untitled event",
        start_time=detection.start_time,
        location=detection.location or "",
        event_type=detection.event_type or "",
        cost=detection.cost or "",
        extracted_text=(post.combined_text or post.caption or "")[:4000],
        confidence=detection.confidence,
        status=status,
        reasons_json=_reasons_json(detection),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def promote_discovery_to_event(
    db: Session,
    discovery: EventDiscovery,
    *,
    origin: str = "instagram_discovery",
) -> Event | None:
    if not discovery.start_time or not discovery.title:
        return None
    account = db.get(SocialAccount, discovery.social_account_id)
    post = db.get(SocialPost, discovery.social_post_id)
    fingerprint = compute_event_fingerprint(
        discovery.user_id,
        discovery.title,
        discovery.start_time,
        discovery.location or None,
    )
    existing = db.execute(
        select(Event).where(Event.user_id == discovery.user_id, Event.fingerprint == fingerprint)
    ).scalar_one_or_none()
    if existing:
        discovery.event_id = existing.id
        discovery.status = "confirmed"
        discovery.updated_at = utcnow()
        if post:
            _link(db, existing.id, post.id, "discovery")
        return existing

    location = discovery.location or "Unspecified"
    if location == "Unspecified" and account and account.account_type == "venue":
        location = account.display_name or account.username

    event = Event(
        user_id=discovery.user_id,
        source_id=None,
        fingerprint=fingerprint,
        title=discovery.title[:500],
        description=(discovery.extracted_text or "")[:2000],
        start_time=discovery.start_time,
        end_time=discovery.end_time,
        location=location[:300],
        cost=(discovery.cost or "Unspecified")[:100],
        category=(discovery.event_type or "General")[:60] or "General",
        url=(post.post_url if post else "")[:1000],
        origin=origin,
    )
    db.add(event)
    db.flush()
    discovery.event_id = event.id
    discovery.status = "confirmed" if origin == "instagram_discovery" else discovery.status
    if discovery.status == "discovered":
        pass  # keep auto-discovered status until user reviews? Spec: discovered = auto created
    discovery.updated_at = utcnow()
    if post:
        _link(db, event.id, post.id, "discovery")
    return event


def auto_or_candidate(
    db: Session,
    *,
    user_id: int,
    account: SocialAccount,
    post: SocialPost,
    detection: DetectionResult,
) -> tuple[EventDiscovery | None, Event | None]:
    """Create discovery row; optionally auto-promote high confidence."""
    if not social_config.auto_discover(db):
        return None, None

    confidence = detection.confidence
    if confidence == "high" and social_config.auto_create_high(db) and detection.start_time:
        disc = create_discovery(
            db,
            user_id=user_id,
            account=account,
            post=post,
            detection=detection,
            status="discovered",
        )
        event = promote_discovery_to_event(db, disc, origin="instagram_discovery")
        disc.status = "discovered"
        disc.updated_at = utcnow()
        account.events_discovered = (account.events_discovered or 0) + 1
        return disc, event

    if confidence in ("high", "medium") and social_config.candidate_review(db):
        disc = create_discovery(
            db,
            user_id=user_id,
            account=account,
            post=post,
            detection=detection,
            status="candidate",
        )
        return disc, None

    if confidence == "high" and detection.start_time:
        # Auto-create disabled but still record as candidate if review on, else discovered pending
        status = "candidate" if social_config.candidate_review(db) else "discovered"
        disc = create_discovery(
            db,
            user_id=user_id,
            account=account,
            post=post,
            detection=detection,
            status=status,
        )
        return disc, None

    return None, None


def confirm_discovery(db: Session, discovery_id: int, user_id: int) -> Event | None:
    disc = db.get(EventDiscovery, discovery_id)
    if not disc or disc.user_id != user_id:
        return None
    if disc.status in ("ignored", "expired"):
        return None
    event = promote_discovery_to_event(db, disc, origin="instagram_discovery")
    disc.status = "confirmed"
    disc.updated_at = utcnow()
    db.commit()
    return event


def ignore_discovery(db: Session, discovery_id: int, user_id: int) -> bool:
    disc = db.get(EventDiscovery, discovery_id)
    if not disc or disc.user_id != user_id:
        return False
    disc.status = "ignored"
    disc.updated_at = utcnow()
    db.commit()
    return True


def _link(db: Session, event_id: int, social_post_id: int, kind: str) -> None:
    existing = db.execute(
        select(EventSocialLink).where(
            EventSocialLink.event_id == event_id,
            EventSocialLink.social_post_id == social_post_id,
        )
    ).scalar_one_or_none()
    if existing:
        return
    db.add(
        EventSocialLink(
            event_id=event_id,
            social_post_id=social_post_id,
            link_kind=kind,
            created_at=utcnow(),
        )
    )


def expire_old_candidates(db: Session, *, now: datetime | None = None) -> int:
    now_utc = now or datetime.now(timezone.utc)
    rows = list(
        db.execute(
            select(EventDiscovery).where(
                EventDiscovery.status.in_(("candidate", "discovered")),
                EventDiscovery.start_time.is_not(None),
                EventDiscovery.start_time < now_utc,
            )
        ).scalars()
    )
    for row in rows:
        row.status = "expired"
        row.updated_at = utcnow()
    if rows:
        db.commit()
    return len(rows)
