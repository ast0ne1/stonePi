from __future__ import annotations

import io
import re
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

COVER_WIDTH = 1200
COVER_HEIGHT = 1600
PAPER = (248, 244, 235)
INK = (18, 18, 18)
RULE = (40, 40, 40)
MUTED = (70, 70, 70)

_FONT_CANDIDATES = {
    "serif": [
        Path(r"C:\Windows\Fonts\georgia.ttf"),
        Path(r"C:\Windows\Fonts\times.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSerif.ttf"),
    ],
    "serif_bold": [
        Path(r"C:\Windows\Fonts\georgiab.ttf"),
        Path(r"C:\Windows\Fonts\timesbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
        Path("/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf"),
    ],
    "sans": [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
    ],
}


def _load_font(kind: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES.get(kind, []):
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(text: str, width: int) -> list[str]:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    return textwrap.wrap(cleaned, width=width, break_long_words=True, break_on_hyphens=True)


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    gap: int,
    max_width: int | None = None,
) -> int:
    cursor = y
    for line in lines:
        draw.text((x, cursor), line, font=font, fill=fill)
        box = draw.textbbox((0, 0), line, font=font)
        cursor += (box[3] - box[1]) + gap
        if max_width is not None and cursor - y > max_width:
            break
    return cursor


def _plain(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(cleaned.split())


def _story_title(story: dict) -> str:
    return _plain(story.get("title") or "Untitled") or "Untitled"


def _story_deck(story: dict, limit: int = 160) -> str:
    from app.services.summarize import clean_summary

    raw = clean_summary(_plain(story.get("summary") or ""))
    if not raw:
        return ""
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rsplit(" ", 1)[0] + "…"


def _pick_stories(stories: list[dict]) -> tuple[dict | None, list[dict]]:
    items = [story for story in stories if _story_title(story)]
    if not items:
        return None, []
    return items[0], items[1:5]


def render_newspaper_cover(
    *,
    heading: str,
    date_label: str,
    stories: list[dict],
    x3_screen: bool = False,
) -> bytes:
    # X3 native portrait is 528×792; keep a 3:4 ratio for the cover JPEG.
    width = 528 if x3_screen else COVER_WIDTH
    height = 792 if x3_screen else COVER_HEIGHT
    scale = width / COVER_WIDTH

    def fz(size: int) -> int:
        return max(10, int(round(size * scale)))

    image = Image.new("RGB", (width, height), PAPER)
    draw = ImageDraw.Draw(image)

    masthead = _load_font("serif_bold", fz(78))
    date_font = _load_font("sans", fz(28))
    kicker = _load_font("sans", fz(22))
    main_font = _load_font("serif_bold", fz(54))
    deck_font = _load_font("serif", fz(28))
    side_font = _load_font("serif_bold", fz(30))
    side_deck = _load_font("serif", fz(22))
    footer_font = _load_font("sans", fz(20))

    margin = max(18, int(round(64 * scale)))
    content_w = width - (margin * 2)
    y = max(16, int(round(56 * scale)))

    brand = " ".join((heading or "NewsCast").split()) or "NewsCast"
    brand_lines = _wrap(brand.upper(), 18 if x3_screen else 22)
    for line in brand_lines:
        box = draw.textbbox((0, 0), line, font=masthead)
        draw.text(((width - (box[2] - box[0])) // 2, y), line, font=masthead, fill=INK)
        y += (box[3] - box[1]) + 4

    y += max(4, int(round(8 * scale)))
    date_text = (date_label or "").strip()
    if date_text:
        box = draw.textbbox((0, 0), date_text, font=date_font)
        draw.text(((width - (box[2] - box[0])) // 2, y), date_text, font=date_font, fill=MUTED)
        y += (box[3] - box[1]) + max(8, int(round(18 * scale)))

    rule_w = 3 if x3_screen else 4
    draw.line((margin, y, width - margin, y), fill=RULE, width=rule_w)
    y += max(6, int(round(10 * scale)))
    draw.line((margin, y, width - margin, y), fill=RULE, width=1)
    y += max(12, int(round(28 * scale)))

    main, flanks = _pick_stories(stories)
    if main is None:
        empty = _wrap("No stories in today's paper yet.", 22 if x3_screen else 34)
        _draw_lines(draw, empty, x=margin, y=y, font=main_font, fill=INK, gap=max(6, int(10 * scale)))
    else:
        source = (main.get("source") or "").strip()
        if source:
            draw.text((margin, y), source.upper(), font=kicker, fill=MUTED)
            y += max(16, int(round(34 * scale)))

        main_lines = _wrap(_story_title(main), 20 if x3_screen else 28)
        y = _draw_lines(
            draw,
            main_lines[:4 if x3_screen else 5],
            x=margin,
            y=y,
            font=main_font,
            fill=INK,
            gap=max(4, int(8 * scale)),
        )
        y += max(8, int(round(16 * scale)))

        deck_lines = _wrap(_story_deck(main, 140 if x3_screen else 220), 28 if x3_screen else 52)
        y = _draw_lines(
            draw,
            deck_lines[:3 if x3_screen else 4],
            x=margin,
            y=y,
            font=deck_font,
            fill=MUTED,
            gap=max(3, int(6 * scale)),
        )
        y += max(10, int(round(24 * scale)))

        draw.line((margin, y, width - margin, y), fill=RULE, width=2)
        y += max(10, int(round(24 * scale)))

        if flanks and not x3_screen:
            col_gap = 28
            col_w = (content_w - col_gap) // 2
            left = flanks[0:2]
            right = flanks[2:4]
            if not right and len(flanks) > 1:
                left = flanks[0:1]
                right = flanks[1:2]

            left_y = y
            right_y = y
            for story in left:
                left_y = _draw_flank(
                    draw,
                    story,
                    x=margin,
                    y=left_y,
                    width=col_w,
                    title_font=side_font,
                    deck_font=side_deck,
                    kicker_font=kicker,
                )
                left_y += 22
            for story in right:
                right_y = _draw_flank(
                    draw,
                    story,
                    x=margin + col_w + col_gap,
                    y=right_y,
                    width=col_w,
                    title_font=side_font,
                    deck_font=side_deck,
                    kicker_font=kicker,
                )
                right_y += 22
            y = max(left_y, right_y)
        elif flanks and x3_screen:
            # Single column of secondary headlines — two-col is too cramped at 528px.
            for story in flanks[:3]:
                y = _draw_flank(
                    draw,
                    story,
                    x=margin,
                    y=y,
                    width=content_w,
                    title_font=side_font,
                    deck_font=side_deck,
                    kicker_font=kicker,
                )
                y += max(10, int(round(16 * scale)))

    footer_y = height - max(36, int(round(70 * scale)))
    draw.line((margin, footer_y, width - margin, footer_y), fill=RULE, width=1)
    mark = "NewsCast daily briefing"
    box = draw.textbbox((0, 0), mark, font=footer_font)
    draw.text(
        ((width - (box[2] - box[0])) // 2, footer_y + max(8, int(round(18 * scale)))),
        mark,
        font=footer_font,
        fill=MUTED,
    )

    buffer = io.BytesIO()
    # Baseline JPEG, modest quality — CrossPoint / X3 prefer simple images.
    image.save(buffer, format="JPEG", quality=82 if x3_screen else 88, optimize=True)
    return buffer.getvalue()


def _draw_flank(
    draw: ImageDraw.ImageDraw,
    story: dict,
    *,
    x: int,
    y: int,
    width: int,
    title_font: ImageFont.ImageFont,
    deck_font: ImageFont.ImageFont,
    kicker_font: ImageFont.ImageFont,
) -> int:
    char_w = max(18, width // 14)
    source = (story.get("source") or "").strip()
    cursor = y
    if source:
        draw.text((x, cursor), source.upper(), font=kicker_font, fill=MUTED)
        cursor += 28
    title_lines = _wrap(_story_title(story), char_w)
    cursor = _draw_lines(draw, title_lines[:4], x=x, y=cursor, font=title_font, fill=INK, gap=4)
    cursor += 8
    deck_lines = _wrap(_story_deck(story, 120), char_w + 6)
    cursor = _draw_lines(draw, deck_lines[:3], x=x, y=cursor, font=deck_font, fill=MUTED, gap=4)
    return cursor
