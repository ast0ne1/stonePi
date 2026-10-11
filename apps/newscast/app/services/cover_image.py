from __future__ import annotations

import io
import math
import re
import textwrap
import time
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

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


# Lead photo: the feed's own thumbnail, fetched once at publish time and drawn as a
# black-and-white dot pattern so it reads on e-ink. Anything wrong -> placeholder.
PHOTO_MAX_BYTES = 4_000_000
PHOTO_TIMEOUT = httpx.Timeout(8.0, connect=4.0)


# One publish builds the main paper plus category papers for each account, often with
# the same lead; remember recent downloads (and failures) briefly instead of refetching.
PHOTO_CACHE_SECONDS = 1800
PHOTO_CACHE_MAX = 16
_photo_cache: dict[str, tuple[float, bytes | None]] = {}


def fetch_cover_photo(url: str) -> bytes | None:
    """Download a story thumbnail through the feed fetcher's address guard; None on any failure."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    now = time.monotonic()
    cached = _photo_cache.get(url)
    if cached is not None and now - cached[0] < PHOTO_CACHE_SECONDS:
        return cached[1]
    data = _download_cover_photo(url)
    if len(_photo_cache) >= PHOTO_CACHE_MAX:
        _photo_cache.pop(min(_photo_cache, key=lambda key: _photo_cache[key][0]))
    _photo_cache[url] = (now, data)
    return data


def _download_cover_photo(url: str) -> bytes | None:
    from app.services import net_guard
    from app.services.ingest import USER_AGENT

    try:
        with httpx.Client(
            timeout=PHOTO_TIMEOUT,
            follow_redirects=True,
            event_hooks=net_guard.EVENT_HOOKS,
            headers={"User-Agent": USER_AGENT, "Accept": "image/*"},
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                if not (response.headers.get("content-type") or "image/").lower().startswith("image/"):
                    return None
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > PHOTO_MAX_BYTES:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
    except Exception:  # noqa: BLE001
        return None


def lead_story(stories: list[dict]) -> dict | None:
    """The story the cover leads with (the same pick render_newspaper_cover makes)."""
    return _pick_stories(stories)[0]


def _photo_panel(data: bytes, size: tuple[int, int]) -> Image.Image | None:
    try:
        with Image.open(io.BytesIO(data)) as source:
            original = source.size
            # Decode near the panel size, not the full photo: a big JPEG would otherwise
            # cost hundreds of MB on the Pi in the middle of the publish job.
            source.draft("L", (size[0] * 2, size[1] * 2))
            source.thumbnail((size[0] * 3, size[1] * 3))
            photo = ImageOps.exif_transpose(source).convert("L")
    except Exception:  # noqa: BLE001
        return None
    if original[0] < 80 or original[1] < 60:
        return None  # tracking pixels and tiny icons look worse than the placeholder
    photo = ImageOps.fit(photo, size, method=Image.LANCZOS)
    photo = ImageOps.autocontrast(photo, cutoff=1)
    dots = photo.convert("1")  # Floyd-Steinberg: newspaper-style dots, pure black on paper
    panel = Image.composite(Image.new("RGB", size, PAPER), Image.new("RGB", size, INK), dots)
    ImageDraw.Draw(panel).rectangle((0, 0, size[0] - 1, size[1] - 1), outline=INK, width=1)
    return panel


def _icon_mask(category_key: str, size: int) -> Image.Image:
    """Category glyph as a smooth mask (ink where the glyph is) at any size."""
    with Image.open(io.BytesIO(render_category_icon(category_key))) as icon:
        glyph = ImageOps.invert(icon.convert("L"))
    return glyph.resize((size, size), Image.LANCZOS)


HALFTONE_PITCH = 7


def _halftone(size: tuple[int, int], *, light: float = 0.1, dark: float = 0.6) -> Image.Image:
    """Newspaper-style halftone: a dot grid shading diagonally from light to dark."""
    width, height = size
    panel = Image.new("RGB", size, PAPER)
    draw = ImageDraw.Draw(panel)
    pitch = HALFTONE_PITCH
    span = max(1, width + height)
    for row, cy in enumerate(range(pitch // 2, height + pitch, pitch)):
        offset = pitch // 2 if row % 2 else 0  # staggered rows, like a 45° screen
        for cx in range(offset, width + pitch, pitch):
            shade = light + (dark - light) * ((cx + cy) / span)
            radius = pitch * 0.62 * math.sqrt(max(0.0, min(1.0, shade)))
            if radius >= 0.6:
                draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=INK)
    return panel


def _placeholder_panel(category_key: str, label: str, size: tuple[int, int], font) -> Image.Image:
    """Stand-in for a missing lead photo: full-bleed halftone with the category knocked out in white."""
    width, height = size
    panel = _halftone(size)
    draw = ImageDraw.Draw(panel)
    band = 30 if label else 0
    # A white disc with an ink ring, like a printer's stamp, keeps the glyph clean on the dots.
    disc = int(min(width, height - band) * 0.72)
    cx, cy = width // 2, (height - band) // 2
    draw.ellipse((cx - disc // 2 - 4, cy - disc // 2 - 4, cx + disc // 2 + 4, cy + disc // 2 + 4), fill=PAPER)
    draw.ellipse((cx - disc // 2, cy - disc // 2, cx + disc // 2, cy + disc // 2), outline=INK, width=3)
    icon_size = int(disc * 0.6)
    spot = (cx - icon_size // 2, cy - icon_size // 2)
    panel.paste(Image.new("RGB", (icon_size, icon_size), INK), spot, _icon_mask(category_key, icon_size))
    if label:
        draw.rectangle((0, height - band, width, height), fill=INK)
        text = label.upper()
        while text and draw.textlength(text, font=font) > width - 16:
            text = text[:-2].rstrip() + "…"
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(
            ((width - (box[2] - box[0])) // 2, height - band + (band - (box[3] + box[1])) // 2),
            text, font=font, fill=PAPER,
        )
    draw.rectangle((0, 0, width - 1, height - 1), outline=INK, width=2)
    return panel


def _wrap_px(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap to a pixel width (character counts overrun narrow columns)."""
    lines: list[str] = []
    line = ""
    words: list[str] = []
    for word in " ".join((text or "").split()).split(" "):
        # A single word wider than the column (a URL, a long compound) is cut into pieces.
        while word and draw.textlength(word, font=font) > max_width:
            cut = len(word) - 1
            while cut > 1 and draw.textlength(word[:cut], font=font) > max_width:
                cut -= 1
            words.append(word[:cut])
            word = word[cut:]
        words.append(word)
    for word in words:
        trial = f"{line} {word}".strip()
        if not line or draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _issue_sections(stories: list[dict]) -> list[tuple[str, str, int]]:
    """(category key, label, story count) in paper order."""
    sections: dict[str, list] = {}
    for story in stories:
        key = (story.get("category") or "news").strip().lower() or "news"
        label = (story.get("category_label") or key).strip()
        entry = sections.setdefault(key, [label, 0])
        entry[1] += 1
    return [(key, label, count) for key, (label, count) in sections.items()]


ISSUE_STRIP_CELLS = 5


def _draw_issue_strip(
    image: Image.Image, draw: ImageDraw.ImageDraw, stories: list[dict], *, x: int, y: int, width: int, kicker, label_font
) -> None:
    sections = _issue_sections(stories)
    if not sections:
        return
    if len(sections) > ISSUE_STRIP_CELLS:
        # Never leave a section out: the last cell sums up the rest.
        shown, rest = sections[: ISSUE_STRIP_CELLS - 1], sections[ISSUE_STRIP_CELLS - 1:]
        sections = shown + [("", f"+{len(rest)} more", sum(count for _key, _label, count in rest))]
    draw.text((x, y), "IN THIS ISSUE", font=kicker, fill=MUTED)
    y += 22
    cell = width // len(sections)
    icon = 34
    for index, (key, label, count) in enumerate(sections):
        left = x + index * cell
        if key:
            image.paste(Image.new("RGB", (icon, icon), INK), (left + (cell - icon) // 2, y), _icon_mask(key, icon))
        else:
            box = draw.textbbox((0, 0), label, font=kicker)
            draw.text((left + (cell - (box[2] - box[0])) // 2, y + (icon - box[3]) // 2), label, font=kicker, fill=INK)
            label = "sections"
        for row, text in enumerate((label, f"{count} stor{'y' if count == 1 else 'ies'}")):
            while text and draw.textlength(text, font=label_font) > cell - 6:
                text = text[:-2].rstrip() + "…"
            box = draw.textbbox((0, 0), text, font=label_font)
            draw.text((left + (cell - (box[2] - box[0])) // 2, y + icon + 6 + row * 15), text, font=label_font,
                      fill=INK if row == 0 else MUTED)


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
    lead_photo: bytes | None = None,
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
        y += box[3] + 4

    y += max(4, int(round(8 * scale)))
    date_text = (date_label or "").strip()
    if date_text:
        box = draw.textbbox((0, 0), date_text, font=date_font)
        draw.text(((width - (box[2] - box[0])) // 2, y), date_text, font=date_font, fill=MUTED)
        y += box[3] + max(8, int(round(18 * scale)))

    rule_w = 3 if x3_screen else 4
    draw.line((margin, y, width - margin, y), fill=RULE, width=rule_w)
    y += max(6, int(round(10 * scale)))
    draw.line((margin, y, width - margin, y), fill=RULE, width=1)
    y += max(12, int(round(28 * scale)))

    main, flanks = _pick_stories(stories)
    if main is None:
        empty = _wrap("No stories in today's paper yet.", 22 if x3_screen else 34)
        _draw_lines(draw, empty, x=margin, y=y, font=main_font, fill=INK, gap=max(6, int(10 * scale)))
    elif x3_screen:
        y = _draw_x3_lead(
            image, draw, main, stories, [story for story in stories if _story_title(story)], lead_photo,
            x=margin, y=y, width=content_w, height=height, scale=scale,
            fonts={"kicker": kicker, "main": main_font, "deck": deck_font, "side": side_font,
                   "side_deck": side_deck, "footer": footer_font, "brief": _load_font("serif_bold", fz(27))},
        )
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


def _line_height(font) -> int:
    box = font.getbbox("Ag")
    return box[3] - box[1]


_X3_STEP = {"kicker": 4, "title": 3, "deck": 2, "gap": 0}


def _x3_story_lines(draw: ImageDraw.ImageDraw, story: dict, width: int, fonts: dict) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    source = (story.get("source") or "").strip()
    if source:
        lines.append(("kicker", source.upper()))
    lines += [("title", line) for line in _wrap_px(draw, _story_title(story), fonts["side"], width)[:3]]
    lines.append(("gap", ""))
    lines += [("deck", line) for line in _wrap_px(draw, _story_deck(story, 150), fonts["side_deck"], width)[:3]]
    return lines


def _x3_font(fonts: dict, kind: str):
    return {"kicker": fonts["kicker"], "title": fonts["side"], "deck": fonts["side_deck"],
            "brief": fonts["brief"], "gap": fonts["side_deck"]}[kind]


def _x3_lines_height(lines: list[tuple[str, str]], fonts: dict) -> int:
    return sum(
        (4 if kind == "gap" else _line_height(_x3_font(fonts, kind)) + _X3_STEP.get(kind, 3)) for kind, _ in lines
    )


def _draw_x3_lines(draw: ImageDraw.ImageDraw, lines: list[tuple[str, str]], *, x: int, y: int, fonts: dict) -> int:
    fills = {"kicker": MUTED, "title": INK, "deck": MUTED, "brief": INK}
    for kind, text in lines:
        if kind == "gap":
            y += 4
            continue
        font = _x3_font(fonts, kind)
        draw.text((x, y - font.getbbox("Ag")[1]), text, font=font, fill=fills[kind])
        y += _line_height(font) + _X3_STEP.get(kind, 3)
    return y


def _shorten(draw: ImageDraw.ImageDraw, lines: list[str], keep: int, font, width: int) -> list[str]:
    """First ``keep`` lines, the last ending in an ellipsis when text was cut."""
    if len(lines) <= keep:
        return lines
    kept = lines[:keep]
    last = kept[-1]
    while last and draw.textlength(last + "…", font=font) > width:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    return kept[:-1] + [last.rstrip(" ,;:") + "…"]


def _draw_in_brief(
    draw: ImageDraw.ImageDraw,
    briefs: list[dict],
    stories: list[dict],
    *,
    x: int,
    y: int,
    width: int,
    bottom: int,
    fonts: dict,
) -> int:
    """Right-hand column: short source + headline items, or an index of sections when stories run out."""
    drawn = 0
    if briefs:
        draw.text((x, y), "IN BRIEF", font=fonts["kicker"], fill=INK)
        cursor = y + _line_height(fonts["kicker"]) + 8
        for story in briefs:
            kicker_line = [("kicker", (story.get("source") or "").strip().upper())] if story.get("source") else []
            headline = _wrap_px(draw, _story_title(story), fonts["brief"], width)
            lines = []
            # Full headline if it fits, else a shortened one, so the column runs down to the rule.
            for keep in (3, 2, 1):
                trial = kicker_line + [("brief", line) for line in _shorten(draw, headline, keep, fonts["brief"], width)]
                if cursor + _x3_lines_height(trial, fonts) <= bottom:
                    lines = trial
                    break
            if not lines:
                break
            if drawn:
                draw.line((x, cursor - 5, x + width, cursor - 5), fill=RULE, width=1)
            cursor = _draw_x3_lines(draw, lines, x=x, y=cursor, fonts=fonts) + 8
            drawn += 1
        if drawn:
            return cursor
    # Out of stories: an honest index of what's inside, never invented copy.
    draw.text((x, y), "INSIDE TODAY", font=fonts["kicker"], fill=INK)
    cursor = y + _line_height(fonts["kicker"]) + 8
    for index, (_key, label, count) in enumerate(_issue_sections(stories), start=1):
        line = [("brief", f"{index}. {label}"), ("kicker", f"{count} stor{'y' if count == 1 else 'ies'}")]
        if cursor + _x3_lines_height(line, fonts) > bottom:
            break
        cursor = _draw_x3_lines(draw, line, x=x, y=cursor, fonts=fonts) + 6
    return cursor


def _draw_x3_lead(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    main: dict,
    stories: list[dict],
    flanks: list[dict],
    lead_photo: bytes | None,
    *,
    x: int,
    y: int,
    width: int,
    height: int,
    scale: float,
    fonts: dict,
) -> int:
    """X3 cover body: lead beside its photo, two columns of stories and briefs, In this issue strip."""
    panel_w, panel_h, gap = 188, 216, 14
    text_w = width - panel_w - gap
    top = y
    panel = _photo_panel(lead_photo, (panel_w, panel_h)) if lead_photo else None
    if panel is None:
        panel = _placeholder_panel(
            main.get("category") or "news", main.get("category_label") or "", (panel_w, panel_h), fonts["kicker"]
        )
    image.paste(panel, (x + width - panel_w, top))

    source = (main.get("source") or "").strip()
    if source:
        draw.text((x, y), source.upper(), font=fonts["kicker"], fill=MUTED)
        y += max(16, int(round(34 * scale)))
    title_lines = _wrap_px(draw, _story_title(main), fonts["main"], text_w)
    y = _draw_lines(draw, title_lines[:5], x=x, y=y, font=fonts["main"], fill=INK, gap=4)
    y += 8
    deck_lines = _wrap_px(draw, _story_deck(main, 160), fonts["deck"], text_w)
    room = max(0, (top + panel_h - y) // 16)  # fill down to the panel's foot, at least two lines
    y = _draw_lines(draw, deck_lines[: max(2, min(5, room))], x=x, y=y, font=fonts["deck"], fill=MUTED, gap=3)
    y = max(y, top + panel_h) + 12
    draw.line((x, y, x + width, y), fill=RULE, width=2)
    y += 12

    footer_y = height - max(36, int(round(70 * scale)))
    strip_top = footer_y - 104  # strip is ~90px tall: keeps its counts clear of the footer rule
    bottom = strip_top - 14
    # Two columns like a real front page: stories with decks on the left, lined up with
    # the lead text; "In brief" on the right, lined up with the photo above it.
    left_w, right_x, right_w = text_w, x + width - panel_w, panel_w
    rest = [story for story in flanks if story is not main]
    column_top = y
    used = 0
    for story in rest[:4]:
        lines = _x3_story_lines(draw, story, left_w, fonts)
        if y + _x3_lines_height(lines, fonts) > bottom:
            # No room for the summary: the headline alone still fills the column.
            lines = [line for line in lines if line[0] in ("kicker", "title")]
            if y + _x3_lines_height(lines, fonts) > bottom:
                break  # never a half-drawn story
        y = _draw_x3_lines(draw, lines, x=x, y=y, fonts=fonts) + 12
        used += 1
    right_bottom = _draw_in_brief(draw, rest[used:], stories, x=right_x, y=column_top, width=right_w,
                                  bottom=bottom, fonts=fonts)
    gutter_x = x + left_w + (width - left_w - right_w) // 2
    # Column rule runs the full height down to the strip rule, as on a printed page.
    draw.line((gutter_x, column_top, gutter_x, strip_top - 6), fill=RULE, width=1)
    draw.line((x, strip_top - 6, x + width, strip_top - 6), fill=RULE, width=1)
    _draw_issue_strip(image, draw, stories, x=x, y=strip_top + 2, width=width,
                      kicker=fonts["kicker"], label_font=fonts["footer"])
    return y


CATEGORY_ICON_SIZE = 150
ICON_INK = (0, 0, 0)
# Material Symbols glyphs pre-rendered to 150px black/white PNGs (see the README there).
CATEGORY_ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "category_icons"
# Drawn in code: Material has no country icons for these.
DRAWN_CATEGORY_ICONS = {"nordic", "australia"}


def _star_points(cx: float, cy: float, outer: float, inner: float) -> list[tuple[float, float]]:
    points = []
    for i in range(10):
        radius = outer if i % 2 == 0 else inner
        angle = math.pi / 2 + i * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
    return points


def _drawn_region_icon(key: str, size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    ink = ICON_INK
    cy = size // 2
    stroke = max(3, size // 15)
    if key == "nordic":
        # Nordic cross flag: outlined field with the off-centre (hoist-side) cross.
        flag_top, flag_bottom = cy - size // 4, cy + size // 4
        flag_left, flag_right = size // 10, size - size // 10
        draw.rectangle([flag_left, flag_top, flag_right, flag_bottom], outline=ink, width=stroke)
        bar = max(stroke * 2, size // 9)
        cross_x = flag_left + (flag_right - flag_left) * 3 // 8
        draw.rectangle([cross_x - bar // 2, flag_top, cross_x + bar // 2, flag_bottom], fill=ink)
        draw.rectangle([flag_left, cy - bar // 2, flag_right, cy + bar // 2], fill=ink)
    else:
        # Southern Cross: four large stars in a kite plus the small Epsilon star.
        big, small = size / 9, size / 16
        for sx, sy, outer in (
            (0.50, 0.18, big),
            (0.24, 0.46, big),
            (0.76, 0.40, big),
            (0.52, 0.80, big),
            (0.62, 0.58, small),
        ):
            draw.polygon(_star_points(sx * size, sy * size, outer, outer * 0.45), fill=ink)
    return image


def render_category_icon(category_key: str, *, size: int = CATEGORY_ICON_SIZE) -> bytes:
    """Pure black-and-white PNG glyph for section pages, at the size the reader shows it.

    Built-in categories use the stored Material Symbols PNGs; Nordic and Australia are
    drawn here; custom categories fall back to the World News newspaper.
    """
    key = (category_key or "").strip().lower() or "news"
    if key in DRAWN_CATEGORY_ICONS:
        image = _drawn_region_icon(key, size)
    else:
        # Category keys are slugs; anything else (or no stored file) gets the newspaper.
        path = CATEGORY_ICON_DIR / f"{key}.png"
        if not re.fullmatch(r"[a-z0-9-]+", key) or not path.exists():
            path = CATEGORY_ICON_DIR / "news.png"
        with Image.open(path) as stored:
            image = stored.convert("RGB")
        if image.size != (size, size):
            # Nearest-neighbour keeps the pixels pure black / white.
            image = image.resize((size, size), Image.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
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
