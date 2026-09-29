"""Emit SportGuide watched-match events via StonePi Notify."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app import db
from app.teams import first_matching_entry, title_matches
from app.timeutil import parse_utc

logger = logging.getLogger("sportguide.notify")

DEFAULT_LEAD_MINUTES = 30
MIN_LEAD_MINUTES = 5
MAX_LEAD_MINUTES = 24 * 60


def emit_watched_match_approaching(
    *,
    user_key: str,
    title: str,
    summary: str,
    match_key: str,
    url: str | None = None,
    start_time: str | None = None,
) -> bool:
    """Personal alert to the person watching (their Auth user id is the pref key).

    The shared pre-platform list (``"local"``) still alerts the household until
    an admin opens SportGuide and it moves to them (``db.move_local_prefs_to``).
    """
    from stonepi_auth.alerts import auth_user_id

    owner = auth_user_id(user_key)
    if owner:
        audience = "personal"
    elif user_key == db.LOCAL_KEY:
        audience = "household"
    else:
        logger.debug("watched match for %r has no platform owner; not sent", user_key)
        return False
    try:
        from stonepi_contracts import EventEnvelope, emit_event

        return emit_event(
            EventEnvelope(
                id="sportguide.watched_match_approaching",
                source="sportguide",
                title=title[:200],
                summary=summary[:500],
                severity="warning",
                audience=audience,
                user=owner,
                dedupe_key=f"sportguide:approaching:{owner or user_key}:{match_key}:{start_time or ''}",
                url=url,
                data={"start_time": start_time, "match_key": match_key},
            )
        )
    except Exception:
        logger.debug("emit_watched_match_approaching failed", exc_info=True)
        return False


def listing_matches_watched(title: str, teams: list[str]) -> str | None:
    """First watched team (as given) found in the listing title, by normalised key."""
    for team in teams:
        if title_matches(title, team):
            return team
    return None


def check_all_watching(*, now: datetime | None = None) -> dict:
    """Run the approaching check for every person with teams to watch."""
    totals = {"people": 0, "checked": 0, "notified": 0, "skipped": 0}
    for user_key in db.watching_user_keys():
        result = check_approaching_watched(user_key=user_key, now=now)
        totals["people"] += 1
        for key in ("checked", "notified", "skipped"):
            totals[key] += int(result.get(key) or 0)
    return totals


def check_approaching_watched(
    *,
    user_key: str = "local",
    now: datetime | None = None,
) -> dict:
    """Notify for watched-team matches starting within the lead window."""
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    entries = db.get_watched_entries(user_key)
    teams = [e["name"] for e in entries]
    if not teams:
        return {"checked": 0, "notified": 0, "skipped": 0, "lead_minutes": 0, "teams": 0}

    lead = db.approaching_lead_minutes(user_key)
    horizon = now_utc + timedelta(minutes=lead)
    starts_from = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    starts_to = horizon.strftime("%Y-%m-%dT%H:%M:%SZ")
    listings = db.query_listings(starts_from=starts_from, starts_to=starts_to, limit=500)

    sent = db.get_approaching_sent(user_key)
    matched: list[dict] = []
    for row in listings:
        entry = first_matching_entry(str(row.get("title") or ""), str(row.get("sport") or ""), entries)
        if not entry:
            continue
        team = entry["name"]
        starts = parse_utc(str(row.get("starts_at") or ""))
        if starts is None or starts < now_utc or starts > horizon:
            continue
        matched.append({**row, "_watched_team": team, "_starts": starts})

    active_keys = {
        f"{r.get('source_id')}:{r.get('external_id')}" for r in matched
    }
    pruned = {k: v for k, v in sent.items() if k in active_keys}
    notified = 0
    skipped = 0
    for row in matched:
        starts: datetime = row["_starts"]
        start_iso = starts.isoformat()
        key = f"{row.get('source_id')}:{row.get('external_id')}"
        if pruned.get(key) == start_iso:
            skipped += 1
            continue
        minutes = max(0, int((starts - now_utc).total_seconds() // 60))
        league = (row.get("league") or "").strip()
        summary = f"Starts in {minutes} min"
        if league:
            summary = f"{summary} · {league}"
        channels = row.get("channels") or []
        if isinstance(channels, list) and channels:
            summary = f"{summary} · {', '.join(str(c) for c in channels[:2] if c)}"
        ok = emit_watched_match_approaching(
            user_key=user_key,
            title=str(row.get("title") or "Match"),
            summary=summary,
            match_key=key,
            url=(row.get("source_url") or "").strip() or None,
            start_time=start_iso,
        )
        if ok:
            pruned[key] = start_iso
            notified += 1
    if pruned != sent:
        db.set_approaching_sent(user_key, pruned)
    return {
        "checked": len(matched),
        "notified": notified,
        "skipped": skipped,
        "lead_minutes": lead,
        "teams": len(teams),
    }
