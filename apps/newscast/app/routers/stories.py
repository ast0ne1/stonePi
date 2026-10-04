from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import effective_user_id, require_user, session_from_request, session_is_admin
from app.db import get_db
from app.models import Feed, User
from app.services.briefing import current_briefing_payload
from app.services.ingest import feed_debug, snapshot, start_ingest

router = APIRouter(dependencies=[Depends(require_user)])


def _user_id(request: Request) -> int:
    return effective_user_id(session_from_request(request))


def _require_own_feed(db: Session, request: Request, feed_id: int) -> None:
    # Another account's source is reported exactly like a missing one.
    feed = db.get(Feed, feed_id)
    if feed is None or int(feed.user_id or 1) != _user_id(request):
        raise HTTPException(status_code=404, detail="Feed not found")


def may_view_all_status(db: Session, request: Request) -> bool:
    """Admins, and members granted can_view_status, see every source's refresh stats."""
    session = session_from_request(request)
    if session_is_admin(session):
        return True
    row = db.get(User, session.user_id) if session and session.user_id else None
    return bool(row and row.can_view_status)


def scoped_snapshot(db: Session, request: Request) -> dict:
    """Refresh status; last_feed_stats limited to the caller's own sources unless allowed to see all."""
    data = snapshot()
    if may_view_all_status(db, request):
        return data
    uid = _user_id(request)
    own = {row[0] for row in db.query(Feed.id).filter(Feed.user_id == uid).all()}
    data["last_feed_stats"] = [row for row in data.get("last_feed_stats") or [] if row.get("feed_id") in own]
    return data


@router.get("/api/stories")
def list_stories(request: Request, db: Annotated[Session, Depends(get_db)]):
    return current_briefing_payload(db, user_id=_user_id(request))


@router.get("/api/ingest/status")
def ingest_status(request: Request, db: Annotated[Session, Depends(get_db)]):
    return scoped_snapshot(db, request)


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
    session = session_from_request(request)
    if session_is_admin(session):
        return start_ingest(force=True)
    # Members refresh only their own sources; refreshing the household is admin work.
    return start_ingest(force=True, user_id=_user_id(request))
