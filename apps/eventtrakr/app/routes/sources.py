from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from app.config import env
from app.db import SessionLocal
from app.models import CatalogSource, Category, Event, EventSource, utcnow
from app.services import auth, categories, favicon, ingest, settings

bp = Blueprint("sources", __name__, url_prefix="/sources")


@bp.route("")
@bp.route("/")
@auth.login_required
def list_sources():
    user = auth.get_current_user()
    with SessionLocal() as db:
        user_sources = list(
            db.execute(select(EventSource).where(EventSource.user_id == user.user_id).order_by(EventSource.name)).scalars()
        )
        catalog_sources = list(
            db.execute(
                select(CatalogSource)
                .where(CatalogSource.is_recommended.is_(True))
                .order_by(CatalogSource.category, CatalogSource.name)
            ).scalars()
        )

        subscribed_urls = {s.url for s in user_sources}
        global_schedule = settings.get_global_schedule(db, env.default_sync_interval_minutes)
        global_schedule_summary = settings.describe_schedule(global_schedule)
        category_list = categories.list_categories(db)
        category_labels = {c.key: c.label for c in category_list}
        source_schedules = {
            s.id: settings.get_source_schedule(s, env.default_sync_interval_minutes) or {"mode": "interval", "interval_minutes": 60}
            for s in user_sources
        }

    return render_template(
        "sources.html",
        user=user,
        user_sources=user_sources,
        catalog_sources=catalog_sources,
        subscribed_urls=subscribed_urls,
        global_schedule_summary=global_schedule_summary,
        category_list=category_list,
        category_labels=category_labels,
        source_schedules=source_schedules,
        weekdays=settings.WEEKDAYS,
        interval_choices=settings.INTERVAL_CHOICES,
    )


@bp.route("/catalog/subscribe/<int:catalog_id>", methods=["POST"])
@auth.login_required
def subscribe_catalog(catalog_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        cat = db.get(CatalogSource, catalog_id)
        if cat:
            existing = db.execute(
                select(EventSource).where(EventSource.user_id == user.user_id, EventSource.url == cat.url)
            ).scalar_one_or_none()
            if not existing:
                src = EventSource(
                    user_id=user.user_id,
                    catalog_id=cat.id,
                    name=cat.name,
                    url=cat.url,
                    source_type=cat.source_type,
                    category=categories.resolve_category_key(db, cat.category),
                    schedule_mode="global",
                    enabled=True,
                    favicon_path=cat.favicon_path,
                )
                db.add(src)
                db.commit()
                if not src.favicon_path:
                    src.favicon_path = favicon.fetch_favicon(src.url)
                src.favicon_checked_at = utcnow()
                db.commit()
                # Run quick initial sync
                ingest.fetch_and_extract_source(db, src)
                flash(f"Subscribed to '{cat.name}' and synced events.", "success")
    return redirect(url_for("sources.list_sources"))


@bp.route("/add", methods=["POST"])
@auth.login_required
def add_source():
    user = auth.get_current_user()
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    category_key = request.form.get("category", "general").strip()
    inc = request.form.get("keywords_include", "").strip()
    exc = request.form.get("keywords_exclude", "").strip()
    custom_schedule = settings.schedule_config_from_form(
        request.form, default_minutes=env.default_sync_interval_minutes
    )

    if not name or not url:
        flash("Source Name and URL are required.", "error")
        return redirect(url_for("sources.list_sources"))

    with SessionLocal() as db:
        existing = db.execute(
            select(EventSource).where(EventSource.user_id == user.user_id, EventSource.url == url)
        ).scalar_one_or_none()
        if existing:
            flash("This source URL is already in your list.", "error")
            return redirect(url_for("sources.list_sources"))

        # Detect source type
        stype = "generic"
        url_lower = url.lower()
        if "meetup.com" in url_lower or "eventbrite." in url_lower or url_lower.endswith(".ics"):
            stype = "supported" if not url_lower.endswith(".ics") else "ics"

        valid_keys = {c.key for c in categories.list_categories(db)}
        if category_key not in valid_keys:
            category_key = "general"

        src = EventSource(
            user_id=user.user_id,
            name=name,
            url=url,
            source_type=stype,
            category=category_key,
            keywords_include=inc,
            keywords_exclude=exc,
            enabled=True,
        )
        settings.apply_source_schedule(src, custom_schedule)
        db.add(src)
        db.commit()

        src.favicon_path = favicon.fetch_favicon(src.url)
        src.favicon_checked_at = utcnow()
        db.commit()

        # Immediate sync for this new source
        new_cnt, _ = ingest.fetch_and_extract_source(db, src)
        flash(f"Source '{name}' added successfully ({new_cnt} upcoming events found).", "success")

    return redirect(url_for("sources.list_sources"))


@bp.route("/delete/<int:source_id>", methods=["POST"])
@auth.login_required
def delete_source(source_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        src = db.get(EventSource, source_id)
        if src and src.user_id == user.user_id:
            db.delete(src)
            db.commit()
            flash("Source deleted.", "success")
    return redirect(url_for("sources.list_sources"))


@bp.route("/toggle/<int:source_id>", methods=["POST"])
@auth.login_required
def toggle_source(source_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        src = db.get(EventSource, source_id)
        if src and src.user_id == user.user_id:
            src.enabled = not src.enabled
            db.commit()
    return redirect(url_for("sources.list_sources"))


@bp.route("/<int:source_id>/category", methods=["POST"])
@auth.login_required
def update_category(source_id: int):
    user = auth.get_current_user()
    category_key = request.form.get("category", "general").strip()

    with SessionLocal() as db:
        src = db.get(EventSource, source_id)
        if src and src.user_id == user.user_id:
            category_labels = {c.key: c.label for c in categories.list_categories(db)}
            src.category = category_key if category_key in category_labels else "general"

            # Relabel this source's already-ingested events immediately,
            # instead of leaving them showing the old category until the
            # next sync.
            db.execute(
                Event.__table__.update()
                .where(Event.source_id == src.id)
                .values(category=category_labels.get(src.category, "General"))
            )
            db.commit()
            flash(f"Category updated for '{src.name}'.", "success")
    return redirect(url_for("sources.list_sources"))


@bp.route("/<int:source_id>/schedule", methods=["POST"])
@auth.login_required
def update_schedule(source_id: int):
    user = auth.get_current_user()
    custom_schedule = settings.schedule_config_from_form(
        request.form, default_minutes=env.default_sync_interval_minutes
    )

    with SessionLocal() as db:
        src = db.get(EventSource, source_id)
        if src and src.user_id == user.user_id:
            settings.apply_source_schedule(src, custom_schedule)
            db.commit()
            if custom_schedule is None:
                flash(f"Schedule for '{src.name}' now follows Global.", "success")
            else:
                flash(
                    f"Schedule for '{src.name}' set to: {settings.describe_schedule(custom_schedule)}.",
                    "success",
                )
    return redirect(url_for("sources.list_sources"))


