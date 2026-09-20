from __future__ import annotations

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from app.config import env
from app.db import SessionLocal
from app.models import CalendarConnection, Category, EventSource, User
from app.services import auth, categories, hostname, passwords, settings, tls, update
from app import __github__, __github_user__, __version__ as app_version

bp = Blueprint("settings", __name__, url_prefix="/settings")

SETTINGS_TABS = [
    ("general", "General"),
    ("privacy", "Privacy & Overview"),
    ("schedule", "Schedule"),
    ("filters", "Filters"),
    ("categories", "Categories"),
    ("calendar", "Calendar Integrations"),
    ("providers", "Data Providers"),
    ("network", "Network & SSL"),
    ("users", "Users"),
    ("update", "Update"),
    ("about", "About"),
]
SETTINGS_LEDES = {
    "general": "Default location and your account password. Colour palette is under StonePi → Settings → General.",
    "privacy": "What others can see on your public calendar and favourites.",
    "schedule": "How often sources sync across EventTrakr.",
    "filters": "Words to keep or drop for every source.",
    "categories": "Labels for organising events and sources.",
    "calendar": "Connect Google Calendar to push favourites out.",
    "providers": "API keys for scrapers that need them.",
    "network": "Hostname and local HTTPS for this copy.",
    "users": "Local accounts when StonePi SSO is not configured.",
    "update": "Check GitHub Releases and install a newer zip.",
    "about": "App name, description, GitHub, and the version running here.",
}
PLATFORM_HIDDEN_SETTINGS_TABS = frozenset({"users", "update"})
PLATFORM_MANAGED_MESSAGE = "Household accounts and updates are managed in StonePi."


def _platform_managed() -> bool:
    return bool(env.stonepi_session_secret.strip())


def _settings_tabs_for(user) -> list[tuple[str, str]]:
    hidden = set()
    if user.role != "admin":
        hidden.update({"update", "users"})
    if _platform_managed():
        hidden.update(PLATFORM_HIDDEN_SETTINGS_TABS)
    return [item for item in SETTINGS_TABS if item[0] not in hidden]


@bp.route("")
@bp.route("/")
@auth.login_required
def view_settings():
    user = auth.get_current_user()
    active_tab = request.args.get("tab", "general")
    tabs = _settings_tabs_for(user)
    allowed = {key for key, _ in tabs}
    if active_tab not in allowed:
        active_tab = "general"
    platform_managed = _platform_managed()

    with SessionLocal() as db:
        db_user = db.get(User, user.user_id)
        users = (
            list(db.execute(select(User).order_by(User.id)).scalars())
            if user.role == "admin" and not platform_managed
            else []
        )
        gcal_conn = db.execute(
            select(CalendarConnection).where(
                CalendarConnection.user_id == user.user_id,
                CalendarConnection.provider == "google",
            )
        ).scalar_one_or_none()

        is_https = settings.https_enabled(db)
        cert_status = tls.certificate_status()
        lan_url = hostname.get_lan_url("https" if is_https else "http")
        public_url = hostname.get_public_base_url(is_https)

        default_loc = db_user.default_location if db_user else env.default_location
        is_public = db_user.is_public if db_user else False
        favourites_public = db_user.favourites_public if db_user else False
        global_schedule = settings.get_global_schedule(db, env.default_sync_interval_minutes)
        global_keyword_include, global_keyword_exclude = settings.get_global_keywords(db)
        category_list = categories.list_categories(db)
        brightdata_configured = bool(settings.get_brightdata_api_key(db))
        google_client_id, _ = settings.get_google_oauth_credentials(db)
        google_configured = bool(google_client_id)
        github_repo = update.repo_from_db(db) if user.role == "admin" and not platform_managed else ""
        update_check = update.last_check(db) if user.role == "admin" and not platform_managed else {}

    return render_template(
        "settings.html",
        user=user,
        settings_tabs=tabs,
        active_tab=active_tab,
        settings_lede=SETTINGS_LEDES.get(active_tab, ""),
        settings_ledes=SETTINGS_LEDES,
        platform_managed=platform_managed,
        db_user=db_user,
        users=users,
        gcal_conn=gcal_conn,
        is_https=is_https,
        cert_status=cert_status,
        lan_url=lan_url,
        public_url=public_url,
        default_location=default_loc,
        is_public=is_public,
        favourites_public=favourites_public,
        global_schedule=global_schedule,
        schedule_summary=settings.describe_schedule(global_schedule),
        weekdays=settings.WEEKDAYS,
        global_keyword_include=global_keyword_include,
        global_keyword_exclude=global_keyword_exclude,
        category_list=category_list,
        brightdata_configured=brightdata_configured,
        google_configured=google_configured,
        github_repo=github_repo,
        update_check=update_check,
        app_name="EventTrakr",
        app_version=app_version,
        app_github_user=__github_user__,
        app_github=__github__,
        interval_choices=[
            (15, "Every 15 minutes"),
            (30, "Every 30 minutes"),
            (60, "Every hour"),
            (120, "Every 2 hours"),
            (240, "Every 4 hours"),
            (360, "Every 6 hours"),
            (720, "Every 12 hours"),
            (1440, "Every 24 hours"),
        ],
        env=env,
    )


@bp.route("/general", methods=["POST"])
@auth.login_required
def save_general():
    user = auth.get_current_user()
    location = request.form.get("default_location", "").strip()
    new_password = request.form.get("new_password", "").strip()

    with SessionLocal() as db:
        db_user = db.get(User, user.user_id)
        if db_user:
            if location:
                db_user.default_location = location
            if new_password and not _platform_managed():
                db_user.password = passwords.hash_password(new_password)
            elif new_password and _platform_managed():
                flash("Change your password in StonePi → Settings → General.", "error")
                return redirect(url_for("settings.view_settings", tab="general"))
            db.commit()
            flash("General settings updated.", "success")

    return redirect(url_for("settings.view_settings", tab="general"))


@bp.route("/privacy", methods=["POST"])
@auth.login_required
def save_privacy():
    user = auth.get_current_user()
    is_public = request.form.get("is_public") == "1"
    favourites_public = request.form.get("favourites_public") == "1"

    with SessionLocal() as db:
        db_user = db.get(User, user.user_id)
        if db_user:
            db_user.is_public = is_public
            db_user.favourites_public = favourites_public
            db.commit()
            flash("Privacy settings updated.", "success")

    return redirect(url_for("settings.view_settings", tab="privacy"))


@bp.route("/schedule", methods=["POST"])
@auth.admin_required
def save_schedule():
    schedule_type = request.form.get("schedule_type", "interval")

    if schedule_type == "weekly":
        days = [d for d in request.form.getlist("schedule_days") if d in settings.WEEKDAY_CODES]
        times = sorted({t for t in request.form.getlist("schedule_times") if t})
        if not days or not times:
            flash("Pick at least one day and one time for a weekly schedule.", "error")
            return redirect(url_for("settings.view_settings", tab="schedule"))
        config = {"mode": "weekly", "days": days, "times": times}
    else:
        raw = request.form.get("interval_minutes", "")
        minutes = int(raw) if raw.isdigit() else env.default_sync_interval_minutes
        config = {"mode": "interval", "interval_minutes": max(15, minutes)}

    with SessionLocal() as db:
        settings.set_global_schedule(db, config)
        flash(f"Global sync schedule set to: {settings.describe_schedule(config)}.", "success")

    return redirect(url_for("settings.view_settings", tab="schedule"))


@bp.route("/filters", methods=["POST"])
@auth.admin_required
def save_filters():
    include = request.form.get("global_keyword_include", "")
    exclude = request.form.get("global_keyword_exclude", "")

    with SessionLocal() as db:
        settings.set_global_keywords(db, include, exclude)
        flash("Global filters updated.", "success")

    return redirect(url_for("settings.view_settings", tab="filters"))


@bp.route("/categories", methods=["POST"])
@auth.admin_required
def add_category():
    label = request.form.get("label", "").strip()
    if not label:
        flash("Category name is required.", "error")
        return redirect(url_for("settings.view_settings", tab="categories"))

    with SessionLocal() as db:
        key = categories.slugify(label)
        existing = db.execute(select(Category).where(Category.key == key)).scalar_one_or_none()
        if existing:
            flash(f"A category called '{label}' already exists.", "error")
        else:
            db.add(Category(key=key, label=label, is_builtin=False))
            db.commit()
            flash(f"Category '{label}' added.", "success")

    return redirect(url_for("settings.view_settings", tab="categories"))


@bp.route("/categories/<key>/rename", methods=["POST"])
@auth.admin_required
def rename_category(key: str):
    label = request.form.get("label", "").strip()

    with SessionLocal() as db:
        cat = db.execute(select(Category).where(Category.key == key)).scalar_one_or_none()
        if cat and not cat.is_builtin and label:
            cat.label = label
            db.commit()
            flash("Category renamed.", "success")

    return redirect(url_for("settings.view_settings", tab="categories"))


@bp.route("/categories/<key>/delete", methods=["POST"])
@auth.admin_required
def delete_category(key: str):
    with SessionLocal() as db:
        cat = db.execute(select(Category).where(Category.key == key)).scalar_one_or_none()
        if cat and not cat.is_builtin:
            # Sources using this category fall back to General rather than
            # being left pointing at a category that no longer exists.
            db.execute(
                EventSource.__table__.update()
                .where(EventSource.category == key)
                .values(category="general")
            )
            db.delete(cat)
            db.commit()
            flash(f"Category '{cat.label}' removed. Its sources moved to General.", "success")

    return redirect(url_for("settings.view_settings", tab="categories"))


@bp.route("/calendar/google-credentials", methods=["POST"])
@auth.admin_required
def save_google_credentials():
    client_id = request.form.get("google_client_id", "").strip()
    client_secret = request.form.get("google_client_secret", "").strip()
    if client_id or client_secret:
        with SessionLocal() as db:
            existing_id, existing_secret = settings.get_google_oauth_credentials(db)
            settings.set_google_oauth_credentials(
                db,
                client_id or existing_id,
                client_secret or existing_secret,
            )
        flash("Google OAuth credentials saved.", "success")
    return redirect(url_for("settings.view_settings", tab="calendar"))


@bp.route("/integrations", methods=["POST"])
@auth.admin_required
def save_integrations():
    api_key = request.form.get("brightdata_api_key", "").strip()
    if api_key:
        with SessionLocal() as db:
            settings.set_brightdata_api_key(db, api_key)
        flash("Bright Data API key saved.", "success")
    return redirect(url_for("settings.view_settings", tab="providers"))


@bp.route("/integrations/clear", methods=["POST"])
@auth.admin_required
def clear_integrations():
    with SessionLocal() as db:
        settings.set_brightdata_api_key(db, "")
    flash("Bright Data API key removed.", "success")
    return redirect(url_for("settings.view_settings", tab="providers"))


@bp.route("/network", methods=["POST"])
@auth.admin_required
def save_network():
    https_on = request.form.get("https_enabled") == "1"
    with SessionLocal() as db:
        settings.set_https_enabled(db, https_on)
        if https_on:
            tls.ensure_certificate()
        flash(f"HTTPS {'enabled' if https_on else 'disabled'}. Restart server to apply TLS listener changes.", "success")
    return redirect(url_for("settings.view_settings", tab="network"))


@bp.route("/network/ca.crt")
def download_root_ca():
    pem = tls.root_ca_pem_bytes()
    return Response(
        pem,
        mimetype="application/x-x509-ca-cert",
        headers={"Content-Disposition": 'attachment; filename="EventTrakr-RootCA.crt"'},
    )


@bp.route("/users/create", methods=["POST"])
@auth.admin_required
def create_user():
    if _platform_managed():
        flash(PLATFORM_MANAGED_MESSAGE, "error")
        return redirect(url_for("settings.view_settings", tab="general"))
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    role = request.form.get("role", "user")

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("settings.view_settings", tab="users"))

    with SessionLocal() as db:
        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing:
            flash("Username already taken.", "error")
            return redirect(url_for("settings.view_settings", tab="users"))

        new_user = User(
            username=username,
            password=passwords.hash_password(password),
            role=role,
            is_public=False,
            default_location="Copenhagen, Denmark",
            active=True,
        )
        db.add(new_user)
        db.commit()
        flash(f"User '{username}' created successfully.", "success")

    return redirect(url_for("settings.view_settings", tab="users"))


@bp.route("/update", methods=["POST"])
@auth.admin_required
def save_update_repo():
    if _platform_managed():
        flash(PLATFORM_MANAGED_MESSAGE, "error")
        return redirect(url_for("settings.view_settings", tab="general"))
    from stonepi_update import normalize_repo

    github_repo = request.form.get("github_repo") or ""
    repo = normalize_repo(github_repo)
    with SessionLocal() as db:
        if github_repo.strip() and not repo:
            flash("Use owner/repo for the GitHub repository.", "error")
        else:
            settings.set_value(db, "github_repo", repo)
            flash("Update settings saved.", "success")
    return redirect(url_for("settings.view_settings", tab="update"))


@bp.route("/updates/check", methods=["POST"])
@auth.admin_required
def check_updates():
    if _platform_managed():
        flash(PLATFORM_MANAGED_MESSAGE, "error")
        return redirect(url_for("settings.view_settings", tab="general"))
    with SessionLocal() as db:
        result = update.check_latest(db)
        flash(result.get("message") or "Checked GitHub.", "success" if result.get("ok") else "error")
    return redirect(url_for("settings.view_settings", tab="update"))


@bp.route("/updates/install", methods=["POST"])
@auth.admin_required
def install_update():
    if _platform_managed():
        flash(PLATFORM_MANAGED_MESSAGE, "error")
        return redirect(url_for("settings.view_settings", tab="general"))
    try:
        with SessionLocal() as db:
            result = update.install_latest(db)
        update.schedule_restart()
        flash(result.get("message") or "Installing…", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:  # noqa: BLE001
        flash(f"Install failed: {exc}", "error")
    return redirect(url_for("settings.view_settings", tab="update"))


@bp.route("/updates/rollback", methods=["POST"])
@auth.admin_required
def rollback_update():
    if _platform_managed():
        flash(PLATFORM_MANAGED_MESSAGE, "error")
        return redirect(url_for("settings.view_settings", tab="general"))
    try:
        update.rollback_code()
        update.schedule_restart()
        flash("Rolled back to the previous app. Restarting…", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:  # noqa: BLE001
        flash(f"Rollback failed: {exc}", "error")
    return redirect(url_for("settings.view_settings", tab="update"))
