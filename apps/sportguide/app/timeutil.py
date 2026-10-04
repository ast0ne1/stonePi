from __future__ import annotations

import re
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


def parse_utc(iso_utc: str) -> datetime | None:
    try:
        return datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        except ValueError:
            return None


def enrich_listing_row(row: dict, tz_name: str | None, *, now: datetime | None = None) -> dict:
    """Add day/time/live fields for the Now feed card layout."""
    tz = resolve_tz(tz_name)
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    starts = parse_utc(str(row.get("starts_at") or ""))
    if starts is None:
        row["day_label"] = ""
        row["time_label"] = row.get("local_time") or ""
        row["is_live"] = False
    else:
        local = starts.astimezone(tz)
        row["day_label"] = local.strftime("%a").upper()
        row["time_label"] = local.strftime("%H:%M")
        # Still “on” for up to 3 hours after kick-off (matches Now window).
        row["is_live"] = starts <= now_utc and starts >= (now_utc - timedelta(hours=3))
    channels = row.get("channels") or []
    if isinstance(channels, list) and channels:
        row["channel_label"] = " · ".join(str(c) for c in channels if c)
    else:
        row["channel_label"] = "Check guide"
    return row


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


def normalize_football_league(raw: str, *, club_hints: bool = True) -> str:
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
    if not club_hints:
        return "Other"
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


_RUGBY_NATIONS = (
    "australia", "new zealand", "south africa", "argentina", "england", "ireland",
    "scotland", "wales", "france", "italy", "fiji", "samoa", "tonga", "japan",
    "georgia", "portugal", "uruguay", "chile", "canada", "usa", "png", "papua new guinea",
    "cook islands", "lebanon", "british and irish lions", "lions", "wallabies",
    "all blacks", "springboks", "pumas", "kangaroos", "kiwis", "jillaroos", "kiwi ferns",
    "wallaroos", "black ferns",
)
_RUGBY_INTL_HINTS = (
    "test", "rugby championship", "bledisloe", "world cup", "pacific championship",
    "pacific cup", "nations championship", "six nations",
)


def _is_rugby_nation(side: str) -> bool:
    side = re.sub(r"\b(w|women|u\d{2})\b", "", side.lower()).strip(" -")
    return side in _RUGBY_NATIONS


def normalize_rugby_league(title: str) -> str:
    """Rugby title → one of RUGBY_LEAGUES. Title only: section blobs mix competitions."""
    low = (title or "").lower()
    if "super rugby" in low:
        return "Super Rugby"
    if any(h in low for h in _RUGBY_INTL_HINTS):
        return "Internationals"
    body = re.sub(r"\s*\brugby (league|union)\b\s*$", "", low).strip()
    sides = re.split(r"\s+(?:-|vs\.?|v)\s+", body, maxsplit=1)
    if len(sides) == 2 and all(_is_rugby_nation(s) for s in sides):
        return "Internationals"
    is_league = "rugby league" in low or "state of origin" in low or re.search(r"\bnrlw?\b", low)
    if is_league and "super league" not in low:
        women = re.search(r"\bnrlw\b", low) or re.search(r"\bw\s+(?:-|vs\.?|v)\s+|\bw$|\bw\s+rugby league", low)
        return "NRLW" if women else "NRL"
    return "Other"
