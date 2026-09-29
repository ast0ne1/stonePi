from __future__ import annotations

import json

from flask import Blueprint, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from app.db import SessionLocal
from app.models import EventDiscovery, SocialAccount, SocialPost
from app.services import auth
from app.services.social import discover as discover_service

bp = Blueprint("discoveries", __name__, url_prefix="/discoveries")


def _reasons(row: EventDiscovery) -> list[str]:
    try:
        data = json.loads(row.reasons_json or "[]")
        if isinstance(data, list):
            return [str(x) for x in data]
    except (TypeError, json.JSONDecodeError):
        pass
    return []


@bp.route("")
@bp.route("/")
@auth.login_required
def list_discoveries():
    user = auth.get_current_user()
    status_filter = (request.args.get("status") or "open").strip()
    with SessionLocal() as db:
        query = select(EventDiscovery).where(EventDiscovery.user_id == user.user_id)
        if status_filter == "open":
            query = query.where(EventDiscovery.status.in_(("candidate", "discovered")))
        elif status_filter != "all":
            query = query.where(EventDiscovery.status == status_filter)
        rows = list(db.execute(query.order_by(EventDiscovery.created_at.desc()).limit(100)).scalars())

        account_ids = {r.social_account_id for r in rows}
        post_ids = {r.social_post_id for r in rows}
        accounts = {
            a.id: a
            for a in db.execute(select(SocialAccount).where(SocialAccount.id.in_(account_ids or [-1]))).scalars()
        }
        posts = {
            p.id: p
            for p in db.execute(select(SocialPost).where(SocialPost.id.in_(post_ids or [-1]))).scalars()
        }

        items = []
        for row in rows:
            account = accounts.get(row.social_account_id)
            post = posts.get(row.social_post_id)
            items.append(
                {
                    "discovery": row,
                    "account": account,
                    "post": post,
                    "reasons": _reasons(row),
                    "auto_added": row.status == "discovered" and row.event_id is not None,
                }
            )

    return render_template(
        "discoveries.html",
        items=items,
        status_filter=status_filter,
    )


@bp.route("/<int:discovery_id>/confirm", methods=["POST"])
@auth.login_required
def confirm(discovery_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        event = discover_service.confirm_discovery(db, discovery_id, user.user_id)
        if event:
            flash(f"Added “{event.title}” to your agenda.", "success")
        else:
            flash("Could not confirm that discovery.", "error")
    return redirect(url_for("discoveries.list_discoveries"))


@bp.route("/<int:discovery_id>/ignore", methods=["POST"])
@auth.login_required
def ignore(discovery_id: int):
    user = auth.get_current_user()
    with SessionLocal() as db:
        ok = discover_service.ignore_discovery(db, discovery_id, user.user_id)
        flash("Discovery ignored." if ok else "Could not ignore that discovery.", "success" if ok else "error")
    return redirect(url_for("discoveries.list_discoveries"))
