"""Article text extraction modes and page-furniture cleanup.

Standard keeps everything trafilatura finds (the long-standing behaviour). Strict uses
trafilatura's precision mode and drops known page furniture ("Advertisement", "Loading",
video rails, empty JS date stamps). Strict falls back to Standard when it finds no
article text, so a story is never left empty. Sources opt in per feed.
"""

from __future__ import annotations

import re

import trafilatura
from sqlalchemy.orm import Session

STANDARD = "standard"
STRICT = "strict"
TEXT_CLEANUP_CHOICES = [
    (STANDARD, "Standard"),
    (STRICT, "Strict — drop ads and page furniture"),
]
TEXT_CLEANUP_IDS = {value for value, _label in TEXT_CLEANUP_CHOICES}
TIDY_DONE_KEY = "tidy_page_furniture_v1"

# Whole lines that are page chrome, never article text (compared case-insensitively).
_FURNITURE_LINES = {
    "advertisement",
    "loading",
    "loading...",
    "loading…",
    "latest in videos",
}
# JS-filled timestamps that arrive empty, e.g. "Updated ,first published".
_EMPTY_STAMP_RE = re.compile(r"^(updated\s*,?\s*)?(first published)?$", re.I)


def normalize_text_cleanup(value: str | None) -> str:
    key = (value or "").strip().lower()
    return key if key in TEXT_CLEANUP_IDS else STANDARD


def _is_furniture(line: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if text.lower() in _FURNITURE_LINES:
        return True
    return bool(_EMPTY_STAMP_RE.fullmatch(text))


def tidy_lines(text: str, *, title: str = "") -> str:
    """Remove whole lines of known page furniture, and a leading copy of the headline."""
    lines = [line for line in (text or "").splitlines() if not _is_furniture(line)]
    if title:
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and lines[0].strip() == title.strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def extract_text(html: str, *, strict: bool = False) -> tuple[str, bool]:
    """(text, used_standard_fallback). Standard mode never reports a fallback."""
    standard = lambda: (  # noqa: E731
        trafilatura.extract(html, include_comments=False, include_tables=False) or ""
    ).strip()
    if not strict:
        return standard(), False
    precise = (
        trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    ).strip()
    precise = tidy_lines(precise)
    if precise:
        return precise, False
    return tidy_lines(standard()), True


def tidy_stored_stories(db: Session) -> int:
    """One-off: strip page furniture from stories already stored. Returns stories changed."""
    from app.models import Story

    changed = 0
    for story in db.query(Story).all():
        summary = tidy_lines(story.summary or "", title=story.title or "")
        excerpt = tidy_lines(story.raw_excerpt or "", title=story.title or "")
        if summary != (story.summary or "").strip() or excerpt != (story.raw_excerpt or "").strip():
            # Keep something readable if a story was nothing but furniture.
            story.summary = summary or story.title
            story.raw_excerpt = excerpt
            changed += 1
    db.commit()
    return changed


def run_tidy_once(db: Session) -> int:
    """Run the stored-story tidy-up a single time per install (tracked in settings)."""
    from app.services import settings

    if settings.get_value(db, TIDY_DONE_KEY).strip() == "1":
        return 0
    changed = tidy_stored_stories(db)
    settings.set_value(db, TIDY_DONE_KEY, "1")
    return changed
