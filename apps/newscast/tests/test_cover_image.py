from pathlib import Path
from zipfile import ZipFile

from app.services.briefing import write_epub
from app.services.cover_image import render_category_icon, render_newspaper_cover


def test_render_newspaper_cover_is_jpeg():
    data = render_newspaper_cover(
        heading="NewsCast · Work",
        date_label="14 Sep 2026",
        stories=[
            {
                "title": "Main headline about markets and rates",
                "summary": "Markets rose after the bank held rates steady.",
                "source": "BBC World",
            },
            {
                "title": "Side story one",
                "summary": "A short deck for the left column.",
                "source": "Reuters",
            },
            {
                "title": "Side story two",
                "summary": "A short deck for the right column.",
                "source": "AP",
            },
        ],
    )
    assert data[:3] == b"\xff\xd8\xff"
    assert len(data) > 5000


def test_render_category_icon_is_png():
    data = render_category_icon("technology")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    fallback = render_category_icon("custom-topic")
    assert fallback[:8] == b"\x89PNG\r\n\x1a\n"


def test_write_epub_includes_cover_image(tmp_path: Path):
    dest = tmp_path / "news.epub"
    write_epub(
        {
            "title": "NewsCast · Work",
            "generated_at": "2026-09-14T06:30:00Z",
            "paper_date": "2026-09-14",
            "stories": [
                {
                    "id": "1",
                    "title": "Lead story",
                    "summary": "Lead deck for the front page.",
                    "source": "BBC",
                    "url": "https://example.com/a",
                    "published_label": "14 Sep 2026",
                    "category": "news",
                    "category_label": "World News",
                    "saved": False,
                },
                {
                    "id": "2",
                    "title": "Flank story",
                    "summary": "Secondary deck.",
                    "source": "Reuters",
                    "url": "https://example.com/b",
                    "published_label": "14 Sep 2026",
                    "category": "news",
                    "category_label": "World News",
                    "saved": False,
                },
            ],
        },
        dest,
    )
    with ZipFile(dest) as archive:
        names = archive.namelist()
        assert any(name.endswith("cover.jpg") for name in names)
        cover_img = next(name for name in names if name.endswith("cover.jpg"))
        assert archive.read(cover_img)[:3] == b"\xff\xd8\xff"
        opf = archive.read("EPUB/content.opf").decode("utf-8", errors="ignore")
        assert 'name="cover"' in opf
        cover_html = archive.read("EPUB/cover.xhtml").decode("utf-8", errors="ignore")
        assert "cover.jpg" in cover_html


def _x3_stories() -> list[dict]:
    return [
        {"title": "Lead story about talks", "summary": "Envoys arrived on Friday.", "source": "BBC World",
         "category": "news", "category_label": "World News", "image_url": "https://img.example/lead.jpg"},
        {"title": "Second story", "summary": "Deck two.", "source": "BBC World", "category": "news",
         "category_label": "World News"},
        {"title": "Chip story", "summary": "Deck three.", "source": "Ars", "category": "technology",
         "category_label": "Technology"},
    ]


def _jpeg(size=(400, 300), colour=(120, 60, 30)) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def _cover(**kw) -> "Image.Image":
    import io

    from PIL import Image

    data = render_newspaper_cover(heading="NewsCast", date_label="10 Oct 2026", stories=_x3_stories(), x3_screen=True, **kw)
    return Image.open(io.BytesIO(data)).convert("L")


def test_x3_cover_uses_photo_or_falls_back_to_placeholder():
    from PIL import ImageChops

    placeholder = _cover()
    photo = _cover(lead_photo=_jpeg())
    assert photo.size == placeholder.size == (528, 792)
    assert ImageChops.difference(photo, placeholder).getbbox() is not None  # the photo was drawn
    # Broken or tiny images (tracking pixels) fall back to the placeholder.
    for bad in (b"not an image", _jpeg(size=(1, 1))):
        assert ImageChops.difference(_cover(lead_photo=bad), placeholder).getbbox() is None


def test_x3_paper_fetches_only_the_lead_photo(tmp_path: Path, monkeypatch):
    from app.services import cover_image
    from app.services.briefing import EpubLayout

    fetched: list = []
    monkeypatch.setattr(cover_image, "fetch_cover_photo", lambda url: fetched.append(url) or None)
    payload = {"title": "NewsCast", "paper_date": "2026-10-10", "stories": _x3_stories()}
    write_epub(payload, tmp_path / "x3.epub", layout=EpubLayout())
    assert fetched == ["https://img.example/lead.jpg"]  # failed fetch still writes the paper
    assert (tmp_path / "x3.epub").stat().st_size > 0
    fetched.clear()
    write_epub(payload, tmp_path / "classic.epub", layout=EpubLayout.classic())
    assert fetched == []  # the desktop cover has no photo slot


def test_fetch_cover_photo_refuses_unsafe_urls():
    from app.services.cover_image import fetch_cover_photo

    assert fetch_cover_photo("") is None
    assert fetch_cover_photo("file:///etc/passwd") is None
    assert fetch_cover_photo("http://127.0.0.1/cover.jpg") is None  # address guard blocks loopback


def test_big_photo_is_decoded_small_and_still_used():
    from PIL import ImageChops

    placeholder = _cover()
    photo = _cover(lead_photo=_jpeg(size=(6000, 4000)))
    assert ImageChops.difference(photo, placeholder).getbbox() is not None


def test_cover_photo_downloads_once_per_url(monkeypatch):
    from app.services import cover_image

    cover_image._photo_cache.clear()
    calls: list = []
    monkeypatch.setattr(cover_image, "_download_cover_photo", lambda url: calls.append(url) or b"img")
    assert cover_image.fetch_cover_photo("https://img.example/a.jpg") == b"img"
    assert cover_image.fetch_cover_photo("https://img.example/a.jpg") == b"img"
    assert calls == ["https://img.example/a.jpg"]
    cover_image._photo_cache.clear()


def test_wrap_px_splits_words_wider_than_the_column():
    from PIL import Image, ImageDraw

    from app.services.cover_image import _load_font, _wrap_px

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = _load_font("serif_bold", 24)
    lines = _wrap_px(draw, "Ruling: Bundesverfassungsgerichtsentscheidungsbegründung today", font, 160)
    assert len(lines) > 2
    assert all(draw.textlength(line, font=font) <= 160 for line in lines)


def test_in_this_issue_keeps_every_section():
    stories = [
        {"title": f"Story {key}", "summary": "Deck.", "source": "Src", "category": key, "category_label": key.title()}
        for key in ("news", "technology", "nordic", "business", "sport", "security", "science")
    ]
    data = render_newspaper_cover(heading="NewsCast", date_label="10 Oct 2026", stories=stories, x3_screen=True)
    assert data[:3] == b"\xff\xd8\xff"  # seven sections: four cells plus "+3 more" render without error
