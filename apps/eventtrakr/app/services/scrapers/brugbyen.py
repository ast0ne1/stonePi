from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
import dateparser

from app.services.scrapers.base import BaseScraper, ScrapedEvent

# The City of Copenhagen runs BrugByen and the 12 local committees'
# (lokaludvalg) calendars on the same event platform, e.g.
# vesterbrolokaludvalg.kk.dk/kalender-0 -- same cards, same filter form.
#
# The card date text is one of three shapes:
#  - "Fri. 18 Sep. 2026 At 11.00 - 18.00" (BrugByen English, single day)
#  - "Søn. 08. nov. 2026 Kl. 10 - 12" (the Danish district sites)
#  - "18.09.2026 - 26.09.2026" (a multi-day run, no time of day)
# dateparser chokes on any combined string as-is, so the "At"/"Kl." time
# range and any trailing end-date both need stripping before parsing the
# start. Danish times drop ":00" ("Kl. 18") and use a dot ("Kl. 18.30").

COMMITTEE_MEETINGS_KEY = "committee-meetings"

# A district calendar mixes the committee's own meetings in with real local
# events (Repair Cafés, swap markets, lantern parades); these title words
# mark the meetings -- the committee's own, its working groups, and the
# public meetings it holds (borgermøde, bydelsmøde, høring).
_MEETING_RE = re.compile(
    r"møde|arbejdsgruppe|-gruppen\b|fagudvalg|høring",
    re.IGNORECASE,
)


def _parse_time(raw: str) -> str:
    start = raw.split(" - ", 1)[0].strip()
    hours, _, minutes = start.partition(".")
    return f"{hours}:{minutes or '00'}"


def _parse_run_end(raw: str) -> datetime | None:
    """The last day of a "18.09.2026 - 26.09.2026" run, at the end of that day."""
    m = re.fullmatch(r"\s*\d{1,2}\.\d{1,2}\.\d{4}\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*", raw)
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    return datetime(year, month, day, 23, 59, tzinfo=timezone.utc)


def _parse_date_text(raw: str) -> datetime | None:
    raw = raw.replace("\xa0", " ")
    for sep in (" At ", " Kl. "):
        date_part, found, time_part = raw.partition(sep)
        if found:
            candidate = f"{date_part.strip()} {_parse_time(time_part)}"
            break
    else:
        candidate = raw.split(" - ", 1)[0].strip()
    dt = dateparser.parse(
        candidate, languages=["en", "da"], settings={"PREFER_DATES_FROM": "future", "DATE_ORDER": "DMY"}
    )
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


_WEEKDAYS = {name: i for i, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
# Most dates read from one schedule entry (a daily run over a year would be 365).
MAX_SCHEDULE_DATES = 60


def _schedule_days(sched: dict) -> list[date]:
    """The days one ``Schedule`` entry covers. A repeating one ("every Sunday
    until 20 Dec": ``repeatFrequency`` P1W, ``byDay``) is one day per
    occurrence, not a single run from its first day to its last."""
    first = date.fromisoformat(sched["startDate"])
    freq = str(sched.get("repeatFrequency") or "")
    m = re.fullmatch(r"P(\d+)([DW])", freq.upper())
    if not m:
        return [first]
    last = date.fromisoformat(sched.get("endDate") or sched["startDate"])
    step = timedelta(days=int(m.group(1)) * (7 if m.group(2) == "W" else 1))
    by_day = sched.get("byDay") or []
    by_day = [by_day] if isinstance(by_day, str) else by_day
    weekdays = {_WEEKDAYS[d.rsplit("/", 1)[-1].lower()] for d in by_day if d.rsplit("/", 1)[-1].lower() in _WEEKDAYS}
    days: list[date] = []
    if weekdays and m.group(2) == "W":
        # Each chosen weekday, every n weeks from the first one.
        week = first - timedelta(days=first.weekday())
        while week <= last and len(days) < MAX_SCHEDULE_DATES:
            for wd in sorted(weekdays):
                day = week + timedelta(days=wd)
                if first <= day <= last:
                    days.append(day)
            week += step
    else:
        day = first
        while day <= last and len(days) < MAX_SCHEDULE_DATES:
            days.append(day)
            day += step
    return days[:MAX_SCHEDULE_DATES]


def read_schedule(content: str) -> list[tuple[datetime, datetime | None]]:
    """Every date of an event from its own page's schema.org ``eventSchedule``
    (local wall-clock times, stored as UTC like the cards' own times)."""
    soup = BeautifulSoup(content, "html.parser")
    dates: list[tuple[datetime, datetime | None]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict) or node.get("@type") != "Event":
                continue
            for sched in node.get("eventSchedule") or []:
                try:
                    repeats = bool(sched.get("repeatFrequency"))
                    days = _schedule_days(sched)
                    start_t = datetime.strptime(sched.get("startTime") or "00:00", "%H:%M").time()
                    end_t = datetime.strptime(sched["endTime"], "%H:%M").time() if sched.get("endTime") else None
                    # Without a repeat, endDate is the end of one run; with one,
                    # each occurrence ends on its own day.
                    last = date.fromisoformat(sched.get("endDate") or sched["startDate"])
                except (KeyError, TypeError, ValueError, AttributeError):
                    continue
                for day in days:
                    start = datetime.combine(day, start_t, tzinfo=timezone.utc)
                    end = None
                    if end_t is not None:
                        end = datetime.combine(day if repeats else last, end_t, tzinfo=timezone.utc)
                    dates.append((start, end))
    return sorted(set(dates))


def is_kk_event_site(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "brugbyen.kk.dk" or host.endswith("lokaludvalg.kk.dk")


def is_listing_url(url: str) -> bool:
    path = urlsplit(url).path.rstrip("/")
    last = path.rsplit("/", 1)[-1]
    return "/whats-on" in path or last.startswith("kalender")


def is_committee_meeting(title: str) -> bool:
    return bool(_MEETING_RE.search(title))


def with_date_window(url: str, days: int) -> str:
    if not is_listing_url(url):
        return url
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    today = date.today()
    end = today + timedelta(days=days)
    query["dates[min]"] = [today.isoformat()]
    query["dates[max]"] = [end.isoformat()]
    new_query = urlencode({k: v[0] for k, v in query.items()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


class BrugbyenExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(content, "html.parser")
        events: list[ScrapedEvent] = []
        is_district = (urlsplit(base_url).hostname or "").lower().endswith("lokaludvalg.kk.dk")

        for card in soup.select(".node--type-event"):
            title_el = card.select_one(".title h3")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            date_el = card.select_one(".field--name-scheduled-dates .date-text")
            if not date_el:
                continue
            date_text = date_el.get_text(" ", strip=True)
            start_time = _parse_date_text(date_text)
            if not start_time:
                continue
            # "2 andre datoer" / "3 other dates": the rest are on the event's own page.
            more_el = card.select_one(".dates")
            has_more_dates = bool(more_el and re.search(r"\d", more_el.get_text()))

            location_el = card.select_one(".pre-title")
            location = location_el.get_text(strip=True) if location_el else "Unspecified"

            link_el = card.select_one("a[href]")
            url = urljoin(base_url, link_el["href"]) if link_el else base_url

            events.append(
                ScrapedEvent(
                    title=title,
                    description="",
                    start_time=start_time,
                    end_time=_parse_run_end(date_text),
                    location=location,
                    url=url,
                    category_key=COMMITTEE_MEETINGS_KEY if is_district and is_committee_meeting(title) else None,
                    has_more_dates=has_more_dates,
                )
            )

        return events
