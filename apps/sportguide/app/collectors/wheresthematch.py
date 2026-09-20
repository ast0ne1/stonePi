from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.collectors.base import ListingRow
from app.timeutil import normalize_football_league

logger = logging.getLogger("sportguide.wheresthematch")

SOURCE_ID = "wheresthematch"
WTM_URL = "https://www.wheresthematch.com/?sportid=1"
TZ_FOOTBALL_URL = "https://timezone.football/football-on-tv-this-week/"
USER_AGENT = "StonePi-SportGuide/0.0.1 (household LAN; +https://github.com/ast0ne1)"

_CHANNEL_NAMES = (
    "Sky Sports",
    "Sky Ultra HD",
    "Sky Go",
    "TNT Sports",
    "BBC",
    "BBC iPlayer",
    "BBC Two",
    "ITV",
    "ITVX",
    "Amazon Prime Video",
    "Amazon Prime",
    "Amazon",
    "DAZN",
    "Premier Sports",
    "Eurosport",
    "Channel 4",
    "Discovery+",
    "NOW",
    "S4C",
    "Ligue 1+",
    "Ziggo Sport",
    "Sony LIV",
)

_WTM_KICKOFF = re.compile(
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})(?:st|nd|rd|th)?\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})\s+(\d{1,2}):(\d{2})",
    re.I,
)
_DAY_HEAD = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)",
    re.I,
)
_TIME_LINE = re.compile(r"^(\d{1,2}):(\d{2})$")
_SKIP_LINES = frozenset(
    {
        "time",
        "match",
        "where to watch",
        "where to watch in united kingdom",
        "channels in",
        "v",
        "vs",
        "vs.",
        "☆",
        "vpn",
        "★",
    }
)


def _browser():
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def _london() -> ZoneInfo:
    try:
        return ZoneInfo("Europe/London")
    except Exception:
        return timezone(timedelta(hours=0))  # type: ignore[return-value]


def _external_id(title: str, starts_at: str, league: str) -> str:
    raw = f"football|{league}|{title}|{starts_at}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _extract_channels(text: str) -> list[str]:
    found: list[str] = []
    low = text.lower()
    for ch in _CHANNEL_NAMES:
        if ch.lower() in low and ch not in found:
            found.append(ch)
    return found[:6]


def _parse_wtm_kickoff(text: str) -> str | None:
    text = " ".join((text or "").split())
    m = _WTM_KICKOFF.search(text)
    if m:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        local = datetime(
            int(m.group(4)),
            months[m.group(3).lower()[:3]],
            int(m.group(2)),
            int(m.group(5)),
            int(m.group(6)),
            tzinfo=_london(),
        )
        return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _league_from_text(*parts: str) -> str:
    blob = " ".join(p for p in parts if p)
    for part in parts:
        cand = normalize_football_league(part or "")
        if cand in {
            "Premier League",
            "La Liga",
            "Serie A",
            "Bundesliga",
            "Ligue 1",
            "Champions League",
            "Europa League",
            "Conference League",
        }:
            return cand
    return normalize_football_league(blob) or "Other"


def _from_wtm(page) -> list[ListingRow]:
    page.goto(WTM_URL, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)
    payload = page.evaluate(
        """() => {
          const out = [];
          document.querySelectorAll('table tr').forEach((tr) => {
            const cells = Array.from(tr.querySelectorAll('td, th')).map(c => (c.innerText || '').trim());
            const text = (tr.innerText || '').trim();
            if (text.length > 20 && cells.length >= 2) out.push({ cells, text });
          });
          if (out.length < 5) {
            document.querySelectorAll('li, article, .fixture, .match, a').forEach((node) => {
              const text = (node.innerText || '').trim();
              if (text.length > 20 && text.length < 500 && /\\d{1,2}:\\d{2}/.test(text)) {
                out.push({ cells: text.split('\\n').map(s => s.trim()).filter(Boolean), text });
              }
            });
          }
          return out.slice(0, 500);
        }"""
    )
    rows: list[ListingRow] = []
    seen: set[str] = set()
    for item in payload or []:
        text = item.get("text") or ""
        cells = item.get("cells") or []
        low = text.lower()
        if " v " not in low and " vs " not in low:
            continue
        title = cells[0] if cells else text.split("\n")[0]
        title = re.sub(r"\s+", " ", title).strip()[:180]
        if len(title) < 6:
            continue
        league = _league_from_text(text, *cells)
        starts = _parse_wtm_kickoff(text)
        if not starts:
            continue
        eid = _external_id(title, starts, league)
        if eid in seen:
            continue
        seen.add(eid)
        rows.append(
            ListingRow(
                external_id=eid,
                source_id=SOURCE_ID,
                sport="football",
                league=league,
                title=title,
                starts_at=starts,
                channels=_extract_channels(text),
                source_url=WTM_URL,
            )
        )
    return rows


def _month_num(name: str) -> int:
    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    }
    return months[(name or "").lower()]


def _from_timezone_football(page) -> list[ListingRow]:
    """Fallback Euro Football TV guide when WheresTheMatch HTML is hostile."""
    page.goto(TZ_FOOTBALL_URL, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2000)
    body = page.evaluate("() => (document.body && document.body.innerText) || ''") or ""
    rows: list[ListingRow] = []
    seen: set[str] = set()
    now = datetime.now(_london())
    year = now.year
    current_date: datetime | None = None
    lines = [ln.strip() for ln in body.splitlines()]

    i = 0
    while i < len(lines):
        line = lines[i]
        dm = _DAY_HEAD.match(line)
        if dm:
            month = _month_num(dm.group(3))
            day = int(dm.group(2))
            y = year
            # Roll year if calendar wraps past Dec→Jan
            if month < now.month - 6:
                y = year + 1
            current_date = datetime(y, month, day, tzinfo=_london())
            i += 1
            continue

        tm = _TIME_LINE.match(line)
        if tm and current_date is not None:
            hour, minute = int(tm.group(1)), int(tm.group(2))
            teams: list[str] = []
            channel_parts: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j].strip()
                if _TIME_LINE.match(nxt) or _DAY_HEAD.match(nxt):
                    break
                low = nxt.lower()
                if not nxt or low in _SKIP_LINES or nxt in {"☆", "★"}:
                    j += 1
                    continue
                if re.fullmatch(r"\d+\s*MATCHES?", nxt, re.I) or nxt.upper() in {"TIME", "MATCH"}:
                    j += 1
                    continue
                if low.startswith("where to watch"):
                    j += 1
                    continue
                if len(teams) < 2:
                    teams.append(nxt.replace("☆", "").strip())
                else:
                    channel_parts.append(nxt)
                j += 1
            if len(teams) >= 2:
                home, away = teams[0], teams[1]
                title = f"{home} vs {away}"
                local = current_date.replace(hour=hour, minute=minute)
                starts = local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                channel_blob = " / ".join(channel_parts)
                league = _league_from_text(title, channel_blob)
                eid = _external_id(title, starts, league)
                if eid not in seen:
                    seen.add(eid)
                    rows.append(
                        ListingRow(
                            external_id=eid,
                            source_id=SOURCE_ID,
                            sport="football",
                            league=league if league else "Other",
                            title=title,
                            starts_at=starts,
                            channels=_extract_channels(channel_blob),
                            source_url=TZ_FOOTBALL_URL,
                        )
                    )
            i = j
            continue
        i += 1
    return rows


def fetch_listings() -> list[ListingRow]:
    """Football listings: WheresTheMatch first, timezone.football fallback."""
    with _browser() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            rows: list[ListingRow] = []
            try:
                rows = _from_wtm(page)
            except Exception:
                logger.exception("wheresthematch primary scrape failed")
            if len(rows) < 3:
                logger.warning("wheresthematch sparse (%s); trying timezone.football", len(rows))
                try:
                    fb = _from_timezone_football(page)
                    if fb:
                        rows = fb
                except Exception:
                    logger.exception("timezone.football fallback failed")
                    if not rows:
                        raise
        finally:
            browser.close()
    logger.info("football collector fetched %s rows", len(rows))
    return rows
