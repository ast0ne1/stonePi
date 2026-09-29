"""Rule-based event detection from SourceContent."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.services.social.types import DetectionResult, SourceContent

EVENT_TERMS = [
    "live",
    "concert",
    "gig",
    "dj",
    "screening",
    "quiz",
    "tournament",
    "exhibition",
    "market",
    "festival",
    "party",
    "performance",
    "workshop",
    "special event",
    "doors",
    "kick-off",
    "kickoff",
    "show",
    "open mic",
    "comedy",
    "theatre",
    "theater",
]

TICKET_TERMS = [
    "tickets",
    "book now",
    "reservations",
    "free entry",
    "free entry",
    "limited spaces",
    "doors open",
    "get tickets",
    "rsvp",
]

CANCEL_TERMS = [
    "cancelled",
    "canceled",
    "postponed",
    "rescheduled",
    "unfortunately not happening",
    "new date",
    "called off",
]

WEEKDAY_WORDS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "tonight": None,
    "tomorrow": None,
}

TIME_RE = re.compile(
    r"\b(?:"
    r"(?:doors?\s*)?(?:at\s*)?"
    r"(?:([01]?\d|2[0-3])[:.]([0-5]\d)\s*(?:hrs?|h)?|"
    r"([1-9]|1[0-2])\s*([:.]\s*[0-5]\d)?\s*(am|pm)|"
    r"([01]?\d|2[0-3])\s*h)"
    r")\b",
    re.I,
)

DATE_HINT_RE = re.compile(
    r"\b("
    r"tonight|tomorrow|this\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+\d{4})?|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}"
    r"(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?"
    r")\b",
    re.I,
)


def _find_terms(text: str, terms: list[str]) -> list[str]:
    lower = text.lower()
    found = []
    for term in terms:
        if term in lower:
            found.append(term)
    return found


def _parse_relative_date(text: str, published_at: datetime | None) -> datetime | None:
    base = published_at or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    lower = text.lower()
    if "tonight" in lower:
        return base.replace(hour=20, minute=0, second=0, microsecond=0)
    if "tomorrow" in lower:
        day = (base + timedelta(days=1)).replace(hour=20, minute=0, second=0, microsecond=0)
        return day
    for name, weekday in WEEKDAY_WORDS.items():
        if weekday is None:
            continue
        if re.search(rf"\b(?:this\s+)?{name}\b", lower):
            # Next occurrence of weekday (including today if still ahead)
            days_ahead = (weekday - base.weekday()) % 7
            target = (base + timedelta(days=days_ahead)).replace(hour=20, minute=0, second=0, microsecond=0)
            return target
    return None


def _parse_date_with_dateparser(text: str, published_at: datetime | None) -> datetime | None:
    try:
        import dateparser
    except ImportError:
        return None
    settings = {
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": "UTC",
    }
    if published_at:
        settings["RELATIVE_BASE"] = published_at
    # Prefer looking at date-like snippets rather than the whole caption
    snippets = DATE_HINT_RE.findall(text) or [text[:400]]
    for snippet in snippets[:5]:
        chunk = snippet if isinstance(snippet, str) else " ".join(snippet)
        dt = dateparser.parse(chunk, settings=settings)
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    dt = dateparser.parse(text[:500], settings=settings)
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_time(text: str) -> tuple[int, int] | None:
    m = TIME_RE.search(text)
    if not m:
        return None
    if m.group(1) is not None:
        return int(m.group(1)), int(m.group(2))
    if m.group(3) is not None:
        hour = int(m.group(3))
        minute = 0
        if m.group(4):
            minute = int(re.sub(r"\D", "", m.group(4)) or "0")
        ampm = (m.group(5) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        return hour, minute
    if m.group(6) is not None:
        return int(m.group(6)), 0
    return None


def _guess_title(text: str, event_terms: list[str]) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Prefer a short uppercase-ish line that isn't just emoji
    for ln in lines[:8]:
        letters = re.sub(r"[^A-Za-z]", "", ln)
        if len(letters) >= 4 and len(ln) <= 80:
            if letters.isupper() or any(t in ln.lower() for t in event_terms):
                return ln[:200]
    for ln in lines[:5]:
        if len(ln) >= 4 and len(ln) <= 120 and not ln.startswith("http"):
            return ln[:200]
    # Fallback: first non-empty sentence fragment
    plain = re.sub(r"\s+", " ", text).strip()
    return plain[:80] if plain else "Untitled event"


def _guess_location(text: str, account_type: str, account_username: str) -> str:
    # "@venue at The Globe" style
    m = re.search(r"\bat\s+([A-Z][\w'&.\-\s]{2,60})", text)
    if m:
        return m.group(1).strip()[:200]
    m = re.search(r"\b(?:venue|location)\s*[:\-]\s*(.+)", text, re.I)
    if m:
        return m.group(1).strip().split("\n")[0][:200]
    if account_type in ("venue", "festival") and account_username:
        return account_username.replace("_", " ").title()
    return ""


def _guess_cost(text: str) -> str:
    lower = text.lower()
    if "free entry" in lower or "free admission" in lower or re.search(r"\bfree\b", lower):
        return "Free"
    m = re.search(r"(?:£|€|\$|dkk|kr)\s?\d+[\d.,]*", text, re.I)
    if m:
        return m.group(0)
    return ""


def detect_event(content: SourceContent) -> DetectionResult:
    text = (content.combined_text or content.caption or content.ocr_text or "").strip()
    if not text:
        return DetectionResult(is_event_like=False, confidence="none", score=0.0)

    signals: list[str] = []
    score = 0.0

    event_hits = _find_terms(text, EVENT_TERMS)
    if event_hits:
        signals.append(f"Event terms: {', '.join(event_hits[:4])}")
        score += 1.5 + 0.3 * min(3, len(event_hits))

    ticket_hits = _find_terms(text, TICKET_TERMS)
    if ticket_hits:
        signals.append(f"Ticket signals: {', '.join(ticket_hits[:3])}")
        score += 1.2

    has_date_hint = bool(DATE_HINT_RE.search(text))
    start = _parse_relative_date(text, content.published_at)
    if start is None:
        start = _parse_date_with_dateparser(text, content.published_at)
    if start or has_date_hint:
        signals.append("Date detected")
        score += 2.0 if start else 0.8

    time_parts = _parse_time(text)
    if time_parts:
        signals.append(f"Time detected ({time_parts[0]:02d}:{time_parts[1]:02d})")
        score += 1.8
        if start:
            start = start.replace(hour=time_parts[0], minute=time_parts[1], second=0, microsecond=0)
        elif content.published_at:
            base = content.published_at
            if base.tzinfo is None:
                base = base.replace(tzinfo=timezone.utc)
            start = base.replace(hour=time_parts[0], minute=time_parts[1], second=0, microsecond=0)

    cancel_hits = _find_terms(text, CANCEL_TERMS)
    is_cancellation = any(t in ("cancelled", "canceled", "called off") for t in cancel_hits)
    is_postponement = any(t in ("postponed", "rescheduled", "new date") for t in cancel_hits)
    change_hints = list(cancel_hits)
    if cancel_hits:
        signals.append(f"Change language: {', '.join(cancel_hits[:3])}")
        score += 0.5

    if content.account_type in ("venue", "organiser", "festival", "artist"):
        signals.append(f"Account type: {content.account_type}")
        score += 0.6 if content.account_type != "other" else 0.0

    if content.hashtags:
        signals.append(f"Hashtags: {len(content.hashtags)}")
        score += min(0.8, 0.15 * len(content.hashtags))

    title = _guess_title(text, event_hits)
    location = _guess_location(text, content.account_type, content.account_username)
    if location:
        signals.append(f"Venue/location: {location}")
        score += 1.0
    cost = _guess_cost(text)
    event_type = event_hits[0].title() if event_hits else ""

    # Named entity-ish: capitalized multi-word after Live/DJ
    if re.search(r"\b(?:live|dj|featuring|with)\s+[A-Z][\w'.\-]+", text):
        signals.append("Named performer/event")
        score += 1.0

    # Confidence bands
    has_core = bool(start) and bool(time_parts) and bool(title) and (bool(location) or content.account_type == "venue")
    has_partial = (bool(start) or bool(time_parts)) and (bool(event_hits) or bool(ticket_hits))

    if score < 2.0 and not has_partial:
        confidence = "none"
        is_event_like = False
    elif has_core and score >= 5.0:
        confidence = "high"
        is_event_like = True
    elif has_partial and score >= 3.0:
        confidence = "medium"
        is_event_like = True
    elif score >= 2.0:
        confidence = "low"
        is_event_like = True
    else:
        confidence = "none"
        is_event_like = False

    return DetectionResult(
        is_event_like=is_event_like,
        confidence=confidence,
        score=score,
        signals=signals,
        title=title if is_event_like else "",
        start_time=start if is_event_like else None,
        location=location,
        event_type=event_type,
        cost=cost,
        is_cancellation=is_cancellation,
        is_postponement=is_postponement,
        change_hints=change_hints,
    )
