from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ScrapedEvent:
    title: str
    description: str
    start_time: datetime
    end_time: datetime | None = None
    location: str = "Unspecified"
    cost: str = "Free / Unspecified"
    category: str = "General"
    url: str = ""
    image_url: str | None = None


class BaseScraper(ABC):
    @abstractmethod
    def extract(self, content: str, base_url: str) -> list[ScrapedEvent]:
        """Extract structured events from page or feed content."""
        pass
