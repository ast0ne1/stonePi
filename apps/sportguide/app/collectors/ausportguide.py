from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.collectors.base import ListingRow
from app.collectors.channels import channel_from_icon_src, channels_from_icon_srcs
from app.timeutil import normalize_sport

logger = logging.getLogger("sportguide.ausportguide")

SOURCE_ID = "ausportguide"
BASE_URL = "https://ausportguide.com/"
USER_AGENT = "StonePi-SportGuide/0.0.1 (household LAN; +https://github.com/ast0ne1)"

_KEEP_SPORTS = frozenset({"afl", "cricket", "rugby"})

_SECTION_SPLIT = re.compile(
    r"\n\s*(Aussie Rules|Rugby League|Rugby Union|Cricket|Soccer|Basketball|"
    r"Motorsport|Tennis|Golf|MMA|Cycling|American Football|Boxing|"
    r"Sailing\s*/\s*Boating|Darts|Climbing)\s*\n",
    re.I,
)

_CHANNEL_NAMES = (
    "Kayo Sports",
    "Kayo",
    "Fox Sports",
    "Fox Footy",
    "Fox League",
    "Fox Cricket",
    "7plus",
    "9Now",
    "10 Play",
    "Stan Sport",
    "Stan Sports",
    "ESPN",
    "Channel 7",
    "Channel 9",
    "Channel 10",
    "SBS",
    "ABC",
    "ABC iview",
    "beIN Sports",
)

_TIME_ONLY = re.compile(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?$", re.I)
_TIME_IN = re.compile(r"(\d{1,2}):(\d{2})\s*(AM|PM)", re.I)
_DATED = re.compile(
    r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?\s+(\d{1,2})\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+from\s+"
    r"(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.I,
)
_VS_HINT = re.compile(r"\bvs\.?\b|\bv\b|\s[-–]\s", re.I)

_PAGE_EVAL = """
() => {
  const body = (document.body && document.body.innerText) || '';
  const icons = [];
  const imgs = document.querySelectorAll(
    'img[src*="channel-live-guide"], img[src*="/images/channel"]'
  );
  imgs.forEach((img) => {
    const src = img.getAttribute('src') || '';
    const alt = img.getAttribute('alt') || '';
    let near = '';
    let node = img.parentElement;
    for (let i = 0; i < 8 && node; i++) {
      const t = (node.innerText || '').replace(/\\s+/g, ' ').trim();
      if (t.length >= 12 && (/\\bvs\\.?\\b|\\bv\\b|\\d{1,2}:\\d{2}/i.test(t))) {
        near = t.slice(0, 700);
        break;
      }
      node = node.parentElement;
    }
    icons.push({ src, alt, near });
  });
  return { body, icons };
}
"""


def _browser():
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def _au_tz() -> ZoneInfo:
    try:
        return ZoneInfo("Australia/Melbourne")
    except Exception:
        return timezone(timedelta(hours=10))  # type: ignore[return-value]


def _heading_sport(heading: str) -> str | None:
    h = (heading or "").strip().lower()
    if "aussie" in h or h == "afl":
        return "afl"
    if "cricket" in h:
        return "cricket"
    if "rugby" in h or h == "nrl":
        return "rugby"
    return normalize_sport(heading)


def _parse_time(hour: int, minute: int, ampm: str | None, *, day: int | None = None, month: int | None = None, year: int | None = None) -> str:
    now = datetime.now(_au_tz())
    h = hour % 12
    if ampm and ampm.upper() == "PM":
        h += 12
    elif ampm and ampm.upper() == "AM":
        pass
    elif not ampm and hour <= 23:
        h = hour  # 24h style already
    local = datetime(
        year or now.year,
        month or now.month,
        day or now.day,
        h,
        minute,
        tzinfo=_au_tz(),
    )
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_kickoff_line(text: str) -> str | None:
    text = " ".join((text or "").split())
    m = _DATED.search(text)
    if m:
        months = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }
        return _parse_time(
            int(m.group(4)),
            int(m.group(5)),
            m.group(6),
            day=int(m.group(2)),
            month=months[m.group(3).lower()[:3]],
        )
    m2 = _TIME_IN.search(text)
    if m2:
        return _parse_time(int(m2.group(1)), int(m2.group(2)), m2.group(3))
    m3 = _TIME_ONLY.match(text.strip())
    if m3:
        return _parse_time(int(m3.group(1)), int(m3.group(2)), m3.group(3) or "PM")
    return None


def _external_id(sport: str, title: str, starts_at: str) -> str:
    raw = f"{sport}|{title}|{starts_at}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _extract_channels(text: str) -> list[str]:
    found: list[str] = []
    low = text.lower()
    for ch in _CHANNEL_NAMES:
        if ch.lower() in low and ch not in found:
            found.append(ch)
    return found


def _merge_channels(*groups: list[str] | None) -> list[str]:
    out: list[str] = []
    for group in groups:
        for ch in group or ():
            label = (ch or "").strip()
            if not label:
                continue
            # Prefer longer labels (Kayo Sports over Kayo) when both present.
            drop = [x for x in out if x.lower() in label.lower() or label.lower() in x.lower()]
            for d in drop:
                if len(label) >= len(d) and d in out:
                    out.remove(d)
            if not any(x.lower() == label.lower() for x in out):
                out.append(label)
    return out


def _channels_near_title(title: str, icons: list[dict]) -> list[str]:
    """Match channel logos whose nearby card text mentions this fixture."""
    title_low = re.sub(r"\s+", " ", (title or "").lower()).strip()
    if not title_low:
        return []
    tokens = [t for t in re.split(r"[^a-z0-9]+", title_low) if len(t) >= 4][:4]
    found: list[str] = []
    for icon in icons:
        near = re.sub(r"\s+", " ", str(icon.get("near") or "").lower())
        if not near:
            continue
        if title_low[:24] in near or (tokens and sum(1 for t in tokens if t in near) >= min(2, len(tokens))):
            label = channel_from_icon_src(str(icon.get("src") or ""))
            if not label:
                alt = str(icon.get("alt") or "").strip()
                if alt and len(alt) < 40:
                    label = alt
            if label and label not in found:
                found.append(label)
    return found


def _clean_title(raw: str) -> str:
    title = re.sub(r"\s+", " ", (raw or "").strip())
    title = re.sub(r"^(Today|Tomorrow)\s*[|–-]?\s*", "", title, flags=re.I)
    title = re.sub(r"\s*[|–-]\s*(AFL|NRL|SANFL|VFL|WAFL|Cricket|Rugby).*$", "", title, flags=re.I)
    return title[:180]


def _dismiss_cookies(page) -> None:
    try:
        for label in ("I agree", "Accept", "Agree"):
            btn = page.get_by_role("button", name=re.compile(label, re.I))
            if btn.count():
                btn.first.click(timeout=1500)
                page.wait_for_timeout(500)
                return
    except Exception:
        pass


def fetch_listings() -> list[ListingRow]:
    """Playwright scrape of ausportguide.com — AFL, Cricket, Rugby only."""
    from stonepi_browser import chromium_lock

    with chromium_lock():
        with _browser() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(BASE_URL, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(1500)
                _dismiss_cookies(page)
                page.wait_for_timeout(1000)
                payload = page.evaluate(_PAGE_EVAL) or {}
            finally:
                browser.close()

    body = str(payload.get("body") or "")
    icons = list(payload.get("icons") or [])
    rows_by_id: dict[str, ListingRow] = {}

    # Split body into sport sections by known headings
    parts = _SECTION_SPLIT.split("\n" + body + "\n")
    # parts: [preamble, heading1, body1, heading2, body2, ...]
    sections: list[tuple[str, str]] = []
    i = 1
    while i + 1 < len(parts):
        sections.append((parts[i], parts[i + 1]))
        i += 2

    def add(sport: str, title: str, starts: str | None, blob: str, *, extra_channels: list[str] | None = None) -> None:
        title = _clean_title(title)
        if len(title) < 6 or not _VS_HINT.search(title):
            if len(title) < 10:
                return
        # Site sections sometimes bleed; prefer strong title/blob cues.
        low = f"{title} {blob[:200]}".lower()
        if re.search(r"\bnrl\b|super rugby|\bnpc\b|rugby league|rugby union", low):
            sport = "rugby"
        elif re.search(r"\bafl\b|aussie rules|aflw|sanfl|\bvfl\b|\bwafl\b", low):
            sport = "afl"
        elif re.search(r"\bodi\b|\bt20\b|test match|one-day|cricket", low):
            sport = "cricket"
        else:
            hinted = normalize_sport(title)
            if hinted in _KEEP_SPORTS:
                sport = hinted
        if sport not in _KEEP_SPORTS:
            return
        if not starts:
            return
        eid = _external_id(sport, title, starts)
        channels = _merge_channels(
            extra_channels,
            _channels_near_title(title, icons),
            _extract_channels(blob),
            _extract_channels(title),
        )
        league = {"afl": "AFL", "cricket": "Cricket", "rugby": "Rugby"}.get(sport, "")
        existing = rows_by_id.get(eid)
        if existing:
            existing.channels = _merge_channels(existing.channels, channels)
            return
        rows_by_id[eid] = ListingRow(
            external_id=eid,
            source_id=SOURCE_ID,
            sport=sport,
            league=league,
            title=title,
            starts_at=starts,
            channels=channels,
            source_url=BASE_URL,
        )

    for heading, chunk in sections:
        sport = _heading_sport(heading)
        if sport not in _KEEP_SPORTS:
            continue
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        pending_time: str | None = None
        pending_teams: list[str] = []
        for line in lines:
            if line.lower() in {"discover more", "explore streaming services", "order sports gear"}:
                break
            if re.search(r"Australia$|Netherlands$|New Zealand$|United Kingdom$", line) and len(line) < 40:
                continue  # competition country labels
            t_only = _TIME_ONLY.match(line)
            if t_only:
                # Flush previous incomplete
                pending_time = _parse_time(int(t_only.group(1)), int(t_only.group(2)), t_only.group(3) or "PM")
                pending_teams = []
                continue
            starts_inline = _parse_kickoff_line(line)
            if starts_inline and _VS_HINT.search(line) and len(line) > 12:
                add(sport, line, starts_inline, chunk)
                pending_time = None
                pending_teams = []
                continue
            if pending_time:
                if _VS_HINT.search(line) or "grand final" in line.lower() or "|" in line:
                    add(sport, line, pending_time, chunk)
                    pending_time = None
                    pending_teams = []
                elif len(line) < 40 and not line.endswith("Australia"):
                    pending_teams.append(line)
                    if len(pending_teams) >= 2:
                        add(sport, f"{pending_teams[0]} vs {pending_teams[1]}", pending_time, chunk)
                        pending_time = None
                        pending_teams = []
                continue

    # Featured dated lines in preamble (AFL grand final etc.)
    for m in re.finditer(
        r"((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?\s+\d{1,2}\s+\w+\s+from\s+\d{1,2}:\d{2}\s*(?:AM|PM))"
        r"\s*[|–-]?\s*(.+?)(?:\n|$)",
        body,
        re.I,
    ):
        when, title = m.group(1), m.group(2)
        sport = normalize_sport(title) or normalize_sport(when + " " + title)
        if sport in _KEEP_SPORTS:
            add(sport, title, _parse_kickoff_line(when), when + " " + title)

    # Card-local pass: each channel logo + nearby text can fill gaps / attach channels.
    for icon in icons:
        near = str(icon.get("near") or "")
        if not near:
            continue
        chans = channels_from_icon_srcs([str(icon.get("src") or "")])
        alt = str(icon.get("alt") or "").strip()
        if alt and len(alt) < 40:
            chans = _merge_channels(chans, [alt])
        starts = _parse_kickoff_line(near)
        # Prefer a vs-line inside the card text.
        title = ""
        for part in re.split(r"[\n|]", near):
            part = part.strip()
            if _VS_HINT.search(part) and len(part) >= 8:
                title = part
                break
        if not title:
            continue
        sport = normalize_sport(title) or normalize_sport(near)
        if sport not in _KEEP_SPORTS:
            # Infer from nearby section words
            if re.search(r"\bafl\b|aussie", near, re.I):
                sport = "afl"
            elif re.search(r"cricket|\bodi\b|\bt20\b", near, re.I):
                sport = "cricket"
            elif re.search(r"rugby|\bnrl\b", near, re.I):
                sport = "rugby"
        if sport in _KEEP_SPORTS:
            add(sport, title, starts, near, extra_channels=chans)

    rows = list(rows_by_id.values())
    logger.info("ausportguide fetched %s rows (%s channel icons)", len(rows), len(icons))
    return rows
