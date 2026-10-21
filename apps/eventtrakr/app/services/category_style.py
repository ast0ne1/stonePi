"""Icon and colour for a category pill (design-system tints, see ``.badge.category.tint-*``).

The colour can be chosen per category in Settings → Categories (``Category.color``);
otherwise each category has a default below.
"""

from __future__ import annotations

import threading
import time
import zlib

from app.services.categories import slugify

# Colours on offer, in picker order: (tint, name shown in Settings).
TINTS: tuple[tuple[str, str], ...] = (
    ("amethyst", "Purple"),
    ("sky", "Blue"),
    ("moss", "Green"),
    ("sand", "Sand"),
    ("ember", "Amber"),
    ("rust", "Red"),
    ("stone", "Grey"),
)
TINT_IDS = frozenset(t for t, _ in TINTS)

# Built-in categories (``categories.DEFAULT_CATEGORIES``), plus labels scrapers often use for them.
_STYLES: dict[str, tuple[str, str]] = {
    "general": ("tag", "stone"),
    "music": ("music", "amethyst"),
    "culture": ("culture", "ember"),
    "community": ("users", "sand"),
    "food-drink": ("cup", "rust"),
    "business": ("briefcase", "stone"),
    "arts": ("palette", "moss"),
    "sports": ("ball", "sky"),
    "family": ("heart", "sand"),
}
_ALIASES = {"art": "arts", "sport": "sports", "food": "food-drink", "food-and-drink": "food-drink", "concerts": "music"}
# Categories you add yourself: a tag, coloured by name so it stays the same everywhere.
_CUSTOM_TINTS = ("amethyst", "ember", "sand", "rust", "sky", "moss")

# Chosen colours, by category key and by slugified label (events store the label).
_CACHE_SECONDS = 30.0
_lock = threading.Lock()
_chosen: dict[str, str] | None = None
_loaded_at = 0.0


def _key(label: str | None) -> str:
    key = slugify(label or "")
    return _ALIASES.get(key, key)


def default_tint(label: str | None) -> str:
    key = _key(label)
    if key in _STYLES:
        return _STYLES[key][1]
    return _CUSTOM_TINTS[zlib.crc32(key.encode("utf-8")) % len(_CUSTOM_TINTS)]


def _chosen_colours() -> dict[str, str]:
    global _chosen, _loaded_at
    with _lock:
        if _chosen is not None and time.monotonic() - _loaded_at < _CACHE_SECONDS:
            return _chosen
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Category

    chosen: dict[str, str] = {}
    try:
        with SessionLocal() as db:
            for cat in db.execute(select(Category)).scalars():
                if cat.color in TINT_IDS:
                    chosen[cat.key] = cat.color
                    chosen[_key(cat.label)] = cat.color
    except Exception:  # noqa: BLE001 — a pill must never break a page
        chosen = {}
    with _lock:
        _chosen, _loaded_at = chosen, time.monotonic()
    return chosen


def forget_chosen_colours() -> None:
    """Call after a colour changes, so the next page shows it."""
    global _chosen
    with _lock:
        _chosen = None


def category_style(label: str | None, chosen: dict[str, str] | None = None) -> dict[str, str]:
    key = _key(label)
    icon = _STYLES[key][0] if key in _STYLES else "tag"
    colours = _chosen_colours() if chosen is None else chosen
    tint = colours.get(key) or colours.get(slugify(label or "")) or default_tint(label)
    return {"icon": icon, "tint": tint}
