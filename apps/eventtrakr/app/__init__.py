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

__version__ = "0.0.11"
__asset_rev__ = asset_rev(Path(__file__).resolve().parent / "static")  # cache-bust token; changes with static/
__github_user__ = "ast0ne1"
__github__ = "https://github.com/ast0ne1"


def create_app() -> Flask:
    app = Flask(__name__)
    add_brand_fonts_route(app)  # /assets/fonts when reached directly (run-dev); nginx serves it on the Pi
    app.config["SECRET_KEY"] = auth.session_secret()
    add_shared_templates(app.jinja_env)
    app.jinja_env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev(), favicon_src=favicon.cached_src)

    # Initialize SQLite database and seed defaults
    init_db()

    # Start background scheduler
    schedule.start_scheduler()

    # Backfill favicons for catalog/sources missing one (non-blocking)
    favicon.backfill_all_async()

    # Register blueprints
    from app.routes.agent import bp as agent_bp
    from app.routes.map import bp as map_bp
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
    app.register_blueprint(agent_bp)
    app.register_blueprint(map_bp)

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
        # Explicit image type: minimal Debian has no /etc/mime.types, and an .ico guessed as
        # application/octet-stream alongside nosniff can render as a broken image.
        ext = filename.rsplit(".", 1)[-1].lower()
        mimetype = favicon.MIMETYPES.get(ext) or ("image/svg+xml" if ext == "svg" else None)
        response = send_from_directory(favicon.FAVICON_DIR, filename, mimetype=mimetype, max_age=60 * 60 * 24 * 30)
        # Cached icons come from source sites and are served without sign-in; older caches may
        # hold SVGs, so never let one run script on the StonePi origin if opened directly.
        response.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

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

    # CSRF: every page carries the household's token (cookie + {{ csrf_token }} in forms,
    # X-StonePi-CSRF on background saves) and every POST must send it back, so another page on
    # the household address can't act as you. The signed Pebble read (/api/internal/agent) has its own check.
    # Log out comes from the shared header's form (no token); being signed out is harmless.
    CSRF_EXEMPT_ENDPOINTS = {"agent.agent_read", "auth.logout", "static", "favicon"}

    @app.before_request
    def check_csrf():
        from stonepi_auth.csrf import csrf_from_request, csrf_ok_request

        g.csrf_token = csrf_from_request(request.cookies)
        if request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint in CSRF_EXEMPT_ENDPOINTS:
            return None
        if app.config.get("TESTING") and not app.config.get("CSRF_IN_TESTS"):
            return None
        form_token = request.form.get("csrf_token") if request.mimetype in (
            "application/x-www-form-urlencoded", "multipart/form-data") else None
        if csrf_ok_request(request, form_token=form_token, header_token=request.headers.get("X-StonePi-CSRF")):
            return None
        from flask import flash, jsonify, redirect

        message = "That page expired. Refresh and try again."
        if request.is_json or request.headers.get("X-StonePi-CSRF") is not None or request.path.startswith(
            (f"{(env.stonepi_prefix or '').rstrip('/')}/api/", "/api/")
        ):
            return jsonify({"ok": False, "message": message}), 403
        flash(message, "error")
        from urllib.parse import urlsplit

        back = request.referrer or ""
        if urlsplit(back).netloc not in ("", request.host):
            back = ""  # a refused cross-site post goes home, not back to the other site
        return redirect(back or (env.stonepi_prefix or "") + "/")

    @app.after_request
    def set_csrf(response):
        # The cookie goes out with the first page; forms and app.js read the same token.
        from stonepi_auth.csrf import set_csrf_cookie
        from stonepi_auth.http import request_is_https
        from stonepi_auth.session import CSRF_COOKIE

        # Every page renews it (same token, a fresh day), so an open tab doesn't go stale.
        token = g.get("csrf_token")
        if token and response.mimetype == "text/html":
            set_csrf_cookie(response, token, secure=request_is_https(request))
        return response

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
            "review_pending": _review_pending(),
            "csrf_token": g.get("csrf_token") or "",
        }

    def _review_pending() -> int:
        """Open Instagram discoveries waiting in Review (the bottom-bar badge)."""
        user = g.get("current_user")
        if user is None:
            return 0
        try:
            from sqlalchemy import func, select

            from app.db import SessionLocal
            from app.models import EventDiscovery

            with SessionLocal() as db:
                return int(
                    db.execute(
                        select(func.count(EventDiscovery.id)).where(
                            EventDiscovery.user_id == user.user_id,
                            EventDiscovery.status.in_(("candidate", "discovered")),
                        )
                    ).scalar_one()
                    or 0
                )
        except Exception:  # noqa: BLE001 — a badge must never break a page
            return 0

    def _alerts_bell_state(home_url: str) -> dict:
        secret = auth._platform_session_secret()
        platform_user = auth._platform_user() if secret and g.get("current_user") else None
        return bell_context(
            platform_user,
            session_cookie=request.cookies.get(PLATFORM_COOKIE_NAME),
            home_url=home_url,
            enabled=bool(secret),
            app_id="eventtrakr",
        )

    @app.template_filter("category_style")
    def category_style_filter(label: str | None) -> dict[str, str]:
        from app.services.category_style import category_style

        return category_style(label)

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
