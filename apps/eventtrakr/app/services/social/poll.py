"""Central Instagram social polling worker."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import SocialAccount, SocialPost, SocialPostMedia, utcnow
from app.services.social import config as social_config
from app.services.social import process as process_service
from app.services.social.providers import get_provider
from app.services.social.providers.brightdata_instagram import BrightDataError

logger = logging.getLogger("eventtrakr.social.poll")

_lock = threading.Lock()
_running = False


def is_running() -> bool:
    return _running


def _account_due(
    account: SocialAccount,
    global_minutes: int,
    now_utc: datetime,
    now_local: datetime,
    hours: tuple[str, str] | None = None,
) -> bool:
    """Due check matching URL sources: Global Discovery interval or per-account custom.

    Interval schedules also keep to the Discovery polling hours (saves Bright Data
    records overnight); a weekly schedule's own days and times are left as set.
    """
    if not account.tracking_enabled:
        return False
    from app.services import settings as settings_service
    from app.services.schedule import is_due_for_config

    global_schedule = {"mode": "interval", "interval_minutes": max(15, int(global_minutes))}
    custom = settings_service.get_source_schedule(account, global_minutes)
    schedule = custom if custom is not None else global_schedule
    if schedule.get("mode") != "weekly" and not social_config.within_active_hours(hours, now_local):
        return False
    return is_due_for_config(schedule, account.last_checked, now_utc, now_local)


def _known_external_ids(db: Session, account_id: int, limit: int = 40) -> list[str]:
    rows = list(
        db.execute(
            select(SocialPost.external_post_id)
            .where(SocialPost.social_account_id == account_id)
            .order_by(SocialPost.id.desc())
            .limit(limit)
        ).scalars()
    )
    return [str(r) for r in rows if r]


def _start_date_mmddyyyy(last_successful: datetime | None) -> str | None:
    if not last_successful:
        return None
    dt = last_successful
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Bright Data start_date is inclusive MM-DD-YYYY; nudge back one day for overlap.
    day = (dt - timedelta(days=1)).astimezone(timezone.utc)
    return day.strftime("%m-%d-%Y")


def poll_account(db: Session, account: SocialAccount, *, force: bool = False) -> dict:
    """Fetch and process new posts for one account. Errors are stored on the account."""
    now = datetime.now(timezone.utc)
    account.last_checked = now
    summary = {
        "account_id": account.id,
        "user_id": account.user_id,
        "username": account.username,
        "fetched": 0,
        "new": 0,
        "processed": 0,
        "error": None,
        "results": [],
    }

    if not social_config.instagram_enabled(db) and not force:
        account.last_error = "Instagram tracking is disabled in Settings → Discovery"
        db.commit()
        summary["error"] = account.last_error
        return summary

    try:
        provider = get_provider(db, account.platform or "instagram")
    except ValueError as e:
        account.last_error = str(e)
        db.commit()
        summary["error"] = str(e)
        return summary

    from app.services import brightdata

    brightdata.ensure_instagram_allowance()
    known = _known_external_ids(db, account.id)
    num_posts = social_config.posts_per_check(db)
    try:
        raw_posts = provider.fetch_recent_posts(
            account.username,
            num_of_posts=num_posts,
            posts_to_not_include=known or None,
            start_date=_start_date_mmddyyyy(account.last_successful_check),
            paced=not force,
        )
    except BrightDataError as e:
        account.last_error = str(e)
        db.commit()
        summary["error"] = str(e)
        logger.warning("Social poll failed for @%s: %s", account.username, e)
        return summary
    except Exception as e:
        account.last_error = f"Provider error: {e}"
        db.commit()
        summary["error"] = account.last_error
        logger.exception("Social poll crashed for @%s", account.username)
        return summary

    summary["fetched"] = len(raw_posts)
    known_set = set(known)
    for raw in raw_posts:
        if raw.external_post_id in known_set:
            continue
        existing = db.execute(
            select(SocialPost).where(
                SocialPost.social_account_id == account.id,
                SocialPost.external_post_id == raw.external_post_id,
            )
        ).scalar_one_or_none()
        if existing:
            continue
        post = SocialPost(
            social_account_id=account.id,
            external_post_id=raw.external_post_id,
            post_url=raw.post_url or "",
            published_at=raw.published_at,
            caption=raw.caption or "",
            media_type=raw.media_type or "",
            hashtags=json.dumps(raw.hashtags or []),
            retrieved_at=utcnow(),
            processing_status="pending",
        )
        db.add(post)
        db.flush()
        for idx, url in enumerate(raw.image_urls or []):
            db.add(
                SocialPostMedia(
                    social_post_id=post.id,
                    media_url=url,
                    media_type="image",
                    position=idx,
                    created_at=utcnow(),
                )
            )
        summary["new"] += 1
        known_set.add(raw.external_post_id)

        try:
            result = process_service.process_post(db, account, post)
            summary["processed"] += 1
            summary["results"].append(result)
        except Exception:
            logger.exception("Failed processing post %s", raw.external_post_id)
            post.processing_status = "error"
            db.commit()

    account.last_error = None
    account.last_successful_check = now
    account.updated_at = utcnow()
    db.commit()
    return summary


def poll_due_accounts(db: Session | None = None) -> dict:
    """Poll all due tracked Instagram accounts. Safe to call from scheduler tick."""
    global _running
    if not _lock.acquire(blocking=False):
        return {"skipped": True, "reason": "already_running"}
    own_session = db is None
    session = db or SessionLocal()
    _running = True
    try:
        if not social_config.instagram_enabled(session):
            return {"skipped": True, "reason": "disabled"}
        now_utc = datetime.now(timezone.utc)
        now_local = datetime.now().astimezone()
        poll_mins = social_config.poll_minutes(session)
        hours = social_config.active_hours(session)
        accounts = list(
            session.execute(
                select(SocialAccount).where(
                    SocialAccount.tracking_enabled == True,  # noqa: E712
                    SocialAccount.platform == "instagram",
                )
            ).scalars()
        )
        # Bright Data is paid: skip people who no longer hold "Instagram & Facebook".
        from app.services import capabilities

        allowed = capabilities.user_ids_with(session, capabilities.USE_SOCIAL, {a.user_id for a in accounts})
        skipped = sum(1 for a in accounts if a.user_id not in allowed)
        accounts = [a for a in accounts if a.user_id in allowed]
        due = [a for a in accounts if _account_due(a, poll_mins, now_utc, now_local, hours)]
        results = []
        for account in due:
            try:
                results.append(poll_account(session, account))
            except Exception:
                logger.exception("poll_account failed for %s", account.id)
        return {
            "checked": len(accounts),
            "skipped_no_permission": skipped,
            "due": len(due),
            "results": results,
            "poll_minutes": poll_mins,
        }
    finally:
        _running = False
        _lock.release()
        if own_session:
            session.close()
