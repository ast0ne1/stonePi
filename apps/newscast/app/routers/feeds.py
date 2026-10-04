from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import (
    SessionUser,
    effective_user_id,
    may_add_custom_sources,
    require_user,
    session_from_request,
    session_is_admin,
)
from app.db import get_db
from app.models import Feed
from app.services.catalog import find_catalog_item, grouped_catalog

router = APIRouter(dependencies=[Depends(require_user)])


class FeedCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(default="", max_length=1000)
    homepage_url: str = Field(default="", max_length=1000)
    rss_url: str = Field(default="", max_length=1000)
    category: str = "news"
    type: str = "auto"
    enabled: bool = True


class FeedUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    homepage_url: str | None = None
    rss_url: str | None = None
    category: str | None = None
    enabled: bool | None = None
    type: str | None = None
    schedule_mode: str | None = None
    interval_minutes: int | None = None
    summarize: bool | None = None
    translate: bool | None = None


def _feed_dict(feed: Feed) -> dict:
    return {
        "id": feed.id,
        "catalog_id": feed.catalog_id,
        "name": feed.name,
        "url": feed.url,
        "homepage_url": getattr(feed, "homepage_url", None) or None,
        "rss_url": getattr(feed, "rss_url", None) or None,
        "enabled": feed.enabled,
        "type": feed.type,
        "category": feed.category,
        "last_fetched_at": feed.last_fetched_at.isoformat() if feed.last_fetched_at else None,
        "last_error": feed.last_error,
        "schedule_mode": getattr(feed, "schedule_mode", None) or "global",
        "interval_minutes": getattr(feed, "interval_minutes", None),
        "summarize": bool(getattr(feed, "summarize", True)),
        "translate": bool(getattr(feed, "translate", False)),
        "translate_provider": getattr(feed, "translate_provider", None) or "global",
    }


def _caller(request: Request) -> SessionUser | None:
    return session_from_request(request)


def _visible_feeds(db: Session, session: SessionUser | None):
    """Admins see every household feed; members only their own."""
    query = db.query(Feed)
    if not session_is_admin(session):
        query = query.filter(Feed.user_id == effective_user_id(session))
    return query


def _feed_for_caller(db: Session, session: SessionUser | None, feed_id: int) -> Feed:
    # Another account's source is reported exactly like a missing one.
    feed = db.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")
    if not session_is_admin(session) and int(feed.user_id or 1) != effective_user_id(session):
        raise HTTPException(status_code=404, detail="Feed not found")
    return feed


def _allowed_urls(feed: Feed) -> set[str]:
    """URLs a feed may keep without can_add_custom_sources: its own and its catalog entry's."""
    from app.services.catalog import catalog_homepage_url, catalog_rss_url
    from app.services.feed_urls import clean_http_url

    allowed = {
        clean_http_url(feed.url),
        clean_http_url(getattr(feed, "homepage_url", None)),
        clean_http_url(getattr(feed, "rss_url", None)),
    }
    item = find_catalog_item(feed.catalog_id) if feed.catalog_id else None
    if item is not None:
        allowed |= {
            clean_http_url(item.get("url")),
            clean_http_url(catalog_homepage_url(item)),
            clean_http_url(catalog_rss_url(item)),
        }
    allowed.discard("")
    return allowed


def repoints_feed(feed: Feed, *urls: str | None) -> bool:
    """True when any submitted URL is new for this feed (a custom URL, not its own or its catalog's)."""
    from app.services.feed_urls import clean_http_url

    allowed = _allowed_urls(feed)
    return any(clean_http_url(url) and clean_http_url(url) not in allowed for url in urls)


@router.get("/api/feeds")
def list_feeds(request: Request, db: Annotated[Session, Depends(get_db)]):
    feeds = _visible_feeds(db, _caller(request)).order_by(Feed.name.asc()).all()
    return {"feeds": [_feed_dict(feed) for feed in feeds]}


@router.post("/api/feeds")
def create_feed(payload: FeedCreate, request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.services import feed_urls

    session = session_from_request(request)
    uid = effective_user_id(session)
    if not may_add_custom_sources(db, session):
        raise HTTPException(status_code=403, detail="Custom sources are not enabled for your account.")
    kind = feed_urls.normalize_feed_type(payload.type, default="auto")
    home = feed_urls.clean_http_url(payload.homepage_url)
    rss = feed_urls.clean_http_url(payload.rss_url)
    legacy = feed_urls.clean_http_url(payload.url)
    if legacy and not home and not rss:
        if kind == "rss":
            rss = legacy
        else:
            home = legacy
    err = feed_urls.required_url_for_type(kind, home, rss)
    if err:
        raise HTTPException(status_code=400, detail=err)
    active = feed_urls.active_url_for(kind, home, rss)
    existing = db.query(Feed).filter(Feed.user_id == uid, Feed.url == active).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="That feed URL is already added.")
    feed = Feed(
        user_id=uid,
        name=payload.name.strip(),
        url=active,
        homepage_url=home or None,
        rss_url=rss or None,
        category=payload.category,
        type=kind,
        enabled=payload.enabled,
    )
    feed_urls.sync_feed_urls(feed)
    db.add(feed)
    db.commit()
    db.refresh(feed)
    from app.services.favicon import capture_for_feed_async

    capture_for_feed_async(feed.id)
    return _feed_dict(feed)


@router.patch("/api/feeds/{feed_id}")
def update_feed(feed_id: int, payload: FeedUpdate, request: Request, db: Annotated[Session, Depends(get_db)]):
    from app.services import feed_urls

    session = _caller(request)
    feed = _feed_for_caller(db, session, feed_id)
    if repoints_feed(feed, payload.url, payload.homepage_url, payload.rss_url) and not may_add_custom_sources(
        db, session
    ):
        raise HTTPException(status_code=403, detail="Custom sources are not enabled for your account.")
    if payload.name is not None:
        feed.name = payload.name.strip()
    if payload.homepage_url is not None or payload.rss_url is not None or payload.type is not None:
        home = feed_urls.clean_http_url(
            payload.homepage_url if payload.homepage_url is not None else getattr(feed, "homepage_url", None)
        )
        rss = feed_urls.clean_http_url(
            payload.rss_url if payload.rss_url is not None else getattr(feed, "rss_url", None)
        )
        kind = feed_urls.normalize_feed_type(
            payload.type if payload.type is not None else feed.type,
            default=feed.type or "rss",
        )
        err = feed_urls.required_url_for_type(kind, home, rss)
        if err:
            raise HTTPException(status_code=400, detail=err)
        feed.homepage_url = home or None
        feed.rss_url = rss or None
        feed.type = kind
        feed_urls.sync_feed_urls(feed)
    elif payload.url is not None:
        cleaned = feed_urls.clean_http_url(payload.url)
        if not feed_urls.is_http_url(cleaned):
            raise HTTPException(status_code=400, detail="Enter a valid http(s) URL.")
        if (feed.type or "rss") == "rss":
            feed.rss_url = cleaned
        else:
            feed.homepage_url = cleaned
        feed_urls.sync_feed_urls(feed)
    if payload.category is not None:
        feed.category = payload.category
    if payload.enabled is not None:
        feed.enabled = payload.enabled
    if payload.schedule_mode is not None:
        feed.schedule_mode = payload.schedule_mode if payload.schedule_mode in {"global", "custom"} else "global"
    if payload.interval_minutes is not None:
        feed.interval_minutes = payload.interval_minutes if payload.interval_minutes > 0 else None
    if payload.summarize is not None:
        feed.summarize = payload.summarize
    if payload.translate is not None:
        feed.translate = payload.translate
    db.commit()
    db.refresh(feed)
    if feed.enabled:
        from app.services.favicon import cached_src, capture_for_feed_async

        if not cached_src(feed.favicon_name):
            capture_for_feed_async(feed.id)
    return _feed_dict(feed)


@router.delete("/api/feeds/{feed_id}")
def delete_feed(feed_id: int, request: Request, db: Annotated[Session, Depends(get_db)]):
    feed = _feed_for_caller(db, _caller(request), feed_id)
    db.delete(feed)
    db.commit()
    return {"ok": True}


@router.get("/api/feeds/recommended")
def recommended_feeds(request: Request, db: Annotated[Session, Depends(get_db)]):
    # Same view as Sources -> Catalog: members see approved sources, with their own "added" state.
    session = _caller(request)
    return {
        "categories": grouped_catalog(
            db, user_id=effective_user_id(session), approved_only=not session_is_admin(session)
        )
    }


def add_catalog_feed(db: Session, catalog_id: str, user_id: int = 1, *, require_approved: bool = False) -> dict:
    item = find_catalog_item(catalog_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown recommended feed")
    if require_approved:
        from app.services.catalog import is_catalog_approved

        if not is_catalog_approved(db, catalog_id):
            raise HTTPException(status_code=403, detail="That source is not approved for this household.")
    from app.services.catalog import apply_catalog_type, catalog_homepage_url, catalog_rss_url
    from app.services.feed_urls import clean_http_url

    uid = int(user_id or 1)
    rss_alt = catalog_rss_url(item)
    home = catalog_homepage_url(item)
    url_match = (Feed.url == item["url"]) | (Feed.url == rss_alt) if rss_alt else (Feed.url == item["url"])
    if home and home != item["url"]:
        url_match = url_match | (Feed.url == home)
    feed = (
        db.query(Feed)
        .filter(Feed.user_id == uid)
        .filter((Feed.catalog_id == catalog_id) | url_match)
        .one_or_none()
    )
    if feed is None:
        feed = Feed(
            user_id=uid,
            catalog_id=item["id"],
            name=item["name"],
            url=item["url"],
            homepage_url=home or None,
            rss_url=rss_alt or None,
            enabled=True,
            type=item.get("type", "rss"),
            category=item.get("category", "news"),
            translate=bool(item.get("translate")),
        )
        apply_catalog_type(feed, item, item.get("type", "rss"))
        db.add(feed)
    else:
        feed.enabled = True
        feed.catalog_id = feed.catalog_id or item["id"]
        feed.category = item.get("category", feed.category)
        feed.translate = bool(item.get("translate"))
        if not clean_http_url(getattr(feed, "homepage_url", None)) and home:
            feed.homepage_url = home
        if not clean_http_url(getattr(feed, "rss_url", None)) and rss_alt:
            feed.rss_url = rss_alt
        apply_catalog_type(feed, item, feed.type or item.get("type", "rss"))
    db.commit()
    db.refresh(feed)
    from app.services.favicon import capture_for_feed_async

    capture_for_feed_async(feed.id)
    return _feed_dict(feed)


@router.post("/api/feeds/recommended/{catalog_id}")
def add_recommended(catalog_id: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    session = session_from_request(request)
    return add_catalog_feed(
        db,
        catalog_id,
        effective_user_id(session),
        require_approved=not session_is_admin(session),
    )


@router.delete("/api/feeds/recommended/{catalog_id}")
def remove_recommended(catalog_id: str, request: Request, db: Annotated[Session, Depends(get_db)]):
    """Unsubscribe the caller from a catalog source (every account shares catalog ids)."""
    item = find_catalog_item(catalog_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown recommended feed")
    session = _caller(request)
    # Always the caller's own copy: an admin removing their subscription must not
    # drop everyone else's, and several rows can match (catalog id + legacy URL).
    feeds = (
        db.query(Feed)
        .filter(Feed.user_id == effective_user_id(session))
        .filter((Feed.catalog_id == catalog_id) | (Feed.url == item["url"]))
        .all()
    )
    if not feeds:
        return {"ok": True, "removed": False}
    for feed in feeds:
        db.delete(feed)
    db.commit()
    return {"ok": True, "removed": True}
