"""Turn a page capture into what a screen shows (F14).

- e-ink (TRMNL): greyscale, stretched contrast, optionally inverted (a dark dashboard
  becomes black on white), then 1, 2 or 4 bits per pixel, thresholded (crisp text) or
  dithered (photos). Written as a true greyscale PNG at that bit depth, which is what
  TRMNL's firmware draws without any processing on its side.
- LCD (Car Thing): the colour capture at exactly the size asked for, as PNG.
"""

from __future__ import annotations

import hashlib
import io
import struct
import zlib
from typing import Any

BIT_DEPTHS = (1, 2, 4)
INVERT_MODES = ("auto", "on", "off")
DEFAULT_THRESHOLD = 128
# "auto" inverts pages whose average brightness is below this (0-255).
DARK_PAGE_MEAN = 110
PNG_SIGNATURE = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A, 0x0A])
ROW_FILTER_NONE = bytes(1)


def _open(png: bytes, width: int, height: int):
    from PIL import Image

    img = Image.open(io.BytesIO(png))
    img.load()
    if img.size != (width, height):
        # The capture can be a pixel out after scaling: crop/pad to the exact panel size.
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(img.convert("RGB").crop((0, 0, min(width, img.width), min(height, img.height))), (0, 0))
        img = canvas
    return img


def _pack(levels_img, bits: int) -> bytes:
    """Rows of ``levels_img`` (mode L, values 0..2^bits-1) packed ``8 // bits`` pixels a byte.

    Done with Pillow, not a Python loop over pixels: a TRMNL X frame is 2.6 M pixels. Each
    pixel slot of a byte is one column phase, pulled out with an exact affine resample.
    """
    from PIL import Image, ImageChops

    per_byte = 8 // bits
    width, height = levels_img.size
    out_w = -(-width // per_byte)
    if out_w * per_byte != width:
        padded = Image.new("L", (out_w * per_byte, height), 0)
        padded.paste(levels_img, (0, 0))
        levels_img = padded
    packed = Image.new("L", (out_w, height), 0)
    for i in range(per_byte):
        # Pillow samples at pixel centres: output x maps to per_byte*(x+0.5)+c, so this c lands
        # exactly on input column per_byte*x + i.
        offset = i - per_byte / 2 + 0.5
        phase = levels_img.transform(
            (out_w, height), Image.Transform.AFFINE, (per_byte, 0, offset, 0, 1, 0), Image.Resampling.NEAREST
        )
        shift = bits * (per_byte - 1 - i)
        packed = ImageChops.add(packed, phase.point(lambda v, s=shift: v << s))
    return packed.tobytes()


def _png_grey(levels_img, bits: int) -> tuple[bytes, bytes]:
    """(greyscale PNG at ``bits`` per pixel, packed pixel rows) from a levels image."""
    width, height = levels_img.size
    rows = _pack(levels_img, bits)
    stride = len(rows) // height
    raw = b"".join(ROW_FILTER_NONE + rows[y * stride : (y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, bits, 0, 0, 0, 0)
    png = PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    return png, rows


def to_eink(
    png: bytes,
    width: int,
    height: int,
    *,
    bits: int = 1,
    dither: bool = False,
    invert: str = "auto",
    threshold: int = DEFAULT_THRESHOLD,
) -> tuple[bytes, dict[str, Any]]:
    """(PNG, info) for an e-ink panel. ``info``: inverted, bytes, and a pixel hash for skip-unchanged."""
    from PIL import Image, ImageOps, ImageStat

    bits = bits if bits in BIT_DEPTHS else 1
    img = ImageOps.autocontrast(_open(png, width, height).convert("L"), cutoff=1)
    inverted = invert == "on" or (invert == "auto" and ImageStat.Stat(img).mean[0] < DARK_PAGE_MEAN)
    if inverted:
        img = ImageOps.invert(img)
    top = (1 << bits) - 1
    if bits == 1 and not dither:
        cut = min(250, max(5, int(threshold)))
        levels = img.point(lambda v: 1 if v >= cut else 0)
    else:
        # Even greys from black to white; Pillow dithers (Floyd-Steinberg) or rounds to them.
        palette: list[int] = []
        for i in range(top + 1):
            grey = round(i * 255 / top)
            palette += [grey, grey, grey]
        pal = Image.new("P", (1, 1))
        pal.putpalette(palette + [0] * (768 - len(palette)))
        quant = img.convert("RGB").quantize(
            palette=pal, dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
        )
        levels = Image.frombytes("L", quant.size, quant.tobytes())  # palette index = level
    out, rows = _png_grey(levels, bits)
    digest = hashlib.sha256(rows).hexdigest()[:20]
    return out, {"inverted": inverted, "bytes": len(out), "hash": digest, "bits": bits}


def to_lcd(png: bytes, width: int, height: int) -> bytes:
    """The colour capture at exactly ``width``×``height`` (PNG)."""
    img = _open(png, width, height)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
