from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.db import get_db
from app.services.briefing import current_briefing_payload
from app.services.ingest import feed_debug, snapshot, start_ingest

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/api/stories")
def list_stories(db: Annotated[Session, Depends(get_db)]):
    return current_briefing_payload(db)


@router.get("/api/ingest/status")
def ingest_status():
    return snapshot()


@router.get("/api/ingest/debug")
def ingest_debug(db: Annotated[Session, Depends(get_db)], feed_id: int):
    result = feed_debug(db, feed_id)
    if not result.get("ok") and result.get("error") == "Feed not found":
        raise HTTPException(status_code=404, detail="Feed not found")
    return result


@router.post("/api/ingest")
def ingest_now(feed_id: int | None = None):
    return start_ingest(force=True, feed_id=feed_id)
