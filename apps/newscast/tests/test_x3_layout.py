"""Xteink paper layout: shared CrossPoint / CrossInk baseline plus CrossInk extras."""

import io
import re
import zipfile
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, User
from app.services import reader_config, settings, user_settings
from app.services.briefing import (
    CROSSINK_EXTRA_CSS,
    EINK_CSS,
    X3_EINK_CSS,
    EpubLayout,
    epub_stylesheet,
    write_epub,
)
from app.services.categories import BUILTIN_LABELS
from app.services.cover_image import CATEGORY_ICON_SIZE, render_category_icon


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def _story(story_id: str, title: str, source: str, category: str = "news", label: str = "World News") -> dict:
    return {
        "id": story_id,
        "title": title,
        "summary": f"<p>{title} summary text that is long enough to make a digest.</p>",
        "source": source,
        "url": f"https://example.com/{story_id}",
        "published_label": "29 Sep 2026",
        "category": category,
        "category_label": label,
    }


def _payload(stories: list[dict]) -> dict:
    return {
        "title": "NewsCast briefing",
        "generated_at": "2026-09-29T06:30:00Z",
        "paper_date": "2026-09-29",
        "stories": stories,
    }


def _sample_payload() -> dict:
    return _payload(
        [
            _story("1", "BBC one", "BBC World"),
            _story("2", "BBC two", "BBC World"),
            _story("3", "Guardian one", "Guardian"),
            _story("4", "Nordic one", "Politiken", category="nordic", label="Nordic"),
        ]
    )


def _read(dest: Path) -> dict[str, str]:
    with zipfile.ZipFile(dest) as archive:
        return {
            name: archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
            if name.endswith((".xhtml", ".opf", ".ncx", ".css"))
        }


def _spine(files: dict[str, str]) -> list[str]:
    opf = files["EPUB/content.opf"]
    return re.findall(r'<itemref idref="([^"]+)"', opf[opf.find("<spine") : opf.find("</spine>")])


def test_chapter_menu_is_numbered_and_stops_at_source(tmp_path: Path):
    dest = tmp_path / "paper.epub"
    write_epub(_sample_payload(), dest, layout=EpubLayout())
    nav = _read(dest)["EPUB/nav.xhtml"]
    for title in ("1. World News", "1.1 BBC World", "1.2 Guardian", "2. Nordic", "2.1 Politiken"):
        assert f">{title}<" in nav
    # Two levels only: category -> source. Story headlines never appear in the menu.
    assert "BBC one" not in nav
    assert "Nordic one" not in nav


def test_paper_opens_on_cover_then_section_pages(tmp_path: Path):
    dest = tmp_path / "paper.epub"
    write_epub(_sample_payload(), dest, layout=EpubLayout())
    files = _read(dest)
    spine = _spine(files)
    assert "nav" not in spine
    opf = files["EPUB/content.opf"]
    # First spine item is the cover page (cover image + briefing + Contents).
    first_href = re.search(rf'<item href="([^"]+)" id="{spine[0]}"', opf) or re.search(
        rf'id="{spine[0]}"[^>]*href="([^"]+)"', opf
    )
    assert first_href is not None and first_href.group(1) == "cover.xhtml"
    source = next(text for name, text in files.items() if name.endswith("source-2.xhtml"))
    # Source heading and its first story share a file: no blank title page.
    assert source.index('class="source-title"') < source.index("BBC one")


def test_stylesheets_only_use_selectors_both_firmwares_read():
    # CrossPoint drops any selector containing a space, >, +, ~, :, #, [ or *, and
    # CrossInk ranks descendant rules below plain classes. Allow tag, .class, tag.class.
    allowed = re.compile(r"^[a-z][a-z0-9]*$|^[a-z0-9]*\.[a-z][a-z0-9-]*$")
    for css in (EINK_CSS, X3_EINK_CSS, X3_EINK_CSS + CROSSINK_EXTRA_CSS):
        for block in re.findall(r"([^{}]+)\{", css):
            for selector in block.split(","):
                selector = selector.strip()
                assert allowed.match(selector), f"unsupported selector: {selector!r}"


def test_x3_baseline_skips_properties_the_firmwares_ignore():
    body = X3_EINK_CSS.lower()
    for prop in ("page-break", "break-before", "font-size", "line-height", "border", " color:", "background"):
        assert prop not in body, prop
    assert not re.search(r"(^|\n)img\s*\{", body)  # no global image hiding rule
    assert ".category-icon" in body


def test_crossink_extras_only_when_selected():
    plain = epub_stylesheet(EpubLayout())
    extras = epub_stylesheet(EpubLayout(crossink_extras=True))
    assert "font-variant-caps" not in plain
    assert "background-color: #000" in extras
    assert "small-caps" in extras


def test_page_breaks_identical_for_both_firmwares(tmp_path: Path):
    plain = tmp_path / "crosspoint.epub"
    fancy = tmp_path / "crossink.epub"
    write_epub(_sample_payload(), plain, layout=EpubLayout())
    write_epub(_sample_payload(), fancy, layout=EpubLayout(crossink_extras=True))
    plain_files, fancy_files = _read(plain), _read(fancy)
    assert _spine(plain_files) == _spine(fancy_files)
    xhtml = sorted(name for name in plain_files if name.endswith(".xhtml"))
    assert xhtml == sorted(name for name in fancy_files if name.endswith(".xhtml"))
    for name in xhtml:
        assert plain_files[name] == fancy_files[name]


def test_contents_detail_titles_and_auto(tmp_path: Path):
    payload = _sample_payload()
    for detail, limit, expect_digest in (
        ("full", 30, True),
        ("titles", 30, False),
        ("auto", 30, True),
        ("auto", 3, False),
    ):
        dest = tmp_path / f"{detail}-{limit}.epub"
        write_epub(payload, dest, layout=EpubLayout(contents_detail=detail, contents_limit=limit))
        cover = _read(dest)["EPUB/cover.xhtml"]
        assert '<div class="toc-title">BBC one</div>' in cover
        assert ('class="toc-digest"' in cover) is expect_digest, (detail, limit)
        assert ">1.1 BBC World<" in cover


def test_contents_categories_and_sources_only(tmp_path: Path):
    dest = tmp_path / "sources.epub"
    write_epub(_sample_payload(), dest, layout=EpubLayout(contents_detail="sources"))
    files = _read(dest)
    cover = files["EPUB/cover.xhtml"]
    contents = cover[cover.index('class="contents"') :]
    for heading in ("1. World News", "1.1 BBC World", "1.2 Guardian", "2. Nordic", "2.1 Politiken"):
        assert f">{heading}<" in contents
    for title in ("BBC one", "BBC two", "Guardian one", "Nordic one"):
        assert title not in contents
    for cls in ("toc-stories", "toc-story", "toc-title", "toc-digest"):
        assert f'class="{cls}"' not in contents
    # Only the cover Contents changes: chapter menu and story pages are as before.
    full = tmp_path / "full.epub"
    write_epub(_sample_payload(), full, layout=EpubLayout())
    full_files = _read(full)
    assert files["EPUB/nav.xhtml"] == full_files["EPUB/nav.xhtml"]
    assert _spine(files) == _spine(full_files)
    assert "BBC one" in next(text for name, text in files.items() if name.endswith("source-2.xhtml"))


def test_every_spine_item_has_a_title(tmp_path: Path):
    for layout in (EpubLayout(), EpubLayout.classic()):
        dest = tmp_path / "paper.epub"
        write_epub(_sample_payload(), dest, layout=layout)
        files = _read(dest)
        opf = files["EPUB/content.opf"]
        ncx = files["EPUB/toc.ncx"]
        nav = files["EPUB/nav.xhtml"]
        hrefs = []
        for idref in _spine(files):
            match = re.search(rf'<item href="([^"]+)" id="{re.escape(idref)}"', opf) or re.search(
                rf'id="{re.escape(idref)}"[^>]*href="([^"]+)"', opf
            )
            assert match is not None, idref
            hrefs.append(match.group(1))
        for href in hrefs:
            title = re.search(r"<title>([^<]*)</title>", files[f"EPUB/{href}"])
            assert title is not None and title.group(1).strip(), href
        # CrossPoint / CrossInk name the footer after the TOC entry for the current spine
        # file, inheriting the previous one when missing; the first file has nothing to
        # inherit, so the cover must be in the TOC itself (else the footer says "Unnamed").
        assert hrefs[0] == "cover.xhtml"
        assert '<content src="cover.xhtml"/>' in ncx
        assert 'href="cover.xhtml">Front page<' in nav
        nav_points = re.findall(r'<content src="([^"#]+)', ncx)
        assert nav_points[0] == "cover.xhtml"


def test_layout_firmware_follows_manual_choice_then_detection():
    db = _session()
    user = User(username="adam", role="user", active=True)
    db.add(user)
    db.commit()
    uid = int(user.id)
    assert EpubLayout.from_db(db, user_id=uid).crossink_extras is False
    reader_config.remember_detected_firmware(db, uid, "crossink", "1.6.0")
    assert reader_config.detected_firmware_label(db, uid) == "CrossInk 1.6.0"
    assert EpubLayout.from_db(db, user_id=uid).crossink_extras is True
    user_settings.set_value(db, uid, "reader_firmware", "crosspoint")
    assert EpubLayout.from_db(db, user_id=uid).crossink_extras is False
    user_settings.set_value(db, uid, "reader_firmware", "crossink")
    user_settings.set_value(db, uid, "reader_device", "kobo")
    assert EpubLayout.from_db(db, user_id=uid).crossink_extras is False


def test_settings_normalizers():
    assert settings.normalize_reader_firmware("CrossInk") == "crossink"
    assert settings.normalize_reader_firmware("nope") == "auto"
    assert settings.normalize_reader_keep_days("7") == 7
    assert settings.normalize_reader_keep_days("5") == 0
    assert settings.normalize_epub_contents_detail("bogus") == "full"
    assert settings.normalize_epub_contents_detail(" Sources ") == "sources"
    assert settings.normalize_epub_contents_detail(None) == "full"
    assert [value for value, _label in settings.EPUB_CONTENTS_DETAILS] == ["full", "titles", "auto", "sources"]
    assert settings.normalize_epub_contents_limit("1") == 5


def test_category_icons_are_distinct_black_and_white():
    seen: dict[bytes, str] = {}
    for key in [*BUILTIN_LABELS, "longreads"]:
        image = Image.open(io.BytesIO(render_category_icon(key))).convert("RGB")
        assert image.size == (CATEGORY_ICON_SIZE, CATEGORY_ICON_SIZE)
        assert set(image.getdata()) <= {(0, 0, 0), (255, 255, 255)}, key
        pixels = image.tobytes()
        assert pixels not in seen, f"{key} reuses the {seen.get(pixels)} icon"
        seen[pixels] = key


def test_builtin_icons_come_from_stored_material_pngs():
    from app.services.cover_image import CATEGORY_ICON_DIR, DRAWN_CATEGORY_ICONS

    for key in [*BUILTIN_LABELS, "longreads"]:
        if key in DRAWN_CATEGORY_ICONS:
            continue
        assert (CATEGORY_ICON_DIR / f"{key}.png").exists(), key
    assert (CATEGORY_ICON_DIR / "LICENSE-material-symbols.txt").exists()
    # Custom categories reuse the World News newspaper.
    assert render_category_icon("my-custom-topic") == render_category_icon("news")
