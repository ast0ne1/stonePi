"""Local OCR for Instagram poster images (Tesseract)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger("eventtrakr.social.ocr")


def _preprocess(image):
    """Resize / contrast / sharpen for poster text. Returns a Pillow Image."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    img = image.convert("L")
    # Upscale small posters for OCR
    w, h = img.size
    if max(w, h) < 1200:
        scale = 1200 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    img = ImageOps.autocontrast(img)
    img = ImageEnhance.Contrast(img).enhance(1.4)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def ocr_image_bytes(data: bytes) -> str:
    if not data:
        return ""
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        logger.warning("OCR deps missing (Pillow/pytesseract)")
        return ""
    try:
        from io import BytesIO

        image = Image.open(BytesIO(data))
        processed = _preprocess(image)
        # Two passes with different page segmentation modes
        texts: list[str] = []
        for psm in (6, 11):
            try:
                txt = pytesseract.image_to_string(processed, config=f"--psm {psm}")
                if txt and txt.strip():
                    texts.append(txt.strip())
            except Exception:
                logger.debug("OCR pass psm=%s failed", psm, exc_info=True)
        if not texts:
            return ""
        # Prefer the longer extraction
        return max(texts, key=len)
    except Exception:
        logger.debug("OCR failed", exc_info=True)
        return ""


def download_and_ocr(urls: list[str], *, timeout: float = 30.0) -> tuple[str, int]:
    """Download image URLs, OCR each, combine. Returns (combined_text, failure_count).

    Temporary files are deleted after processing (privacy).
    """
    if not urls:
        return "", 0
    parts: list[str] = []
    failures = 0
    with tempfile.TemporaryDirectory(prefix="eventtrakr-ocr-") as tmp:
        tmp_path = Path(tmp)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            for idx, url in enumerate(urls[:8]):
                if not url or not str(url).startswith("http"):
                    continue
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.content
                    local = tmp_path / f"img_{idx}.bin"
                    local.write_bytes(data)
                    text = ocr_image_bytes(data)
                    if text:
                        parts.append(text)
                    else:
                        failures += 1
                except Exception:
                    logger.debug("Failed to download/OCR %s", url, exc_info=True)
                    failures += 1
    return "\n\n".join(parts), failures
