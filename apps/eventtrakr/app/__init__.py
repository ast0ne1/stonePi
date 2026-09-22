from __future__ import annotations

import os
from datetime import datetime
from flask import Flask, g, request, send_from_directory

from app.config import env
from app.db import init_db
from app.services import auth, favicon, ingest, schedule
from stonepi_auth.http import portal_home_url

__version__ = "0.0.3"
__github_user__ = "ast0ne1"
__github__ = "https://github.com/ast0ne1"

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _asset_version(relative_path: str) -> str:
    """Cache-busting token for a static asset, derived from its own
    last-modified time. A hardcoded version string never changes across
    deploys, so browsers can keep serving a stale cached app.js/app.css
    indefinitely after an update -- tying it to mtime forces a refetch
    whenever the file actually changes, and restarting the server is enough
    to pick that up (no separate build/versioning step needed)."""
    path = os.path.join(_STATIC_DIR, relative_path)
    try:
        return str(int(os.path.getmtime(path)))
    except OSError:
        return "0"


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = auth.session_secret()

    # Initialize SQLite database and seed defaults
    init_db()

    # Start background scheduler
    schedule.start_scheduler()

    # Backfill favicons for catalog/sources missing one (non-blocking)
    favicon.backfill_all_async()

    # Register blueprints
    from app.routes.api import bp as api_bp
    from app.routes.auth import bp as auth_bp
    from app.routes.calendar import bp as calendar_bp
    from app.routes.settings import bp as settings_bp
    from app.routes.sources import bp as sources_bp
    from app.routes.ui import bp as ui_bp

    app.register_blueprint(ui_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(calendar_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(api_bp)

    @app.route("/healthz")
    def healthz():
        return {"ok": True, "service": "eventtrakr"}

    @app.after_request
    def _stonepi_prefix(response):
        if env.stonepi_prefix:
            from stonepi_auth.prefix import PrefixRewriter

            return PrefixRewriter(env.stonepi_prefix).apply_flask(response)
        return response

    @app.route("/favicon-cache/<path:filename>")
    def favicon_cache(filename):
        return send_from_directory(favicon.FAVICON_DIR, filename, max_age=60 * 60 * 24 * 30)

    @app.before_request
    def load_user():
        g.current_user = auth.get_current_user()

    app_js_version = _asset_version("js/app.js")
    app_css_version = _asset_version("css/app.css")

    @app.context_processor
    def inject_globals():
        factory = False
        try:
            from app.services import users as users_svc

            factory = users_svc.using_factory_admin()
        except Exception:
            factory = False
        return {
            "current_user": g.get("current_user"),
            "ingest_state": ingest.state,
            "app_js_version": app_js_version,
            "app_css_version": app_css_version,
            "using_factory_admin": factory,
            "stonepi_home_url": portal_home_url(request, env.stonepi_public_origin),
        }

    @app.template_filter("format_datetime")
    def format_datetime_filter(dt: datetime | None) -> str:
        if not dt:
            return ""
        return dt.strftime("%a %d %b, %H:%M")

    @app.template_filter("format_date")
    def format_date_filter(dt: datetime | None) -> str:
        if not dt:
            return ""
        return dt.strftime("%A, %d %B %Y")

    @app.template_filter("format_time")
    def format_time_filter(dt: datetime | None) -> str:
        if not dt:
            return ""
        return dt.strftime("%H:%M")

    return app
