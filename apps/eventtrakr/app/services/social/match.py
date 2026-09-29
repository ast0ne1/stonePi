"""Match detected social content against existing EventTrakr events."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event
from app.services.dedupe import normalize_string
from app.services.social.types import DetectionResult, MatchResult, SourceContent


def _token_overlap(a: str, b: str) -> float:
    ta = set(normalize_string(a).split())
    tb = set(normalize_string(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def match_existing_events(
    db: Session,
    user_id: int,
    content: SourceContent,
    detection: DetectionResult,
    *,
    now: datetime | None = None,
) -> MatchResult:
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    horizon_start = now_utc - timedelta(days=2)
    horizon_end = now_utc + timedelta(days=60)
    events = list(
        db.execute(
            select(Event).where(
                Event.user_id == user_id,
                Event.start_time >= horizon_start,
                Event.start_time <= horizon_end,
            )
        ).scalars()
    )
    if not events:
        return MatchResult(band="none", score=0.0)

    best: MatchResult = MatchResult(band="none", score=0.0)
    det_title = detection.title or ""
    det_loc = detection.location or ""
    det_start = detection.start_time

    for event in events:
        score = 0.0
        reasons: list[str] = []

        title_overlap = _token_overlap(det_title, event.title or "")
        if title_overlap >= 0.6:
            score += 4.0
            reasons.append("Exact/strong title match")
        elif title_overlap >= 0.35:
            score += 2.0
            reasons.append("Partial title match")

        # Caption/combined text contains event title tokens
        combined = content.combined_text or ""
        if event.title and normalize_string(event.title) and normalize_string(event.title) in normalize_string(combined):
            score += 3.5
            reasons.append("Event name in post text")

        loc_overlap = _token_overlap(det_loc or content.account_username, event.location or "")
        if loc_overlap >= 0.5 or (
            content.account_type == "venue"
            and content.account_username
            and normalize_string(content.account_username) in normalize_string(event.location or "")
        ):
            score += 2.5
            reasons.append("Venue match")

        if det_start and event.start_time:
            ev_start = event.start_time
            if ev_start.tzinfo is None:
                ev_start = ev_start.replace(tzinfo=timezone.utc)
            if det_start.date() == ev_start.astimezone(timezone.utc).date():
                score += 2.5
                reasons.append("Date match")
                # Time within 90 minutes
                delta = abs((det_start - ev_start).total_seconds())
                if delta <= 90 * 60:
                    score += 2.0
                    reasons.append("Time match")

        if event.url and event.url.strip() and event.url.strip() in (content.post_url or ""):
            score += 5.0
            reasons.append("Event URL match")

        for tag in content.hashtags:
            if tag and normalize_string(tag) in normalize_string(event.title or ""):
                score += 1.0
                reasons.append(f"Hashtag #{tag}")
                break

        if score > best.score:
            if score >= 7.0:
                band = "strong"
            elif score >= 4.5:
                band = "likely"
            elif score >= 2.5:
                band = "possible"
            else:
                band = "none"
            best = MatchResult(band=band, score=score, event_id=event.id if band != "none" else None, reasons=reasons)

    return best


def extract_time_change(text: str) -> tuple[int, int] | None:
    m = re.search(
        r"(?:now|instead|doors?\s+(?:now\s+)?(?:open\s+)?(?:at\s+)?)"
        r"(\d{1,2})[:.](\d{2})\s*(am|pm)?",
        text,
        re.I,
    )
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2))
    ampm = (m.group(3) or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return hour, minute
