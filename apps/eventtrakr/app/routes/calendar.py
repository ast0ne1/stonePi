from __future__ import annotations

import logging
import secrets

from flask import Blueprint, Response, abort, flash, redirect, request, url_for
from sqlalchemy import select

from app.config import env
from app.db import SessionLocal
from app.models import CalendarConnection, Event, User
from app.services import auth, calendar_sync, capabilities, settings as settings_service
from app.services import token_crypto

bp = Blueprint("calendar", __name__, url_prefix="/calendar")
logger = logging.getLogger("eventtrakr.routes.calendar")

FEED_TOKEN_KEY = "calendar_feed_token"

sync_calendar_required = auth.capability_required(
    capabilities.SYNC_CALENDAR, redirect_endpoint="settings.view_settings", tab="calendar"
)


def ensure_feed_token(db, user_id: int) -> str:
    """Stable secret for webcal clients that cannot send a login cookie."""
    token = (settings_service.get_user_value(db, user_id, FEED_TOKEN_KEY) or "").strip()
    if token:
        return token
    token = secrets.token_urlsafe(24)
    settings_service.set_user_value(db, user_id, FEED_TOKEN_KEY, token)
    return token


def _require_login_user():
    user = auth.get_current_user()
    if not user:
        abort(404)
    return user


@bp.route("/ics/<int:event_id>")
def download_event_ics(event_id: int):
    """Download one event as .ics — owner (or admin) only, always authenticated."""
    viewer = _require_login_user()
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if not event:
            abort(404)
        if event.user_id != viewer.user_id and viewer.role != "admin":
            abort(404)
        ics_data = calendar_sync.build_event_ics(event)

    filename = f"event-{event_id}.ics"
    return Response(
        ics_data,
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _serve_user_feed(db, owner: User) -> Response:
    if not owner or not capabilities.user_can(owner, capabilities.SYNC_CALENDAR):
        abort(404)
    ics_data = calendar_sync.build_user_calendar_feed(db, owner.id)
    return Response(
        ics_data,
        mimetype="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{owner.username}-events.ics"'},
    )


@bp.route("/feed/<username>/<token>.ics")
def user_calendar_feed_token(username: str, token: str):
    """Unauthenticated webcal poll — gated by a per-user secret in the URL."""
    expect = (token or "").strip()
    if not expect:
        abort(404)
    with SessionLocal() as db:
        owner = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not owner:
            abort(404)
        stored = (settings_service.get_user_value(db, owner.id, FEED_TOKEN_KEY) or "").strip()
        try:
            token_ok = bool(stored) and secrets.compare_digest(stored, expect)
        except ValueError:
            token_ok = False
        if not token_ok:
            abort(404)
        return _serve_user_feed(db, owner)


@bp.route("/feed/<username>.ics")
def user_calendar_feed(username: str):
    """Browser download of the feed — must be signed in as that person (or admin)."""
    viewer = _require_login_user()
    with SessionLocal() as db:
        owner = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not owner:
            abort(404)
        if owner.id != viewer.user_id and viewer.role != "admin":
            abort(404)
        return _serve_user_feed(db, owner)


@bp.route("/google/connect")
@auth.login_required
@sync_calendar_required
def google_connect():
    with SessionLocal() as db:
        client_id, client_secret = settings_service.get_google_oauth_credentials(db)

    if not client_id or not client_secret:
        flash("Google Client ID and Secret are not configured -- add them under Settings > Calendar Integrations.", "error")
        return redirect(url_for("settings.view_settings", tab="calendar"))

    try:
        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=["https://www.googleapis.com/auth/calendar.events"],
            redirect_uri=env.google_redirect_uri,
        )
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
        return redirect(auth_url)
    except Exception as e:
        logger.exception("Google connect error: %s", e)
        flash(f"Failed to initiate Google OAuth: {e}", "error")
        return redirect(url_for("settings.view_settings", tab="calendar"))


@bp.route("/google/callback")
@auth.login_required
@sync_calendar_required
def google_callback():
    user = auth.get_current_user()
    code = request.args.get("code")
    if not code:
        flash("Google authorization failed or was cancelled.", "error")
        return redirect(url_for("settings.view_settings", tab="calendar"))

    try:
        from google_auth_oauthlib.flow import Flow

        with SessionLocal() as cred_db:
            client_id, client_secret = settings_service.get_google_oauth_credentials(cred_db)

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=["https://www.googleapis.com/auth/calendar.events"],
            redirect_uri=env.google_redirect_uri,
        )
        flow.fetch_token(code=code)
        creds = flow.credentials

        with SessionLocal() as db:
            conn = db.execute(
                select(CalendarConnection).where(
                    CalendarConnection.user_id == user.user_id,
                    CalendarConnection.provider == "google",
                )
            ).scalar_one_or_none()

            if not conn:
                conn = CalendarConnection(
                    user_id=user.user_id,
                    provider="google",
                    account_email=getattr(creds, "id_token", {}).get("email", "Google Account") if hasattr(creds, "id_token") and isinstance(creds.id_token, dict) else "Connected Google Account",
                )
                db.add(conn)

            conn.access_token = token_crypto.seal(creds.token)
            if creds.refresh_token:
                conn.refresh_token = token_crypto.seal(creds.refresh_token)
            elif conn.refresh_token and not token_crypto.is_sealed(conn.refresh_token):
                conn.refresh_token = token_crypto.seal(conn.refresh_token)
            conn.token_expiry = creds.expiry
            db.commit()

        flash("Google Calendar connected successfully! Favourited events will now be forwarded to your calendar.", "success")
    except Exception as e:
        logger.exception("Google callback error: %s", e)
        flash(f"Error connecting Google Calendar: {e}", "error")

    return redirect(url_for("settings.view_settings", tab="calendar"))


@bp.route("/google/disconnect", methods=["POST"])
@auth.login_required
def google_disconnect():
    user = auth.get_current_user()
    with SessionLocal() as db:
        conn = db.execute(
            select(CalendarConnection).where(
                CalendarConnection.user_id == user.user_id,
                CalendarConnection.provider == "google",
            )
        ).scalar_one_or_none()
        if conn:
            db.delete(conn)
            db.commit()
            flash("Google Calendar disconnected.", "success")
    return redirect(url_for("settings.view_settings", tab="calendar"))
