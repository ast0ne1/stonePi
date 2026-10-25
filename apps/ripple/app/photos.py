"""Ripple photos: check, clean and store uploads (ideation/ripple.md, Phase 3).

Every upload is decoded with Pillow, so the file's real content decides what it is, not its
name or the browser's MIME type. Only JPEG, PNG and WebP within the size and pixel limits
get through. Each photo is turned upright (EXIF orientation) and saved again without its
EXIF, which drops GPS, camera and time details. A JPEG thumbnail is made for the feed.

Files live in ``<data>/media`` under random keys (never the uploaded file name):
``<key>.<ext>`` is the photo and ``<key>-thumb.jpg`` its thumbnail. They're written as
``.part`` first and renamed when complete. Photos are deleted together with their post or
when removed from it; ``sweep_orphans`` clears anything a crash left behind.
"""

from __future__ import annotations

import io
import re
import secrets
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app import db

# Pillow format -> (MIME type, file extension)
FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
# Phones save portrait and depth shots as JPEGs with extra pictures inside (MPO); Pillow opens
# them through its JPEG reader. The first picture is the photo: it's kept as a plain JPEG.
SAME_AS = {"MPO": "JPEG"}
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 50_000_000
MAX_SIDE = 12_000
# PNG and WebP decode at full size, so they get a lower limit (24 MP is ~72 MB in memory).
MAX_PIXELS_FULL_DECODE = 24_000_000
# Stored photos are at most this on their long side (~12–17 MP): plenty for the feed and the
# display, and it keeps memory and disk use small on a Pi. JPEGs decode at a reduced size.
STORE_SIDE = 4096
DRAFT_SIDE = STORE_SIDE // 2
THUMB_SIDE = 1200
CAPTION_MAX = 200
DEFAULT_MAX_PER_POST = 4
MAX_PER_POST_LIMIT = 10
# Files older than this that no post refers to are left over from a failed upload.
ORPHAN_AGE_S = 15 * 60
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{24}$")
THUMB_SUFFIX = "-thumb.jpg"

_store_lock = threading.Lock()


class PhotoError(ValueError):
    """The upload isn't a photo Ripple can take; the message says why, for the person."""


@dataclass
class Photo:
    """A cleaned photo, on disk, not yet in the database."""

    key: str
    ext: str
    mime_type: str
    file_size: int
    width: int
    height: int
    caption: str


def media_dir() -> Path:
    return db.DATA_DIR / "media"


def photo_path(key: str, ext: str) -> Path:
    return media_dir() / f"{key}.{ext}"


def thumb_path(key: str) -> Path:
    return media_dir() / f"{key}{THUMB_SUFFIX}"


def clean_caption(raw: str) -> str:
    return " ".join((raw or "").split())[:CAPTION_MAX]


def thumb_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, THUMB_SIDE / max(width, height, 1))
    return max(1, round(width * scale)), max(1, round(height * scale))


def max_per_post() -> int:
    try:
        value = int(db.get_setting("max_photos", str(DEFAULT_MAX_PER_POST)))
    except ValueError:
        return DEFAULT_MAX_PER_POST
    return min(MAX_PER_POST_LIMIT, max(1, value))


def _open(data: bytes) -> Image.Image:
    if not data:
        raise PhotoError("is empty")
    if len(data) > MAX_BYTES:
        raise PhotoError(f"is over {MAX_BYTES // (1024 * 1024)} MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data), formats=list(FORMATS))
            width, height = img.size
            if width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
                raise PhotoError("is too large (over 50 megapixels)")
            if SAME_AS.get(img.format or "", img.format) == "JPEG":
                # Decode straight at 1/2, 1/4 or 1/8 size while the long side stays at least
                # DRAFT_SIDE: a 48 MP photo decodes as ~12 MP, a usual 12 MP one at full size.
                long_side = max(width, height)
                img.draft(img.mode, (max(1, DRAFT_SIDE * width // long_side), max(1, DRAFT_SIDE * height // long_side)))
            elif width * height > MAX_PIXELS_FULL_DECODE:
                raise PhotoError("is too large (PNG and WebP up to 24 megapixels)")
            img.load()
    except PhotoError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError, ValueError):
        raise PhotoError("isn't a JPEG, PNG or WebP photo") from None
    return img


def _write(path: Path, save) -> None:
    part = path.with_name(path.name + ".part")
    try:
        with open(part, "wb") as fh:
            save(fh)
        part.replace(path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _flatten(img: Image.Image) -> Image.Image:
    """RGB on white (thumbnails and JPEGs have no transparency)."""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, "white")
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return img.convert("RGB")


def store(data: bytes, caption: str = "") -> Photo:
    """Check and clean one upload, write the photo and its thumbnail. Raises PhotoError.
    One photo at a time across the app, so several uploads at once can't stack up memory."""
    with _store_lock:
        return _store(data, caption)


def _store(data: bytes, caption: str) -> Photo:
    img = _open(data)
    fmt = SAME_AS.get(img.format or "", img.format or "")
    if fmt not in FORMATS:
        raise PhotoError("isn't a JPEG, PNG or WebP photo")
    mime_type, ext = FORMATS[fmt]
    icc = img.info.get("icc_profile")  # colour profile only; no personal data
    img = ImageOps.exif_transpose(img)  # upright, as the camera meant it
    if max(img.size) > STORE_SIDE:
        img.thumbnail((STORE_SIDE, STORE_SIDE), Image.Resampling.LANCZOS)
    key = secrets.token_urlsafe(18)
    folder = media_dir()
    folder.mkdir(parents=True, exist_ok=True)
    full, thumb = photo_path(key, ext), thumb_path(key)
    extra = {"icc_profile": icc} if icc else {}
    # No exif= is passed when saving, so none is written: GPS and camera details are gone.
    try:
        if fmt == "JPEG":
            out = img if img.mode in ("RGB", "L") else _flatten(img)
            # A profile describes the original colours (CMYK, say): after converting, it no longer fits.
            out_extra = extra if out is img else {}
            _write(full, lambda fh: out.save(fh, "JPEG", quality=92, **out_extra))
        elif fmt == "PNG":
            _write(full, lambda fh: img.save(fh, "PNG", **extra))
        else:
            _write(full, lambda fh: img.save(fh, "WEBP", quality=90, **extra))
        small = _flatten(img)
        small.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.Resampling.LANCZOS)
        _write(thumb, lambda fh: small.save(fh, "JPEG", quality=82, optimize=True))
    except BaseException:
        delete_files(key, ext)
        raise
    return Photo(
        key=key,
        ext=ext,
        mime_type=mime_type,
        file_size=full.stat().st_size,
        width=img.width,
        height=img.height,
        caption=clean_caption(caption),
    )


def delete_files(key: str, ext: str) -> None:
    if not KEY_RE.match(key or ""):
        return
    photo_path(key, ext).unlink(missing_ok=True)
    thumb_path(key).unlink(missing_ok=True)


def discard(photos: list[Photo]) -> None:
    for photo in photos:
        delete_files(photo.key, photo.ext)


def available(key: str) -> bool:
    return thumb_path(key).is_file()


def _key_of(name: str) -> str:
    if name.endswith(".part"):
        name = name[: -len(".part")]
    if name.endswith(THUMB_SUFFIX):
        return name[: -len(THUMB_SUFFIX)]
    return name.rsplit(".", 1)[0]


def sweep_orphans(now: float | None = None) -> int:
    """Delete media files no live post refers to (older than ORPHAN_AGE_S). Returns how many."""
    folder = media_dir()
    if not folder.is_dir():
        return 0
    now = time.time() if now is None else now
    keep = db.media_keys()
    removed = 0
    for path in folder.iterdir():
        if not path.is_file() or _key_of(path.name) in keep:
            continue
        try:
            if now - path.stat().st_mtime < ORPHAN_AGE_S:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
