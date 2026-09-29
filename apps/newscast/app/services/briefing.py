from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from ebooklib import epub
from sqlalchemy import delete, func, or_
from sqlalchemy.orm import Session

from app.config import BRIEFING_DIR, env
from app.models import Feed, Story, SyncTask, utcnow
from app.services import settings
from app.services.categories import BUILTIN_LABELS, DEFAULT_CATEGORY, category_labels, slugify
from app.services.cover_image import render_category_icon, render_newspaper_cover
from app.services.filters import story_kept
from app.services.paper_naming import (
    day_from_briefing_path,
    format_paper_date,
    paper_category_display_title,
    paper_display_title,
    paper_download_name,
    reader_date_format,
)

BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
MAX_BRIEFING_STORIES = 20
BRIEFING_DAYS = {"today", "yesterday", "all"}
BRIEFING_SAVE_RE = re.compile(r"(?:NewsCast|news)-(\d{4}-\d{2}-\d{2})(?:-[a-z0-9-]+)?\.(epub|txt)$", re.I)
ISO_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CATEGORY_PAPER_STEM_RE = re.compile(r"^news-(\d{4}-\d{2}-\d{2})-([a-z0-9-]+)$", re.I)
KEEP_DATED_BRIEFINGS = 7
DEFAULT_PUBLISH_AT = "06:30"
SAVED_CATEGORY = "longreads"
SAVED_CATEGORY_LABEL = "Long reads"


def briefing_dir_for(user_id: int | None = None) -> Path:
    uid = int(user_id or 1)
    path = BRIEFING_DIR / str(uid)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _scope_stories(query, user_id: int | None):
    if user_id is not None:
        return query.filter(Story.user_id == user_id)
    return query


def _scope_feeds(query, user_id: int | None):
    if user_id is not None:
        return query.filter(Feed.user_id == user_id)
    return query


# Chapter-menu and footer name for the first spine file (front image, briefing, Contents).
COVER_CHAPTER_TITLE = "Front page"


@dataclass(frozen=True)
class EpubLayout:
    omit_article_links: bool = True
    chapters_by_source: bool = True
    x3_screen: bool = True
    toc_outline_numbers: bool = True
    # Cover Contents: "full" (titles + summaries), "titles", "auto" (titles past
    # contents_limit), or "sources" (numbered categories and sources, no stories).
    contents_detail: str = "full"
    contents_limit: int = 30
    # Optional CrossInk-only styling layered on the shared baseline (ignored by CrossPoint).
    crossink_extras: bool = False

    @classmethod
    def from_db(cls, db: Session, user_id: int | None = None) -> "EpubLayout":
        crossink = False
        if user_id is not None:
            from app.services import reader_config

            crossink = reader_config.effective_reader_firmware(db, int(user_id)) == "crossink"
        return cls(
            omit_article_links=settings.epub_omit_article_links(db),
            chapters_by_source=settings.epub_chapters_by_source(db),
            x3_screen=settings.epub_x3_screen(db),
            toc_outline_numbers=settings.epub_toc_outline_numbers(db),
            contents_detail=settings.epub_contents_detail(db),
            contents_limit=settings.epub_contents_limit(db),
            crossink_extras=crossink,
        )

    @classmethod
    def classic(cls) -> "EpubLayout":
        """Legacy layout: per-story chapters, article links, desktop-sized cover."""
        return cls(
            omit_article_links=False,
            chapters_by_source=False,
            x3_screen=False,
            toc_outline_numbers=False,
        )

    def contents_stories(self) -> bool:
        """False when Contents lists only the numbered categories and sources."""
        return self.contents_detail != "sources"

    def contents_summaries(self, story_count: int) -> bool:
        if self.contents_detail in ("titles", "sources"):
            return False
        if self.contents_detail == "auto":
            return story_count <= self.contents_limit
        return True


# Stylesheets use only selectors every Xteink firmware reads: `tag`, `.class`, or
# `tag.class`, one per rule. CrossPoint drops anything with a space, `>` or `:`, and
# CrossInk ranks two-part descendant rules *below* plain classes, so overrides that
# lean on nesting silently fail. Page breaks come from starting a new spine file
# (cover, section page, source), never from CSS — CrossPoint ignores page-break.

# Classic paper for full-CSS readers (KOReader, desktop apps).
EINK_CSS = """
body {
  font-family: Georgia, "Times New Roman", serif;
  font-size: 1.12em;
  line-height: 1.5;
  color: #111;
  background: #fff;
  margin: 1.1em 1.2em 1.6em;
  text-align: left;
  word-spacing: normal;
  letter-spacing: normal;
}
h1 { font-size: 1.55em; line-height: 1.25; margin: 0 0 0.35em; font-weight: bold; text-align: left; }
h2 { font-size: 1.2em; line-height: 1.3; margin: 1.4em 0 0.55em; font-weight: bold; text-align: left; }
h3 { font-size: 1.05em; line-height: 1.3; margin: 1.25em 0 0.45em; font-weight: bold; text-align: left; }
h4 { font-size: 0.98em; line-height: 1.3; margin: 0.95em 0 0.35em; font-weight: normal; font-style: italic; text-align: left; }
p { margin: 0 0 0.75em; text-align: left; word-spacing: normal; letter-spacing: normal; }
a { color: #111; text-decoration: underline; }
.meta { font-style: italic; color: #333; margin: 0 0 0.35em; }
.byline { font-style: italic; color: #333; margin: 0 0 1em; }
.masthead { margin: 0 0 0.75em; font-style: italic; color: #333; }
.cover-image { margin: 0 0 1em; text-align: center; }
.cover-img { width: 100%; height: auto; }
.paper-title { margin: 0 0 0.25em; }
.cover-rule { border: 0; border-top: 1px solid #111; margin: 0.9em 0 1.1em; }
.contents-title { margin: 0.4em 0 0.55em; }
.toc-category { margin: 0 0 1.1em; }
.toc-category-title { margin: 0 0 0.55em; padding-bottom: 0.25em; border-bottom: 1px solid #111; }
.toc-source { margin: 0 0 0.85em 0.75em; }
.toc-source-title { margin: 0.7em 0 0.4em; }
.toc-stories { margin: 0 0 0 0.35em; }
.toc-story { margin: 0 0 0.85em; line-height: 1.4; }
.toc-title { font-weight: bold; margin: 0 0 0.2em; }
.toc-digest { font-size: 0.92em; color: #333; line-height: 1.35; margin: 0; }
.category-plate { text-align: center; padding: 2.5em 0.5em 1.5em; }
.category-icon { width: 160px; height: 160px; margin: 0 0 1.2em; }
.plate-title { font-size: 1.45em; text-align: center; margin: 0 0 0.75em; }
.plate-sources { text-align: center; font-style: italic; color: #333; margin: 1.5em 0 0.35em; }
.plate-count { text-align: center; color: #333; margin: 0; }
.source-title { font-size: 1.35em; margin: 0 0 0.75em; padding-bottom: 0.35em; border-bottom: 1px solid #111; }
.story-block { margin: 0 0 0.6em; }
.story-position { font-style: italic; color: #333; font-size: 0.9em; margin: 0 0 0.2em; }
.story-title { margin: 0 0 0.35em; }
.story-body { margin: 0; }
.story-rule { border: 0; border-top: 1px solid #ccc; margin: 1.2em 0; }
.story { margin: 0; }
.original { margin-top: 1.2em; }
"""

# Shared Xteink baseline (X3, 528×792, buttons only) — identical on CrossPoint and
# CrossInk. Neither firmware applies font-size, line-height, color, or borders, so
# hierarchy is bold / italic / spacing only.
X3_EINK_CSS = """
body { margin: 0.45em 0.55em 0.75em; text-align: left; }
h1 { margin: 0 0 0.35em; font-weight: bold; text-align: left; }
h2 { margin: 0.9em 0 0.35em; font-weight: bold; text-align: left; }
h3 { margin: 0.8em 0 0.3em; font-weight: bold; text-align: left; }
h4 { margin: 0.6em 0 0.25em; font-weight: normal; font-style: italic; text-align: left; }
p { margin: 0 0 0.55em; text-align: left; }
a { text-decoration: none; }
.meta { font-style: italic; margin: 0 0 0.3em; }
.byline { font-style: italic; margin: 0 0 0.6em; }
.masthead { font-style: italic; margin: 0 0 0.45em; }
.cover-image { margin: 0 0 0.55em; text-align: center; }
.cover-img { width: 100%; }
.paper-title { margin: 0 0 0.2em; }
.cover-rule { margin: 0.55em 0 0.7em; }
.contents-title { margin: 0.25em 0 0.4em; }
.toc-category { margin: 0 0 0.7em; }
.toc-category-title { margin: 0 0 0.35em; }
.toc-source { margin: 0 0 0.55em 0.65em; }
.toc-source-title { margin: 0.45em 0 0.25em; }
.toc-stories { margin: 0 0 0 0.25em; }
.toc-story { margin: 0 0 0.45em; }
.toc-title { font-weight: bold; margin: 0 0 0.1em; }
.toc-digest { margin: 0; }
.category-plate { text-align: center; padding: 1.8em 0.35em 1em; }
.category-icon { width: 150px; height: 150px; margin: 0 0 0.85em; }
.plate-title { text-align: center; margin: 0 0 0.55em; }
.plate-sources { text-align: center; font-style: italic; margin: 1em 0 0.3em; }
.plate-count { text-align: center; margin: 0; }
.source-title { margin: 0 0 0.55em; }
.story-block { margin: 0 0 0.6em; }
.story-position { font-style: italic; margin: 0 0 0.15em; }
.story-title { margin: 0 0 0.25em; }
.story-body { margin: 0; }
.story-rule { margin: 0.9em 0; }
.story { margin: 0; }
.original { display: none; }
"""

# CrossInk-only extras: an inverted band behind section titles (shown when the
# reader honours publisher styling) and small-caps source names. CrossPoint
# ignores both properties, so a wrong firmware choice only looks plainer.
CROSSINK_EXTRA_CSS = """
.plate-title { background-color: #000; padding: 0.25em 0; }
.source-title { font-variant-caps: small-caps; }
.toc-source-title { font-variant-caps: small-caps; }
"""


def epub_stylesheet(opts: "EpubLayout") -> str:
    css = X3_EINK_CSS if opts.x3_screen else EINK_CSS
    if opts.crossink_extras:
        css += CROSSINK_EXTRA_CSS
    return css


_ALLOWED_TAGS = {"p", "br", "em", "strong", "b", "i", "u", "a", "ul", "ol", "li", "blockquote", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style"}
_DROP_TAGS = {"img", "video", "audio", "source", "iframe", "object", "embed"}
_WS_RE = re.compile(r"[ \t\f\v]+")


def _story_count_label(count: int) -> str:
    return f"{count} stor{'y' if count == 1 else 'ies'}"


def _category_plate_html(label: str, icon_href: str, sources: list[tuple[str, list[dict]]]) -> str:
    """Section page: icon, title, and what is inside (sources + story count)."""
    count = sum(len(items) for _source, items in sources)
    names = " · ".join(source for source, _items in sources)
    return (
        '<div class="category-plate">'
        f'<img class="category-icon" alt="" src="{html.escape(icon_href, quote=True)}"/>'
        f'<h1 class="plate-title">{html.escape(label)}</h1>'
        f'<p class="plate-sources">{html.escape(names)}</p>'
        f'<p class="plate-count">{html.escape(_story_count_label(count))}</p>'
        "</div>"
    )


def _outline_titles(label: str, sources: list[str], cat_index: int, *, numbered: bool) -> tuple[str, list[str]]:
    """Category / source titles for the Contents page and the reader's chapter menu.

    Numbers live in the title text itself: the reader's nested menu restarts every
    level at 1, so "1.1" only survives when it is part of the entry name.
    """
    if not numbered:
        return label, list(sources)
    return f"{cat_index}. {label}", [f"{cat_index}.{i} {source}" for i, source in enumerate(sources, start=1)]


def _cover_contents_html(groups: list[tuple[str, str, list[dict]]], *, opts: EpubLayout, summaries: bool) -> list[str]:
    blurb_limit = 90 if opts.x3_screen else 160
    bits = ['<div class="contents"><h2 class="contents-title">Contents</h2>']
    for cat_index, (_key, label, group) in enumerate(groups, start=1):
        by_source = group_stories_by_source(group)
        cat_heading, src_headings = _outline_titles(
            label, [source for source, _ in by_source], cat_index, numbered=opts.toc_outline_numbers
        )
        bits.append(f'<div class="toc-category"><h3 class="toc-category-title">{html.escape(cat_heading)}</h3>')
        for src_heading, (_source, source_stories) in zip(src_headings, by_source):
            bits.append(f'<div class="toc-source"><h4 class="toc-source-title">{html.escape(src_heading)}</h4>')
            if not opts.contents_stories():
                bits.append("</div>")
                continue
            bits.append('<div class="toc-stories">')
            for story in source_stories:
                title = story.get("title") or "Untitled"
                bits.append('<div class="toc-story">')
                bits.append(f'<div class="toc-title">{html.escape(title)}</div>')
                blurb = digest_blurb(story.get("summary") or "", limit=blurb_limit) if summaries else ""
                if blurb:
                    bits.append(f'<div class="toc-digest">{html.escape(blurb)}</div>')
                bits.append("</div>")
            bits.append("</div></div>")
        bits.append("</div>")
    bits.append("</div>")
    return bits


def write_epub(payload: dict, dest: Path, *, layout: EpubLayout | None = None) -> None:
    opts = layout if layout is not None else EpubLayout.classic()
    book = epub.EpubBook()
    heading = payload.get("title") or "NewsCast briefing"
    date_label = _payload_date_label(payload)
    paper_day = _payload_paper_date(payload)
    paper_title = (payload.get("paper_title") or "").strip() or f"{heading} {paper_day}"
    stories = payload.get("stories") or []
    book.set_identifier((payload.get("paper_id") or "").strip() or f"newscast-{paper_day}")
    book.set_title(paper_title)
    book.set_language("en")
    book.add_author("NewsCast")

    cover_bytes = render_newspaper_cover(
        heading=heading,
        date_label=date_label,
        stories=stories,
        x3_screen=opts.x3_screen,
    )
    book.set_cover("cover.jpg", cover_bytes, create_page=False)

    style = epub.EpubItem(
        uid="style", file_name="style/eink.css", media_type="text/css", content=epub_stylesheet(opts).encode()
    )
    book.add_item(style)

    def _chapter(title: str, file_name: str, content: str) -> epub.EpubHtml:
        chapter = epub.EpubHtml(title=title[:120], file_name=file_name, lang="en")
        chapter.content = content
        chapter.add_link(href="style/eink.css", rel="stylesheet", type="text/css")
        book.add_item(chapter)
        return chapter

    groups = group_stories(stories)
    cover_bits = [
        '<div class="cover-image"><img class="cover-img" alt="Front page" src="cover.jpg"/></div>',
        f'<h1 class="paper-title">{html.escape(heading)}</h1>',
        f'<p class="meta">{html.escape(date_label)}</p>',
        f'<p class="masthead">{html.escape(masthead_line(stories))}</p>',
    ]
    if stories:
        cover_bits.append('<hr class="cover-rule"/>')
        cover_bits.extend(_cover_contents_html(groups, opts=opts, summaries=opts.contents_summaries(len(stories))))
    else:
        cover_bits.append("<p>No stories yet.</p>")
    cover = _chapter(COVER_CHAPTER_TITLE, "cover.xhtml", "".join(cover_bits))

    body_chapters: list = []
    toc: list = []
    index = 1
    icon_uids: set[str] = set()

    def _ensure_category_icon(category_key: str) -> str:
        slug = slugify(category_key) or DEFAULT_CATEGORY
        file_name = f"images/cat-{slug}.png"
        uid = f"cat-icon-{slug}"
        if uid not in icon_uids:
            book.add_item(
                epub.EpubItem(
                    uid=uid,
                    file_name=file_name,
                    media_type="image/png",
                    content=render_category_icon(category_key),
                )
            )
            icon_uids.add(uid)
        return file_name

    for cat_index, (key, label, group) in enumerate(groups, start=1):
        by_source = group_stories_by_source(group)
        cat_title, src_titles = _outline_titles(
            label, [source for source, _ in by_source], cat_index, numbered=opts.toc_outline_numbers
        )
        # Every section and source is its own spine file, so each starts on a fresh page
        # on every firmware without relying on CSS page-break support.
        cat_chapter = _chapter(
            cat_title, f"category-{index}.xhtml", _category_plate_html(label, _ensure_category_icon(key), by_source)
        )
        body_chapters.append(cat_chapter)
        index += 1
        # The reader's chapter menu stops at source level: category -> sources.
        source_links: list = []
        for src_title, (source, source_stories) in zip(src_titles, by_source):
            total = len(source_stories)
            if opts.chapters_by_source:
                parts = [f'<div class="source-chapter"><h1 class="source-title">{html.escape(source)}</h1>']
                for position, story in enumerate(source_stories, start=1):
                    if position > 1:
                        parts.append('<hr class="story-rule"/>')
                    parts.append(_story_block_html(story, heading="h2", opts=opts, position=position, total=total))
                parts.append("</div>")
                chapter = _chapter(src_title, f"source-{index}.xhtml", "".join(parts))
                body_chapters.append(chapter)
                source_links.append(chapter)
                index += 1
                continue
            first_story_file = ""
            for position, story in enumerate(source_stories, start=1):
                title = story.get("title") or "Untitled"
                chapter = _chapter(
                    title,
                    f"story-{index}.xhtml",
                    _story_block_html(story, heading="h1", opts=opts, position=position, total=total),
                )
                body_chapters.append(chapter)
                first_story_file = first_story_file or chapter.file_name
                index += 1
            if first_story_file:
                link_uid = f"src-{cat_index}-{len(source_links) + 1}"
                source_links.append(epub.Link(first_story_file, src_title[:120], link_uid))
        toc.append((cat_chapter, tuple(source_links)) if source_links else cat_chapter)

    # The reader's footer shows the TOC entry for the current spine file; a spine file
    # with no entry inherits the previous one, and the first has none to inherit, so
    # the cover (front image, briefing, Contents) needs its own entry or it reads "Unnamed".
    book.toc = [cover, *toc]
    book.add_item(epub.EpubNcx())
    # The nav document stays in the manifest for the reader's chapter menu but out of
    # the spine, so the paper opens on the front cover instead of a bare outline.
    book.add_item(epub.EpubNav())
    book.spine = [cover, *body_chapters]
    dest.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(dest), book)


def normalize_briefing_day(value: str | None) -> str:
    key = (value or "today").strip().lower()
    if ISO_DAY_RE.fullmatch(key):
        return key
    return key if key in BRIEFING_DAYS else "today"


def paper_day_for(value: str | None = "today", now: datetime | None = None) -> date | None:
    key = normalize_briefing_day(value)
    if ISO_DAY_RE.fullmatch(key):
        try:
            return date.fromisoformat(key)
        except ValueError:
            return None
    today = _local_today(now)
    if key == "yesterday":
        return today - timedelta(days=1)
    if key == "all":
        return None
    return today


def briefing_path(day: str | None = "today") -> str:
    key = normalize_briefing_day(day)
    if key == "today":
        return "/"
    if key in BRIEFING_DAYS or ISO_DAY_RE.fullmatch(key):
        return f"/?day={key}"
    return "/"


def canonical_briefing_day(value: str | None, now: datetime | None = None) -> str:
    """Day filter key for the Briefing page: a specific date maps onto Today / Yesterday
    when it is one of those, and dates outside the retention window fall back to Today."""
    key = normalize_briefing_day(value)
    if not ISO_DAY_RE.fullmatch(key):
        return key
    target = paper_day_for(key, now=now)
    today = _local_today(now)
    if target is None or target > today or target <= today - timedelta(days=env.story_retention_days):
        return "today"
    if target == today:
        return "today"
    if target == today - timedelta(days=1):
        return "yesterday"
    return key


def briefing_day_label(key: str) -> str:
    """Short label for a specific-date segment ("24 Sep"); empty for Today/Yesterday/All."""
    if not ISO_DAY_RE.fullmatch(key or ""):
        return ""
    return date.fromisoformat(key).strftime("%d %b").lstrip("0")


def briefing_day_options(db: Session, user_id: int | None = None, now: datetime | None = None) -> list[dict]:
    """Every day in the retention window for the Briefing day picker, newest first."""
    today = _local_today(now)
    tz = _local_tz()
    with_stories: set[date] = set()
    for story in current_stories(db, day="all", user_id=user_id, now=now):
        when = _aware(story.published_at)
        if when is not None and not story.saved:
            with_stories.add(when.astimezone(tz).date())
    options = []
    for offset in range(max(1, env.story_retention_days)):
        day = today - timedelta(days=offset)
        key = "today" if offset == 0 else "yesterday" if offset == 1 else day.isoformat()
        options.append(
            {
                "key": key,
                "href": briefing_path(key),
                "name": "Today" if offset == 0 else "Yesterday" if offset == 1 else day.strftime("%A"),
                "date_label": day.strftime("%d %b").lstrip("0"),
                "has_stories": day in with_stories,
            }
        )
    return options


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _local_tz():
    return datetime.now().astimezone().tzinfo or timezone.utc


def _day_window(day: str, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    key = normalize_briefing_day(day)
    if key == "all":
        return None
    target = paper_day_for(key, now=now)
    if target is None:
        return None
    # Bound the paper day in the Pi's local timezone (not UTC midnight on that
    # calendar date), so household "today" matches the wall clock.
    start = datetime.combine(target, datetime.min.time(), tzinfo=_local_tz())
    return start, start + timedelta(days=1)


def _in_day(story: Story, window: tuple[datetime, datetime] | None) -> bool:
    if window is None:
        return True
    start, end = window
    # Day chips are calendar publish date only. Stories without a publish time
    # belong on All, not Today/Yesterday.
    when = _aware(story.published_at)
    if when is None:
        return False
    when_local = when.astimezone(start.tzinfo)
    return start <= when_local < end


def _apply_keyword_filters(db: Session, stories: list[Story]) -> list[Story]:
    feeds = {feed.name: feed for feed in db.query(Feed).all()}
    include = settings.get_value(db, "keyword_include")
    exclude = settings.get_value(db, "keyword_exclude")
    return [story for story in stories if story_kept(story, feeds, include, exclude)]


def _story_category(story: Story, feeds: dict[str, Feed]) -> str:
    if getattr(story, "saved", False):
        return SAVED_CATEGORY
    feed = feeds.get(story.source_name)
    return getattr(feed, "category", None) or DEFAULT_CATEGORY


def _apply_importance_filter(db: Session, stories: list[Story]) -> list[Story]:
    from app.services.importance import effective_importance

    minimum = settings.briefing_min_importance(db)
    feeds = {feed.name: feed for feed in db.query(Feed).all()}
    kept: list[Story] = []
    for story in stories:
        if getattr(story, "saved", False) or getattr(story, "favourited", False):
            kept.append(story)
            continue
        if effective_importance(story, category=_story_category(story, feeds)) >= minimum:
            kept.append(story)
    return kept


def digest_blurb(summary: str, *, limit: int = 160) -> str:
    text = summary or ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    compact = " ".join(text.split()).strip()
    if not compact:
        return ""
    for sep in (". ", "! ", "? "):
        index = compact.find(sep)
        if 8 <= index <= limit:
            return compact[: index + 1].strip()
    if len(compact) <= limit:
        return compact
    clipped = compact[: limit - 1].rsplit(" ", 1)[0]
    return (clipped or compact[: limit - 1]).rstrip(".,;:") + "…"


def masthead_line(stories: list[dict]) -> str:
    if not stories:
        return "No stories yet"
    total = len(stories)
    parts = [f"{total} stor{'y' if total == 1 else 'ies'}"]
    for _key, label, group in group_stories(stories):
        parts.append(f"{len(group)} {label.lower()}")
    return " · ".join(parts)


def retention_cutoff() -> datetime:
    return utcnow() - timedelta(days=env.story_retention_days)


def _story_age():
    return func.coalesce(Story.published_at, Story.created_at)


def _saved_still_current():
    now = utcnow()
    return or_(Story.expires_at.is_(None), Story.expires_at >= now)


def current_saved_stories(db: Session, user_id: int | None = None) -> list[Story]:
    query = db.query(Story).filter(Story.saved.is_(True)).filter(_saved_still_current())
    query = _scope_stories(query, user_id)
    return query.order_by(Story.created_at.desc()).all()


def _diverse_recent(stories: list[Story], limit: int) -> list[Story]:
    """Round-robin across sources; each source group is already newest-first."""
    by_source: dict[str, list[Story]] = {}
    for story in stories:
        by_source.setdefault(story.source_name, []).append(story)
    for group in by_source.values():
        group.sort(
            key=lambda story: story.published_at or story.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
    picked: list[Story] = []
    seen: set[int] = set()
    index = 0
    while len(picked) < limit:
        added = False
        for group in by_source.values():
            if index >= len(group):
                continue
            story = group[index]
            if story.id in seen:
                continue
            picked.append(story)
            seen.add(story.id)
            added = True
            if len(picked) >= limit:
                return picked
        if not added:
            break
        index += 1
    return picked


def current_stories(
    db: Session,
    limit: int | None = None,
    day: str | None = None,
    user_id: int | None = None,
    now: datetime | None = None,
) -> list[Story]:
    """Live Briefing list.

    Today / Yesterday: only stories with published_at on that local calendar day.
    Favourites and saved long-reads follow the same publish-day rule (no pin to Today).
    All: full retention window, newest first.
    """
    if limit is None:
        limit = settings.briefing_limit(db)
    day_key = normalize_briefing_day(day) if day else None
    cutoff = retention_cutoff()
    age = _story_age()
    window = _day_window(day_key, now=now) if day_key else None

    favourites_q = (
        db.query(Story)
        .filter(Story.favourited.is_(True))
    )
    favourites = _scope_stories(favourites_q, user_id).order_by(age.desc()).all()
    pool_size = max(limit * 8, 80) if settings.briefing_category_mix_enabled(db) else max(limit * 4, 40)
    # Include saved long-reads in the day-filtered pool (they must still pass _in_day).
    recent_q = (
        db.query(Story)
        .filter(or_(Story.favourited.is_(False), Story.favourited.is_(None)))
        .filter(age >= cutoff)
    )
    if window is not None:
        # Narrow to the chosen day (a day's margin either side for timezone storage)
        # before taking the newest pool, so an older day isn't crowded out by newer
        # stories. _in_day below still applies the exact local-day boundary.
        start, end = window
        low = (start.astimezone(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
        high = (end.astimezone(timezone.utc) + timedelta(days=1)).replace(tzinfo=None)
        recent_q = recent_q.filter(Story.published_at >= low).filter(Story.published_at < high)
    recent_query = _scope_stories(recent_q, user_id).order_by(age.desc())
    if day_key == "all":
        recent = recent_query.all()
    else:
        recent = recent_query.limit(pool_size).all()
    favourites = [story for story in favourites if _in_day(story, window)]
    recent = [story for story in recent if _in_day(story, window)]

    feeds = {feed.name: feed for feed in _scope_feeds(db.query(Feed), user_id).all()}
    favourites = _apply_keyword_filters(db, favourites)
    recent = _apply_importance_filter(db, _apply_keyword_filters(db, recent))

    if day_key == "all":
        selected = recent
    elif settings.briefing_category_mix_enabled(db):
        selected = _pick_by_category_mix(
            recent,
            feeds,
            limit,
            settings.briefing_category_shares(db),
        )
    else:
        selected = _diverse_recent(recent, limit)

    seen: set[int] = set()
    stories: list[Story] = []
    for story in [*favourites, *selected]:
        if story.id in seen:
            continue
        stories.append(story)
        seen.add(story.id)
    stories.sort(
        key=lambda story: story.published_at or story.created_at or cutoff,
        reverse=True,
    )
    return stories


def seats_from_percents(limit: int, percents: dict[str, float]) -> dict[str, int]:
    if limit <= 0 or not percents:
        return {key: 0 for key in percents}
    raw = {key: limit * (value / 100.0) for key, value in percents.items()}
    floors = {key: int(value) for key, value in raw.items()}
    remaining = limit - sum(floors.values())
    order = sorted(
        raw.keys(),
        key=lambda key: (raw[key] - floors[key], percents[key], key),
        reverse=True,
    )
    for key in order[: max(0, remaining)]:
        floors[key] += 1
    return floors


def category_slot_targets(
    limit: int,
    shares: dict[str, int],
    category_keys: list[str],
) -> dict[str, int]:
    """Map categories to story counts. Explicit % win slots; blank keys share leftover; 0 excludes."""
    keys = [key for key in category_keys if key]
    if limit <= 0 or not keys:
        return {key: 0 for key in keys}

    fixed = {key: shares[key] for key in keys if key in shares and shares[key] > 0}
    excluded = {key for key in keys if shares.get(key) == 0}
    auto = [key for key in keys if key not in fixed and key not in excluded]

    fixed_sum = sum(fixed.values())
    if fixed_sum > 100 and fixed:
        scaled = {key: (value * 100.0) / fixed_sum for key, value in fixed.items()}
        targets = seats_from_percents(limit, scaled)
        return {key: targets.get(key, 0) if key not in excluded else 0 for key in keys}

    remaining_pct = max(0, 100 - fixed_sum)
    percents: dict[str, float] = {key: float(value) for key, value in fixed.items()}
    if auto and remaining_pct > 0:
        each = remaining_pct / len(auto)
        for key in auto:
            percents[key] = each
    elif not auto and remaining_pct > 0 and fixed:
        boost = remaining_pct / len(fixed)
        for key in fixed:
            percents[key] = fixed[key] + boost

    targets = seats_from_percents(limit, percents) if percents else {}
    return {key: targets.get(key, 0) if key not in excluded else 0 for key in keys}


def _pick_by_category_mix(
    stories: list[Story],
    feeds: dict[str, Feed],
    limit: int,
    shares: dict[str, int],
) -> list[Story]:
    if limit <= 0 or not stories:
        return []

    by_category: dict[str, list[Story]] = {}
    for story in stories:
        key = _story_category(story, feeds)
        by_category.setdefault(key, []).append(story)
    for key, group in list(by_category.items()):
        by_category[key] = _diverse_recent(group, len(group))

    category_keys = sorted(set(by_category) | set(shares))
    targets = category_slot_targets(limit, shares, category_keys)

    picked: list[Story] = []
    seen: set[int] = set()
    leftovers: list[Story] = []
    for key in category_keys:
        pool = by_category.get(key, [])
        take = targets.get(key, 0)
        for story in pool[:take]:
            if story.id in seen:
                continue
            picked.append(story)
            seen.add(story.id)
        leftovers.extend(story for story in pool[take:] if story.id not in seen)

    if len(picked) < limit:
        leftovers.sort(
            key=lambda story: story.published_at or story.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for story in leftovers:
            if len(picked) >= limit:
                break
            if story.id in seen:
                continue
            picked.append(story)
            seen.add(story.id)

    return picked[:limit]


def _searchable_stories(db: Session, user_id: int | None):
    """Stories Search can return: within retention, plus favourites and Saved long-reads."""
    age = _story_age()
    query = db.query(Story).filter(
        or_(
            Story.favourited.is_(True),
            Story.saved.is_(True),
            age >= retention_cutoff(),
        )
    )
    return _scope_stories(query, user_id)


def _drop_expired_saved(stories: list[Story]) -> list[Story]:
    now = utcnow()
    kept: list[Story] = []
    for story in stories:
        if story.saved and story.expires_at is not None:
            expires = story.expires_at if story.expires_at.tzinfo else story.expires_at.replace(tzinfo=timezone.utc)
            if expires < now and not story.favourited:
                continue
        kept.append(story)
    return kept


def search_stories(
    db: Session,
    query: str,
    limit: int = 50,
    user_id: int | None = None,
    source: str | None = None,
) -> list[Story]:
    """Keyword and/or outlet search. Either one is enough; both narrow together."""
    term = (query or "").strip()
    outlet = (source or "").strip()
    if not term and not outlet:
        return []
    rows_q = _searchable_stories(db, user_id)
    if outlet:
        rows_q = rows_q.filter(Story.source_name == outlet)
    if term:
        pattern = f"%{term}%"
        rows_q = rows_q.filter(
            or_(
                Story.title.ilike(pattern),
                Story.summary.ilike(pattern),
                Story.source_name.ilike(pattern),
                Story.raw_excerpt.ilike(pattern),
            )
        )
    rows = rows_q.order_by(_story_age().desc()).limit(limit).all()
    return _drop_expired_saved(rows)


def search_outlets(db: Session, user_id: int | None = None) -> list[tuple[str, int]]:
    """(outlet, story count) for the Search outlet picker, alphabetical."""
    counts: dict[str, int] = {}
    for story in _drop_expired_saved(_searchable_stories(db, user_id).all()):
        name = (story.source_name or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: item[0].lower())


def story_breakdown(db: Session, user_id: int | None = None) -> dict:
    """Where every stored story sits, for Reader → Status. Buckets add up to ``total``.

    on_briefing: what Briefing → All shows (starred included, counted in ``starred``).
    saved: Saved long-reads (Saved tab, not Briefing).
    below_importance / keyword_filtered: stored but hidden by those Briefing filters.
    awaiting_cleanup: past the retention window; deleted when the next refresh completes.
    """
    rows = _scope_stories(db.query(Story), user_id).all()
    on_briefing = {story.id for story in current_stories(db, day="all", user_id=user_id) if not story.saved}
    cutoff = retention_cutoff()
    counts = {
        "total": len(rows),
        "on_briefing": 0,
        "starred": 0,
        "saved": 0,
        "keyword_filtered": 0,
        "below_importance": 0,
        "awaiting_cleanup": 0,
    }
    remaining: list[Story] = []
    for story in rows:
        if story.id in on_briefing:
            counts["on_briefing"] += 1
            counts["starred"] += 1 if story.favourited else 0
        elif story.saved:
            counts["saved"] += 1
        elif (_aware(story.published_at or story.created_at) or cutoff) < cutoff:
            counts["awaiting_cleanup"] += 1
        else:
            remaining.append(story)
    keyword_kept = _apply_keyword_filters(db, remaining)
    counts["keyword_filtered"] = len(remaining) - len(keyword_kept)
    # Anything else that passed the keyword filter was left off by the importance filter.
    counts["below_importance"] = len(keyword_kept)
    return counts


def purge_expired_stories(db: Session) -> int:
    cutoff = retention_cutoff()
    now = utcnow()
    regular = db.execute(
        delete(Story).where(
            or_(Story.saved.is_(False), Story.saved.is_(None)),
            or_(Story.favourited.is_(False), Story.favourited.is_(None)),
            _story_age() < cutoff,
        )
    )
    saved = db.execute(
        delete(Story).where(
            Story.saved.is_(True),
            Story.expires_at.isnot(None),
            Story.expires_at < now,
        )
    )
    return (regular.rowcount or 0) + (saved.rowcount or 0)


def format_published(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%d %b %Y")


def briefing_title(instance_name: str = "") -> str:
    name = (instance_name or "").strip()
    return f"NewsCast · {name}" if name else "NewsCast briefing"


def normalize_publish_at(value: str | None) -> str:
    raw = (value or "").strip().replace(".", ":")
    if not raw:
        return DEFAULT_PUBLISH_AT
    parts = raw.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return DEFAULT_PUBLISH_AT
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return DEFAULT_PUBLISH_AT
    return f"{hour:02d}:{minute:02d}"


def briefing_publish_at(db: Session) -> str:
    return normalize_publish_at(settings.get_value(db, "briefing_publish_at"))


def dated_stem(day: date) -> str:
    return f"news-{day.isoformat()}"


def dated_category_stem(day: date, category: str) -> str:
    slug = slugify(category) or DEFAULT_CATEGORY
    return f"news-{day.isoformat()}-{slug}"


def dated_briefing_path(day: date, suffix: str = "epub", user_id: int | None = None) -> Path:
    return briefing_dir_for(user_id) / f"{dated_stem(day)}.{suffix}"


def dated_category_path(day: date, category: str, suffix: str = "epub", user_id: int | None = None) -> Path:
    return briefing_dir_for(user_id) / f"{dated_category_stem(day, category)}.{suffix}"


def parse_category_from_stem(stem: str) -> str | None:
    match = CATEGORY_PAPER_STEM_RE.match((stem or "").strip())
    return match.group(2).lower() if match else None


def _local_today(now: datetime | None = None) -> date:
    when = now or datetime.now()
    return when.date() if isinstance(when, datetime) else when


def frozen_briefing_path(
    day: str | None = "today",
    *,
    suffix: str = "epub",
    fallback: bool = True,
    now: datetime | None = None,
    category: str | None = None,
    user_id: int | None = None,
) -> Path | None:
    key = normalize_briefing_day(day)
    target = paper_day_for(key, now=now)
    if target is None:
        return None
    slug = slugify(category or "") if category else ""
    path = (
        dated_category_path(target, slug, suffix, user_id=user_id)
        if slug
        else dated_briefing_path(target, suffix, user_id=user_id)
    )
    if path.exists():
        return path
    if fallback and key == "today":
        yesterday = _local_today(now) - timedelta(days=1)
        prior = (
            dated_category_path(yesterday, slug, suffix, user_id=user_id)
            if slug
            else dated_briefing_path(yesterday, suffix, user_id=user_id)
        )
        if prior.exists():
            return prior
    return None


def available_daily_papers(*, now: datetime | None = None, days: int = 2, user_id: int | None = None) -> list[date]:
    today = _local_today(now)
    found: list[date] = []
    for offset in range(max(1, days)):
        day = today - timedelta(days=offset)
        if dated_briefing_path(day, user_id=user_id).exists():
            found.append(day)
    return found


def available_category_papers(
    category: str,
    *,
    now: datetime | None = None,
    days: int = 2,
    user_id: int | None = None,
) -> list[date]:
    slug = slugify(category)
    if not slug:
        return []
    today = _local_today(now)
    found: list[date] = []
    for offset in range(max(1, days)):
        day = today - timedelta(days=offset)
        if dated_category_path(day, slug, user_id=user_id).exists():
            found.append(day)
    return found


def category_keys_with_papers(*, now: datetime | None = None, days: int = 2, user_id: int | None = None) -> list[str]:
    today = _local_today(now)
    found: set[str] = set()
    root = briefing_dir_for(user_id)
    for offset in range(max(1, days)):
        day = today - timedelta(days=offset)
        for path in root.glob(f"news-{day.isoformat()}-*.epub"):
            key = parse_category_from_stem(path.stem)
            if key:
                found.add(key)
    return sorted(found)


def write_category_briefing_files(
    db: Session,
    payload: dict,
    day: date,
    user_id: int | None = None,
) -> list[Path]:
    enabled = settings.briefing_category_opds_keys(db)
    root = briefing_dir_for(user_id)
    for path in root.glob(f"news-{day.isoformat()}-*.epub"):
        key = parse_category_from_stem(path.stem)
        if key and key not in enabled:
            path.unlink(missing_ok=True)
            path.with_suffix(".txt").unlink(missing_ok=True)
    if not enabled:
        return []
    labels = category_labels(db)
    grouped: dict[str, list[dict]] = {}
    for story in payload.get("stories") or []:
        key = slugify(str(story.get("category") or DEFAULT_CATEGORY)) or DEFAULT_CATEGORY
        if key not in enabled:
            continue
        grouped.setdefault(key, []).append(story)

    written: list[Path] = []
    for key, stories in grouped.items():
        if not stories:
            continue
        label = labels.get(key) or stories[0].get("category_label") or key
        cat_payload = dict(payload)
        cat_payload["stories"] = stories
        title = paper_category_display_title(db, day, label)
        cat_payload["title"] = title
        cat_payload["paper_title"] = title
        cat_payload["paper_id"] = f"newscast-{day.isoformat()}-{key}"
        paths = write_briefing_files(
            cat_payload, stem=dated_category_stem(day, key), user_id=user_id, db=db
        )
        written.append(paths["epub"])
    return written


def prune_old_briefings(keep: int = KEEP_DATED_BRIEFINGS, user_id: int | None = None) -> int:
    from app.services.paper_naming import day_from_briefing_path

    root = briefing_dir_for(user_id)
    main_files = sorted(root.glob("news-????-??-??.epub"), reverse=True)
    keep_days = {day_from_briefing_path(path.stem) for path in main_files[:keep]}
    keep_days.discard(None)
    removed = 0
    for path in root.glob("news-*.epub"):
        day = day_from_briefing_path(path.stem)
        if day is None or day in keep_days:
            continue
        path.unlink(missing_ok=True)
        path.with_suffix(".txt").unlink(missing_ok=True)
        removed += 1
    for path in root.glob("news-*.txt"):
        day = day_from_briefing_path(path.stem)
        if day is None or day in keep_days:
            continue
        path.unlink(missing_ok=True)
    return removed


def paper_status(db: Session, now: datetime | None = None, user_id: int | None = None) -> dict:
    when = now or datetime.now()
    today = _local_today(when)
    path = dated_briefing_path(today, user_id=user_id)
    publish_at = briefing_publish_at(db)
    published = path.exists()
    empty = frozen_paper_is_empty(user_id=user_id, day=today) if published else True
    story_count = len(list(current_stories(db, day="today", user_id=user_id, now=when)))
    if published and empty:
        message = (
            f"Today’s paper file exists but is empty (frozen at {publish_at} with no stories). "
            "Generate again once Briefing has stories, or wait for the next refresh."
        )
    elif published:
        message = f"Today’s paper is ready ({story_count} stor{'y' if story_count == 1 else 'ies'}). Scheduled publish {publish_at}."
    else:
        message = f"Publishes at {publish_at} — not ready yet."
    return {
        "publish_at": publish_at,
        "published": published,
        "empty": bool(published and empty),
        "story_count": story_count,
        "date": today.isoformat(),
        "path": str(path) if published else None,
        "message": message,
    }


def frozen_paper_is_empty(*, user_id: int | None = None, day: date | None = None) -> bool:
    """True when today's frozen txt/epub is missing content (empty shell)."""
    target = day or _local_today()
    txt = dated_briefing_path(target, "txt", user_id=user_id)
    if txt.exists():
        text = txt.read_text(encoding="utf-8", errors="replace")
        if "No stories yet" in text:
            return True
        if re.search(r"(?m)^\d+\. ", text):
            return False
        return len(text.strip()) < 80
    epub = dated_briefing_path(target, "epub", user_id=user_id)
    if not epub.exists():
        return True
    # Tiny EPUB cover-only shells are well under a normal paper with stories.
    return epub.stat().st_size < 20_000


def maybe_publish_daily_briefing(db: Session, now: datetime | None = None) -> Path | None:
    """Publish (or refill an empty shell) once past publish_at and stories exist."""
    from app.models import User

    when = now or datetime.now()
    hour, minute = (int(part) for part in briefing_publish_at(db).split(":"))
    if (when.hour, when.minute) < (hour, minute):
        for user in db.query(User).filter(User.active.is_(True)).all():
            prune_old_briefings(user_id=user.id)
        return None
    published: Path | None = None
    users = db.query(User).filter(User.active.is_(True)).all()
    targets = users or [None]
    for user in targets:
        uid = int(user.id) if user is not None else 1
        stories = list(current_stories(db, day="today", user_id=uid, now=when))
        if not stories:
            continue
        path = dated_briefing_path(when.date(), user_id=uid)
        if path.exists() and not frozen_paper_is_empty(user_id=uid, day=when.date()):
            continue
        overwrite = path.exists()
        published = publish_daily_briefing(db, now=when, user_id=uid, overwrite=overwrite)
    return published


def publish_daily_briefing(
    db: Session,
    *,
    now: datetime | None = None,
    overwrite: bool = False,
    user_id: int | None = None,
) -> Path:
    uid = int(user_id or 1)
    when = now or datetime.now()
    day = _local_today(when)
    dest = dated_briefing_path(day, user_id=uid)
    created = overwrite or not dest.exists()
    story_count = 0
    if created:
        payload = current_briefing_payload(db, day="today", user_id=uid, now=when)
        story_count = len(payload.get("stories") or [])
        payload["paper_date"] = day.isoformat()
        payload["date_label"] = format_paper_date(day, reader_date_format(db))
        payload["paper_title"] = paper_display_title(db, day)
        write_briefing_files(payload, stem=dated_stem(day), user_id=uid, db=db)
        write_category_briefing_files(db, payload, day, user_id=uid)
    prune_old_briefings(user_id=uid)
    if created:
        enqueue_latest_briefing(db, user_id=uid)
        from app.services import reader_config, reader_push

        # Only push a paper that actually has stories — empty shells confuse the reader.
        if story_count and reader_config.reader_push_enabled(db, uid):
            reader_push.enqueue_frozen_briefing(db, user_id=uid)
        from app.services import ntfy

        paper_title = paper_display_title(db, day)
        instance = (settings.get_value(db, "instance_name") or "").strip() or "NewsCast"
        if story_count:
            ntfy.notify(
                db,
                kind="publish",
                title=instance,
                body=f"Morning paper ready — {paper_title}",
                user_id=uid,
            )
    return dest


class _HtmlSanitizer(HTMLParser):
    def __init__(self, *, strip_links: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self.strip_links = strip_links

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip or tag in _DROP_TAGS:
            return
        if tag not in _ALLOWED_TAGS:
            return
        if tag == "br":
            self.parts.append("<br/>")
            return
        if tag == "a":
            if self.strip_links:
                return
            href = ""
            for key, value in attrs:
                if key == "href" and value and value.startswith(("http://", "https://", "/")):
                    href = html.escape(value, quote=True)
                    break
            self.parts.append(f'<a href="{href}">' if href else "<a>")
            return
        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or tag in _DROP_TAGS or tag not in _ALLOWED_TAGS or tag == "br":
            return
        if tag == "a" and self.strip_links:
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        # Collapse runs of spaces/tabs so e-ink readers don't show uneven gaps.
        cleaned = _WS_RE.sub(" ", data or "")
        if cleaned:
            self.parts.append(html.escape(cleaned))


def sanitize_html(value: str, *, strip_links: bool = False) -> str:
    raw = value or ""
    if "<" not in raw:
        return html.escape(_WS_RE.sub(" ", raw))
    parser = _HtmlSanitizer(strip_links=strip_links)
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)


def _story_body(summary: str, *, strip_links: bool = False) -> str:
    cleaned = sanitize_html(summary or "", strip_links=strip_links).strip()
    if not cleaned:
        return "<p></p>"
    if cleaned.lstrip().startswith("<"):
        return cleaned
    return f"<p>{cleaned}</p>"


def _payload_date_label(payload: dict) -> str:
    explicit = str(payload.get("date_label") or "").strip()
    if explicit:
        return explicit
    paper = str(payload.get("paper_date") or "").strip()
    if paper:
        try:
            return date.fromisoformat(paper[:10]).strftime("%d %b %Y")
        except ValueError:
            pass
    raw = str(payload.get("generated_at") or "")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone().strftime("%d %b %Y")
    except ValueError:
        return raw[:10]


def _payload_paper_date(payload: dict) -> str:
    paper = str(payload.get("paper_date") or "").strip()
    if paper:
        try:
            return date.fromisoformat(paper[:10]).isoformat()
        except ValueError:
            pass
    raw = str(payload.get("generated_at") or "")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone().date().isoformat()
    except ValueError:
        return _local_today().isoformat()


def group_stories(stories: list[dict], labels: dict[str, str] | None = None) -> list[tuple[str, str, list[dict]]]:
    names = labels or {}
    order = [SAVED_CATEGORY, *BUILTIN_LABELS.keys()]
    grouped: dict[str, list[dict]] = {}
    extras: list[str] = []
    for story in stories:
        key = story.get("category") or DEFAULT_CATEGORY
        if key not in grouped:
            grouped[key] = []
            if key not in order:
                extras.append(key)
        grouped[key].append(story)
    result: list[tuple[str, str, list[dict]]] = []
    for key in [*order, *extras]:
        items = grouped.get(key)
        if not items:
            continue
        if key == SAVED_CATEGORY:
            label = SAVED_CATEGORY_LABEL
        else:
            label = names.get(key) or BUILTIN_LABELS.get(key) or items[0].get("category_label") or key
        result.append((key, label, items))
    return result


def group_stories_by_source(stories: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: list[tuple[str, list[dict]]] = []
    index: dict[str, list[dict]] = {}
    for story in stories:
        source = (story.get("source") or "").strip() or "Unknown"
        bucket = index.get(source)
        if bucket is None:
            bucket = []
            index[source] = bucket
            groups.append((source, bucket))
        bucket.append(story)
    return groups


def stories_payload(
    stories: list[Story],
    instance_name: str = "",
    *,
    feeds: dict[str, Feed] | None = None,
    labels: dict[str, str] | None = None,
) -> dict:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    title = briefing_title(instance_name)
    feed_map = feeds or {}
    names = labels or {}
    items = []
    for story in stories:
        saved = bool(getattr(story, "saved", False))
        if saved:
            category = SAVED_CATEGORY
            category_label = SAVED_CATEGORY_LABEL
        else:
            feed = feed_map.get(story.source_name)
            category = getattr(feed, "category", None) or DEFAULT_CATEGORY
            category_label = names.get(category) or BUILTIN_LABELS.get(category, category)
        items.append(
            {
                "id": str(story.id),
                "title": story.title,
                "summary": story.summary,
                "source": story.source_name,
                "url": story.canonical_url,
                "published_at": (
                    story.published_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    if story.published_at
                    else None
                ),
                "published_label": format_published(story.published_at or story.created_at),
                "category": category,
                "category_label": category_label,
                "saved": saved,
            }
        )
    return {
        "title": title,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "device": "xteink-x3",
        "stories": items,
    }


def render_txt(payload: dict, *, layout: EpubLayout | None = None) -> str:
    opts = layout if layout is not None else EpubLayout.classic()
    lines = [payload.get("title") or "NewsCast briefing", payload["generated_at"], ""]
    stories = payload.get("stories") or []
    lines.append(masthead_line(stories))
    lines.append("")
    if not stories:
        lines.append("No stories yet. Refresh from the NewsCast UI.")
        return "\n".join(lines) + "\n"
    index = 1
    for _key, label, group in group_stories(stories):
        lines.append(label)
        lines.append("")
        for source, source_stories in group_stories_by_source(group):
            lines.append(source)
            lines.append("")
            for story in source_stories:
                lines.append(f"{index}. {story['title']}")
                if story.get("published_label"):
                    lines.append(story["published_label"])
                lines.append(story["summary"])
                if story.get("url") and not opts.omit_article_links:
                    lines.append(story["url"])
                lines.append("")
                index += 1
    return "\n".join(lines).rstrip() + "\n"


def _story_block_html(
    story: dict,
    *,
    heading: str,
    opts: EpubLayout,
    position: int = 1,
    total: int = 1,
) -> str:
    title = story.get("title") or "Untitled"
    # "3 of 8" — your place within the source; menu entries stop at source level.
    position_line = f'<p class="story-position">{position} of {total}</p>' if total > 1 else ""
    byline_bits = [bit for bit in (story.get("source"), story.get("published_label")) if bit]
    # When chapter is already the source, skip repeating source in the byline.
    if opts.chapters_by_source:
        byline_bits = [bit for bit in (story.get("published_label"),) if bit]
    byline = f'<p class="byline">{html.escape(" · ".join(byline_bits))}</p>' if byline_bits else ""
    url = story.get("url") or ""
    link = ""
    if url and not opts.omit_article_links:
        link = f'<p class="original"><a href="{html.escape(url, quote=True)}">Original article</a></p>'
    body = _story_body(story.get("summary") or "", strip_links=opts.omit_article_links)
    block_class = "story-block" if opts.chapters_by_source else "story"
    return (
        f'<div class="{block_class}">{position_line}'
        f'<{heading} class="story-title">{html.escape(title)}</{heading}>{byline}'
        f'<div class="story-body">{body}</div>{link}</div>'
    )


def write_briefing_files(
    payload: dict,
    stem: str = "news",
    user_id: int | None = None,
    *,
    layout: EpubLayout | None = None,
    db: Session | None = None,
) -> dict[str, Path]:
    opts = layout
    if opts is None and db is not None:
        opts = EpubLayout.from_db(db, user_id=user_id)
    opts = opts if opts is not None else EpubLayout.classic()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in stem).strip("-") or "news"
    root = briefing_dir_for(user_id)
    txt_path = root / f"{safe}.txt"
    epub_path = root / f"{safe}.epub"
    txt_path.write_text(render_txt(payload, layout=opts), encoding="utf-8")
    write_epub(payload, epub_path, layout=opts)
    return {"txt": txt_path, "epub": epub_path}


def publication_include_saved(db: Session, user_id: int | None = None) -> bool:
    """Per-user: append Saved long-reads to today's frozen paper (default off)."""
    if user_id is None:
        return False
    from app.services import user_settings as user_settings_service

    return user_settings_service.flag_enabled(db, int(user_id), "publication_include_saved")


def current_briefing_payload(
    db: Session,
    day: str | None = "today",
    user_id: int | None = None,
    now: datetime | None = None,
) -> dict:
    feeds = {feed.name: feed for feed in _scope_feeds(db.query(Feed), user_id).all()}
    stories = list(current_stories(db, day=day, user_id=user_id, now=now))
    day_key = normalize_briefing_day(day)
    if day_key == "today" and publication_include_saved(db, user_id):
        seen = {story.id for story in stories}
        for story in current_saved_stories(db, user_id=user_id):
            if story.id in seen:
                continue
            stories.append(story)
            seen.add(story.id)
    return stories_payload(
        stories,
        settings.get_value(db, "instance_name"),
        feeds=feeds,
        labels=category_labels(db),
    )


def enqueue_latest_briefing(db: Session, user_id: int | None = None) -> SyncTask | None:
    uid = int(user_id or 1)
    fmt = (settings.get_value(db, "x3_briefing_format") or env.x3_briefing_format or "txt").lower()
    if fmt not in {"txt", "epub"}:
        fmt = "txt"
    path = frozen_briefing_path("today", suffix=fmt, fallback=False, user_id=uid)
    if path is None:
        return None
    day = day_from_briefing_path(path.stem) or date.fromisoformat(path.stem.removeprefix("news-")[:10])
    return enqueue_sync_file(db, path, paper_download_name(db, day, suffix=fmt), user_id=uid)


def enqueue_sync_file(
    db: Session,
    path: Path,
    save_name: str,
    *,
    kind: str = "x3",
    save_path: str | None = None,
    user_id: int | None = None,
) -> SyncTask:
    uid = int(user_id or 1)
    device_id = settings.get_value(db, "x3_device_id") or env.x3_device_id or ""
    dest = save_path or (env.x3_save_path.rstrip("/") + "/" + save_name)
    kind_name = kind if kind in {"x3", "crosspoint"} else "x3"
    existing = (
        db.query(SyncTask)
        .filter(SyncTask.user_id == uid)
        .filter(SyncTask.kind == kind_name)
        .filter(SyncTask.status == "pending")
        .filter(SyncTask.save_path == dest)
        .first()
    )
    if existing:
        existing.file_path = str(path)
        existing.size = path.stat().st_size
        db.commit()
        db.refresh(existing)
        return existing
    task = SyncTask(
        user_id=uid,
        task_id=uuid.uuid4().hex,
        device_id=device_id,
        status="pending",
        kind=kind_name,
        file_path=str(path),
        save_path=dest,
        size=path.stat().st_size,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task
