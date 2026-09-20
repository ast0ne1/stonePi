from __future__ import annotations

import logging
from datetime import timedelta
from functools import wraps
from io import BytesIO

import httpx
from flask import (
    Flask,
    Response,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from app import __github__, __github_user__, __version__
from app.auth import (
    attach_session,
    clear_login_failures,
    clear_session,
    current_user,
    is_admin,
    is_signed_in,
    login_rate_limited,
    login_redirect,
    password_matches,
    record_login_failure,
    request_is_https,
    safe_next,
    session_secret,
    wants_json,
)
from stonepi_auth.csrf import csrf_from_request, csrf_ok_request, set_csrf_cookie
from stonepi_auth.http import portal_home_url
from app.config import BACKUPS_DIR, DATA_DIR, HOSTED_DIR, UPDATES_DIR, env
from app.db import init_db
from app import db as database
from app.services import backup, hostname, pages as pages_svc, qrcode, settings, tls, update
from app.services import fetch_proxy, users as users_svc

logging.basicConfig(level=logging.INFO)

SETTINGS_TABS = (
    ("device", "General"),
    ("users", "Users"),
    ("backup", "Backup/Restore"),
    ("update", "Update"),
    ("about", "About"),
)
ADMIN_ONLY_SETTINGS_TABS = frozenset({"users", "backup", "update"})
PLATFORM_HIDDEN_SETTINGS_TABS = frozenset({"users", "update"})
PLATFORM_MANAGED_MESSAGE = "Household accounts and updates are managed in StonePi."
USER_SETTINGS_TABS = tuple(
    (key, label) for key, label in SETTINGS_TABS if key not in ADMIN_ONLY_SETTINGS_TABS
)
SETTINGS_LEDES = {
    "device": "Your login password, hostname, HTTPS, and instance name. Colour palette is under StonePi → Settings → General.",
    "device_user": "Your login password. Colour palette is under StonePi → Settings → General.",
    "users": "Household accounts. Create users, reset passwords, or remove access.",
    "backup": "Download or restore a zip of your pages and settings. Roll back the last app install here.",
    "update": "Check GitHub Releases and install a newer zip.",
    "about": "App name, description, GitHub, and the version running here.",
}
SETTINGS_SAVE_TABS = {"device", "update"}


def platform_managed_settings() -> bool:
    return bool(env.stonepi_session_secret.strip())


def settings_tabs_for(*, admin: bool, platform_managed: bool | None = None) -> tuple[tuple[str, str], ...]:
    tabs = SETTINGS_TABS if admin else USER_SETTINGS_TABS
    if platform_managed is None:
        platform_managed = platform_managed_settings()
    if platform_managed:
        tabs = tuple((key, label) for key, label in tabs if key not in PLATFORM_HIDDEN_SETTINGS_TABS)
    return tabs


def normalize_settings_tab(
    value: str | None, *, admin: bool = True, platform_managed: bool | None = None
) -> str:
    tab = (value or "device").strip().lower()
    allowed = {key for key, _label in settings_tabs_for(admin=admin, platform_managed=platform_managed)}
    return tab if tab in allowed else "device"


def settings_path(tab: str = "device") -> str:
    return url_for(
        "settings_page",
        tab=normalize_settings_tab(tab, admin=is_admin(), platform_managed=platform_managed_settings()),
    )

def create_app(config: dict | None = None) -> Flask:
    extra = config or {}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HOSTED_DIR.mkdir(parents=True, exist_ok=True)
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    init_db(extra.get("DATABASE_URL"))

    app = Flask(__name__)
    app.config["SECRET_KEY"] = extra.get("SECRET_KEY") or session_secret()
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
    if extra.get("TESTING"):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False

    @app.before_request
    def _open_db():
        if database.SessionLocal is None:
            init_db()
        g.db = database.SessionLocal()
        pages_svc.purge_expired(g.db)
        g.csrf_token = csrf_from_request(request.cookies)

    @app.teardown_request
    def _close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.context_processor
    def _inject():
        db = getattr(g, "db", None)
        page_count = 0
        factory = False
        home = "FileServe"
        admin = is_admin()
        pending_tls = False
        platform_managed = platform_managed_settings()
        if db is not None:
            viewer = current_user(db)
            if viewer is not None:
                page_count = len(pages_svc.list_pages(db, viewer=viewer))
            factory = settings.using_factory_admin(db)
            home = hostname.homescreen_name(db)
            pending_tls = settings.https_enabled(db) and not request_is_https()
        return {
            "app_name": "FileServe",
            "app_version": __version__,
            "app_github_user": __github_user__,
            "app_github": __github__,
            "app_port": env.port,
            "using_factory_admin": factory,
            "homescreen_name": home,
            "page_count": page_count,
            "current_user_is_admin": admin,
            "tls_pending_restart": pending_tls,
            "platform_managed": platform_managed,
            "settings_tabs": settings_tabs_for(admin=admin, platform_managed=platform_managed),
            "settings_ledes": {
                **SETTINGS_LEDES,
                "device": SETTINGS_LEDES["device"] if admin else SETTINGS_LEDES["device_user"],
            },
            "settings_save_tabs": SETTINGS_SAVE_TABS if admin else {"device"},
            "csrf_token": getattr(g, "csrf_token", csrf_from_request(request.cookies)),
            "stonepi_home_url": portal_home_url(request, env.stonepi_public_origin),
        }

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if is_signed_in():
                return view(*args, **kwargs)
            if wants_json():
                return jsonify({"ok": False, "message": "Not signed in"}), 401
            return login_redirect()

        return wrapped

    def admin_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not is_signed_in():
                if wants_json():
                    return jsonify({"ok": False, "message": "Not signed in"}), 401
                return login_redirect()
            if not is_admin():
                if wants_json():
                    return jsonify({"ok": False, "message": "Admin only"}), 403
                flash("That action is for the household admin.", "error")
                return redirect(settings_path("device"))
            return view(*args, **kwargs)

        return wrapped

    def json_or_redirect(message: str, location: str, *, error: bool = False, status: int = 200, **extra):
        if wants_json():
            payload = {"ok": not error, "message": message, **extra}
            return jsonify(payload), (400 if error else status)
        if error:
            flash(message, "error")
        else:
            flash(message, "ok")
        return redirect(location)

    def _csrf_header() -> str | None:
        return request.headers.get("X-StonePi-CSRF") or request.headers.get("x-stonepi-csrf")

    def _csrf_valid() -> bool:
        if app.config.get("TESTING"):
            return True
        return csrf_ok_request(
            request.cookies,
            form_token=request.form.get("csrf_token"),
            header_token=_csrf_header(),
        )

    def _csrf_reject(message: str = "That form expired. Refresh and try again.", location: str | None = None):
        if wants_json():
            return jsonify({"ok": False, "message": message}), 403
        flash(message, "error")
        return redirect(location or request.referrer or url_for("pages_list"))

    def _require_csrf(location: str | None = None):
        if _csrf_valid():
            return None
        return _csrf_reject(location=location)

    def _require_viewer():
        user = current_user(g.db)
        if user is None:
            abort(401)
        return user

    def _page_form():
        label = (request.form.get("label") or request.form.get("title") or "").strip()
        slug = (request.form.get("slug") or "").strip()
        protect = request.form.get("protect") == "1"
        username = (request.form.get("page_username") or "").strip()
        password = request.form.get("page_password") or ""
        expiry_mode = (request.form.get("expiry") or "none").strip().lower()
        expiry_date = (request.form.get("expiry_date") or "").strip()
        description = (request.form.get("description") or "").strip()
        return label, slug, protect, username, password, expiry_mode, expiry_date, description

    def _remember_reveal(protect: bool, username: str, password: str, page):
        if not (protect and password):
            return None
        reveal = {
            "id": page.id,
            "title": page.title,
            "username": username,
            "password": password,
        }
        session["page_reveal"] = reveal
        return reveal

    def _page_unauthorized():
        return Response("Authentication required.\n", 401, {"WWW-Authenticate": 'Basic realm="FileServe page"'})

    def _require_page(page_id: int):
        page = pages_svc.get_page(g.db, page_id)
        if page is None:
            abort(404)
        return page

    def _require_managed_page(page_id: int):
        page = _require_page(page_id)
        viewer = _require_viewer()
        if not pages_svc.can_manage(viewer, page):
            abort(403)
        return page, viewer

    def _authorize_public_page(page):
        if page is None:
            abort(404)
        if not page.enabled and not is_signed_in():
            abort(404)
        if page.is_protected and not is_signed_in():
            auth = request.authorization
            username = auth.username if auth else ""
            password = auth.password if auth else ""
            if not pages_svc.credentials_allowed(page, username, password):
                return _page_unauthorized()
        return None

    def _serve_hosted_page(page, *, asset: str = ""):
        denied = _authorize_public_page(page)
        if denied is not None:
            return denied
        if page.page_type == "site" and not asset and not request.path.endswith("/"):
            return redirect(page.public_path + "/", code=302)
        path = pages_svc.resolve_public_path(page, asset)
        if path is None:
            abort(404)
        if not asset:
            pages_svc.record_open(g.db, page)
        send_kwargs = {
            "mimetype": pages_svc.asset_mimetype(path) if asset or page.page_type == "site" else pages_svc.public_mimetype(page),
            "as_attachment": False,
            "max_age": 0,
        }
        if page.page_type == "pdf" and not asset:
            send_kwargs["download_name"] = pages_svc.download_name(page)
            send_kwargs["mimetype"] = pages_svc.public_mimetype(page)
        elif page.page_type == "docx" and not asset:
            send_kwargs["mimetype"] = pages_svc.public_mimetype(page)
        elif page.page_type == "html" and not asset:
            send_kwargs["mimetype"] = pages_svc.public_mimetype(page)
        if page.page_type == "site":
            # Serve from memory so Windows can delete the site folder while responses settle.
            payload = path.read_bytes()
            buffer = BytesIO(payload)
            buffer.seek(0)
            return send_file(buffer, download_name=path.name, **send_kwargs)
        return send_file(path, **send_kwargs)

    def _owner_filter_id(raw: str | None) -> int | None:
        value = (raw or "").strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        owner = users_svc.get_by_username(g.db, value)
        return owner.id if owner is not None else None

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "service": "fileserve"})

    @app.get("/api/display")
    def api_display():
        """Compact household stats for StonePi → TRMNL overview push."""
        from app.models import Page

        db = getattr(g, "db", None)
        pages = 0
        if db is not None:
            try:
                pages = db.query(Page).filter(Page.enabled.is_(True)).count()
            except Exception:
                pages = 0
        return jsonify({"ok": True, "pages": int(pages)})

    @app.after_request
    def _csrf_cookie(response):
        token = getattr(g, "csrf_token", None)
        if token:
            set_csrf_cookie(response, token, secure=request_is_https())
        return response

    @app.after_request
    def _stonepi_prefix(response):
        if env.stonepi_prefix:
            from stonepi_auth.prefix import PrefixRewriter

            return PrefixRewriter(env.stonepi_prefix).apply_flask(response)
        return response

    @app.get("/")
    def home():
        if is_signed_in():
            return redirect(url_for("pages_list"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        nxt = safe_next(request.values.get("next"))
        if env.stonepi_session_secret.strip():
            if is_signed_in() and request.method == "GET":
                return redirect(nxt)
            if request.method == "GET":
                return login_redirect()
        if is_signed_in() and request.method == "GET":
            return redirect(nxt)
        error = None
        username = request.form.get("username", "")
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            if not _csrf_valid():
                error = "That sign-in form expired. Refresh and try again."
                if wants_json():
                    return jsonify({"ok": False, "message": error}), 403
            elif login_rate_limited(request, username):
                error = "Too many failed attempts. Wait a few minutes and try again."
                if wants_json():
                    return jsonify({"ok": False, "message": error}), 429
            else:
                password = request.form.get("password") or ""
                user = users_svc.authenticate(g.db, username, password)
                if user is not None:
                    clear_login_failures(request, username)
                    attach_session(user)
                    if wants_json():
                        return jsonify({"ok": True, "message": "Signed in."})
                    return redirect(nxt)
                record_login_failure(request, username)
                error = "Username or password is incorrect."
                if wants_json():
                    return jsonify({"ok": False, "message": error}), 400
        return render_template("login.html", error=error, username=username, next=nxt)

    @app.post("/logout")
    def logout():
        denied = _require_csrf(url_for("pages_list"))
        if denied is not None:
            return denied
        clear_session()
        settings = None
        if env.stonepi_session_secret.strip():
            from app.auth import _platform_settings
            from stonepi_auth import logout_url

            settings = _platform_settings()
            target = logout_url(settings, "/") if settings else url_for("login")
        else:
            target = url_for("login")
        if wants_json():
            return jsonify({"ok": True, "message": "Signed out.", "reauth": True})
        return redirect(target)

    @app.get("/admin")
    @login_required
    def pages_list():
        viewer = _require_viewer()
        share_url = hostname.get_share_url(g.db)
        owner_id = _owner_filter_id(request.args.get("user")) if is_admin() else None
        listed = pages_svc.list_pages(g.db, viewer=viewer, owner_id=owner_id)
        items = []
        for page in listed:
            path = page.public_path.lstrip("/")
            url = qrcode.page_url(share_url, path)
            items.append(
                {
                    "page": page,
                    "url": url,
                    "qr_png": qrcode.png_data_uri(url),
                }
            )
        reveal = session.pop("page_reveal", None)
        filter_users = users_svc.list_users(g.db) if is_admin() else []
        return render_template(
            "pages.html",
            pages=items,
            active="pages",
            share_url=share_url,
            reveal=reveal,
            filter_users=filter_users,
            filter_user_id=owner_id,
            current_user_is_admin=is_admin(),
        )

    @app.get("/browse")
    def browse_pages():
        share_url = hostname.get_share_url(g.db)
        viewer = current_user(g.db) if is_signed_in() else None
        items = pages_svc.list_public_pages(g.db, viewer=viewer)
        return render_template("browse.html", pages=items, share_url=share_url)

    @app.route("/admin/add", methods=["GET", "POST"])
    @login_required
    def add_page():
        viewer = _require_viewer()
        error = None
        label = request.form.get("label") or request.form.get("title") or ""
        slug = request.form.get("slug") or ""
        protect = request.form.get("protect") == "1"
        page_username = request.form.get("page_username") or ""
        expiry_mode = (request.form.get("expiry") or "none").strip().lower()
        expiry_date = (request.form.get("expiry_date") or "").strip()
        description = request.form.get("description") or ""
        if request.method == "POST":
            denied = _require_csrf(url_for("add_page"))
            if denied is not None:
                return denied
            label, slug, protect, page_username, password, expiry_mode, expiry_date, description = _page_form()
            upload = request.files.get("file")
            try:
                if upload is None:
                    raise ValueError("Choose an HTML, PDF, or Word file.")
                page = pages_svc.create_page(
                    g.db,
                    label,
                    upload,
                    owner=viewer,
                    slug=slug,
                    protect=protect,
                    username=page_username,
                    password=password,
                    expires_at=pages_svc.resolve_expiry(expiry_mode, expiry_date),
                    description=description,
                )
                reveal = _remember_reveal(protect, page_username, password, page)
                extra = {"reveal": reveal} if reveal else {}
                return json_or_redirect(f"Hosted {page.title}.", url_for("pages_list"), **extra)
            except ValueError as exc:
                error = str(exc)
                if wants_json():
                    return jsonify({"ok": False, "message": error}), 400
        return render_template(
            "add.html",
            active="add",
            add_tab="file",
            error=error,
            label=label,
            slug=slug,
            protected=protect,
            page_username=page_username,
            expiry_mode=expiry_mode,
            expiry_date=expiry_date,
            description=description,
        )

    @app.get("/admin/add/url")
    @login_required
    def add_from_url():
        return render_template(
            "add.html",
            active="add",
            add_tab="url",
            error=None,
            label="",
            slug="",
            protected=False,
            page_username="",
            expiry_mode="none",
            expiry_date="",
            description="",
        )

    @app.get("/admin/add/fetch")
    @login_required
    def add_fetch():
        target = request.args.get("url") or ""
        try:
            body, content_type = fetch_proxy.fetch_url(target)
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        except httpx.HTTPStatusError as exc:
            return jsonify({"ok": False, "message": f"Upstream HTTP {exc.response.status_code}"}), 502
        except httpx.HTTPError as exc:
            return jsonify({"ok": False, "message": f"Fetch failed: {exc}"}), 502
        return Response(body, mimetype=content_type or "application/octet-stream")

    @app.get("/admin/tools/url-to-zip")
    @login_required
    def url_to_zip_tool():
        return redirect(url_for("add_from_url"))

    @app.get("/admin/tools/fetch")
    @login_required
    def tools_fetch():
        # Old bookmark path; keep working for any saved scripts.
        return add_fetch()

    @app.post("/admin/edit/<int:page_id>")
    @login_required
    def edit_page(page_id: int):
        denied = _require_csrf(url_for("pages_list"))
        if denied is not None:
            return denied
        viewer = _require_viewer()
        label, slug, protect, page_username, password, expiry_mode, expiry_date, description = _page_form()
        upload = request.files.get("file")
        try:
            page = pages_svc.update_page(
                g.db,
                page_id,
                viewer=viewer,
                title=label,
                slug=slug,
                protect=protect,
                username=page_username,
                password=password,
                expires_at=pages_svc.resolve_expiry(expiry_mode, expiry_date),
                description=description,
                upload=upload,
            )
        except ValueError as exc:
            return json_or_redirect(str(exc), url_for("pages_list"), error=True)
        reveal = _remember_reveal(protect, page_username, password, page)
        extra = {"reveal": reveal} if reveal else {}
        return json_or_redirect(f"Updated {page.title}.", url_for("pages_list"), **extra)

    @app.get("/admin/download/<int:page_id>")
    @login_required
    def download_page_file(page_id: int):
        page, _viewer = _require_managed_page(page_id)
        if page.page_type == "site":
            try:
                payload = pages_svc.site_download_bytes(page)
            except FileNotFoundError:
                abort(404)
            buffer = BytesIO(payload)
            buffer.seek(0)
            return send_file(
                buffer,
                as_attachment=True,
                download_name=pages_svc.download_name(page),
                mimetype="application/zip",
            )
        path = pages_svc.source_path(page)
        if not path.is_file():
            abort(404)
        return send_file(
            path,
            as_attachment=True,
            download_name=pages_svc.download_name(page),
            mimetype=pages_svc.source_mimetype(page),
        )

    @app.get("/admin/qr/<int:page_id>.png")
    @login_required
    def download_qr(page_id: int):
        page, _viewer = _require_managed_page(page_id)
        share_url = hostname.get_share_url(g.db)
        url = qrcode.page_url(share_url, page.public_path)
        png = qrcode.png_bytes(url)
        buffer = BytesIO(png)
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"{page.slug}-qr.png",
            mimetype="image/png",
        )

    @app.get("/admin/qr/<int:page_id>/print")
    @login_required
    def print_qr(page_id: int):
        page, _viewer = _require_managed_page(page_id)
        share_url = hostname.get_share_url(g.db)
        url = qrcode.page_url(share_url, page.public_path)
        return render_template(
            "qr_print.html",
            page=page,
            url=url,
            qr_png=qrcode.png_data_uri(url),
        )

    @app.post("/admin/toggle/<int:page_id>")
    @login_required
    def toggle_page(page_id: int):
        denied = _require_csrf(url_for("pages_list"))
        if denied is not None:
            return denied
        viewer = _require_viewer()
        try:
            page = pages_svc.toggle_page(g.db, page_id, viewer=viewer)
        except ValueError as exc:
            return json_or_redirect(str(exc), url_for("pages_list"), error=True)
        state = "Enabled" if page.enabled else "Disabled"
        return json_or_redirect(f"{state} {page.title}.", url_for("pages_list"))

    @app.post("/admin/delete/<int:page_id>")
    @login_required
    def delete_page(page_id: int):
        denied = _require_csrf(url_for("pages_list"))
        if denied is not None:
            return denied
        viewer = _require_viewer()
        try:
            page = pages_svc.delete_page(g.db, page_id, viewer=viewer)
        except ValueError as exc:
            return json_or_redirect(str(exc), url_for("pages_list"), error=True)
        return json_or_redirect(f"Removed {page.title}.", url_for("pages_list"))

    @app.post("/api/studio/publish")
    @login_required
    def studio_publish():
        denied = _require_csrf(url_for("pages_list"))
        if denied is not None:
            return denied
        viewer = current_user(g.db)
        if viewer is None:
            return (
                jsonify(
                    {
                        "ok": False,
                        "message": (
                            "FileServe access required — ask an admin to enable FileServe "
                            "for your account (Studio Publish needs both)."
                        ),
                    }
                ),
                403,
            )
        upload = request.files.get("file")
        if upload is None:
            return jsonify({"ok": False, "message": "Missing zip file."}), 400
        title = (request.form.get("title") or "").strip()
        slug = (request.form.get("slug") or "").strip()
        description = (request.form.get("description") or "").strip()
        protect = request.form.get("protected") == "1" or request.form.get("protect") == "1"
        username = (request.form.get("username") or request.form.get("page_username") or "").strip()
        password = request.form.get("password") or request.form.get("page_password") or ""
        expiry_mode = (request.form.get("expiry") or "none").strip().lower()
        expiry_date = (request.form.get("expiry_date") or "").strip()
        page_id_raw = (request.form.get("page_id") or "").strip()
        try:
            if page_id_raw:
                page = pages_svc.update_page(
                    g.db,
                    int(page_id_raw),
                    viewer=viewer,
                    title=title,
                    slug=slug,
                    description=description,
                    protect=protect,
                    username=username,
                    password=password,
                    expires_at=pages_svc.resolve_expiry(expiry_mode, expiry_date),
                    upload=upload,
                )
            else:
                if not title:
                    raise ValueError("Enter a label.")
                page = pages_svc.create_page(
                    g.db,
                    title,
                    upload,
                    owner=viewer,
                    slug=slug,
                    description=description,
                    protect=protect,
                    username=username,
                    password=password,
                    expires_at=pages_svc.resolve_expiry(expiry_mode, expiry_date),
                )
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 400
        return jsonify(
            {
                "ok": True,
                "message": f"Hosted {page.title}.",
                "page_id": page.id,
                "public_path": page.public_path,
            }
        )

    @app.get("/admin/settings")
    @login_required
    def settings_page():
        viewer = _require_viewer()
        admin = is_admin()
        platform_managed = platform_managed_settings()
        tab = normalize_settings_tab(request.args.get("tab"), admin=admin, platform_managed=platform_managed)
        latest = backup.latest_backup() if admin else None
        https_on = settings.https_enabled(g.db)
        device_lede = SETTINGS_LEDES["device"] if admin else SETTINGS_LEDES["device_user"]
        return render_template(
            "settings.html",
            active="settings",
            settings_tab=tab,
            settings_tabs=settings_tabs_for(admin=admin, platform_managed=platform_managed),
            settings_lede=device_lede if tab == "device" else SETTINGS_LEDES[tab],
            is_admin=admin,
            platform_managed=platform_managed,
            admin_username=viewer.username,
            instance_name=settings.get_value(g.db, "instance_name") if admin else "",
            device_hostname=settings.get_value(g.db, "device_hostname") if admin else "",
            github_repo=update.repo_from_db(g.db) if admin else "",
            update_check=update.last_check(g.db) if admin else None,
            latest_backup=latest,
            share_url=hostname.get_share_url(g.db),
            https_enabled=https_on,
            tls_status=tls.certificate_status(g.db) if admin else None,
            tls_download_url=url_for("download_root_ca") if admin else "",
            https_share_url=hostname.get_share_url(g.db) if https_on else "",
            tls_pending_restart=https_on and not request_is_https(),
            household_users=users_svc.list_users(g.db) if admin and not platform_managed else [],
        )

    @app.post("/admin/settings")
    @login_required
    def save_settings():
        denied = _require_csrf(settings_path("device"))
        if denied is not None:
            return denied
        viewer = _require_viewer()
        admin = is_admin()
        platform_managed = platform_managed_settings()
        tab = normalize_settings_tab(
            request.form.get("settings_tab"), admin=admin, platform_managed=platform_managed
        )
        if tab in PLATFORM_HIDDEN_SETTINGS_TABS and platform_managed:
            return json_or_redirect(PLATFORM_MANAGED_MESSAGE, settings_path("device"), error=True)
        reauth = False
        message = "Settings saved."
        new_password = request.form.get("new_password") or ""
        new_password_confirm = request.form.get("new_password_confirm") or ""
        current_password = request.form.get("current_password") or ""
        changing_password = bool(new_password.strip())

        def fail(message: str):
            if wants_json():
                return jsonify({"ok": False, "message": message}), 400
            flash(message, "error")
            return redirect(settings_path(tab))

        if changing_password and not platform_managed:
            if not password_matches(viewer.password_hash, current_password):
                return fail("Current password is incorrect.")
            if new_password != new_password_confirm:
                return fail("New passwords do not match.")
            if len(new_password) < 4:
                return fail("New password must be at least 4 characters.")
            try:
                users_svc.update_user(g.db, viewer, new_password=new_password)
            except ValueError as exc:
                return fail(str(exc))
            reauth = True
        elif changing_password and platform_managed:
            return fail("Change your password in StonePi → Settings → General.")

        turning_https_on = False
        turning_https_off = False
        if admin:
            previous_https = settings.https_enabled(g.db)
            want_https = False if platform_managed else (request.form.get("https_enabled") == "1")
            turning_https_on = want_https and not previous_https
            turning_https_off = previous_https and not want_https

            if not platform_managed:
                if want_https:
                    try:
                        tls.ensure_certificate(g.db)
                    except Exception:
                        logging.getLogger("fileserve").exception("TLS certificate generation failed")
                        return fail("Could not create the local HTTPS certificate. Check disk space and try again.")
                    settings.set_https_enabled(g.db, True)
                else:
                    settings.set_https_enabled(g.db, False)

            instance_name = (request.form.get("instance_name") or "").strip()
            if instance_name:
                settings.set_value(g.db, "instance_name", instance_name[:80])
            else:
                settings.clear_value(g.db, "instance_name")

            wanted_host = hostname.normalize_hostname(request.form.get("device_hostname") or "")
            previous_host = hostname.normalize_hostname(settings.get_value(g.db, "device_hostname"))
            if wanted_host:
                if not hostname.valid_hostname(wanted_host):
                    return fail("Hostname must be letters, digits, or hyphens.")
                settings.set_value(g.db, "device_hostname", wanted_host)
                hostname.apply_os_hostname(wanted_host)
            else:
                settings.clear_value(g.db, "device_hostname")

            if want_https and wanted_host != previous_host:
                try:
                    tls.ensure_certificate(g.db)
                except Exception:
                    logging.getLogger("fileserve").exception("TLS certificate refresh after hostname save failed")
                    return fail("HTTPS is on but the certificate could not be refreshed for this hostname.")

            if not platform_managed:
                github_repo = request.form.get("github_repo") or ""
                repo = update.normalize_repo(github_repo)
                if github_repo.strip() and not repo:
                    return fail("GitHub repository must look like owner/FileServe.")
                if repo:
                    settings.set_value(g.db, "github_repo", repo)
                else:
                    settings.clear_value(g.db, "github_repo")

            if turning_https_off:
                update.schedule_restart()
                message = "HTTPS is off. FileServe is restarting on plain HTTP."
            elif turning_https_on:
                message = (
                    "Certificate ready. Download the root CA below, trust it on each device, "
                    "then use Restart to enable HTTPS."
                )

        extra = {"reauth": reauth}
        if wants_json():
            if reauth:
                clear_session()
            return jsonify({"ok": True, "message": message, **extra})
        response = redirect(url_for("login") if reauth else settings_path(tab))
        if reauth:
            clear_session()
        else:
            flash(message, "ok")
        return response
    @app.get("/admin/settings/tls/root-ca.pem")
    @admin_required
    def download_root_ca():
        try:
            pem = tls.root_ca_pem_bytes()
        except FileNotFoundError:
            return json_or_redirect(
                "No root CA yet. Turn on Use HTTPS on the LAN and save settings first.",
                settings_path("device"),
                error=True,
            )
        buffer = BytesIO(pem)
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name="fileserve-root-ca.pem",
            mimetype="application/x-pem-file",
        )

    @app.post("/admin/settings/tls/restart")
    @admin_required
    def restart_for_tls():
        denied = _require_csrf(settings_path("device"))
        if denied is not None:
            return denied
        if not settings.https_enabled(g.db):
            return json_or_redirect("Turn on Use HTTPS on the LAN first.", settings_path("device"), error=True)
        try:
            tls.root_ca_pem_bytes()
        except FileNotFoundError:
            return json_or_redirect(
                "No certificate yet. Save General settings with HTTPS enabled first.",
                settings_path("device"),
                error=True,
            )
        update.schedule_restart()
        share = hostname.get_share_url(g.db)
        return json_or_redirect(f"Restarting… then open {share} (not http://).", settings_path("device"))

    @app.post("/admin/settings/users")
    @admin_required
    def create_household_user():
        denied = _require_csrf(settings_path("users"))
        if denied is not None:
            return denied
        if platform_managed_settings():
            return json_or_redirect(PLATFORM_MANAGED_MESSAGE, settings_path("device"), error=True)
        username = request.form.get("username") or ""
        password = request.form.get("password") or ""
        try:
            user = users_svc.create_user(g.db, username=username, password=password, role="user")
        except ValueError as exc:
            return json_or_redirect(str(exc), settings_path("users"), error=True)
        return json_or_redirect(f"Created {user.username}.", settings_path("users"))

    @app.post("/admin/settings/users/<int:user_id>")
    @admin_required
    def update_household_user(user_id: int):
        denied = _require_csrf(settings_path("users"))
        if denied is not None:
            return denied
        if platform_managed_settings():
            return json_or_redirect(PLATFORM_MANAGED_MESSAGE, settings_path("device"), error=True)
        user = users_svc.get_user(g.db, user_id)
        if user is None:
            return json_or_redirect("User not found.", settings_path("users"), error=True)
        new_password = (request.form.get("new_password") or "").strip()
        new_password_confirm = (request.form.get("new_password_confirm") or "").strip()
        if new_password or new_password_confirm:
            if new_password != new_password_confirm:
                return json_or_redirect("New passwords do not match.", settings_path("users"), error=True)
        active = None
        if user.role != "admin":
            active = request.form.get("active") == "1"
        try:
            users_svc.update_user(
                g.db,
                user,
                active=active,
                new_password=new_password or None,
            )
        except ValueError as exc:
            return json_or_redirect(str(exc), settings_path("users"), error=True)
        return json_or_redirect(f"Updated {user.username}.", settings_path("users"))

    @app.post("/admin/settings/users/<int:user_id>/delete")
    @admin_required
    def delete_household_user(user_id: int):
        denied = _require_csrf(settings_path("users"))
        if denied is not None:
            return denied
        if platform_managed_settings():
            return json_or_redirect(PLATFORM_MANAGED_MESSAGE, settings_path("device"), error=True)
        user = users_svc.get_user(g.db, user_id)
        if user is None:
            return json_or_redirect("User not found.", settings_path("users"), error=True)
        username = user.username
        try:
            users_svc.delete_user(g.db, user)
        except ValueError as exc:
            return json_or_redirect(str(exc), settings_path("users"), error=True)
        return json_or_redirect(f"Removed {username}.", settings_path("users"))

    @app.get("/admin/settings/backup")
    @admin_required
    def download_backup():
        buffer = BytesIO(backup.backup_bytes())
        buffer.seek(0)
        return send_file(
            buffer,
            as_attachment=True,
            download_name="fileserve-backup.zip",
            mimetype="application/zip",
        )

    @app.post("/admin/settings/backup/restore")
    @admin_required
    def restore_backup():
        denied = _require_csrf(settings_path("backup"))
        if denied is not None:
            return denied
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return json_or_redirect("Choose a backup zip.", settings_path("backup"), error=True)
        try:
            backup.restore_backup(upload.read())
        except ValueError as exc:
            return json_or_redirect(str(exc), settings_path("backup"), error=True)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("fileserve").exception("restore failed")
            return json_or_redirect(f"Could not restore the backup: {exc}", settings_path("backup"), error=True)
        clear_session()
        return json_or_redirect(
            "Backup restored. Sign in again if your password changed.",
            url_for("login"),
            reauth=True,
        )

    @app.post("/admin/settings/updates/check")
    @admin_required
    def check_updates():
        denied = _require_csrf(settings_path("update"))
        if denied is not None:
            return denied
        if platform_managed_settings():
            return json_or_redirect(PLATFORM_MANAGED_MESSAGE, settings_path("device"), error=True)
        result = update.check_latest(g.db)
        message = result.get("message") or "Checked GitHub."
        if wants_json():
            return jsonify({"ok": bool(result.get("ok")), "message": message})
        flash(message, "ok" if result.get("ok") else "error")
        return redirect(settings_path("update"))

    @app.post("/admin/settings/updates/install")
    @admin_required
    def install_update():
        denied = _require_csrf(settings_path("update"))
        if denied is not None:
            return denied
        if platform_managed_settings():
            return json_or_redirect(PLATFORM_MANAGED_MESSAGE, settings_path("device"), error=True)
        try:
            result = update.install_latest(g.db)
        except ValueError as exc:
            return json_or_redirect(str(exc), settings_path("update"), error=True)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("fileserve").exception("update install failed")
            return json_or_redirect(f"Could not install the update: {exc}", settings_path("update"), error=True)
        update.schedule_restart()
        return json_or_redirect(result.get("message") or "Installed. Restarting…", settings_path("update"))

    @app.post("/admin/settings/updates/rollback")
    @admin_required
    def rollback_update():
        denied = _require_csrf(settings_path("backup"))
        if denied is not None:
            return denied
        if platform_managed_settings():
            return json_or_redirect(PLATFORM_MANAGED_MESSAGE, settings_path("device"), error=True)
        try:
            update.rollback_code()
        except ValueError as exc:
            return json_or_redirect(str(exc), settings_path("backup"), error=True)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("fileserve").exception("update rollback failed")
            return json_or_redirect(f"Could not roll back: {exc}", settings_path("backup"), error=True)
        update.schedule_restart()
        return json_or_redirect("Rolled back. Restarting…", settings_path("backup"))

    @app.get("/manifest.webmanifest")
    def manifest():
        db = g.db
        name = hostname.homescreen_name(db) if db is not None else "FileServe"
        return jsonify(
            {
                "name": name,
                "short_name": name,
                "start_url": "/",
                "display": "standalone",
                "background_color": "#f3eee4",
                "theme_color": "#f3eee4",
                "icons": [{"src": "/static/icons/apple-touch-icon.svg", "sizes": "180x180", "type": "image/svg+xml"}],
            }
        )

    @app.get("/u/<username>/<slug>")
    def user_public_page(username: str, slug: str):
        page = pages_svc.get_user_page(g.db, username, slug)
        return _serve_hosted_page(page)

    @app.get("/u/<username>/<slug>/")
    def user_public_page_slash(username: str, slug: str):
        return user_public_page(username, slug)

    @app.get("/u/<username>/<slug>/<path:asset>")
    def user_public_asset(username: str, slug: str, asset: str):
        page = pages_svc.get_user_page(g.db, username, slug)
        return _serve_hosted_page(page, asset=asset)

    @app.get("/<slug>")
    def public_page(slug: str):
        page = pages_svc.get_root_by_slug(g.db, slug)
        return _serve_hosted_page(page)

    @app.get("/<slug>/")
    def public_page_slash(slug: str):
        return public_page(slug)

    @app.get("/<slug>/<path:asset>")
    def public_asset(slug: str, asset: str):
        page = pages_svc.get_root_by_slug(g.db, slug)
        return _serve_hosted_page(page, asset=asset)

    return app


app = create_app()
