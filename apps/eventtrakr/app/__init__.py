from __future__ import annotations

from datetime import datetime
from pathlib import Path

from flask import Flask, g, request, send_from_directory

from app.config import env
from app.db import init_db
from app.services import auth, favicon, ingest, schedule
from stonepi_auth.alerts import add_shared_templates, bell_context
from stonepi_auth.brand import add_brand_fonts_route, asset_rev, fonts_rev
from stonepi_auth.http import portal_home_url
from stonepi_auth.session import COOKIE_NAME as PLATFORM_COOKIE_NAME

__version__ = "0.0.6"
__asset_rev__ = asset_rev(Path(__file__).resolve().parent / "static")  # cache-bust token; changes with static/
__github_user__ = "ast0ne1"
__github__ = "https://github.com/ast0ne1"


def create_app() -> Flask:
    app = Flask(__name__)
    add_brand_fonts_route(app)  # /assets/fonts when reached directly (run-dev); nginx serves it on the Pi
    app.config["SECRET_KEY"] = auth.session_secret()
    add_shared_templates(app.jinja_env)
    app.jinja_env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())

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
    from app.routes.discoveries import bp as discoveries_bp
    from app.routes.settings import bp as settings_bp
    from app.routes.sources import bp as sources_bp
    from app.routes.ui import bp as ui_bp

    app.register_blueprint(ui_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(sources_bp)
    app.register_blueprint(discoveries_bp)
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
        path = request.path or ""
        pfx = (env.stonepi_prefix or "").rstrip("/")
        if (
            path.startswith("/static")
            or path.startswith("/favicon")
            or (pfx and (path.startswith(f"{pfx}/static") or path.startswith(f"{pfx}/favicon")))
        ):
            g.current_user = None
            return
        g.current_user = auth.get_current_user()

    @app.context_processor
    def inject_globals():
        factory = False
        try:
            from stonepi_auth.session import factory_admin_warning

            if env.stonepi_session_secret.strip():
                factory = factory_admin_warning(auth._platform_user())
            else:
                from app.services import users as users_svc

                factory = users_svc.using_factory_admin()
        except Exception:
            factory = False
        home_url = portal_home_url(request, env.stonepi_public_origin)
        return {
            "current_user": g.get("current_user"),
            "ingest_state": ingest.state,
            "app_version": __version__,
            "asset_rev": __asset_rev__,
            "using_factory_admin": factory,
            "platform_managed": bool(env.stonepi_session_secret.strip()),
            "stonepi_home_url": home_url,
            "stonepi_prefix": env.stonepi_prefix,
            "alerts_bell_state": _alerts_bell_state(home_url),
        }

    def _alerts_bell_state(home_url: str) -> dict:
        secret = auth._platform_session_secret()
        platform_user = auth._platform_user() if secret and g.get("current_user") else None
        return bell_context(
            platform_user,
            session_cookie=request.cookies.get(PLATFORM_COOKIE_NAME),
            home_url=home_url,
            enabled=bool(secret),
        )

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
