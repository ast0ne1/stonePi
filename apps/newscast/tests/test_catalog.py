from app.services.catalog import (
    apply_catalog_type,
    catalog_url_for_type,
    load_bundled_catalog,
    load_catalog,
    source_kind,
    source_label,
)


def test_catalog_source_defaults_to_rss():
    assert source_kind({}) == "rss"
    assert source_label({"url": "https://example.com/rss.xml"}) == "RSS"


def test_catalog_source_webpage_is_scrape():
    item = {"type": "webpage", "url": "https://hackaday.com/"}
    assert source_kind(item) == "webpage"
    assert source_label(item) == "Scrape"


def test_itnews_defaults_to_scrape_with_rss_alternate():
    item = next(entry for entry in load_catalog() if entry["id"] == "itnews")
    assert source_kind(item) == "webpage"
    assert item["url"] == "https://www.itnews.com.au/"
    assert item["rss_url"] == "https://www.itnews.com.au/RSS/rss.ashx"
    assert catalog_url_for_type(item, "webpage") == item["url"]
    assert catalog_url_for_type(item, "rss") == item["rss_url"]


def test_apply_catalog_type_switches_url():
    from app.models import Feed

    item = next(entry for entry in load_catalog() if entry["id"] == "itnews")
    feed = Feed(user_id=1, name="iTnews", url=item["url"], type="webpage", catalog_id="itnews")
    apply_catalog_type(feed, item, "rss")
    assert feed.type == "rss"
    assert feed.url == item["rss_url"]
    assert feed.homepage_url == item["url"]
    assert feed.rss_url == item["rss_url"]
    apply_catalog_type(feed, item, "webpage")
    assert feed.type == "webpage"
    assert feed.url == item["url"]
    assert feed.homepage_url == item["url"]
    assert feed.rss_url == item["rss_url"]


def test_catalog_includes_techcrunch_rss():
    item = next(entry for entry in load_catalog() if entry["id"] == "techcrunch")
    assert item["url"] == "https://techcrunch.com/feed/"
    assert source_kind(item) == "rss"
    assert item["category"] == "technology"


def test_catalog_ids_and_urls_are_unique():
    items = load_bundled_catalog()
    ids = [item["id"] for item in items]
    urls = [item["url"] for item in items]
    assert len(ids) == len(set(ids))
    assert len(urls) == len(set(urls))
    assert len(items) >= 75


def test_catalog_has_culture_library():
    culture = [item for item in load_catalog() if item["category"] == "culture"]
    ids = {item["id"] for item in culture}
    assert {"guardian-culture", "hyperallergic", "lithub", "pitchfork"} <= ids


def test_catalog_has_nordic_sources_that_translate():
    nordic = [item for item in load_catalog() if item["category"] == "nordic"]
    ids = {item["id"] for item in nordic}
    assert {"dr-nyheder", "politiken", "nrk", "svt-nyheter", "yle", "dr-copenhagen", "tv2-kosmopol"} <= ids
    english = {"the-local-dk"}
    assert english <= ids
    assert all(item.get("translate") is True for item in nordic if item["id"] not in english)
    assert all(not item.get("translate") for item in nordic if item["id"] in english)


def test_catalog_has_australian_library():
    australia = [item for item in load_catalog() if item["category"] == "australia"]
    ids = {item["id"] for item in australia}
    assert {"abc-news-au", "guardian-australia", "sbs-news", "smh", "the-age", "afr", "abc-sport-au", "itnews"} <= ids
    assert all(item["category"] == "australia" for item in australia)


def test_remove_recommended_deletes_enabled_feed():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base, Feed
    from app.routers.feeds import add_catalog_feed, remove_recommended
    from app.services.catalog import catalog_with_status

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    add_catalog_feed(db, "techcrunch")
    assert any(item["id"] == "techcrunch" and item["added"] for item in catalog_with_status(db))
    assert remove_recommended("techcrunch", db) == {"ok": True, "removed": True}
    assert db.query(Feed).filter(Feed.catalog_id == "techcrunch").one_or_none() is None
    assert any(item["id"] == "techcrunch" and not item["added"] for item in catalog_with_status(db))


def test_disabled_feed_still_counts_as_added():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base, Feed
    from app.routers.feeds import add_catalog_feed
    from app.services.catalog import catalog_with_status

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    add_catalog_feed(db, "techcrunch")
    feed = db.query(Feed).filter(Feed.catalog_id == "techcrunch").one()
    feed.enabled = False
    db.commit()
    status = next(item for item in catalog_with_status(db) if item["id"] == "techcrunch")
    assert status["added"] is True
    assert status["enabled"] is False


def test_seed_only_creates_default_enabled_feeds(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base, Feed, User
    from app.services import catalog as catalog_service
    import app.services.users as users_service

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    admin = User(id=1, username="admin", role="admin", password="x")
    db.add(admin)
    db.commit()

    monkeypatch.setattr(catalog_service.env, "seed_recommended_feeds", True)
    monkeypatch.setattr(users_service, "ensure_admin_user", lambda _db: admin)

    catalog_service.seed_recommended_feeds(db)
    feeds = db.query(Feed).filter(Feed.user_id == admin.id).all()
    assert feeds
    assert all(feed.enabled for feed in feeds)
    seeded_ids = {feed.catalog_id for feed in feeds}
    defaults = {item["id"] for item in catalog_service.load_catalog() if item.get("default_enabled")}
    assert seeded_ids == defaults


def test_seed_removes_unused_disabled_catalog_stubs(monkeypatch):
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models import Base, Feed, User
    from app.services import catalog as catalog_service
    import app.services.users as users_service

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    admin = User(id=1, username="admin", role="admin", password="x")
    db.add(admin)
    stub = Feed(
        user_id=1,
        catalog_id="techcrunch",
        name="TechCrunch",
        url="https://techcrunch.com/feed/",
        enabled=False,
        type="rss",
        category="technology",
    )
    kept = Feed(
        user_id=1,
        catalog_id="the-verge",
        name="The Verge",
        url="https://www.theverge.com/rss/index.xml",
        enabled=False,
        type="rss",
        category="technology",
        last_fetched_at=datetime.now(timezone.utc),
    )
    db.add_all([stub, kept])
    db.commit()

    monkeypatch.setattr(catalog_service.env, "seed_recommended_feeds", True)
    monkeypatch.setattr(users_service, "ensure_admin_user", lambda _db: admin)

    catalog_service.seed_recommended_feeds(db)
    assert db.query(Feed).filter(Feed.catalog_id == "techcrunch").one_or_none() is None
    assert db.query(Feed).filter(Feed.catalog_id == "the-verge").one() is not None
