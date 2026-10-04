from __future__ import annotations

import os
from collections import OrderedDict
from datetime import timezone
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __asset_rev__, __github__, __github_user__, __version__, db
from app.config import COMMON_TIMEZONES, ROOT_DIR, SPORT_LEAGUES, SPORTS, env
from app.services import ingest
from app.timeutil import enrich_listing_row, format_local, timezone_choices, window_utc

from stonepi_auth.brand import fonts_rev
from stonepi_auth import login_url, logout_url
from stonepi_auth.alerts import add_shared_templates, bell_context, notifications_card_context
from stonepi_auth.config import PlatformSettings
from stonepi_auth.csrf import CSRF_COOKIE, csrf_from_request, csrf_ok, set_csrf_cookie
from stonepi_auth.http import portal_home_url, request_is_https
from stonepi_auth.session import COOKIE_NAME, decode_session

templates = Jinja2Templates(directory=str(ROOT_DIR / "app" / "templates"))
templates.env.globals.update(asset_rev=__asset_rev__, fonts_rev=fonts_rev())
add_shared_templates(templates.env)
router = APIRouter()


def _session_secret() -> str:
    try:
        from stonepi_vault import get_secret

        vaulted = get_secret("STONEPI_SESSION_SECRET", env_name="STONEPI_SESSION_SECRET", default="")
        if vaulted.strip():
            return vaulted.strip()
    except Exception:
        pass
    return env.session_secret.strip()


def _auth_optional() -> bool:
    """Running without a session secret is allowed only in dev (Windows run-dev or STONEPI_DEV=1).

    On a Pi a missing/unreadable secret must refuse privileged actions (refresh)
    rather than open them to everyone; ``app.main`` locks the whole app then.
    """
    return os.name == "nt" or (os.environ.get("STONEPI_DEV") or "").strip() == "1"


def _prefix() -> str:
    return (env.stonepi_prefix or "").rstrip("/")


def _settings() -> PlatformSettings:
    from stonepi_auth.http import browser_auth_url

    pfx = _prefix()
    return PlatformSettings(
        enabled=bool(_session_secret()),
        session_secret=_session_secret(),
        app_id="sportguide",
        prefix=pfx,
        auth_url=browser_auth_url(env.auth_url, routing=env.routing),
        public_origin=env.public_origin,
        hostname=env.hostname,
    )


def _user(request: Request):
    return decode_session(request.cookies.get(COOKIE_NAME), _session_secret())


def _require_user(request: Request, *, capability: str | None = None):
    user = _user(request)
    secret = _session_secret()
    if secret and user is None:
        return None, RedirectResponse(login_url(_settings(), f"{_prefix()}/"), status_code=303)
    if user and not user.is_admin and not user.can_access("sportguide"):
        return None, HTMLResponse("No access to SportGuide.", status_code=403)
    if capability and user and not user.is_admin and not user.has_capability("sportguide", capability):
        return None, HTMLResponse(f"Missing capability: {capability}", status_code=403)
    if secret and user and user.is_admin:
        # First admin under StonePi sign-in inherits the shared pre-platform prefs.
        db.move_local_prefs_to(_user_key(user))
    return user, None


def _user_key(user) -> str:
    if user is None:
        return "local"
    return str(getattr(user, "user_id", None) or getattr(user, "username", None) or "local")


def _can_refresh(user) -> bool:
    if not _session_secret():
        return _auth_optional()
    return bool(user and (user.is_admin or user.has_capability("sportguide", "can_refresh")))


def _csrf_response(request: Request, name: str, ctx: dict) -> HTMLResponse:
    csrf = csrf_from_request(request.cookies)
    ctx["csrf_token"] = csrf
    ctx.setdefault("public_origin", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("stonepi_home_url", portal_home_url(request, env.public_origin).rstrip("/"))
    ctx.setdefault("app_prefix", _prefix())
    ctx.setdefault("app_name", "SportGuide")
    ctx.setdefault("app_version", __version__)
    ctx.setdefault("asset_rev", __asset_rev__)
    ctx.setdefault("app_github", __github__)
    ctx.setdefault("app_github_user", __github_user__)
    ctx.setdefault("sso", bool(_session_secret()))
    ctx.setdefault("format_local", format_local)
    ctx.setdefault("refresh", ingest.refresh_status())
    from stonepi_auth.session import factory_admin_warning

    ctx.setdefault("using_factory_admin", factory_admin_warning(ctx.get("user")))
    if "can_refresh" not in ctx and "user" in ctx:
        ctx["can_refresh"] = _can_refresh(ctx.get("user"))
    ctx.setdefault(
        "alerts_bell_state",
        bell_context(
            ctx.get("user"),
            session_cookie=request.cookies.get(COOKIE_NAME),
            home_url=ctx["stonepi_home_url"],
            enabled=bool(_session_secret()),
        ),
    )
    response = templates.TemplateResponse(request, name, ctx)
    set_csrf_cookie(response, csrf, secure=request_is_https(request))
    return response


def _redirect(path: str, *, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    pfx = _prefix()
    url = f"{pfx}{path}" if path.startswith("/") else f"{pfx}/{path}"
    parts = []
    if msg:
        parts.append(f"msg={quote(msg)}")
    if error:
        parts.append(f"error={quote(error)}")
    if parts:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{'&'.join(parts)}"
    return RedirectResponse(url, status_code=303)


def _group_listings(listings: list[dict], *, sport: str, league: str = "all") -> list[dict]:
    """Group for the feed: All → sport then league; league sports → league; single league → flat."""
    if not listings:
        return []
    def _meta(sid: str, label: str | None = None) -> dict:
        sport_meta = next((x for x in SPORTS if x["id"] == sid), None)
        return {
            "key": sid,
            "label": label or (sport_meta["label"] if sport_meta else sid.title()),
            "image": sport_meta["image"] if sport_meta else None,
        }

    if sport == "all":
        by_sport: OrderedDict[str, list] = OrderedDict()
        order = ("afl", "cricket", "rugby", "football")
        for s in order:
            by_sport[s] = []
        for row in listings:
            by_sport.setdefault(row["sport"], []).append(row)
        sections = []
        for sid, rows in by_sport.items():
            if not rows:
                continue
            meta = _meta(sid)
            if sid in SPORT_LEAGUES:
                subsections = _group_by_league(rows, SPORT_LEAGUES[sid])
                sections.append({**meta, "subsections": subsections, "rows": []})
            else:
                sections.append({**meta, "subsections": [], "rows": rows})
        return sections
    if sport in SPORT_LEAGUES:
        meta = _meta(sport)
        if league and league != "all":
            return [{**meta, "label": league, "subsections": [], "rows": listings}]
        return [{**meta, "subsections": _group_by_league(listings, SPORT_LEAGUES[sport]), "rows": []}]
    meta = _meta(sport)
    return [{**meta, "subsections": [], "rows": listings}]


def _group_by_league(rows: list[dict], leagues: tuple[str, ...]) -> list[dict]:
    buckets: OrderedDict[str, list] = OrderedDict()
    for name in leagues:
        buckets[name] = []
    for row in rows:
        league = row.get("league") or "Other"
        if league not in buckets:
            league = "Other"
        buckets[league].append(row)
    return [{"key": k, "label": k, "rows": v} for k, v in buckets.items() if v]


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "sportguide"}


def _panel_feed(tz: str, start: str, end: str, watched_entries: list, limit: int) -> dict:
    """Car Thing panel extras for ``/api/display?items=N``: watched teams first, then by start."""
    from app.teams import first_matching_entry

    rows = []
    for row in db.query_listings(starts_from=start, starts_to=end, limit=200):
        enrich_listing_row(row, tz)
        title, sport = str(row.get("title") or ""), str(row.get("sport") or "")
        watched = bool(watched_entries) and first_matching_entry(title, sport, watched_entries) is not None
        rows.append((not watched, str(row.get("starts_at") or ""), row, watched))
    rows.sort(key=lambda r: (r[0], r[1]))
    items = []
    for _, _, row, watched in rows[: max(1, min(50, limit))]:
        when = f"{str(row.get('day_label') or '').title()} {row.get('time_label') or ''}".strip()
        league = str(row.get("league") or row.get("sport") or "")
        items.append(
            {
                "id": str(row.get("id")),
                "title": str(row.get("title") or "")[:90],
                "sub": " · ".join(p for p in (when, league) if p),
                "badge": "On now" if row.get("is_live") else ("Yours" if watched else ""),
                "detail": "\n".join(
                    p
                    for p in (
                        " · ".join(x for x in (str(row.get("sport") or ""), league) if x),
                        when,
                        f"Watch on: {row.get('channel_label') or 'Check guide'}",
                    )
                    if p
                ),
            }
        )
    lead = items[0] if items else None
    on_now = sum(1 for i in items if i["badge"] == "On now")
    card = {
        "headline": lead["title"] if lead else "Nothing scheduled",
        "sub": (lead["sub"] if lead else "") + (f" · {on_now} on now" if on_now else ""),
        "badge": lead["badge"] if lead else "",
    }
    return {"card": card, "items": items, "refresh_s": 120}


@router.get("/api/display")
def api_display(items: int = 0):
    # Household display: household timezone, everyone's teams.
    tz = db.household_timezone()
    start, end, _ = window_utc(tz)
    n = db.count_on_now(start, end)
    teams = db.all_watched_teams()
    watched_entries = db.all_watched_entries()
    next_watched = None
    if watched_entries:
        from app.teams import first_matching_entry
        from app.timeutil import parse_utc

        upcoming = db.query_listings(starts_from=start, starts_to=end, limit=200)
        for row in upcoming:
            if first_matching_entry(str(row.get("title") or ""), str(row.get("sport") or ""), watched_entries):
                starts = parse_utc(str(row.get("starts_at") or ""))
                if starts is None:
                    continue
                next_watched = {
                    "title": row.get("title"),
                    "starts_at": row.get("starts_at"),
                    "league": row.get("league") or "",
                    "local_time": format_local(str(row.get("starts_at") or ""), tz),
                }
                break
    payload = {
        "ok": True,
        "on_now": n,
        "detail": f"{n} on now" if n else "—",
        "watched_teams": teams,
        "next_watched": next_watched,
    }
    if items > 0:  # Car Thing panel lists; the plain call (Dashboard tile, TRMNL) is unchanged
        payload.update(_panel_feed(tz, start, end, watched_entries, items))
    return JSONResponse(payload)


@router.get("/", response_class=HTMLResponse)
def now_page(
    request: Request,
    sport: str = "all",
    league: str = "all",
    msg: str | None = None,
    error: str | None = None,
):
    user, denied = _require_user(request)
    if denied:
        return denied
    uk = _user_key(user)
    tz = db.user_timezone(uk)
    city = db.get_pref(uk, "city", "")
    sport = (sport or "all").lower()
    if sport not in {s["id"] for s in SPORTS}:
        sport = "all"
    league = league or "all"
    sport_leagues = SPORT_LEAGUES.get(sport, ())
    if league not in sport_leagues:
        league = "all"
    start, end, local_now = window_utc(tz)
    listings = db.query_listings(
        sport=None if sport == "all" else sport,
        league=None if league == "all" else league,
        starts_from=start,
        starts_to=end,
        limit=250,
    )
    now_utc = local_now.astimezone(timezone.utc)
    for row in listings:
        row["local_time"] = format_local(row["starts_at"], tz)
        enrich_listing_row(row, tz, now=now_utc)
    groups = _group_listings(listings, sport=sport, league=league)
    can_refresh = _can_refresh(user)
    return _csrf_response(
        request,
        "now.html",
        {
            "user": user,
            "active": "now",
            "sports": SPORTS,
            "sport": sport,
            "league": league,
            "leagues": sport_leagues,
            "groups": groups,
            "listings": listings,
            "timezone": tz,
            "city": city,
            "local_now": local_now.strftime("%a %H:%M"),
            "look_ahead_hours": env.look_ahead_hours,
            "message": msg,
            "error": error,
            "refresh": ingest.refresh_status(),
            "has_data": db.listing_count() > 0,
            "can_refresh": can_refresh,
        },
    )


@router.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, msg: str | None = None, error: str | None = None):
    user, denied = _require_user(request)
    if denied:
        return denied
    can_refresh = _can_refresh(user)
    from app.services import favicon

    sources = db.list_sources()
    icons = favicon.map_for_sources([s["id"] for s in sources])
    for s in sources:
        s["favicon"] = icons.get(s["id"])
    return _csrf_response(
        request,
        "sources.html",
        {
            "user": user,
            "active": "sources",
            "sources": sources,
            "refresh": ingest.refresh_status(),
            "message": msg,
            "error": error,
            "can_refresh": can_refresh,
        },
    )


@router.post("/sources/refresh")
async def sources_refresh(request: Request, csrf_token: str = Form(""), next: str = Form("sources")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _can_refresh(user):
        return HTMLResponse("Missing capability: can_refresh", status_code=403)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        dest = "/" if next == "now" else "/sources"
        return _redirect(dest, error="Invalid session token")
    ingest.refresh_async()
    if next == "now":
        return _redirect("/", msg="Refresh started")
    return _redirect("/sources", msg="Refresh started")


@router.post("/sources/{source_id}/refresh")
async def source_refresh_one(request: Request, source_id: str, csrf_token: str = Form("")):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not _can_refresh(user):
        return HTMLResponse("Missing capability: can_refresh", status_code=403)
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/sources", error="Invalid session token")
    if source_id not in ingest.COLLECTORS:
        return _redirect("/sources", error="Unknown source")
    ingest.refresh_one_async(source_id)
    return _redirect("/sources", msg=f"Refreshing {source_id}…")


# Tabs with drill-in sections on phones. Notifications is one panel: the shared
# alerts card with the lead-time form below it.
_SG_SECTIONS: dict[str, tuple[tuple[str, str, str, str], ...]] = {}


def _sportguide_settings_view(tab: str | None, panel: str | None) -> tuple[bool, str, str, str]:
    raw = (tab or "").strip().lower()
    allowed = {"general", "watch", "notifications", "about"}
    if not raw:
        return True, "general", "hub", ""
    key = raw if raw in allowed else "general"
    sections = _SG_SECTIONS.get(key, ())
    ids = {item[0] for item in sections}
    chosen = (panel or "").strip().lower()
    if sections and chosen not in ids:
        return False, key, "list", ""
    return False, key, "panel", chosen if chosen in ids else ""


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    tab: str | None = None,
    panel: str | None = None,
    msg: str | None = None,
    error: str | None = None,
):
    user, denied = _require_user(request)
    if denied:
        return denied
    uk = _user_key(user)
    settings_hub, tab, settings_level, settings_panel = _sportguide_settings_view(tab, panel)
    ledes = {
        "general": "City and timezone for SportGuide times.",
        "watch": "Your teams and nations — used for your approaching alerts and the household display.",
        "notifications": "Alerts when one of your teams is about to kick off.",
        "about": "App name, description, GitHub, and the version running here.",
    }
    tab_titles = {
        "general": "General",
        "watch": "Teams to Watch",
        "notifications": "Notifications",
        "about": "About",
    }
    panel_labels = {item[0]: item[1] for item in _SG_SECTIONS.get(tab, ())}
    if settings_hub:
        title, lede = "Settings", "City, teams to watch, and approaching alerts."
    elif settings_level == "list":
        title, lede = tab_titles[tab], "Choose a section."
    else:
        title = panel_labels.get(settings_panel) or tab_titles[tab]
        lede = ledes.get(tab, "")
    home = portal_home_url(request, env.public_origin).rstrip("/")
    return _csrf_response(
        request,
        "settings.html",
        {
            "user": user,
            "active": "settings",
            "settings_tab": tab,
            "settings_hub": settings_hub,
            "settings_level": settings_level,
            "settings_panel": settings_panel,
            "settings_title": title,
            "settings_sections": _SG_SECTIONS.get(tab, ()),
            "settings_groups": (
                ("guide", "Guide", (
                    ("general", "General", "City and timezone.", "general"),
                    ("watch", "Teams to Watch", "Your teams and nations.", "star"),
                )),
                ("alerts", "Alerts", (
                    ("notifications", "Notifications", "Phone alerts and lead time.", "bell"),
                )),
                ("app", "App", (
                    ("about", "About", "Version and project links.", "about"),
                )),
            ),
            "city": db.get_pref(uk, "city", ""),
            "timezone": db.user_timezone(uk),
            "timezones": timezone_choices(),
            **_watch_context(uk),
            "notify_approaching_minutes": db.approaching_lead_minutes(uk),
            "notifications_card_state": notifications_card_context(
                "sportguide",
                user if _session_secret() else None,
                home_url=home,
            ),
            "message": msg,
            "error": error,
            "settings_lede": lede,
            "stonepi_home_url": home,
        },
    )


@router.post("/settings/general")
async def settings_general(
    request: Request,
    city: str = Form(""),
    timezone: str = Form("Australia/Melbourne"),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/settings?tab=general", error="Invalid session token")
    uk = _user_key(user)
    db.set_pref(uk, "city", city.strip())
    tz = timezone.strip()
    if tz not in COMMON_TIMEZONES:
        tz = "Australia/Melbourne"
    db.set_pref(uk, "timezone", tz)
    # Admins (or standalone) also set the household timezone used by the
    # display widget and as the default for people who haven't picked one.
    if not _session_secret() or (user and user.is_admin):
        db.set_pref(db.LOCAL_KEY, "timezone", tz)
    return _redirect("/settings?tab=general", msg="Saved")


def _watch_context(uk: str) -> dict:
    """Teams to Watch panel: the person's teams, and the picker (standard teams + listings)."""
    from app import teams as team_names

    rows = db.listing_titles()
    groups = team_names.picker_groups(rows, SPORTS)
    labels = {s["id"]: s["label"] for s in SPORTS}
    entries = [dict(e) for e in db.get_watched_entries(uk)]
    watched = {db.watched_id(e) for e in entries} | {e["key"] for e in entries if not e.get("sport")}
    for entry in entries:
        entry["id"] = db.watched_id(entry)
        entry["sport_label"] = labels.get(str(entry.get("sport") or ""), "")
        entry["in_listings"] = any(
            team_names.entry_matches(str(r.get("title") or ""), str(r.get("sport") or ""), entry) for r in rows
        )
    picker = [
        {**g, "teams": [t for t in g["teams"] if f"{g['sport']}:{t['key']}" not in watched and t["key"] not in watched]}
        for g in groups
    ]
    return {
        "watched_teams": [e["name"] for e in entries],
        "watched_entries": entries,
        "team_groups": [g for g in picker if g["teams"]],
        "has_listed_teams": any(t["listed"] for g in groups for t in g["teams"]),
    }


@router.post("/settings/watch/add")
def settings_watch_add(
    request: Request,
    team: str = Form(""),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/settings?tab=watch", error="Invalid session token")
    from app import teams as team_names

    picked, reason = team_names.resolve_pick(team, team_names.picker_groups(db.listing_titles(), SPORTS))
    if not picked:
        return _redirect("/settings?tab=watch", error=reason)
    name = picked["name"]
    if not db.add_watched_team(_user_key(user), picked["key"], name, picked["sport"]):
        return _redirect("/settings?tab=watch", msg=f"Already watching {name}.")
    return _redirect("/settings?tab=watch", msg=f"Watching {name} ({picked['label']}).")


@router.post("/settings/watch/remove")
def settings_watch_remove(
    request: Request,
    team: str = Form(""),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/settings?tab=watch", error="Invalid session token")
    uk = _user_key(user)
    ident = str(team or "").strip()
    entries = db.get_watched_entries(uk)
    name = next((e["name"] for e in entries if db.watched_id(e) == ident), "") or next(
        (e["name"] for e in entries if e["key"] == ident), ""
    )
    if not db.remove_watched_team(uk, ident):
        return _redirect("/settings?tab=watch", error="That team isn't on your list.")
    return _redirect("/settings?tab=watch", msg=f"Stopped watching {name}.")


@router.post("/settings/notifications")
async def settings_notifications(
    request: Request,
    notify_approaching_minutes: str = Form("30"),
    csrf_token: str = Form(""),
):
    user, denied = _require_user(request)
    if denied:
        return denied
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/settings?tab=notifications", error="Invalid session token")
    try:
        minutes = int(str(notify_approaching_minutes or "30").strip())
    except (TypeError, ValueError):
        minutes = db.DEFAULT_APPROACHING_MINUTES
    saved = db.set_approaching_lead_minutes(_user_key(user), minutes)
    return _redirect(
        "/settings?tab=notifications",
        msg=f"Approaching alerts set to {saved} minutes before kick-off.",
    )


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form("")):
    if not csrf_ok(request.cookies.get(CSRF_COOKIE), csrf_token):
        return _redirect("/", error="Invalid session token")
    response = RedirectResponse(logout_url(_settings()), status_code=303)
    return response
