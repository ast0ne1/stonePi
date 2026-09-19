from __future__ import annotations

import logging
from datetime import datetime, timezone
from icalendar import Calendar

from app.services.scrapers.base import BaseScraper, ScrapedEvent

logger = logging.getLogger("eventtrakr.scrapers.ics")


def _to_utc(dt_val: Any) -> datetime | None:
    if not dt_val:
        return None
    if hasattr(dt_val, "dt"):
        dt = dt_val.dt
    else:
        dt = dt_val
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    # date object
    return datetime(dt.year, dt.month, dt.day, 0, 0, tzinfo=timezone.utc)


class IcsFeedExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        try:
            cal = Calendar.from_ical(content)
        except Exception as e:
            logger.warning("Failed to parse ICS feed: %s", e)
            return events

        for component in cal.walk():
            if component.name == "VEVENT":
                summary = component.get("summary")
                dtstart = component.get("dtstart")
                if not summary or not dtstart:
                    continue

                start_time = _to_utc(dtstart)
                if not start_time:
                    continue
                end_time = _to_utc(component.get("dtend"))

                desc = str(component.get("description", "") or "")
                location = str(component.get("location", "Unspecified") or "Unspecified")
                url = str(component.get("url", "") or base_url)
                categories = component.get("categories")
                cat_str = "General"
                if categories:
                    cat_str = str(categories.to_ical().decode("utf-8", errors="ignore")) if hasattr(categories, "to_ical") else str(categories)

                events.append(
                    ScrapedEvent(
                        title=str(summary).strip(),
                        description=desc.strip()[:1000],
                        start_time=start_time,
                        end_time=end_time,
                        location=location[:250],
                        cost="Free / Unspecified",
                        category=cat_str[:60],
                        url=url or base_url,
                        image_url=None,
                    )
                )
        return events
