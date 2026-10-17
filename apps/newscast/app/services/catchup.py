"""AI catch-up (backlog U18b): redo stories that used the non-AI path while AI was off or down.

Runs after each full refresh and once when AI is switched on in NewsCast. Rules:

1. **Only stories not yet in a published paper.** A story saved before its user's latest
   paper was written may be in it, and papers (and their EPUB) are frozen, so it is left
   alone. Web cards update as stories are redone.
2. **Only tasks marked "fallback"** in the story's AI record (U1), and only tasks NewsCast
   uses AI for now. Opted-out sources are never touched. Translation is not redone: the
   original text isn't kept, and the Google translation is already usable.
3. **Capped per run** (``CATCHUP_LIMIT`` stories, most important and newest first), several
   stories per request. A 429 or quota error stops the run and leaves that model cooling
   in the shared health state rather than moving down the order.
4. **Free models only** (Free in Dashboard → Settings → AI); new stories still use the
   full order.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from threading import Thread

from sqlalchemy.orm import Session

from app.models import Feed, Story
from app.services import ai as ai_mod

logger = logging.getLogger("newscast.catchup")

CATCHUP_LIMIT = 30
BATCH_SIZE = 5
LOOKBACK = timedelta(days=2)
_EXCERPT_CHARS = 2500


def _aware(when: datetime | None) -> datetime | None:
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def last_paper_written(user_id: int, today: date | None = None) -> datetime | None:
    """When the user's newest published (non-empty) paper was written, or None."""
    from app.services.briefing import dated_briefing_path, frozen_paper_is_empty

    today = today or datetime.now().date()
    for back in range(0, 3):
        day = today - timedelta(days=back)
        for suffix in ("epub", "txt"):
            path = dated_briefing_path(day, suffix, user_id=user_id)
            if path.exists() and not frozen_paper_is_empty(user_id=user_id, day=day):
                return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return None


def pending(db: Session, ai, *, limit: int = CATCHUP_LIMIT, now: datetime | None = None) -> list[tuple[Story, list[str]]]:
    """Stories to redo and which tasks, most important and newest first."""
    tasks = [task for task in ("summary", "importance") if ai.uses(task)]
    if not tasks:
        return []
    now = now or datetime.now(timezone.utc)
    rows = (
        db.query(Story)
        .filter(Story.ai_record.is_not(None))
        .filter(Story.ai_record.like('%"fallback"%'))
        .filter(Story.created_at >= now - LOOKBACK)
        .all()
    )
    summarise = {(f.user_id, f.name): bool(f.summarize is not False) for f in db.query(Feed).all()}
    papers: dict[int, datetime | None] = {}
    picked: list[tuple[Story, list[str]]] = []
    for story in rows:
        uid = int(story.user_id or 1)
        if uid not in papers:
            papers[uid] = last_paper_written(uid)
        written = papers[uid]
        created = _aware(story.created_at)
        if written is not None and created is not None and created <= written:
            continue
        record = ai_mod.load_record(story)
        todo = [t for t in tasks if (record.get(t) or {}).get("by") == ai_mod.BY_FALLBACK]
        if "summary" in todo and not summarise.get((uid, story.source_name), True):
            todo.remove("summary")  # the source has stopped summarising since
        if todo:
            picked.append((story, todo))
    picked.sort(key=lambda row: (row[0].importance or 0, _aware(row[0].published_at or row[0].created_at)), reverse=True)
    return picked[:limit]


def _stories_block(stories: list[Story]) -> str:
    parts = []
    for n, story in enumerate(stories, 1):
        text = (story.raw_excerpt or story.summary or story.title or "")[:_EXCERPT_CHARS]
        parts.append(f"### Story {n}\nOutlet: {story.source_name}\nHeadline: {story.title}\n\nSource text:\n{text}")
    return "\n\n".join(parts)


def _json_list(text: str, n: int) -> list | None:
    match = re.search(r"\[.*\]", text or "", re.S)
    if not match:
        return None
    try:
        items = json.loads(match.group(0))
    except ValueError:
        return None
    return items if isinstance(items, list) and len(items) == n else None


def _ask(function: str, system: str, prompt: str, max_tokens: int):
    from stonepi_ai import complete

    return complete(
        function,
        system=system,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=0.2,
        free_only=True,
        stop_on_rate_limit=True,
        alert=False,
    )


def _redo_summaries(stories: list[Story]) -> int:
    from app.services.summarize import SYSTEM_PROMPT, clean_summary

    system = (
        SYSTEM_PROMPT
        + " You get several stories, each starting with '### Story N'. Reply with only a JSON array of"
        " strings: one summary per story, in the same order."
    )
    result = _ask(ai_mod.FN_SUMMARY, system, _stories_block(stories), 230 * len(stories) + 60)
    items = _json_list(result.text, len(stories))
    if items is None:
        raise ValueError("the model did not reply with one summary per story")
    done = 0
    for story, text in zip(stories, items):
        cleaned = clean_summary(str(text or ""), title=story.title, excerpt=story.raw_excerpt or "")
        if not str(text or "").strip() or not cleaned:
            continue
        story.summary = cleaned
        story.ai_record = ai_mod.dump_record({**ai_mod.load_record(story), "summary": ai_mod.by_ai(result)})
        done += 1
    return done


def _redo_importance(stories: list[Story]) -> int:
    from app.services.importance import IMPORTANCE_SYSTEM, clamp_importance

    system = (
        IMPORTANCE_SYSTEM.replace("Reply with only an integer from 1 to 5.", "")
        + "You get several stories, each starting with '### Story N'. Rate each for a daily newspaper"
        " briefing: 1=ignore, 2=interesting, 3=worth reading, 4=important, 5=major story. Reply with"
        " only a JSON array of integers, one per story, in the same order."
    )
    result = _ask(ai_mod.FN_IMPORTANCE, system, _stories_block(stories), 12 * len(stories) + 40)
    items = _json_list(result.text, len(stories))
    if items is None:
        raise ValueError("the model did not reply with one score per story")
    done = 0
    for story, value in zip(stories, items):
        try:
            score = clamp_importance(int(value))
        except (TypeError, ValueError):
            continue
        story.importance = score
        story.ai_record = ai_mod.dump_record({**ai_mod.load_record(story), "importance": ai_mod.by_ai(result)})
        done += 1
    return done


def run_catchup(db: Session, ai=None, *, limit: int = CATCHUP_LIMIT) -> int:
    """Redo what it can within the cap; returns how many stories got an AI result."""
    from stonepi_ai import AIUnavailable

    ai = ai or ai_mod.ai_settings(db)
    if not ai.enabled:
        return 0
    rows = pending(db, ai, limit=limit)
    if not rows:
        return 0
    redone: set[int] = set()
    for task, redo in (("summary", _redo_summaries), ("importance", _redo_importance)):
        batch_all = [story for story, todo in rows if task in todo]
        for start in range(0, len(batch_all), BATCH_SIZE):
            batch = batch_all[start : start + BATCH_SIZE]
            try:
                if redo(batch):
                    redone.update(story.id for story in batch)
                db.commit()
            except AIUnavailable as exc:
                # Rate limited (that model now cools for every app), no Free model, or the
                # shared allowance is used up: try again on a later refresh.
                logger.info("AI catch-up stopped: %s", exc)
                db.commit()
                return len(redone)
            except ValueError as exc:
                logger.info("AI catch-up stopped: %s", exc)
                db.rollback()
                return len(redone)
    return len(redone)


def start_catchup() -> bool:
    """Run a catch-up in the background (AI just switched on); skipped while a refresh runs,
    which does its own catch-up at the end."""
    from app.db import SessionLocal
    from app.services import ingest

    if not ingest._lock.acquire(blocking=False):
        return False

    def worker() -> None:
        db = SessionLocal()
        try:
            run_catchup(db)
        except Exception:  # noqa: BLE001
            logger.exception("AI catch-up failed")
        finally:
            db.close()
            ingest._lock.release()

    Thread(target=worker, daemon=True, name="newscast-ai-catchup").start()
    return True
