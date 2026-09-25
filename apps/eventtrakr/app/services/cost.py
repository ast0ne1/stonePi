"""Normalize event cost labels for storage, filters, and badge display."""

from __future__ import annotations

import re

UNSPECIFIED_COST = "Unspecified"
FREE_COST = "Free"
# Legacy default written by older scrapers / forms.
LEGACY_UNSPECIFIED = "Free / Unspecified"

_COST_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_FREE_WORD_RE = re.compile(r"\bfree\b", re.IGNORECASE)


def is_unspecified_cost(cost: str | None) -> bool:
    if cost is None or not str(cost).strip():
        return True
    normalized = (
        str(cost)
        .strip()
        .lower()
        .replace("–", "/")
        .replace("—", "/")
        .replace("／", "/")
    )
    compact = re.sub(r"\s+", " ", normalized)
    if compact in {
        "unspecified",
        "unknown",
        "n/a",
        "na",
        "tba",
        "tbd",
        "none",
        "?",
    }:
        return True
    # Legacy combined label — unknown price, not confirmed free.
    if compact in {"free / unspecified", "free/unspecified", "free or unspecified"}:
        return True
    if "unspecified" in compact and "free" in compact:
        return True
    return False


def is_free_cost(cost: str | None) -> bool:
    """True only when the cost is explicitly free (not unspecified / unknown)."""
    if is_unspecified_cost(cost):
        return False
    text = str(cost).strip()
    if _FREE_WORD_RE.search(text):
        return True
    match = _COST_NUMBER_RE.search(text)
    if not match:
        return False
    try:
        return float(match.group(0).replace(",", ".")) == 0
    except ValueError:
        return False


def cost_value_label(cost: str | None) -> str:
    if is_unspecified_cost(cost):
        return UNSPECIFIED_COST
    if is_free_cost(cost):
        text = str(cost).strip()
        # Plain free variants collapse to "Free"; keep longer phrases as-is.
        if _FREE_WORD_RE.fullmatch(text) or text.lower() in {"gratis", "free entry", "free event"}:
            return FREE_COST
        if _FREE_WORD_RE.search(text) and not _COST_NUMBER_RE.search(text):
            return FREE_COST
        return text
    return str(cost).strip()


def cost_display_label(cost: str | None) -> str:
    return f"Price: {cost_value_label(cost)}"


def cost_badge_class(cost: str | None) -> str:
    if is_unspecified_cost(cost):
        return "cost-unspecified"
    if is_free_cost(cost):
        return "cost-free"
    return "cost-paid"


def normalize_stored_cost(cost: str | None) -> str:
    """Map empty / legacy values to the current unspecified default."""
    if is_unspecified_cost(cost):
        return UNSPECIFIED_COST
    return str(cost).strip()
