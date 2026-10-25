"""The family display page (``/display``) and its one-off capture keys (ideation/ripple.md, Phase 4).

Notify captures ``/display`` with a headless browser that isn't signed in. Just before each
capture it asks Ripple, over a signed internal call, for a **capture key**: it works once,
within KEY_TTL_S (two minutes), for this page only, and only for a request straight from the Pi itself
(loopback, not through nginx). Notify adds the key and the panel's size and bit depth to
the address: ``/display?k=…&w=800&h=480&scale=1&bits=1``.

Photos are dithered here, at the panel's exact pixel size and greys, and embedded in the
page, so the capture makes no other requests and Notify converts it without dithering
(text stays sharp; Phase 1 finding). ``bits=0`` (a colour screen, the Car Thing) embeds
plain colour photos instead.
"""

from __future__ import annotations

import base64
import io
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from PIL import Image, ImageOps

from app import db, photos

# Notify fetches the key, then may wait up to a minute for the shared browser (a scraper's
# turn) before it navigates; single use and loopback-only keep the window harmless.
KEY_TTL_S = 120
LAYOUTS = ("hero", "list")
DEFAULT_LAYOUT = "hero"
DEFAULT_POSTS = 3
MAX_POSTS = 6
BITS = (0, 1, 2, 4)
HEADER_CSS = 44
# The photo area shows up to this many photos together: the newest always, plus others from
# posts in the last RECENT_PHOTO_DAYS days.
MAX_PHOTOS = 4
RECENT_PHOTO_DAYS = 3
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "x-forwarded-host", "forwarded")

_keys: dict[str, float] = {}
_keys_lock = threading.Lock()


# ── Capture keys ─────────────────────────────────────────────────────────────


def issue_key(now: float | None = None) -> str:
    now = time.monotonic() if now is None else now
    key = secrets.token_urlsafe(24)
    with _keys_lock:
        for old, expires in list(_keys.items()):
            if expires <= now:
                del _keys[old]
        _keys[key] = now + KEY_TTL_S
    return key


def redeem_key(key: str, now: float | None = None) -> bool:
    """True once for a key that hasn't expired; it's gone afterwards."""
    now = time.monotonic() if now is None else now
    with _keys_lock:
        expires = _keys.pop(key or "", None)
    return expires is not None and expires > now


def direct_from_pi(client_host: str | None, headers) -> bool:
    """A request from this machine that didn't come through nginx (nginx adds forwarding headers)."""
    lowered = {str(k).lower() for k in headers.keys()}
    return (client_host or "") in LOOPBACK and not any(h in lowered for h in PROXY_HEADERS)


# ── Settings ─────────────────────────────────────────────────────────────────


def layout() -> str:
    value = db.get_setting("display_layout", DEFAULT_LAYOUT)
    return value if value in LAYOUTS else DEFAULT_LAYOUT


def post_count() -> int:
    try:
        value = int(db.get_setting("display_posts", str(DEFAULT_POSTS)))
    except ValueError:
        return DEFAULT_POSTS
    return min(MAX_POSTS, max(1, value))


# ── Screen and photos ────────────────────────────────────────────────────────


# What a person previewing the page may ask for (``?device=v2``): the real TRMNL screens only.
# Any other size or depth comes from Notify's keyed capture, never from a browser.
PREVIEWS: dict[str, dict[str, Any]] = {
    "og": {"w": 800, "h": 480, "scale": 1.0, "bits": 1},
    "v2": {"w": 1040, "h": 780, "scale": 1.8, "bits": 4},
}


def preview_screen(device: str | None) -> dict[str, Any]:
    return dict(PREVIEWS.get(device or "", PREVIEWS["og"]))


def screen(params) -> dict[str, Any]:
    """The panel Notify's capture asks for (in the address with its key)."""

    def number(name: str, default: float, lo: float, hi: float) -> float:
        try:
            return min(hi, max(lo, float(params.get(name, default))))
        except (TypeError, ValueError):
            return default

    try:
        bits = int(params.get("bits", 1))
    except (TypeError, ValueError):
        bits = 1
    return {
        "w": int(number("w", 800, 200, 2000)),
        "h": int(number("h", 480, 200, 2000)),
        "scale": round(number("scale", 1.0, 0.5, 3.0), 3),
        "bits": bits if bits in BITS else 1,
    }


def snap(css: int, scale: float) -> int:
    """The nearest CSS size at or below ``css`` that is a whole number of panel pixels, so a
    dithered photo lands pixel for pixel instead of being resampled (and blurred)."""
    for size in range(css, max(1, css - 20), -1):
        if abs(size * scale - round(size * scale)) < 1e-6:
            return size
    return css


@lru_cache(maxsize=48)
def _photo_uri(key: str, ext: str, width: int, height: int, bits: int, stamp: float) -> str:
    with Image.open(photos.photo_path(key, ext)) as src:
        if src.format == "JPEG":
            src.draft(src.mode, (width, height))  # decode at the smallest scale that still covers the box
        img = ImageOps.fit(src.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if bits:
        from stonepi_display.webimage import quantize_grey

        grey = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        grey = grey.point(lambda v: round(255 * (v / 255) ** 0.8))  # lift mid-tones: e-ink reads dark
        # Notify's own greys, so its conversion keeps every pixel as it is.
        quantize_grey(grey, bits, dither=True).convert("L").save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def photo_uri(media: dict[str, Any], css_w: int, css_h: int, panel: dict[str, Any]) -> str | None:
    """The photo cropped to its box at panel pixels, dithered unless ``bits`` is 0. None if it's gone."""
    path = photos.photo_path(media["storage_key"], media["ext"])
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    width, height = round(css_w * panel["scale"]), round(css_h * panel["scale"])
    try:
        return _photo_uri(media["storage_key"], media["ext"], width, height, panel["bits"], stamp)
    except OSError:
        return None


# ── The page ─────────────────────────────────────────────────────────────────


def _recent(stamp: str, cutoff: datetime) -> bool:
    try:
        return datetime.fromisoformat(stamp) >= cutoff
    except (TypeError, ValueError):
        return False


def photo_boxes(count: int, width: int, height: int, gap: int) -> list[tuple[int, int, int, int]]:
    """(x, y, w, h) per photo in the photo area: one fills it; two stack; three are one on top
    and two below; four are two by two. Sizes add up to the area exactly, gaps included."""
    top = (height - gap) // 2
    bottom = height - top - gap
    left = (width - gap) // 2
    right = width - left - gap
    if count <= 1:
        return [(0, 0, width, height)]
    if count == 2:
        return [(0, 0, width, top), (0, top + gap, width, bottom)]
    lower = [(0, top + gap, left, bottom), (left + gap, top + gap, right, bottom)]
    if count == 3:
        return [(0, 0, width, top), *lower]
    return [(0, 0, left, top), (left + gap, 0, right, top), *lower]


def page_data(panel: dict[str, Any]) -> dict[str, Any]:
    """Everything display.html needs: header, the photo area (if any) and the posts."""
    posts = db.display_posts(post_count())
    big = panel["w"] >= 1000
    # Main area a whole number of panel pixels tall: shrink it to the nearest that is.
    main_h = snap(panel["h"] - HEADER_CSS, panel["scale"])
    header = panel["h"] - main_h
    data: dict[str, Any] = {
        "panel": panel,
        "big": big,
        "header_h": header,
        "main_h": main_h,
        "hero": None,
        "posts": posts,
        "layout": layout(),
        "thumb": snap(160 if big else 120, panel["scale"]),
    }
    if data["layout"] == "hero":
        # The newest photo, then others from the last few days (newest first), up to four.
        # Photos can come from posts past the ones listed beside them (shown posts are few).
        cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_PHOTO_DAYS)
        listed = {post["id"]: post for post in posts}
        candidates = [listed.get(post["id"], post) for post in db.display_posts(MAX_POSTS + MAX_PHOTOS)]
        picked: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for post in candidates:
            for media in post["media"]:
                if len(picked) >= MAX_PHOTOS or (picked and not _recent(post["created_at"], cutoff)):
                    break
                if photos.photo_path(media["storage_key"], media["ext"]).exists():
                    picked.append((post, media))
        if picked:
            hero_w = snap(round(panel["w"] * 0.47), panel["scale"])
            gap = snap(5 if big else 4, panel["scale"])
            tiles = []
            for (post, media), (x, y, w, h) in zip(picked, photo_boxes(len(picked), hero_w, main_h, gap)):
                uri = photo_uri(media, w, h, panel)
                if uri:
                    tiles.append({"post": post, "media": media, "uri": uri, "x": x, "y": y, "w": w, "h": h})
            if tiles:
                lead = tiles[0]
                # Each shown post keeps its first shown photo's caption beside its text.
                captions: dict[int, str] = {}
                for tile in tiles:
                    captions.setdefault(tile["post"]["id"], tile["media"]["caption"] or "")
                data["hero"] = {"post": lead["post"], "media": lead["media"], "uri": lead["uri"], "w": hero_w,
                                "tiles": tiles, "captions": captions}
    if data["hero"] is None:
        for post in posts:
            post["thumb_uri"] = None
            for media in post["media"]:
                post["thumb_uri"] = photo_uri(media, data["thumb"], data["thumb"], panel)
                post["thumb_caption"] = media["caption"]
                if post["thumb_uri"]:
                    break
    return data
