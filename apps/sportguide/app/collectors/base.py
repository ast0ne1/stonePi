from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ListingRow:
    external_id: str
    source_id: str
    sport: str
    title: str
    starts_at: str
    league: str = ""
    ends_at: str | None = None
    channels: list[str] = field(default_factory=list)
    source_url: str | None = None
    # Football: the two teams and their badges as found on the source site (U28); the
    # badges are downloaded to the Pi and served from there (services/badges.py).
    home: str = ""
    away: str = ""
    home_badge: str = ""
    away_badge: str = ""

    def as_dict(self) -> dict:
        return {
            "external_id": self.external_id,
            "source_id": self.source_id,
            "sport": self.sport,
            "league": self.league,
            "title": self.title,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "channels": self.channels,
            "source_url": self.source_url,
            "home": self.home,
            "away": self.away,
            "home_badge": self.home_badge,
            "away_badge": self.away_badge,
        }


__all__ = ["ListingRow"]
