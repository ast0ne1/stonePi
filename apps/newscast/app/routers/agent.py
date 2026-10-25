"""Agent reads for AgentLink (Pebble now, MCP later): one signed internal endpoint.

    POST /api/internal/agent   {"tool": "...", "user": "<platform user id>", "params": {...}}
    → {"ok": true, "payload": {...}} | {"ok": false, "error": "...", "message": "..."}

Signed with stonepi_auth.internal v2 (the body, so the user, is covered); nginx never
routes /news/api/internal/. Answers from the user's own NewsCast: today's Briefing (the
same list and order as their Briefing page) and search. Summaries are the ones stored at
ingest, so no AI runs here.

Both take an optional ``category`` ("sport", "Tech", "world news"), matched loosely against
the user's feed categories, so Pebble can answer "what's in the news in sport?". The briefing
also lists today's categories with counts, so Pebble can ask which one to read.
"""

from __future__ import annotations

import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import _platform_session_secret
from app.db import get_db
from app.models import User

router = APIRouter()

MAX_ITEMS = 8


def _story_row(story, category: str = "") -> dict:
    from app.services.briefing import digest_blurb, format_published

    row = {
        "title": story.title,
        "source": story.source_name or "",
        "summary": digest_blurb(story.summary or "", limit=200),
        "published": format_published(story.published_at or story.created_at),
    }
    if category:
        row["category"] = category
    return row


def _norm(text: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower()).split()
    return " ".join(_singular(w) for w in words)


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _story_categories(db: Session, user: User) -> tuple[dict[str, str], dict[str, str]]:
    """(feed name → category key, category key → label) for this user."""
    from app.services.briefing import _scope_feeds
    from app.services.categories import DEFAULT_CATEGORY, category_labels
    from app.models import Feed

    feeds = {f.name: (f.category or DEFAULT_CATEGORY) for f in _scope_feeds(db.query(Feed), user.id).all()}
    return feeds, category_labels(db)


def _match_category(wanted: str, keys: list[str], labels: dict[str, str]) -> str | None:
    """The category key for "sport" / "sports" / "Tech" / "world news", or None."""
    q = _norm(wanted)
    if not q:
        return None
    for key in keys:
        if q in (_norm(key), _norm(labels.get(key, key))):
            return key
    for key in keys:
        label = _norm(labels.get(key, key))
        if q in label.split() or label.startswith(q) or _norm(key).startswith(q):
            return key
    return None


def _get_news_briefing(db: Session, user: User, params: dict) -> dict:
    from app.services.briefing import current_stories, paper_status

    wanted = str(params.get("category") or "").strip()[:40]
    feeds, labels = _story_categories(db, user)
    stories = [s for s in current_stories(db, day="today", user_id=user.id) if not getattr(s, "saved", False)]
    status = paper_status(db, user_id=user.id)
    counts: dict[str, int] = {}
    for story in stories:
        key = feeds.get(story.source_name, "news")
        counts[key] = counts.get(key, 0) + 1
    out = {
        "paper_ready": bool(status["published"] and not status["empty"]),
        "publishes_at": status["publish_at"],
        "story_count": len(stories),
        "categories": [{"name": labels.get(k, k), "stories": n} for k, n in counts.items()],
    }
    if wanted:
        key = _match_category(wanted, list(counts) or list(labels), labels)
        if key is None:
            out["category_not_found"] = wanted
        else:
            out["category"] = labels.get(key, key)
            stories = [s for s in stories if feeds.get(s.source_name, "news") == key]
            out["category_story_count"] = len(stories)
    out["headlines"] = [_story_row(s, labels.get(feeds.get(s.source_name, "news"), "")) for s in stories[:MAX_ITEMS]]
    return out


def _search_news(db: Session, user: User, params: dict) -> dict:
    from app.services.briefing import search_stories

    query = str(params.get("query") or "").strip()[:120]
    if not query:
        return {"query": "", "total": 0, "stories": []}
    feeds, labels = _story_categories(db, user)
    found = search_stories(db, query, limit=MAX_ITEMS * 4, user_id=user.id)
    out: dict = {"query": query}
    wanted = str(params.get("category") or "").strip()[:40]
    if wanted:
        key = _match_category(wanted, sorted(set(feeds.values())) or list(labels), labels)
        if key is None:
            out["category_not_found"] = wanted
        else:
            out["category"] = labels.get(key, key)
            found = [s for s in found if feeds.get(s.source_name, "news") == key]
    found = found[:MAX_ITEMS]
    out.update(total=len(found), stories=[_story_row(s, labels.get(feeds.get(s.source_name, "news"), "")) for s in found])
    return out


TOOLS = {"get_news_briefing": _get_news_briefing, "search_news": _search_news}


@router.post("/api/internal/agent")
async def agent_read(request: Request, db: Annotated[Session, Depends(get_db)]):
    from stonepi_auth.internal import verify_internal_v2

    body = await request.body()
    if not verify_internal_v2(_platform_session_secret(), "POST", request.url.path, request.headers, body):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad_request", "message": "Body must be JSON"}, status_code=400)
    tool = TOOLS.get(str(data.get("tool") or ""))
    user_id = str(data.get("user") or "").strip()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if tool is None or not user_id:
        return JSONResponse({"ok": False, "error": "bad_request", "message": "Unknown tool or no user"}, status_code=400)
    user = db.query(User).filter(User.auth_user_id == user_id, User.active.is_(True)).first()
    if user is None:
        # Never opened NewsCast: no feeds or Briefing of theirs yet.
        return {"ok": True, "payload": {"note": "No NewsCast account yet", "headlines": [], "stories": []}}
    return {"ok": True, "payload": tool(db, user, params)}
