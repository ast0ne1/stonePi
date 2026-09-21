from app.services.article_links import (
    DEFAULT_ARTICLE_LINK_LABEL,
    article_open_url,
    normalize_article_link_label,
)


def test_article_open_url_wraps_when_enabled():
    url = "https://www.example.com/story?id=1"
    wrapped = article_open_url(url, use_paywall_skip=True)
    assert wrapped.startswith("https://www.paywallskip.com/article?url=")
    assert "example.com" in wrapped
    assert article_open_url(url, use_paywall_skip=False) == url


def test_normalize_article_link_label_defaults():
    assert normalize_article_link_label("") == DEFAULT_ARTICLE_LINK_LABEL
    assert normalize_article_link_label("  View story  ") == "View story"
    assert len(normalize_article_link_label("x" * 80)) == 40
