from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import effective_user_id, require_admin, session_from_request
from app.db import get_db
from app.models import Feed
from app.services.briefing import current_briefing_payload
from app.services.ingest import feed_debug, snapshot, start_ingest

router = APIRouter(dependencies=[Depends(require_admin)])


def _user_id(request: Request) -> int:
    return effective_user_id(session_from_request(request))


def _require_own_feed(db: Session, request: Request, feed_id: int) -> None:
    # Another account's source is reported exactly like a missing one.
    feed = db.get(Feed, feed_id)
    if feed is None or int(feed.user_id or 1) != _user_id(request):
        raise HTTPException(status_code=404, detail="Feed not found")


@router.get("/api/stories")
def list_stories(request: Request, db: Annotated[Session, Depends(get_db)]):
    return current_briefing_payload(db, user_id=_user_id(request))


@router.get("/api/ingest/status")
def ingest_status():
    return snapshot()


@router.get("/api/ingest/debug")
def ingest_debug(request: Request, db: Annotated[Session, Depends(get_db)], feed_id: int):
    _require_own_feed(db, request, feed_id)
    result = feed_debug(db, feed_id)
    if not result.get("ok") and result.get("error") == "Feed not found":
        raise HTTPException(status_code=404, detail="Feed not found")
    return result


@router.post("/api/ingest")
def ingest_now(request: Request, db: Annotated[Session, Depends(get_db)], feed_id: int | None = None):
    if feed_id is not None:
        _require_own_feed(db, request, feed_id)
    return start_ingest(force=True, feed_id=feed_id)
