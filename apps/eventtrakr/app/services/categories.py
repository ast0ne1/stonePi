from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, EventSource

DEFAULT_CATEGORIES = [
    ("general", "General"),
    ("music", "Music"),
    ("culture", "Culture"),
    ("community", "Community"),
    ("food-drink", "Food & Drink"),
    ("business", "Business"),
    ("arts", "Arts"),
    ("sports", "Sports"),
    ("family", "Family"),
]


def slugify(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")
    return key or "category"


def seed_default_categories(db: Session) -> None:
    for key, label in DEFAULT_CATEGORIES:
        existing = db.execute(select(Category).where(Category.key == key)).scalar_one_or_none()
        if existing:
            existing.label = label
            existing.is_builtin = True
        else:
            db.add(Category(key=key, label=label, is_builtin=True))
    db.commit()


def list_categories(db: Session) -> list[Category]:
    return list(db.execute(select(Category).order_by(Category.is_builtin.desc(), Category.label)).scalars())


def resolve_category_key(db: Session, raw: str | None) -> str:
    """Best-effort match of a free-text category label (e.g. a value carried
    over from the old category_filter column, or a CatalogSource.category
    label) to a defined category key. Falls back to "general"."""
    if not raw or raw.strip().lower() in ("", "all"):
        return "general"
    raw_norm = raw.strip().lower()
    for cat in list_categories(db):
        if cat.key == raw_norm or cat.label.lower() == raw_norm:
            return cat.key
    return "general"


def backfill_event_source_categories(db: Session) -> None:
    """Normalize EventSource.category against the managed list -- covers
    rows carried over verbatim from the old free-text category_filter column
    (see db.py _ensure_schema) plus anything else that doesn't match a
    defined key (e.g. a category that was since renamed/removed)."""
    valid_keys = {c.key for c in list_categories(db)}
    changed = False
    for src in db.execute(select(EventSource)).scalars():
        if src.category not in valid_keys:
            src.category = resolve_category_key(db, src.category)
            changed = True
    if changed:
        db.commit()
