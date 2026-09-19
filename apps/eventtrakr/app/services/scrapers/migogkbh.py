from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.services.scrapers.base import BaseScraper, ScrapedEvent

# Unlike the human-readable "I morgen"/"kl. x" text next to it, each card's
# <time datetime="..."> attribute is a plain machine-readable
# "YYYY-MM-DD HH:MM:SS" -- no Danish relative-date parsing needed.


def _parse_datetime(raw: str) -> datetime | None:
    try:
        dt = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


class MigogKbhExtractor(BaseScraper):
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        soup = BeautifulSoup(content, "html.parser")
        events: list[ScrapedEvent] = []

        for card in soup.select(r"article.tpo\:article-thumb"):
            title_el = card.select_one(r"h3.tpo\:article-thumb--headline")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)

            time_el = card.select_one(r".tpo\:article-thumb--excerpt time[datetime]")
            if not time_el:
                continue
            start_time = _parse_datetime(time_el["datetime"])
            if not start_time:
                continue

            loc_el = card.select_one('a[rel="category tag"]')
            location = loc_el.get_text(strip=True) if loc_el else "Unspecified"

            # The site's own detail page, not the "Billet" ticket-vendor link.
            link_el = card.select_one('a[href*="/kalender/begivenhed/"]')
            ev_url = link_el["href"] if link_el else base_url

            events.append(
                ScrapedEvent(
                    title=title,
                    description="",
                    start_time=start_time,
                    location=location,
                    url=ev_url,
                )
            )

        return events
