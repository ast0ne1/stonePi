from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from app.collectors.base import ListingRow
from app.timeutil import normalize_football_league

logger = logging.getLogger("sportguide.wheresthematch")

# One football source, two guides, merged per match:
#   timezone.football — channels in the Pi's own country (the site picks the
#     country from the visitor's IP), the competition, and a page per match.
#   WheresTheMatch — UK channels (it is a UK guide).
# Both serve plain HTML with structured markup, read over HTTP; headless
# Chromium is only a fallback when a site refuses a plain request.

SOURCE_ID = "wheresthematch"
WTM_URL = "https://www.wheresthematch.com/?sportid=1"
TZ_FOOTBALL_URL = "https://timezone.football/football-on-tv-this-week/"
TZ_BASE = "https://timezone.football"
USER_AGENT = "StonePi-SportGuide/0.0.1 (household LAN; +https://github.com/ast0ne1)"
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
UK = "United Kingdom"
# Same match on both sites: kick-offs within this window.
MERGE_WINDOW = timedelta(minutes=20)

_MONTHS = {
    m: i
    for i, m in enumerate(
        ("january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"),
        start=1,
    )
}
_DAY_HEAD = re.compile(
    r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{1,2})\s+([A-Za-z]+)", re.I
)
_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
_TEAM_NOISE = re.compile(r"\b(fc|afc|cf|sc|ac|ssc|club|de|the|women|w)\b")


def _london() -> ZoneInfo:
    return ZoneInfo("Europe/London")


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _external_id(title: str, starts_at: str, league: str) -> str:
    raw = f"football|{league}|{title}|{starts_at}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _league(competition: str) -> str:
    """Competition name → SportGuide league; never guessed from team names."""
    low = (competition or "").lower()
    if any(x in low for x in ("u21", "u23", "u19", "women", "wsl", "premier league 2", "premier league cup")):
        return "Other"
    return normalize_football_league(competition, club_hints=False) or "Other"


def _team_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ").replace("utd", "united").replace("man ", "manchester ")
    text = _TEAM_NOISE.sub(" ", re.sub(r"[^a-z0-9 ]+", " ", text))
    return " ".join(text.split())


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        item = " ".join((item or "").split())
        if item and item.lower() not in {o.lower() for o in out}:
            out.append(item)
    return out


# ---------- WheresTheMatch (UK) ----------


def parse_wtm(html: str) -> list[dict]:
    """Fixtures from the WheresTheMatch football table."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for tr in soup.select("tr"):
        fixture = tr.select_one("td.fixture-details .fixture")
        when = tr.select_one("td.start-details time[datetime]")
        if not fixture or not when:
            continue
        teams = [em.get_text(" ", strip=True) for em in fixture.select("a em")]
        if len(teams) < 2:
            continue
        try:
            starts = datetime.fromisoformat(when["datetime"])
        except ValueError:
            continue
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=_london())
        comp_el = tr.select_one("td.competition-name span") or tr.select_one(".fixture-comp")
        competition = comp_el.get_text(" ", strip=True) if comp_el else ""
        channels = [s.get_text(" ", strip=True) for s in tr.select("td.channel-details .sr-only")]
        if not channels:
            channels = [
                re.sub(r"^.*\bBroadcast on\s+", "", img.get("alt") or "")
                for img in tr.select("td.channel-details img.channel")
            ]
        link = tr.select_one("td.channel-details a[href]") or tr.select_one("td.home-team a[href]")
        out.append(
            {
                "home": teams[0],
                "away": teams[1],
                "starts": starts.astimezone(timezone.utc),
                "competition": competition,
                "channels": _dedupe(channels),
                "url": link["href"] if link else WTM_URL,
            }
        )
    return out


# ---------- timezone.football (the Pi's own country) ----------


def _kickoff(raw: str) -> datetime | None:
    """schema.org startDate → UTC. Proper ISO first (offsets honoured); the site
    also emits malformed "…T18:45:00.000:00Z", read as minutes in UTC."""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError:
        pass
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d)", raw)
    return datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc) if m else None


def _ld_kickoffs(soup: BeautifulSoup) -> dict[str, datetime]:
    """Match page path → UTC kick-off from the page's schema.org data (unique paths only)."""
    found: dict[str, list[datetime]] = {}

    def scan(node):
        if isinstance(node, dict):
            if node.get("@type") == "SportsEvent":
                kickoff = _kickoff(str(node.get("startDate") or ""))
                path = str(node.get("url") or "").replace(TZ_BASE, "")
                if kickoff and path:
                    found.setdefault(path, []).append(kickoff)
            for v in node.values():
                scan(v)
        elif isinstance(node, list):
            for v in node:
                scan(v)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            scan(json.loads(script.string or ""))
        except ValueError:
            continue
    # Friendlies all link to /friendlies/: those can't be told apart here.
    return {path: times[0] for path, times in found.items() if len(times) == 1}


def _chip_channels(cell, country: str) -> tuple[list[str], str]:
    """(channels in the viewer's country, status) for one row's channel cell."""
    channels: list[str] = []
    status = ""
    for chip in cell.select(".tm-chip") if cell else []:
        classes = set(chip.get("class") or [])
        name_el = chip.select_one(".tm-chip-name")
        name = (name_el or chip).get_text(" ", strip=True)
        if "tm-ch-alt" in classes or "tm-blackout" in classes:
            # Not shown here (the alternative is a VPN to another country's
            # service — not something to point a household at).
            status = f"Not on TV in {country}" if country else "Not on TV here"
        elif "tm-ch-tbc" in classes:
            status = status or "Channel TBC"
        elif "tm-ch-club" in classes:
            channels.append("Club's official stream")
        elif "tm-ch-season" in classes:
            channels.append(re.sub(r"\s*rights holder\s*$", "", name, flags=re.I) + " (channel TBC)")
        elif name:
            channels.append(name)
    return _dedupe(channels), status


def parse_timezone_football(html: str, *, now: datetime | None = None) -> tuple[list[dict], str]:
    """(fixtures, viewer's country) from timezone.football's week page."""
    soup = BeautifulSoup(html, "html.parser")
    flag = soup.select_one(".tm-ch .tm-ch-flag")
    country = (flag.get("alt") or "").strip() if flag else ""
    kickoffs = _ld_kickoffs(soup)
    now = now or datetime.now(timezone.utc)

    # Walk day headings and rows in page order.
    pending: list[tuple[datetime | None, dict]] = []  # (local naive time, fixture)
    day: datetime | None = None
    for node in soup.find_all(lambda t: (t.name in ("h2", "h3") and "wk-h" in (t.get("class") or []))
                              or (t.name == "li" and "tm-row" in (t.get("class") or []))):
        if node.name != "li":
            m = _DAY_HEAD.match(node.get_text(" ", strip=True))
            if m and m.group(2).lower() in _MONTHS:
                month = _MONTHS[m.group(2).lower()]
                # The week can span New Year either way.
                year = now.year + (1 if month < now.month - 6 else -1 if month > now.month + 6 else 0)
                day = datetime(year, month, int(m.group(1)))
            continue
        names = [a.get_text(" ", strip=True) for a in node.select(".tm-team .tm-name")]
        hh = node.select_one(".tm-hh")
        if len(names) < 2 or not hh:
            continue
        link = node.select_one("a.tm-status-link[href]")
        path = link["href"] if link else ""
        comp = node.select_one(".tm-meta-comp")
        channels, status = _chip_channels(node.select_one(".tm-ch"), country)
        local = None
        tm = _HHMM.match(hh.get_text(strip=True))
        if day and tm:
            local = day.replace(hour=int(tm.group(1)), minute=int(tm.group(2)))
        fixture = {
            "home": names[0],
            "away": names[1],
            "starts": kickoffs.get(path),
            "competition": (comp.get("alt") or "") if comp else "",
            "channels": channels,
            "status": status,
            "url": f"{TZ_BASE}{path}" if "/match/" in path else TZ_FOOTBALL_URL,
        }
        pending.append((local, fixture))

    # Rows without their own match page: the page's clock offset, learnt from
    # rows that have both, applied to the shown day + time.
    offsets = Counter(
        round((local - f["starts"].replace(tzinfo=None)).total_seconds() / 900) * 900
        for local, f in pending
        if local and f["starts"]
    )
    offset = timedelta(seconds=offsets.most_common(1)[0][0]) if offsets else None
    fixtures: list[dict] = []
    for local, f in pending:
        if f["starts"] is None and local is not None:
            if offset is not None:
                f["starts"] = (local - offset).replace(tzinfo=timezone.utc)
            else:
                f["starts"] = local.replace(tzinfo=_london()).astimezone(timezone.utc)
        if f["starts"] is not None:
            fixtures.append(f)
    return fixtures, country


# ---------- merge ----------


def _same_match(a: dict, b: dict) -> bool:
    if abs(a["starts"] - b["starts"]) > MERGE_WINDOW:
        return False
    ah, aa, bh, ba = (_team_key(x) for x in (a["home"], a["away"], b["home"], b["away"]))

    def close(x: str, y: str) -> bool:
        return bool(x and y) and (x == y or x in y or y in x)

    return close(ah, bh) and close(aa, ba)


def merge(tz_rows: list[dict], wtm_rows: list[dict], country: str) -> list[ListingRow]:
    """One listing per match. Local (timezone.football) channels first; UK
    channels from WheresTheMatch are added, marked "(UK)" when the Pi isn't in the UK."""
    local_is_uk = (country or UK) == UK
    rows: list[dict] = []
    for f in tz_rows:
        rows.append({**f, "uk": []})
    for w in wtm_rows:
        match = next((r for r in rows if _same_match(r, w)), None)
        if match is None:
            rows.append({**w, "channels": [], "status": "", "uk": w["channels"], "url": w["url"]})
        else:
            match["uk"] = w["channels"]
            if not match.get("competition"):
                match["competition"] = w["competition"]

    out: list[ListingRow] = []
    seen: set[str] = set()
    for r in rows:
        uk = r["uk"] if local_is_uk else [f"{c} (UK)" for c in r["uk"]]
        channels = _dedupe(list(r["channels"]) + uk)
        if not r["channels"] and r.get("status"):
            channels = [r["status"]] + uk  # e.g. "Not on TV in Denmark", then UK options
        title = f"{r['home']} vs {r['away']}"
        starts = _utc_iso(r["starts"])
        league = _league(r.get("competition") or "")
        eid = _external_id(title, starts, league)
        if eid in seen:
            continue
        seen.add(eid)
        out.append(
            ListingRow(
                external_id=eid,
                source_id=SOURCE_ID,
                sport="football",
                league=league,
                title=title[:180],
                starts_at=starts,
                channels=channels[:6],
                source_url=r["url"],
            )
        )
    out.sort(key=lambda row: row.starts_at)
    return out


# ---------- fetching ----------


def _fetch_http(url: str) -> str:
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
        resp.raise_for_status()
        return resp.text


def _fetch_browser(url: str) -> str:
    from playwright.sync_api import sync_playwright
    from stonepi_browser import chromium_lock

    with chromium_lock():
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(2000)
                return page.content()
            finally:
                browser.close()


def _load(url: str, parse) -> list:
    """Parsed rows from ``url``: plain HTTP first, the browser if that fails or finds nothing."""
    try:
        rows = parse(_fetch_http(url))
        if rows:
            return rows
        logger.warning("%s: nothing found over HTTP; trying the browser", url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s over HTTP failed (%s); trying the browser", url, exc)
    return parse(_fetch_browser(url))


def fetch_listings() -> list[ListingRow]:
    """Football listings: timezone.football (local channels) merged with WheresTheMatch (UK)."""
    country = ""
    tz_rows: list[dict] = []
    wtm_rows: list[dict] = []
    errors: list[str] = []

    def tz_parse(html: str) -> list[dict]:
        nonlocal country
        rows, country = parse_timezone_football(html)
        return rows

    try:
        tz_rows = _load(TZ_FOOTBALL_URL, tz_parse)
    except Exception as exc:  # noqa: BLE001
        logger.exception("timezone.football failed")
        errors.append(f"timezone.football: {exc}")
    try:
        wtm_rows = _load(WTM_URL, parse_wtm)
    except Exception as exc:  # noqa: BLE001
        logger.exception("WheresTheMatch failed")
        errors.append(f"WheresTheMatch: {exc}")
    if not tz_rows and not wtm_rows:
        raise RuntimeError("; ".join(errors) or "No football listings found")
    rows = merge(tz_rows, wtm_rows, country)
    logger.info(
        "football: %s listings (timezone.football %s, WheresTheMatch %s, country %s)",
        len(rows), len(tz_rows), len(wtm_rows), country or "?",
    )
    return rows
