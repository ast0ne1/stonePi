from __future__ import annotations

import logging
import re
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_, select

from app.config import env
from app.db import SessionLocal
from app.models import Category, Event, EventSource, User, utcnow
from app.services import auth, categories, ingest
from app.services import settings as settings_service
from app.services.calendar_sync import (
    build_facebook_share_url,
    build_google_calendar_url,
)
from app.services.dedupe import compute_event_fingerprint
from app.services.scrapers.base import ScrapedEvent
from app.services.scrapers.brightdata_facebook import BrightDataError, fetch_single_event
from app.services.scrapers.browser_fetch import browser_session

logger = logging.getLogger("eventtrakr.ui")

bp = Blueprint("ui", __name__)

_COST_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def is_free_cost(cost: str | None) -> bool:
    """True only when the cost is literally "free" or a numeric amount of 0.

    A naive "0.00" substring check (the old approach) wrongly flags any price
    that happens to end in a round number, e.g. "DKK 250.00" or "kr 100.00".
    """
    if not cost:
        return False
    if "free" in cost.lower():
        return True
    match = _COST_NUMBER_RE.search(cost)
    if not match:
        return False
    try:
        return float(match.group(0).replace(",", ".")) == 0
    except ValueError:
        return False


def _enrich_events(events: list[Event]) -> None:
    for ev in events:
        ev.gcal_url = build_google_calendar_url(ev)
        ev.fb_share_url = build_facebook_share_url(ev)
        ev.is_free = is_free_cost(ev.cost)


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _build_day_tabs(base_date: datetime):
    tabs = [{"key": "all", "label": "All 7 Days", "date_str": None}]
    for i in range(7):
        day_dt = base_date + timedelta(days=i)
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else f"{day_dt.strftime('%A')} {_ordinal(day_dt.day)}")
        tabs.append({
            "key": day_dt.strftime("%Y-%m-%d"),
            "label": label,
            "date_str": day_dt.strftime("%Y-%m-%d"),
        })
    return tabs


@bp.route("/")
@bp.route("/agenda")
@auth.login_required
def agenda():
    user = auth.get_current_user()
    now = datetime.now(timezone.utc)
    max_lookahead = now + timedelta(days=7)

    day_filter = request.args.get("day", "all")
    cat_filter = request.args.get("category", "all")
    src_filter = request.args.get("source", "all")
    free_only = request.args.get("free", "0") == "1"

    day_tabs = _build_day_tabs(now)

    with SessionLocal() as db:
        query = select(Event).where(
            Event.user_id == user.user_id,
            Event.is_cancelled == False,
            Event.start_time >= now - timedelta(hours=3),
            Event.start_time <= max_lookahead,
        )

        if day_filter and day_filter != "all":
            try:
                target_date = datetime.strptime(day_filter, "%Y-%m-%d").date()
                day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, tzinfo=timezone.utc)
                day_end = day_start + timedelta(days=1)
                query = query.where(Event.start_time >= day_start, Event.start_time < day_end)
            except ValueError:
                pass

        if cat_filter and cat_filter != "all":
            query = query.where(Event.category.ilike(f"%{cat_filter}%"))

        if src_filter and src_filter != "all" and src_filter.isdigit():
            query = query.where(Event.source_id == int(src_filter))

        events = list(db.execute(query.order_by(Event.start_time)).scalars())

        if free_only:
            events = [ev for ev in events if is_free_cost(ev.cost)]

        # Collect distinct categories for filter chips -- categories now come
        # from the assigned-per-source label (see ingest.py), not whatever a
        # scraper happened to call things, so this stays a clean, curated list.
        cat_rows = db.execute(
            select(Event.category).where(Event.user_id == user.user_id).distinct()
        ).scalars().all()
        categories = sorted({c.strip() for c in cat_rows if c and c.strip()})

        # Sources that actually have an upcoming event, for the Source filter
        source_rows = db.execute(
            select(EventSource.id, EventSource.name)
            .join(Event, Event.source_id == EventSource.id)
            .where(
                Event.user_id == user.user_id,
                Event.is_cancelled == False,
                Event.start_time >= now - timedelta(hours=3),
                Event.start_time <= max_lookahead,
            )
            .distinct()
            .order_by(EventSource.name)
        ).all()
        source_options = [{"id": row.id, "name": row.name} for row in source_rows]

        _enrich_events(events)

    return render_template(
        "agenda.html",
        user=user,
        events=events,
        day_tabs=day_tabs,
        selected_day=day_filter,
        selected_category=cat_filter,
        selected_source=src_filter,
        free_only=free_only,
        categories=categories,
        source_options=source_options,
        view_title="7-Day Agenda",
    )


@bp.route("/favourites")
@auth.login_required
def favourites():
    user = auth.get_current_user()
    now = datetime.now(timezone.utc)

    with SessionLocal() as db:
        events = list(
            db.execute(
                select(Event)
                .where(Event.user_id == user.user_id, Event.is_favourited == True)
                .order_by(Event.start_time)
            ).scalars()
        )
        _enrich_events(events)

    return render_template(
        "agenda.html",
        user=user,
        events=events,
        day_tabs=[],
        selected_day="all",
        selected_category="all",
        selected_source="all",
        free_only=False,
        categories=[],
        source_options=[],
        view_title="My Favourites ⭐",
        is_favourites_view=True,
    )


def _deep_search_sources(db, user, target_date, cat_query: str) -> int:
    """A search date past the normal Agenda horizon means we've likely never
    scraped that far out. Rather than come back empty, go fetch it live from
    whichever of the user's sources match the requested category (or all of
    them, when no category was chosen). Returns how many sources were checked.
    """
    src_query = select(EventSource).where(EventSource.user_id == user.user_id, EventSource.enabled == True)
    if cat_query and cat_query != "all":
        matching_keys = db.execute(select(Category.key).where(Category.label == cat_query)).scalars().all()
        if not matching_keys:
            return 0
        src_query = src_query.where(EventSource.category.in_(matching_keys))

    sources = list(db.execute(src_query).scalars())
    if not sources:
        return 0

    # A couple of days of slack either side so we don't miss the event by an
    # off-by-one on the source's own listing boundaries.
    days_needed = max(env.max_lookahead_days, (target_date - datetime.now(timezone.utc).date()).days + 2)

    needs_browser = any(not ingest.is_ics_source(s) for s in sources)
    with browser_session() if needs_browser else nullcontext() as browser:
        for src in sources:
            try:
                ingest.fetch_and_extract_source(db, src, browser=browser, max_lookahead_days=days_needed)
            except Exception:
                logger.exception("Deep search sync failed for source %s", src.id)

    return len(sources)


@bp.route("/search")
@auth.login_required
def search():
    user = auth.get_current_user()
    date_query = request.args.get("date", "").strip()
    raw_loc_query = request.args.get("location", "").strip()
    term_query = request.args.get("q", "").strip()
    cat_query = request.args.get("category", "all").strip()

    events = []
    # A bare page visit still pre-fills the location field with the user's
    # default (a convenience for the form), but that resolved default must
    # not, by itself, look like a search was already submitted -- only
    # explicit criteria should trigger one.
    performed = bool(date_query or raw_loc_query or term_query)
    loc_query = raw_loc_query or user.default_location
    deep_search_sources_checked = None

    with SessionLocal() as db:
        if performed:
            target_date = None
            if date_query:
                try:
                    target_date = datetime.strptime(date_query, "%Y-%m-%d").date()
                except ValueError:
                    pass

            if target_date:
                horizon = datetime.now(timezone.utc).date() + timedelta(days=env.max_lookahead_days)
                if target_date > horizon:
                    deep_search_sources_checked = _deep_search_sources(db, user, target_date, cat_query)

            query = select(Event).where(Event.user_id == user.user_id, Event.is_cancelled == False)

            if target_date:
                day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, tzinfo=timezone.utc)
                day_end = day_start + timedelta(days=1)
                query = query.where(Event.start_time >= day_start, Event.start_time < day_end)

            if loc_query:
                query = query.where(Event.location.ilike(f"%{loc_query}%"))

            if term_query:
                term_pat = f"%{term_query}%"
                query = query.where(or_(Event.title.ilike(term_pat), Event.description.ilike(term_pat)))

            if cat_query and cat_query != "all":
                query = query.where(Event.category.ilike(f"%{cat_query}%"))

            events = list(db.execute(query.order_by(Event.start_time)).scalars())
            _enrich_events(events)

        cat_rows = db.execute(select(Event.category).where(Event.user_id == user.user_id).distinct()).scalars().all()
        categories = sorted({c.strip() for c in cat_rows if c and c.strip()})

    return render_template(
        "search.html",
        user=user,
        events=events,
        performed=performed,
        date_query=date_query,
        loc_query=loc_query,
        term_query=term_query,
        cat_query=cat_query,
        categories=categories,
        deep_search_sources_checked=deep_search_sources_checked,
    )


@bp.route("/u/<username>")
def public_agenda(username: str):
    with SessionLocal() as db:
        target_user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not target_user or not target_user.is_public:
            return render_template(
                "public_agenda.html",
                is_private=True,
                is_favourites=False,
                username=username,
                events=[],
            ), 403

        now = datetime.now(timezone.utc)
        max_lookahead = now + timedelta(days=7)
        events = list(
            db.execute(
                select(Event)
                .where(
                    Event.user_id == target_user.id,
                    Event.is_cancelled == False,
                    Event.start_time >= now - timedelta(hours=3),
                    Event.start_time <= max_lookahead,
                )
                .order_by(Event.start_time)
            ).scalars()
        )
        _enrich_events(events)

        return render_template(
            "public_agenda.html",
            is_private=False,
            is_favourites=False,
            target_user=target_user,
            events=events,
        )


@bp.route("/u/<username>/favourites")
def public_favourites(username: str):
    with SessionLocal() as db:
        target_user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not target_user or not target_user.favourites_public:
            return render_template(
                "public_agenda.html",
                is_private=True,
                is_favourites=True,
                username=username,
                events=[],
            ), 403

        events = list(
            db.execute(
                select(Event)
                .where(Event.user_id == target_user.id, Event.is_favourited == True)
                .order_by(Event.start_time)
            ).scalars()
        )
        _enrich_events(events)

        return render_template(
            "public_agenda.html",
            is_private=False,
            is_favourites=True,
            target_user=target_user,
            events=events,
        )


@bp.route("/add-event")
@auth.login_required
def add_event():
    with SessionLocal() as db:
        category_list = categories.list_categories(db)
        brightdata_configured = bool(settings_service.get_brightdata_api_key(db))

    return render_template(
        "add_event.html",
        category_list=category_list,
        brightdata_configured=brightdata_configured,
    )


@bp.route("/add-event/fetch-facebook", methods=["POST"])
@auth.login_required
def add_event_fetch_facebook():
    event_url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not event_url or "facebook.com" not in event_url.lower():
        return jsonify({"error": "Enter a facebook.com event URL."}), 400

    with SessionLocal() as db:
        api_key = settings_service.get_brightdata_api_key(db)
    if not api_key:
        return jsonify({"error": "Bright Data API key not configured (Settings > Data Providers)."}), 400

    try:
        event = fetch_single_event(api_key, event_url)
    except BrightDataError as e:
        logger.warning("Bright Data single-event lookup failed for %s: %s", event_url, e)
        return jsonify({"error": str(e)}), 502

    if not event:
        return jsonify({"error": "No event details found for that URL."}), 404

    return jsonify(
        {
            "title": event.title,
            "description": event.description,
            "start_time": event.start_time.strftime("%Y-%m-%dT%H:%M"),
            "end_time": event.end_time.strftime("%Y-%m-%dT%H:%M") if event.end_time else "",
            "location": event.location,
            "cost": event.cost,
            "url": event.url,
            "image_url": event.image_url or "",
        }
    )


@bp.route("/add-event/save", methods=["POST"])
@auth.login_required
def add_event_save():
    user = auth.get_current_user()
    title = request.form.get("title", "").strip()
    date_raw = request.form.get("date", "").strip()
    time_raw = request.form.get("time", "").strip()
    end_date_raw = request.form.get("end_date", "").strip()
    end_time_raw = request.form.get("end_time", "").strip()
    location = request.form.get("location", "").strip() or "Unspecified"
    cost = request.form.get("cost", "").strip() or "Free / Unspecified"
    description = request.form.get("description", "").strip()
    category_key = request.form.get("category", "general").strip()
    event_url = request.form.get("url", "").strip()
    image_url = request.form.get("image_url", "").strip()

    if not title or not date_raw:
        flash("Title and date are required.", "error")
        return redirect(url_for("ui.add_event"))

    try:
        if time_raw:
            start_time = datetime.strptime(f"{date_raw} {time_raw}", "%Y-%m-%d %H:%M")
        else:
            start_time = datetime.strptime(date_raw, "%Y-%m-%d")
        start_time = start_time.replace(tzinfo=timezone.utc)
    except ValueError:
        flash("Invalid date or time.", "error")
        return redirect(url_for("ui.add_event"))

    end_time = None
    if end_date_raw:
        try:
            if end_time_raw:
                end_time = datetime.strptime(f"{end_date_raw} {end_time_raw}", "%Y-%m-%d %H:%M")
            else:
                end_time = datetime.strptime(end_date_raw, "%Y-%m-%d")
            end_time = end_time.replace(tzinfo=timezone.utc)
        except ValueError:
            flash("Invalid end date or time.", "error")
            return redirect(url_for("ui.add_event"))

    with SessionLocal() as db:
        category_labels = {c.key: c.label for c in categories.list_categories(db)}
        category_label = category_labels.get(category_key, "General")

        fp = compute_event_fingerprint(user.user_id, title, start_time, location)
        existing = db.execute(
            select(Event).where(Event.user_id == user.user_id, Event.fingerprint == fp)
        ).scalar_one_or_none()
        if existing:
            flash("An event with this title, date, and location already exists.", "error")
            return redirect(url_for("ui.add_event"))

        db.add(
            Event(
                user_id=user.user_id,
                source_id=None,
                fingerprint=fp,
                title=title,
                description=description,
                start_time=start_time,
                end_time=end_time,
                location=location,
                cost=cost,
                category=category_label,
                url=event_url,
                image_url=image_url or None,
                is_favourited=False,
                calendar_synced=False,
                created_at=utcnow(),
            )
        )
        db.commit()

    flash(f"'{title}' added to your Agenda.", "success")
    return redirect(url_for("ui.agenda"))
