"""RSS parse fixtures for common catalog sources (BBC World, DR Copenhagen)."""

from __future__ import annotations

from datetime import datetime, timezone

import feedparser
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed
from app.services.ingest import _items_from_parsed


BBC_WORLD_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>BBC News - World</title>
    <link>https://www.bbc.com/news/world</link>
    <item>
      <title>Example world headline</title>
      <description>Short BBC world blurb for the briefing.</description>
      <link>https://www.bbc.com/news/world-123</link>
      <pubDate>Mon, 21 Sep 2026 10:00:00 GMT</pubDate>
      <guid>https://www.bbc.com/news/world-123</guid>
    </item>
    <item>
      <title>Second world story</title>
      <description></description>
      <link>https://www.bbc.com/news/world-456</link>
      <pubDate>Mon, 21 Sep 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

DR_COPENHAGEN_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>DR Nyheder - København</title>
    <link>https://www.dr.dk/nyheder</link>
    <item>
      <title>København story one</title>
      <description><![CDATA[<p>Regional blurb with HTML.</p>]]></description>
      <link>https://www.dr.dk/nyheder/indland/kbh-1</link>
      <pubDate>Mon, 21 Sep 2026 08:30:00 +0200</pubDate>
      <guid isPermaLink="true">https://www.dr.dk/nyheder/indland/kbh-1</guid>
    </item>
    <item>
      <title>København story two</title>
      <description>Another regional item</description>
      <link>https://www.dr.dk/nyheder/indland/kbh-2</link>
      <pubDate>Sun, 20 Sep 2026 18:00:00 +0200</pubDate>
    </item>
  </channel>
</rss>
"""


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_bbc_world_rss_fixture_parses_dated_items():
    db = _session()
    feed = Feed(name="BBC World", url="https://feeds.bbci.co.uk/news/world/rss.xml", type="rss", summarize=False)
    db.add(feed)
    db.commit()
    items = _items_from_parsed(feedparser.parse(BBC_WORLD_RSS), feed)
    assert len(items) >= 2
    assert all(item["title"] and item["url"] for item in items)
    assert items[0]["published_at"] is not None
    assert items[0]["published_at"].astimezone(timezone.utc).date() == datetime(2026, 9, 21, tzinfo=timezone.utc).date()
    # Full-article mode keeps RSS description as excerpt (no LLM).
    assert "BBC world blurb" in (items[0]["excerpt"] or "")


def test_dr_copenhagen_rss_fixture_parses_items():
    db = _session()
    feed = Feed(
        name="DR Copenhagen",
        url="https://www.dr.dk/nyheder/service/feeds/regionale/kbh",
        type="rss",
        summarize=False,
    )
    db.add(feed)
    db.commit()
    items = _items_from_parsed(feedparser.parse(DR_COPENHAGEN_RSS), feed)
    assert len(items) >= 2
    assert items[0]["title"] == "København story one"
    assert items[0]["published_at"] is not None
    assert "Regional blurb" in (items[0]["excerpt"] or "") or "HTML" in (items[0]["excerpt"] or "")


def test_rss_no_summary_uses_excerpt_or_title():
    """Document expected RSS + Summarise off behaviour."""
    db = _session()
    feed = Feed(name="Test", url="https://example.com/rss.xml", type="rss", summarize=False)
    db.add(feed)
    db.commit()
    items = _items_from_parsed(feedparser.parse(BBC_WORLD_RSS), feed)
    for item in items:
        # Ingest later sets summary = excerpt.strip() or title when summarize=False.
        summary = (item.get("excerpt") or "").strip() or item["title"]
        assert summary
        assert item["published_at"] is not None or item["title"]
