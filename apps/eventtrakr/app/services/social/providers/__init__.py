from __future__ import annotations

from sqlalchemy.orm import Session

from app.services import settings as settings_service
from app.services.social.providers.brightdata_instagram import (
    BrightDataInstagramProvider,
    INSTAGRAM_POSTS_DATASET_ID,
)


def get_provider(db: Session, platform: str = "instagram"):
    platform = (platform or "instagram").strip().lower()
    if platform != "instagram":
        raise ValueError(f"Unsupported social platform: {platform}")
    api_key = settings_service.get_brightdata_api_key(db)
    if not api_key:
        raise ValueError("Bright Data API key is not configured")
    dataset_id = settings_service.get_value(db, "social_instagram_dataset_id", "") or INSTAGRAM_POSTS_DATASET_ID
    return BrightDataInstagramProvider(api_key, dataset_id=dataset_id)
