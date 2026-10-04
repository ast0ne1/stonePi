from __future__ import annotations

from typing import Protocol

from app.services.social.types import RawSocialPost


class SocialProvider(Protocol):
    platform: str

    def fetch_recent_posts(
        self,
        username: str,
        *,
        num_of_posts: int = 10,
        posts_to_not_include: list[str] | None = None,
        start_date: str | None = None,
        paced: bool = True,
    ) -> list[RawSocialPost]:
        """Retrieve recent public posts for a tracked account username. ``paced`` is
        False for a manual check (see services/brightdata.py)."""
        ...
