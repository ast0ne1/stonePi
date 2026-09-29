from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawSocialPost:
    """Provider-agnostic post payload before persistence."""

    external_post_id: str
    post_url: str
    published_at: datetime | None = None
    caption: str = ""
    media_type: str = ""
    hashtags: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class SourceContent:
    """Generic content unit for event detection (platform-agnostic)."""

    platform: str
    account_username: str
    account_type: str
    post_url: str
    published_at: datetime | None
    caption: str
    ocr_text: str
    combined_text: str
    hashtags: list[str] = field(default_factory=list)
    image_count: int = 0


@dataclass
class DetectionResult:
    is_event_like: bool
    confidence: str  # high | medium | low | none
    score: float
    signals: list[str] = field(default_factory=list)
    title: str = ""
    start_time: datetime | None = None
    end_time: datetime | None = None
    location: str = ""
    event_type: str = ""
    cost: str = ""
    is_cancellation: bool = False
    is_postponement: bool = False
    change_hints: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    band: str  # strong | likely | possible | none
    score: float
    event_id: int | None = None
    reasons: list[str] = field(default_factory=list)
