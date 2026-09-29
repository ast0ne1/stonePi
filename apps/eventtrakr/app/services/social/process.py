"""Process a single social post through OCR → detect → match → discover/update."""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, EventSocialLink, EventUpdate, SocialAccount, SocialPost, SocialPostMedia, utcnow
from app.services.social import config as social_config
from app.services.social import detect as detect_service
from app.services.social import discover as discover_service
from app.services.social import match as match_service
from app.services.social import ocr as ocr_service
from app.services.social.types import SourceContent

logger = logging.getLogger("eventtrakr.social.process")


def process_post(db: Session, account: SocialAccount, post: SocialPost) -> dict:
    """Run the full pipeline for one post. Returns a result summary dict."""
    if post.processing_status in ("processed", "ignored", "matched", "discovered"):
        return {"status": post.processing_status, "skipped": True}

    post.processing_status = "processing"
    db.flush()

    ocr_text = ""
    ocr_failures = 0
    image_urls = [
        m.media_url
        for m in db.execute(
            select(SocialPostMedia).where(SocialPostMedia.social_post_id == post.id).order_by(SocialPostMedia.position)
        ).scalars()
    ]

    if social_config.ocr_enabled(db) and image_urls:
        ocr_text, ocr_failures = ocr_service.download_and_ocr(image_urls)
        if ocr_failures:
            account.ocr_failures = (account.ocr_failures or 0) + ocr_failures

    caption = (post.caption or "").strip()
    post.caption_text = caption
    post.ocr_text = ocr_text or ""
    combined = "\n\n".join(p for p in (caption, ocr_text) if p).strip()
    post.combined_text = combined

    hashtags: list[str] = []
    if post.hashtags:
        try:
            parsed = json.loads(post.hashtags)
            if isinstance(parsed, list):
                hashtags = [str(h) for h in parsed]
        except (TypeError, json.JSONDecodeError):
            hashtags = [h.strip() for h in post.hashtags.split(",") if h.strip()]

    content = SourceContent(
        platform=account.platform or "instagram",
        account_username=account.username,
        account_type=account.account_type or "other",
        post_url=post.post_url or "",
        published_at=post.published_at,
        caption=caption,
        ocr_text=ocr_text or "",
        combined_text=combined,
        hashtags=hashtags,
        image_count=len(image_urls),
    )

    detection = detect_service.detect_event(content)
    account.posts_processed = (account.posts_processed or 0) + 1

    if not detection.is_event_like or detection.confidence == "none":
        post.processing_status = "ignored"
        account.posts_ignored = (account.posts_ignored or 0) + 1
        db.commit()
        return {"status": "ignored", "confidence": detection.confidence, "signals": detection.signals}

    match = match_service.match_existing_events(db, account.user_id, content, detection)

    if match.band == "strong" and match.event_id:
        event = db.get(Event, match.event_id)
        result = _enrich_event(db, account, post, event, detection, content, match.reasons)
        post.processing_status = "matched"
        account.events_matched = (account.events_matched or 0) + 1
        db.commit()
        return result

    # No strong match → discovery path
    disc, event = discover_service.auto_or_candidate(
        db,
        user_id=account.user_id,
        account=account,
        post=post,
        detection=detection,
    )
    if disc is None and event is None:
        post.processing_status = "ignored"
        account.posts_ignored = (account.posts_ignored or 0) + 1
        db.commit()
        return {"status": "ignored", "confidence": detection.confidence, "reason": "low_or_disabled"}

    post.processing_status = "discovered" if event else "candidate"
    db.commit()

    notify_payload = {
        "status": post.processing_status,
        "confidence": detection.confidence,
        "discovery_id": disc.id if disc else None,
        "event_id": event.id if event else None,
        "signals": detection.signals,
        "title": detection.title,
        "notify": bool(event or (disc and disc.status == "candidate")),
        "notify_kind": "discovered" if event else "candidate",
    }
    return notify_payload


def _enrich_event(
    db: Session,
    account: SocialAccount,
    post: SocialPost,
    event: Event | None,
    detection,
    content: SourceContent,
    reasons: list[str],
) -> dict:
    if not event:
        return {"status": "matched", "event_id": None}

    existing_link = db.execute(
        select(EventSocialLink).where(
            EventSocialLink.event_id == event.id,
            EventSocialLink.social_post_id == post.id,
        )
    ).scalar_one_or_none()
    if not existing_link:
        db.add(
            EventSocialLink(
                event_id=event.id,
                social_post_id=post.id,
                link_kind="update" if detection.change_hints else "enrichment",
                created_at=utcnow(),
            )
        )

    update_kind = "info"
    priority = "normal"
    summary = f"New Instagram update from @{account.username}"
    if detection.is_cancellation:
        update_kind = "cancellation"
        priority = "high"
        summary = f"Possible cancellation noted on Instagram (@{account.username})"
        event.is_cancelled = True
    elif detection.is_postponement:
        update_kind = "postponement"
        priority = "high"
        summary = f"Possible postponement/reschedule on Instagram (@{account.username})"
    else:
        time_change = match_service.extract_time_change(content.combined_text or "")
        if time_change and event.start_time:
            hour, minute = time_change
            new_start = event.start_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if new_start != event.start_time:
                summary = f"Time change on Instagram: now {hour:02d}:{minute:02d} (@{account.username})"
                update_kind = "time_change"
                priority = "high"
                event.start_time = new_start
                event.origin = event.origin or "instagram_update"

    snippet = (post.caption or post.combined_text or "")[:280]
    if snippet:
        summary = f"{summary}\n“{snippet}”"

    db.add(
        EventUpdate(
            event_id=event.id,
            social_post_id=post.id,
            update_kind=update_kind,
            summary=summary[:2000],
            priority=priority,
            created_at=utcnow(),
        )
    )

    return {
        "status": "matched",
        "event_id": event.id,
        "update_kind": update_kind,
        "priority": priority,
        "summary": summary,
        "reasons": reasons,
        "notify": priority == "high" or update_kind != "info",
        "notify_kind": "cancelled" if update_kind == "cancellation" else "updated",
        "title": event.title,
    }
