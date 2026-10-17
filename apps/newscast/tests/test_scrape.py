from datetime import datetime, timezone

from app.services.scrape import (
    article_candidates,
    discover_rss,
    guess_feed_urls,
    looks_like_feed_url,
    published_from_url,
)

HOME = """
<html>
  <head>
    <link rel="alternate" type="application/rss+xml" href="/feed/" />
  </head>
  <body>
    <div class="featured-slides-callout">
      <a href="/2026/09/10/old-featured/"><h2>Old featured headline</h2>Blurb that makes the link text very long.</a>
    </div>
    <div class="entry-intro">
      <a href="/2026/09/13/example-story/"><h2>A long enough headline for scraping</h2></a>
    </div>
    <a href="/about">About us</a>
    <a href="/blog">See all blog entries</a>
    <a href="/2026/09/13/example-story/#comments">1 Comment</a>
    <a href="https://other.example/story">Ignore offsite</a>
  </body>
</html>
"""


def test_discover_rss_from_link_tag():
    assert discover_rss("https://hackaday.com/", HOME) == "https://hackaday.com/feed/"


def test_article_candidates_prefer_recent_over_featured():
    found = article_candidates("https://hackaday.com/", HOME)
    assert found == [
        {"title": "A long enough headline for scraping", "url": "https://hackaday.com/2026/09/13/example-story"},
        {"title": "Old featured headline", "url": "https://hackaday.com/2026/09/10/old-featured"},
    ]


def test_guess_feed_urls_from_homepage():
    assert guess_feed_urls("https://techcrunch.com/") == [
        "https://techcrunch.com/RSS/rss.ashx",
        "https://techcrunch.com/feed/",
        "https://techcrunch.com/rss.xml",
        "https://techcrunch.com/rss",
    ]


def test_looks_like_feed_url():
    assert looks_like_feed_url("https://techcrunch.com/feed/")
    assert looks_like_feed_url("https://feeds.bbci.co.uk/news/world/rss.xml")
    assert not looks_like_feed_url("https://techcrunch.com/")


def test_published_from_url():
    when = published_from_url("https://techcrunch.com/2026/09/12/example-story/")
    assert when == datetime(2026, 9, 12, tzinfo=timezone.utc)
    month_only = published_from_url("https://krebsonsecurity.com/2026/09/example-story/")
    assert month_only == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert published_from_url("https://techcrunch.com/") is None


def test_published_from_url_reads_slug_date():
    when = published_from_url("https://www.theage.com.au/national/police-release-images-20261017-p616eh.html")
    assert when == datetime(2026, 10, 17, tzinfo=timezone.utc)
    assert published_from_url("https://example.com/products/widget-12345678-blue") is None
    assert published_from_url("https://example.com/news/story-20261399-p1.html") is None  # not a real date


NINE_HOME = """
<html><body>
  <nav>
    <a href="/politics/western-australia">Western Australia</a>
    <a href="/business/banking-and-finance">Banking &amp; finance</a>
    <a href="/lifestyle/health-and-wellness">Health &amp; wellness</a>
  </nav>
  <a href="/national/police-release-images-after-string-of-assaults-20261017-p616eh.html">
    <h3>Police release images after string of assaults</h3></a>
  <a href="/world/trump-puts-new-russia-sanctions-on-hold-20261016-p616df.html">
    <h3>Trump puts new Russia sanctions on hold</h3></a>
</body></html>
"""


def test_article_candidates_skip_section_links_when_slugs_carry_dates():
    found = article_candidates("https://www.theage.com.au/", NINE_HOME)
    assert [item["title"] for item in found] == [
        "Police release images after string of assaults",
        "Trump puts new Russia sanctions on hold",
    ]
