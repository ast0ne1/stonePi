from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import COMMON_TIMEZONES, env


def resolve_tz(name: str | None) -> ZoneInfo | timezone:
    raw = (name or "").strip() or "Australia/Melbourne"
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        try:
            return ZoneInfo("UTC")
        except ZoneInfoNotFoundError:
            return timezone.utc


def now_local(tz_name: str | None) -> datetime:
    return datetime.now(resolve_tz(tz_name))


def window_utc(tz_name: str | None, *, look_ahead_hours: int | None = None) -> tuple[str, str, datetime]:
    """Return (starts_from_iso, starts_to_iso, local_now) for the Now feed."""
    hours = look_ahead_hours if look_ahead_hours is not None else env.look_ahead_hours
    local = now_local(tz_name)
    # Include events that started up to 3h ago (still "on") through look-ahead.
    start = (local - timedelta(hours=3)).astimezone(timezone.utc)
    end = (local + timedelta(hours=hours)).astimezone(timezone.utc)
    return (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        local,
    )


def format_local(iso_utc: str, tz_name: str | None) -> str:
    try:
        dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        except ValueError:
            return iso_utc
    local = dt.astimezone(resolve_tz(tz_name))
    return local.strftime("%a %H:%M")


def needs_daily_refresh(last_refresh_iso: str | None, tz_name: str | None) -> bool:
    """True if we have never refreshed today (local calendar day) after the daily hour."""
    local = now_local(tz_name)
    if not last_refresh_iso:
        return True
    try:
        last = datetime.strptime(last_refresh_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    last_local = last.astimezone(resolve_tz(tz_name))
    boundary = local.replace(hour=env.daily_refresh_hour, minute=0, second=0, microsecond=0)
    if local >= boundary and last_local < boundary:
        return True
    if last_local.date() < local.date() and local.hour >= env.daily_refresh_hour:
        return True
    return False


def timezone_choices() -> list[str]:
    # Prefer curated list; append any others available is heavy — keep curated.
    return list(COMMON_TIMEZONES)


def normalize_sport(raw: str) -> str | None:
    t = (raw or "").strip().lower()
    if not t:
        return None
    if "soccer" in t or t == "football" or "football" in t:
        return "football"
    if "afl" in t or "aussie rules" in t or "australian football" in t:
        return "afl"
    if "cricket" in t:
        return "cricket"
    if "rugby" in t or "nrl" in t or "super league" in t:
        return "rugby"
    return None


def normalize_football_league(raw: str) -> str:
    t = (raw or "").strip()
    low = t.lower()
    mapping = (
        ("premier league", "Premier League"),
        ("la liga", "La Liga"),
        ("serie a", "Serie A"),
        ("bundesliga", "Bundesliga"),
        ("ligue 1", "Ligue 1"),
        ("champions league", "Champions League"),
        ("europa league", "Europa League"),
        ("conference league", "Conference League"),
        ("uefa champions", "Champions League"),
        ("uefa europa", "Europa League"),
        ("uefa conference", "Conference League"),
        ("uecl", "Conference League"),
        ("ucl", "Champions League"),
        ("uel", "Europa League"),
    )
    for needle, label in mapping:
        if needle in low:
            return label
    known = {
        "Premier League",
        "La Liga",
        "Serie A",
        "Bundesliga",
        "Ligue 1",
        "Champions League",
        "Europa League",
        "Conference League",
        "Other",
    }
    if t in known:
        return t
    # Club-name hints when competition string is missing
    clubs = (
        (("liverpool", "man city", "manchester city", "man united", "manchester united",
          "arsenal", "chelsea", "tottenham", "newcastle", "aston villa", "brighton",
          "west ham", "fulham", "brentford", "crystal palace", "everton", "nottingham",
          "bournemouth", "wolves", "wolverhampton", "leeds", "sunderland"), "Premier League"),
        (("real madrid", "barcelona", "atletico", "atlético", "atleti", "sevilla",
          "villarreal", "real sociedad", "athletic", "valencia", "getafe", "betis",
          "levante", "mallorca", "osasco", "girona", "celta"), "La Liga"),
        (("juventus", "inter", "milan", "napoli", "roma", "lazio", "atalanta",
          "fiorentina", "bologna", "torino", "frosinone", "como", "genoa", "parma",
          "lecce"), "Serie A"),
        (("bayern", "dortmund", "leverkusen", "leipzig", "frankfurt", "wolfsburg",
          "gladbach", "stuttgart", "hoffenheim", "union berlin", "schalke",
          "paderborn", "heidenheim"), "Bundesliga"),
        (("psg", "paris saint", "marseille", "lyon", "monaco", "lille", "nice",
          "rennes", "lens", "brest", "auxerre"), "Ligue 1"),
    )
    for names, label in clubs:
        if any(n in low for n in names):
            return label
    return "Other"
