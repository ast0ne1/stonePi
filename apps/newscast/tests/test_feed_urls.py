from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import Base, Feed
from app.services import feed_urls
from app.services.catalog import apply_catalog_type, load_catalog


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_active_url_for_prefers_type():
    assert (
        feed_urls.active_url_for("rss", "https://example.com/", "https://example.com/feed")
        == "https://example.com/feed"
    )
    assert (
        feed_urls.active_url_for("webpage", "https://example.com/", "https://example.com/feed")
        == "https://example.com/"
    )
    assert feed_urls.active_url_for("auto", "https://example.com/", "https://example.com/feed") == "https://example.com/"
    assert feed_urls.active_url_for("auto", "", "https://example.com/feed") == "https://example.com/feed"


def test_required_url_for_type():
    assert feed_urls.required_url_for_type("rss", "", "")
    assert feed_urls.required_url_for_type("rss", "https://example.com/", "")
    assert feed_urls.required_url_for_type("rss", "", "https://example.com/feed") is None
    assert feed_urls.required_url_for_type("webpage", "", "https://example.com/feed")
    assert feed_urls.required_url_for_type("webpage", "https://example.com/", "") is None
    assert feed_urls.required_url_for_type("auto", "", "") 
    assert feed_urls.required_url_for_type("auto", "", "https://example.com/feed") is None


def test_sync_feed_urls_sets_active():
    feed = Feed(
        user_id=1,
        name="Dual",
        url="https://example.com/",
        type="webpage",
        homepage_url="https://example.com/",
        rss_url="https://example.com/rss",
    )
    assert feed_urls.sync_feed_urls(feed) == "https://example.com/"
    feed.type = "rss"
    assert feed_urls.sync_feed_urls(feed) == "https://example.com/rss"
    assert feed.homepage_url == "https://example.com/"
    assert feed.rss_url == "https://example.com/rss"


def test_apply_catalog_type_keeps_both_urls():
    item = next(entry for entry in load_catalog() if entry["id"] == "itnews")
    feed = Feed(
        user_id=1,
        name="iTnews",
        url=item["url"],
        type="webpage",
        catalog_id="itnews",
        homepage_url=item["url"],
        rss_url=item["rss_url"],
    )
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


def test_schema_backfill_rss_and_homepage(tmp_path, monkeypatch):
    db_path = tmp_path / "feeds.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Re-import config is heavy; exercise SQL backfill statements directly.
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE feeds ("
                "id INTEGER PRIMARY KEY, user_id INTEGER, name VARCHAR, url VARCHAR, type VARCHAR)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO feeds (id, user_id, name, url, type) VALUES "
                "(1, 1, 'BBC', 'https://bbc.example/rss', 'rss'), "
                "(2, 1, 'Site', 'https://site.example/', 'webpage')"
            )
        )
        conn.execute(text("ALTER TABLE feeds ADD COLUMN homepage_url VARCHAR(1000)"))
        conn.execute(text("ALTER TABLE feeds ADD COLUMN rss_url VARCHAR(1000)"))
        conn.execute(
            text(
                "UPDATE feeds SET rss_url = url "
                "WHERE (rss_url IS NULL OR rss_url = '') "
                "AND lower(coalesce(type, 'rss')) = 'rss'"
            )
        )
        conn.execute(
            text(
                "UPDATE feeds SET homepage_url = url "
                "WHERE (homepage_url IS NULL OR homepage_url = '') "
                "AND lower(coalesce(type, 'rss')) IN ('webpage', 'auto')"
            )
        )
        rows = {
            row[0]: (row[1], row[2])
            for row in conn.execute(text("SELECT id, homepage_url, rss_url FROM feeds")).fetchall()
        }
    assert rows[1] == (None, "https://bbc.example/rss")
    assert rows[2] == ("https://site.example/", None)
