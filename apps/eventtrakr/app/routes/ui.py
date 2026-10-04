from __future__ import annotations

import logging
import re
import threading
import unicodedata
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.config import env
from app.db import SessionLocal
from app.models import Category, Event, EventSource, User, utcnow
from app.services import auth, capabilities, categories, ingest
from app.services import settings as settings_service
from app.services.calendar_sync import (
    build_facebook_share_url,
    build_google_calendar_url,
)
from app.services.cost import (
    UNSPECIFIED_COST,
    cost_badge_class,
    cost_display_label,
    is_free_cost,
)
from app.services.dedupe import compute_event_fingerprint
from app.services.scrapers.base import ScrapedEvent
from app.services.scrapers.brightdata_facebook import BrightDataError, fetch_single_event
from app.services.scrapers.browser_fetch import browser_session

logger = logging.getLogger("eventtrakr.ui")

bp = Blueprint("ui", __name__)

# Pending sentinel for deep_search_sources_checked (browser scrape still running).
DEEP_SEARCH_PENDING = -1
_deep_search_lock = threading.Lock()
_deep_search_inflight: set[tuple[int, str, str]] = set()


def _enrich_events(events: list[Event]) -> None:
    for ev in events:
        ev.gcal_url = build_google_calendar_url(ev)
        ev.fb_share_url = build_facebook_share_url(ev)
        ev.is_free = is_free_cost(ev.cost)
        ev.cost_label = cost_display_label(ev.cost)
        ev.cost_badge_class = cost_badge_class(ev.cost)


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
        view_title="My Favourites",
        is_favourites_view=True,
    )


def _deep_search_sources(db, user_id: int, target_date, cat_query: str) -> int:
    """A search date past the normal Agenda horizon means we've likely never
    scraped that far out. Rather than come back empty, go fetch it live from
    whichever of the user's sources match the requested category (or all of
    them, when no category was chosen). Returns how many sources were checked.
    """
    src_query = select(EventSource).where(EventSource.user_id == user_id, EventSource.enabled == True)
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


def _deep_search_worker(user_id: int, target_date, cat_query: str, key: tuple[int, str, str]) -> None:
    try:
        with SessionLocal() as db:
            _deep_search_sources(db, user_id, target_date, cat_query)
    except Exception:
        logger.exception("Background deep search failed for user=%s date=%s", user_id, target_date)
    finally:
        with _deep_search_lock:
            _deep_search_inflight.discard(key)


def _start_deep_search_background(user_id: int, target_date, cat_query: str) -> bool:
    """Kick off a deep search in a daemon thread. Returns False if one is already
    in flight for the same user/date/category (so we still show pending)."""
    key = (user_id, target_date.isoformat(), cat_query or "all")
    with _deep_search_lock:
        if key in _deep_search_inflight:
            return False
        _deep_search_inflight.add(key)
    threading.Thread(
        target=_deep_search_worker,
        args=(user_id, target_date, cat_query, key),
        daemon=True,
        name="eventtrakr-deep-search",
    ).start()
    return True


def _deep_search_needs_browser(db, user_id: int, cat_query: str) -> bool | None:
    """True if any matching source needs Chromium; False for ICS-only; None if
    no sources match (caller can treat as checked=0)."""
    src_query = select(EventSource).where(EventSource.user_id == user_id, EventSource.enabled == True)
    if cat_query and cat_query != "all":
        matching_keys = db.execute(select(Category.key).where(Category.label == cat_query)).scalars().all()
        if not matching_keys:
            return None
        src_query = src_query.where(EventSource.category.in_(matching_keys))
    sources = list(db.execute(src_query).scalars())
    if not sources:
        return None
    return any(not ingest.is_ics_source(s) for s in sources)


# -- search ------------------------------------------------------------------------

SEARCH_MAX_TOKENS = 8
_TOKEN_TRIM = "@#.,;:!?\"'()[]{}\u00ab\u00bb\u201c\u201d\u2018\u2019"
_COMBINING_MARKS = re.compile("[\u0300-\u036f]")
# Rank tiers: every word in the title, then also in place/source/category, then description.
RANK_TITLE, RANK_META, RANK_DESCRIPTION = 0, 1, 2


def fold_text(value: str | None) -> str:
    """Case- and accent-insensitive form for matching (SQLite LIKE only folds ASCII).

    ``casefold`` handles Æ/Ø/Å and ß; NFKD + dropping combining marks makes "cafe"
    match "café". Both sides go through this, so "Århus" and "arhus" agree.
    """
    if not value:
        return ""
    if value.isascii():
        return value.lower()
    return _COMBINING_MARKS.sub("", unicodedata.normalize("NFKD", value.casefold()))


def search_tokens(query: str | None) -> list[str]:
    """Folded words of a search, in order: short words dropped unless nothing else, max 8."""
    words: list[str] = []
    for raw in (query or "").split():
        word = fold_text(raw).strip(_TOKEN_TRIM)
        if word and word not in words:
            words.append(word)
    longer = [w for w in words if len(w) >= 2]
    return (longer or words)[:SEARCH_MAX_TOKENS]


def match_rank(tokens: list[str], title: str, meta: str, description: str) -> int | None:
    """Best tier where every token appears (AND across fields), or None. Inputs are folded."""
    rank = RANK_TITLE
    for token in tokens:
        if token in title:
            continue
        if token in meta:
            rank = max(rank, RANK_META)
        elif token in description:
            rank = RANK_DESCRIPTION
        else:
            return None
    return rank


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _social_names_by_event(db, user_id: int) -> dict[int, str]:
    """Instagram account names behind social-linked events, so "theglobe" finds them."""
    from app.models import EventSocialLink, SocialAccount, SocialPost

    rows = db.execute(
        select(EventSocialLink.event_id, SocialAccount.username, SocialAccount.display_name)
        .join(SocialPost, SocialPost.id == EventSocialLink.social_post_id)
        .join(SocialAccount, SocialAccount.id == SocialPost.social_account_id)
        .where(SocialAccount.user_id == user_id)
    ).all()
    names: dict[int, list[str]] = {}
    for event_id, username, display_name in rows:
        names.setdefault(event_id, []).extend([username or "", display_name or ""])
    return {event_id: " ".join(parts) for event_id, parts in names.items()}


def run_search(
    db,
    user_id: int,
    *,
    tokens: list[str],
    location: str = "",
    category: str = "",
    target_date=None,
    now: datetime | None = None,
) -> list[Event]:
    """Events for a search, best matches first.

    SQL narrows by owner, date and category; words and location are matched in
    Python (Unicode-aware) over the user's events -- a few thousand on a Pi.
    Order: rank tier, then upcoming before past, then soonest first.
    """
    now = now or datetime.now(timezone.utc)
    conditions = [Event.user_id == user_id, Event.is_cancelled == False]  # noqa: E712
    if target_date:
        day_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, tzinfo=timezone.utc)
        conditions += [Event.start_time >= day_start, Event.start_time < day_start + timedelta(days=1)]
    if category and category != "all":
        conditions.append(Event.category.ilike(f"%{category}%"))

    if not tokens and not location:
        return list(db.execute(select(Event).where(*conditions).order_by(Event.start_time)).scalars())

    rows = db.execute(
        select(
            Event.id, Event.title, Event.description, Event.location, Event.category,
            Event.start_time, EventSource.name,
        )
        .outerjoin(EventSource, EventSource.id == Event.source_id)
        .where(*conditions)
    ).all()
    social_names = _social_names_by_event(db, user_id) if tokens else {}
    loc_needle = fold_text(location)
    upcoming_from = now - timedelta(hours=3)

    ranked: list[tuple[int, bool, datetime, int]] = []
    for event_id, title, description, ev_location, ev_category, start_time, source_name in rows:
        folded_location = fold_text(ev_location)
        if loc_needle and loc_needle not in folded_location:
            continue
        rank = RANK_TITLE
        if tokens:
            folded_title = fold_text(title)
            meta = " ".join(
                [folded_location, fold_text(ev_category), fold_text(source_name), fold_text(social_names.get(event_id))]
            )
            # Description is folded only when the cheaper fields don't settle it.
            found = match_rank(tokens, folded_title, meta, "")
            if found is None:
                found = match_rank(tokens, folded_title, meta, fold_text(description))
            if found is None:
                continue
            rank = found
        start = _aware(start_time)
        ranked.append((rank, start < upcoming_from, start, event_id))

    ranked.sort()
    ids = [row[3] for row in ranked]
    by_id: dict[int, Event] = {}
    for offset in range(0, len(ids), 500):
        chunk = ids[offset:offset + 500]
        by_id.update({ev.id: ev for ev in db.execute(select(Event).where(Event.id.in_(chunk))).scalars()})
    return [by_id[i] for i in ids if i in by_id]


@bp.route("/search")
@auth.login_required
def search():
    user = auth.get_current_user()
    date_query = request.args.get("date", "").strip()
    # Location filters only when the person typed or picked one: the default
    # location is offered as a chip, never applied silently.
    loc_query = request.args.get("location", "").strip()
    term_query = request.args.get("q", "").strip()
    cat_query = request.args.get("category", "all").strip() or "all"
    tokens = search_tokens(term_query)

    events = []
    performed = bool(date_query or loc_query or term_query or cat_query != "all")
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
                    needs_browser = _deep_search_needs_browser(db, user.user_id, cat_query)
                    if needs_browser is None:
                        deep_search_sources_checked = 0
                    elif needs_browser:
                        # Browser scrapes must not block the request on the Pi.
                        _start_deep_search_background(user.user_id, target_date, cat_query)
                        deep_search_sources_checked = DEEP_SEARCH_PENDING
                    else:
                        # ICS-only: cheap enough to finish before render.
                        deep_search_sources_checked = _deep_search_sources(db, user.user_id, target_date, cat_query)

            events = run_search(
                db,
                user.user_id,
                tokens=tokens,
                location=loc_query,
                category=cat_query,
                target_date=target_date,
            )
            _enrich_events(events)

        cat_rows = db.execute(select(Event.category).where(Event.user_id == user.user_id).distinct()).scalars().all()
        categories = sorted({c.strip() for c in cat_rows if c and c.strip()})

    without_location = {k: v for k, v in request.args.items() if k != "location" and v}
    return render_template(
        "search.html",
        user=user,
        events=events,
        performed=performed,
        date_query=date_query,
        loc_query=loc_query,
        default_location=(user.default_location or "").strip(),
        clear_location_url=url_for("ui.search", **without_location),
        term_query=term_query,
        cat_query=cat_query,
        categories=categories,
        deep_search_sources_checked=deep_search_sources_checked,
        deep_search_pending=deep_search_sources_checked == DEEP_SEARCH_PENDING,
    )


def _may_share(db, target_user: User) -> bool:
    """Public pages show nothing once the owner loses "Share agenda publicly"."""
    if capabilities.refresh_from_auth(db):
        db.refresh(target_user)
    return capabilities.user_can(target_user, capabilities.SHARE_AGENDA)


@bp.route("/u/<username>")
def public_agenda(username: str):
    with SessionLocal() as db:
        target_user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not target_user or not target_user.is_public or not _may_share(db, target_user):
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
        if not target_user or not target_user.favourites_public or not _may_share(db, target_user):
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
@auth.capability_required(capabilities.USE_SOCIAL, redirect_endpoint="ui.add_event")
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
    cost = request.form.get("cost", "").strip() or UNSPECIFIED_COST
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
