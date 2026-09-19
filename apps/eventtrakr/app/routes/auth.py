from __future__ import annotations

from flask import Blueprint, make_response, redirect, render_template, request, url_for
from sqlalchemy import select
from stonepi_auth.csrf import csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import client_ip, request_is_https
from stonepi_auth.session import CSRF_COOKIE

from app.db import SessionLocal
from app.models import User
from app.services import auth, passwords

bp = Blueprint("auth", __name__)


def _login_response(*, error: str | None, next_url: str, status: int = 200):
    token = csrf_from_request(request.cookies)
    html = render_template(
        "login.html",
        error=error,
        next=next_url,
        csrf_token=token,
    )
    response = make_response(html, status)
    set_csrf_cookie(response, token, secure=request_is_https(request))
    return response


@bp.route("/login", methods=["GET", "POST"])
def login():
    if auth.get_current_user():
        return redirect(url_for("ui.agenda"))

    next_url = request.args.get("next") or url_for("ui.agenda")
    settings = auth._platform_settings()
    if settings is not None:
        from stonepi_auth import login_url

        return redirect(login_url(settings, next_url))

    error = None
    ip = client_ip(request)

    if request.method == "POST":
        if not csrf_ok(request.cookies.get(CSRF_COOKIE), request.form.get("csrf_token")):
            return _login_response(
                error="That sign-in form expired. Refresh and try again.",
                next_url=next_url,
                status=400,
            )
        if not auth.check_rate_limit(ip):
            return _login_response(
                error="Too many failed attempts. Please wait 15 minutes.",
                next_url=next_url,
                status=429,
            )

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with SessionLocal() as db:
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user and user.active and passwords.verify_password(user.password, password):
                auth.clear_login_failures(ip)
                token = auth.create_session_token(user.id, user.username, user.role)
                resp = redirect(next_url)
                auth.set_auth_cookie(resp, token)
                return resp
            auth.record_login_failure(ip)
            error = "Invalid username or password"

    return _login_response(error=error, next_url=next_url)


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    settings = auth._platform_settings()
    if settings is not None:
        from stonepi_auth import logout_url

        resp = redirect(logout_url(settings, "/"))
    else:
        resp = redirect(url_for("auth.login"))
    auth.clear_auth_cookie(resp)
    return resp
