import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.services.briefing import briefing_title, format_published, render_txt, sanitize_html, stories_payload, write_epub


class FakeStory:
    def __init__(self, saved=False, source="BBC World"):
        self.id = 7
        self.title = "Test headline"
        self.summary = "A short summary."
        self.source_name = source
        self.canonical_url = "https://example.com/story"
        self.published_at = datetime(2026, 9, 13, 8, 30, tzinfo=timezone.utc)
        self.created_at = datetime(2026, 9, 13, 9, 0, tzinfo=timezone.utc)
        self.saved = saved


class FakeFeed:
    def __init__(self, category="news"):
        self.category = category


def test_stories_payload_shape():
    payload = stories_payload([FakeStory()])
    assert payload["device"] == "xteink-x3"
    assert "generated_at" in payload
    story = payload["stories"][0]
    assert story["id"] == "7"
    assert story["title"] == "Test headline"
    assert story["source"] == "BBC World"
    assert story["published_label"] == "13 Sep 2026"
    assert story["category"] == "news"
    assert story["saved"] is False


def test_txt_briefing_includes_story():
    payload = stories_payload([FakeStory()])
    text = render_txt(payload)
    assert "NewsCast briefing" in text
    assert "1 story" in text
    assert "Test headline" in text
    assert "A short summary." in text
    assert "13 Sep 2026" in text


def test_format_published_empty():
    assert format_published(None) == ""


def test_instance_name_in_briefing():
    payload = stories_payload([FakeStory()], instance_name="Work")
    assert payload["title"] == "NewsCast · Work"
    assert render_txt(payload).startswith("NewsCast · Work")
    assert briefing_title("") == "NewsCast briefing"


def test_saved_story_uses_long_reads_category():
    payload = stories_payload([FakeStory(saved=True)])
    assert payload["stories"][0]["category"] == "longreads"
    assert payload["stories"][0]["category_label"] == "Long reads"
    assert "Long reads" in render_txt(payload)


def test_payload_uses_feed_category():
    payload = stories_payload(
        [FakeStory()],
        feeds={"BBC World": FakeFeed("technology")},
        labels={"technology": "Tech"},
    )
    assert payload["stories"][0]["category"] == "technology"
    assert payload["stories"][0]["category_label"] == "Tech"


def test_sanitize_html_strips_script_and_images():
    cleaned = sanitize_html('<p>Hello</p><script>alert(1)</script><img src="http://x/y.jpg"><p onclick="x">Body</p>')
    assert "Hello" in cleaned
    assert "Body" in cleaned
    assert "<script" not in cleaned
    assert "alert(1)" not in cleaned
    assert "<img" not in cleaned
    assert "onclick" not in cleaned


def test_write_epub_strips_unsafe_html_and_groups_toc(tmp_path: Path):
    dest = tmp_path / "news.epub"
    payload = {
        "title": "NewsCast briefing",
        "generated_at": "2026-09-14T06:30:00Z",
        "paper_date": "2026-09-14",
        "stories": [
            {
                "id": "1",
                "title": "Saved essay",
                "summary": "<p>Long read.</p>",
                "source": "Saved",
                "url": "https://example.com/saved",
                "published_label": "14 Sep 2026",
                "category": "longreads",
                "category_label": "Long reads",
                "saved": True,
            },
            {
                "id": "2",
                "title": "Wire story",
                "summary": '<p>Hello</p><script>alert(1)</script><img src="http://x/y.jpg">',
                "source": "BBC",
                "url": "https://example.com/a",
                "published_label": "14 Sep 2026",
                "category": "news",
                "category_label": "World News",
                "saved": False,
            },
            {
                "id": "3",
                "title": "Second wire",
                "summary": "<p>More.</p>",
                "source": "Reuters",
                "url": "https://example.com/b",
                "published_label": "14 Sep 2026",
                "category": "news",
                "category_label": "World News",
                "saved": False,
            },
        ],
    }
    write_epub(payload, dest)
    with zipfile.ZipFile(dest) as archive:
        names = archive.namelist()
        chapters = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in names
            if name.endswith(".xhtml")
        )
        assert "<script" not in chapters
        assert "alert(1)" not in chapters
        story_html = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in names
            if name.replace("\\", "/").endswith(tuple(f"story-{i}.xhtml" for i in range(1, 10)))
            or "/story-" in name.replace("\\", "/")
        )
        assert "<img" not in story_html
        assert "Hello" in chapters
        cover = archive.read("EPUB/cover.xhtml").decode("utf-8", errors="ignore")
        assert 'src="cover.jpg"' in cover
        assert "toc-category" in cover
        assert "toc-source" in cover
        assert cover.index("World News") < cover.index("BBC")
        assert cover.index("BBC") < cover.index("Wire story")
        assert cover.index("Reuters") < cover.index("Second wire")
        toc_name = next(name for name in names if name.endswith(("nav.xhtml", "toc.ncx")))
        toc = archive.read(toc_name).decode("utf-8", errors="ignore")
        assert "Long reads" in toc
        assert "World News" in toc
        assert "BBC" in toc
        assert "Reuters" in toc
        assert toc.index("World News") < toc.index("BBC")
        assert toc.index("BBC") < toc.index("Wire story")
        css = next(name for name in names if name.endswith("eink.css"))
        assert "Georgia" in archive.read(css).decode("utf-8")
        assert "toc-stories" in archive.read(css).decode("utf-8")
        assert any(name.endswith("cover.jpg") for name in names)


def test_group_stories_by_source_keeps_first_seen_order():
    from app.services.briefing import group_stories_by_source

    grouped = group_stories_by_source(
        [
            {"title": "A", "source": "BBC"},
            {"title": "B", "source": "Reuters"},
            {"title": "C", "source": "BBC"},
        ]
    )
    assert [name for name, _items in grouped] == ["BBC", "Reuters"]
    assert [story["title"] for story in grouped[0][1]] == ["A", "C"]


def test_write_epub_x3_layout_omits_links_and_chapters_by_source(tmp_path: Path):
    from app.services.briefing import EpubLayout, write_epub
    from PIL import Image
    import io

    dest = tmp_path / "x3.epub"
    payload = {
        "title": "NewsCast briefing",
        "generated_at": "2026-09-14T06:30:00Z",
        "paper_date": "2026-09-14",
        "stories": [
            {
                "id": "1",
                "title": "First BBC",
                "summary": '<p>Body <a href="https://example.com/a">link</a>.</p>',
                "source": "BBC",
                "url": "https://example.com/a",
                "published_label": "14 Sep 2026",
                "category": "news",
                "category_label": "World News",
            },
            {
                "id": "2",
                "title": "Second BBC",
                "summary": "<p>More.</p>",
                "source": "BBC",
                "url": "https://example.com/b",
                "published_label": "14 Sep 2026",
                "category": "news",
                "category_label": "World News",
            },
            {
                "id": "3",
                "title": "Reuters piece",
                "summary": "<p>Wire.</p>",
                "source": "Reuters",
                "url": "https://example.com/c",
                "published_label": "14 Sep 2026",
                "category": "news",
                "category_label": "World News",
            },
        ],
    }
    layout = EpubLayout(omit_article_links=True, chapters_by_source=True, x3_screen=True)
    write_epub(payload, dest, layout=layout)
    with zipfile.ZipFile(dest) as archive:
        names = archive.namelist()
        assert any(name.endswith("source-1.xhtml") for name in names)
        assert any(name.endswith("source-2.xhtml") for name in names)
        assert not any("/story-" in name.replace("\\", "/") for name in names)
        html_all = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in names
            if name.endswith(".xhtml")
        )
        assert "Original article" not in html_all
        assert "https://example.com/a" not in html_all
        assert "source-chapter" in html_all
        assert "First BBC" in html_all and "Second BBC" in html_all
        css = archive.read(next(n for n in names if n.endswith("eink.css"))).decode("utf-8")
        assert "528px" in css or "0.45em" in css
        cover = archive.read(next(n for n in names if n.endswith("cover.jpg")))
        img = Image.open(io.BytesIO(cover))
        assert img.size == (528, 792)


def test_render_txt_omits_urls_when_configured():
    from app.services.briefing import EpubLayout, render_txt, stories_payload

    payload = stories_payload([FakeStory()])
    classic = render_txt(payload, layout=EpubLayout.classic())
    assert "https://example.com/story" in classic
    x3 = render_txt(payload, layout=EpubLayout())
    assert "https://example.com/story" not in x3
