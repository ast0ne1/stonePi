from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from app.config import env
from app.db import SessionLocal
from app.models import CatalogSource, Category, Event, EventSource, SocialAccount, utcnow
from app.services import auth, capabilities, categories, favicon, ingest, settings
from app.services.social import config as social_config
from app.services.social import poll as social_poll

bp = Blueprint("sources", __name__, url_prefix="/sources")

ACCOUNT_TYPE_LABELS = dict(social_config.ACCOUNT_TYPES)

manage_sources_required = auth.capability_required(
    capabilities.MANAGE_SOURCES, redirect_endpoint="sources.list_sources"
)
social_required = auth.capability_required(capabilities.USE_SOCIAL, redirect_endpoint="sources.social_accounts")


def _facebook_refused(user, url: str) -> bool:
    """Facebook sources are fetched through paid Bright Data: they need Instagram & Facebook too."""
    if "facebook.com" in (url or "").lower() and not user.can(capabilities.USE_SOCIAL):
        flash(capabilities.denied_message(capabilities.USE_SOCIAL), "error")
        return True
    return False


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

        # Heal icons whose cached file has gone missing (or are due a recheck) in the
        # background; the template shows the placeholder until they're back.
        if any(favicon.needs_backfill(row) for row in [*user_sources, *catalog_sources]):
            favicon.backfill_all_async()

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
@manage_sources_required
def subscribe_catalog(catalog_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        cat = db.get(CatalogSource, catalog_id)
        if cat and _facebook_refused(user, cat.url):
            return redirect(url_for("sources.list_sources"))
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
                    favicon_path=cat.favicon_path if favicon.is_usable(cat.favicon_path) else None,
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
@manage_sources_required
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
    if _facebook_refused(user, url):
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
@manage_sources_required
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
@manage_sources_required
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
@manage_sources_required
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
@manage_sources_required
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


@bp.route("/social")
@auth.login_required
def social_accounts():
    user = auth.get_current_user()
    with SessionLocal() as db:
        accounts = list(
            db.execute(
                select(SocialAccount)
                .where(SocialAccount.user_id == user.user_id)
                .order_by(SocialAccount.username)
            ).scalars()
        )
        tracking_on = social_config.instagram_enabled(db)
        brightdata_ok = bool(settings.get_brightdata_api_key(db))
        poll_mins = social_config.poll_minutes(db)
        global_schedule = {"mode": "interval", "interval_minutes": poll_mins}
        global_schedule_summary = settings.describe_schedule(global_schedule)
        account_schedules = {
            a.id: settings.get_source_schedule(a, poll_mins)
            or {"mode": "interval", "interval_minutes": poll_mins}
            for a in accounts
        }
        account_schedule_summaries = {
            a.id: (
                settings.describe_schedule(account_schedules[a.id])
                if getattr(a, "schedule_mode", "global") == "custom"
                else f"Global ({global_schedule_summary})"
            )
            for a in accounts
        }
    return render_template(
        "social.html",
        accounts=accounts,
        account_types=social_config.ACCOUNT_TYPES,
        account_type_labels=ACCOUNT_TYPE_LABELS,
        tracking_on=tracking_on,
        brightdata_ok=brightdata_ok,
        poll_minutes=poll_mins,
        global_schedule_summary=global_schedule_summary,
        account_schedules=account_schedules,
        account_schedule_summaries=account_schedule_summaries,
        weekdays=settings.WEEKDAYS,
        interval_choices=settings.INTERVAL_CHOICES,
    )


@bp.route("/social/<int:account_id>/schedule", methods=["POST"])
@auth.login_required
@social_required
def social_schedule(account_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        poll_mins = social_config.poll_minutes(db)
        custom_schedule = settings.schedule_config_from_form(request.form, default_minutes=poll_mins)
        account = db.get(SocialAccount, account_id)
        if account and account.user_id == user.user_id:
            settings.apply_source_schedule(account, custom_schedule)
            account.updated_at = utcnow()
            db.commit()
            if custom_schedule is None:
                flash(f"@{account.username} now follows the Discovery global interval.", "success")
            else:
                flash(
                    f"@{account.username} schedule: {settings.describe_schedule(custom_schedule)}.",
                    "success",
                )
    return redirect(url_for("sources.social_accounts"))


@bp.route("/social/add", methods=["POST"])
@auth.login_required
@social_required
def social_add():
    user = auth.get_current_user()
    username = social_config.normalize_username(request.form.get("username", ""))
    account_type = (request.form.get("account_type") or "other").strip()
    display_name = (request.form.get("display_name") or "").strip()
    if account_type not in ACCOUNT_TYPE_LABELS:
        account_type = "other"
    if not username:
        flash("Enter an Instagram username.", "error")
        return redirect(url_for("sources.social_accounts"))
    with SessionLocal() as db:
        existing = db.execute(
            select(SocialAccount).where(
                SocialAccount.user_id == user.user_id,
                SocialAccount.platform == "instagram",
                SocialAccount.username == username,
            )
        ).scalar_one_or_none()
        if existing:
            flash(f"@{username} is already tracked.", "error")
            return redirect(url_for("sources.social_accounts"))
        account = SocialAccount(
            user_id=user.user_id,
            platform="instagram",
            username=username,
            display_name=display_name or username,
            profile_url=social_config.profile_url_for(username),
            account_type=account_type,
            tracking_enabled=True,
            schedule_mode="global",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(account)
        db.commit()
        flash(f"Tracking @{username}.", "success")
    return redirect(url_for("sources.social_accounts"))


@bp.route("/social/<int:account_id>/toggle", methods=["POST"])
@auth.login_required
@social_required
def social_toggle(account_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        account = db.get(SocialAccount, account_id)
        if account and account.user_id == user.user_id:
            account.tracking_enabled = not account.tracking_enabled
            account.updated_at = utcnow()
            db.commit()
            state = "on" if account.tracking_enabled else "off"
            flash(f"Tracking @{account.username} {state}.", "success")
    return redirect(url_for("sources.social_accounts"))


@bp.route("/social/<int:account_id>/delete", methods=["POST"])
@auth.login_required
@social_required
def social_delete(account_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        account = db.get(SocialAccount, account_id)
        if account and account.user_id == user.user_id:
            name = account.username
            from app.models import EventDiscovery, EventSocialLink, EventUpdate, SocialPost, SocialPostMedia

            posts = list(
                db.execute(select(SocialPost).where(SocialPost.social_account_id == account.id)).scalars()
            )
            post_ids = [p.id for p in posts]
            if post_ids:
                for link in db.execute(
                    select(EventSocialLink).where(EventSocialLink.social_post_id.in_(post_ids))
                ).scalars():
                    db.delete(link)
                for upd in db.execute(
                    select(EventUpdate).where(EventUpdate.social_post_id.in_(post_ids))
                ).scalars():
                    db.delete(upd)
                for media in db.execute(
                    select(SocialPostMedia).where(SocialPostMedia.social_post_id.in_(post_ids))
                ).scalars():
                    db.delete(media)
                for disc in db.execute(
                    select(EventDiscovery).where(EventDiscovery.social_account_id == account.id)
                ).scalars():
                    db.delete(disc)
                for post in posts:
                    db.delete(post)
            db.delete(account)
            db.commit()
            flash(f"Removed @{name}.", "success")
    return redirect(url_for("sources.social_accounts"))


@bp.route("/social/<int:account_id>/check", methods=["POST"])
@auth.login_required
@social_required
def social_check(account_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        account = db.get(SocialAccount, account_id)
        if not account or account.user_id != user.user_id:
            flash("Account not found.", "error")
            return redirect(url_for("sources.social_accounts"))
        result = social_poll.poll_account(db, account, force=True)
        if result.get("error"):
            flash(f"Check failed: {result['error']}", "error")
        else:
            flash(
                f"Checked @{account.username}: {result.get('new', 0)} new post(s), "
                f"{result.get('processed', 0)} processed.",
                "success",
            )
            from app.services import notify as notify_service

            for item in result.get("results") or []:
                notify_service.notify_from_process_result(
                    db, item, username=account.username, account_user_id=account.user_id
                )
    return redirect(url_for("sources.social_accounts"))

