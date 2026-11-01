from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ScrapedEvent:
    title: str
    description: str
    start_time: datetime
    end_time: datetime | None = None
    location: str = "Unspecified"
    cost: str = "Unspecified"
    category: str = "General"
    url: str = ""
    image_url: str | None = None
    # A category key the scraper is sure of for this one event (e.g. a
    # district committee's own meeting), overriding the source's category.
    category_key: str | None = None
    # Further dates of the same event (a show's other performances), as
    # (start, end) pairs. Ingest stores each as its own event, even past the
    # sync window, so later weeks fill in without another scan.
    more_dates: list[tuple[datetime, datetime | None]] = field(default_factory=list)
    # The listing says there are other dates but doesn't give them; ingest
    # reads them from the event's own page (see brugbyen.read_schedule()).
    has_more_dates: bool = False


class BaseScraper(ABC):
    @abstractmethod
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        """Extract structured events from page or feed content."""
        pass
