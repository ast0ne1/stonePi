"""Agent reads for AgentLink (Pebble now, MCP later): one signed internal endpoint.

    POST /api/internal/agent   {"tool": "...", "user": "<platform user id>", "params": {...}}
    → {"ok": true, "payload": {...}} | {"ok": false, "error": "...", "message": "..."}

Signed with stonepi_auth.internal v2 (the body, so the user, is covered); nginx never
routes /sports/api/internal/. Times are in the person's own SportGuide timezone, and their
Teams to Watch come first. Shaped for speech: a few matches, with counts for the rest.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import db
from app.config import SPORT_LEAGUES, SPORTS
from app.teams import entry_matches, first_matching_entry
from app.timeutil import enrich_listing_row, normalize_sport, now_local, parse_utc, resolve_tz

router = APIRouter()

MAX_ITEMS = 8
WHENS = ("now", "today", "tonight", "tomorrow", "this_weekend")
TONIGHT_FROM = time(17, 0)
SPORT_LABELS = {s["id"]: s["label"] for s in SPORTS if s["id"] != "all"}


def _session_secret() -> str:
    from app.routes import _session_secret as secret

    return secret()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window(when: str, local: datetime) -> tuple[datetime, datetime]:
    """(from, to) local times for ``when``; matches started up to 3 h ago still count as on."""
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    started = local - timedelta(hours=3)
    if when == "today":
        return started, midnight + timedelta(days=1)
    if when == "tonight":
        return max(started, midnight.replace(hour=TONIGHT_FROM.hour)), midnight + timedelta(days=1, hours=2)
    if when == "tomorrow":
        return midnight + timedelta(days=1), midnight + timedelta(days=2)
    if when == "this_weekend":
        # On a Sunday "this weekend" is the one we're in, so it began yesterday.
        saturday = midnight - timedelta(days=1) if local.weekday() == 6 else midnight + timedelta(days=5 - local.weekday())
        return max(started, saturday), saturday + timedelta(days=2)
    return started, local + timedelta(hours=24)


def _sport_or_league(text: str) -> tuple[str | None, str | None]:
    """'football' → (football, None); 'premier league' / 'nrl' → (its sport, the league)."""
    low = text.strip().lower()
    for sport, leagues in SPORT_LEAGUES.items():
        for league in leagues:
            if league != "Other" and league.lower() == low:
                return sport, league
    return normalize_sport(low), None


def _item(row: dict, tz: str, entries: list[dict]) -> dict:
    starts = parse_utc(str(row.get("starts_at") or ""))
    local = starts.astimezone(resolve_tz(tz)) if starts else None
    team = first_matching_entry(str(row.get("title") or ""), str(row.get("sport") or ""), entries) if entries else None
    return {
        "title": str(row.get("title") or "")[:90],
        "sport": SPORT_LABELS.get(str(row.get("sport") or ""), str(row.get("sport") or "")),
        "league": str(row.get("league") or ""),
        "day": local.strftime("%A") if local else "",
        "time": local.strftime("%H:%M") if local else "",
        "start": local.isoformat(timespec="minutes") if local else "",
        "on_now": bool(row.get("is_live")),
        "channels": [str(c) for c in row.get("channels") or [] if c][:4],
        "your_team": team["name"] if team else "",
    }


def _get_sport(user_id: str, params: dict) -> dict:
    tz = db.user_timezone(user_id)
    local = now_local(tz)
    when = str(params.get("when") or "").strip().lower().replace(" ", "_") or "now"
    if when not in WHENS:
        when = "now"
    start, end = _window(when, local)
    wanted = str(params.get("sport") or "").strip()
    sport, league = _sport_or_league(wanted) if wanted else (None, None)
    rows = db.query_listings(starts_from=_iso(start), starts_to=_iso(end), limit=400)
    counts: dict[str, int] = {}
    for row in rows:
        label = SPORT_LABELS.get(str(row.get("sport") or ""), str(row.get("sport") or ""))
        counts[label] = counts.get(label, 0) + 1
    out: dict = {"when": when, "timezone": tz, "sports": counts}
    if wanted and sport is None:
        return {**out, "total": 0, "listings": [], "sport_not_found": wanted}
    if sport:
        rows = [r for r in rows if r.get("sport") == sport and (not league or r.get("league") == league)]
        out["sport"] = league or SPORT_LABELS.get(sport, sport)
    entries = db.get_watched_entries(user_id)
    items = [_item(enrich_listing_row(row, tz), tz, entries) for row in rows]
    items.sort(key=lambda i: (not i["your_team"], not i["on_now"], i["start"]))
    return {**out, "total": len(items), "listings": items[:MAX_ITEMS]}


def _get_my_teams(user_id: str, _params: dict) -> dict:
    entries = db.get_watched_entries(user_id)
    if not entries:
        return {"teams": [], "note": "No Teams to Watch picked in SportGuide yet"}
    tz = db.user_timezone(user_id)
    start = now_local(tz) - timedelta(hours=3)
    rows = [enrich_listing_row(row, tz) for row in db.query_listings(starts_from=_iso(start), limit=1000)]
    teams = []
    for entry in entries:
        match = next((r for r in rows if entry_matches(str(r.get("title") or ""), str(r.get("sport") or ""), entry)), None)
        teams.append({
            "team": entry["name"],
            "sport": SPORT_LABELS.get(str(entry.get("sport") or ""), ""),
            "next": _item(match, tz, [entry]) if match else None,
        })
    return {"timezone": tz, "teams": teams}


TOOLS = {"get_sport": _get_sport, "get_my_teams": _get_my_teams}


@router.post("/api/internal/agent")
async def agent_read(request: Request):
    from stonepi_auth.internal import verify_internal_v2

    body = await request.body()
    if not verify_internal_v2(_session_secret(), "POST", request.url.path, request.headers, body):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad_request", "message": "Body must be JSON"}, status_code=400)
    tool = TOOLS.get(str(data.get("tool") or ""))
    user_id = str(data.get("user") or "").strip()
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    if tool is None or not user_id:
        return JSONResponse({"ok": False, "error": "bad_request", "message": "Unknown tool or no user"}, status_code=400)
    return {"ok": True, "payload": tool(user_id, params)}
