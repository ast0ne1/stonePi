from __future__ import annotations

import logging
from flask import Blueprint, Response, abort, flash, redirect, request, url_for
from sqlalchemy import select

from stonepi_auth import is_public_exposure

from app.config import env
from app.db import SessionLocal
from app.models import CalendarConnection, Event, User
from app.services import auth, calendar_sync, settings as settings_service

bp = Blueprint("calendar", __name__, url_prefix="/calendar")
logger = logging.getLogger("eventtrakr.routes.calendar")


def _ics_requires_login() -> None:
    if is_public_exposure() and not auth.get_current_user():
        abort(404)


@bp.route("/ics/<int:event_id>")
def download_event_ics(event_id: int):
    _ics_requires_login()
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if not event:
            abort(404)
        ics_data = calendar_sync.build_event_ics(event)

    filename = f"event-{event_id}.ics"
    return Response(
        ics_data,
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/feed/<username>.ics")
def user_calendar_feed(username: str):
    _ics_requires_login()
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if not user:
            abort(404)
        ics_data = calendar_sync.build_user_calendar_feed(db, user.id)

    return Response(
        ics_data,
        mimetype="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{username}-events.ics"'},
    )


@bp.route("/google/connect")
@auth.login_required
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

            conn.access_token = creds.token
            conn.refresh_token = creds.refresh_token or conn.refresh_token
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
