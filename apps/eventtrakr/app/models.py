from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    favourites_public: Mapped[bool] = mapped_column(Boolean, default=False)
    default_location: Mapped[str] = mapped_column(String(200), default="Copenhagen, Denmark")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    auth_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)


class UserSetting(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CatalogSource(Base):
    __tablename__ = "catalog_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    category: Mapped[str] = mapped_column(String(60), default="General")
    city_or_region: Mapped[str] = mapped_column(String(100), default="Global")
    source_type: Mapped[str] = mapped_column(String(30), default="supported")
    description: Mapped[str] = mapped_column(Text, default="")
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=True)
    favicon_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    favicon_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventSource(Base):
    __tablename__ = "event_sources"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_sources_user_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    catalog_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))
    source_type: Mapped[str] = mapped_column(String(30), default="generic")
    category: Mapped[str] = mapped_column(String(60), default="general")
    keywords_include: Mapped[str] = mapped_column(Text, default="")
    keywords_exclude: Mapped[str] = mapped_column(Text, default="")
    schedule_mode: Mapped[str] = mapped_column(String(20), default="global")
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_config: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    favicon_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    favicon_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_events_user_fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, default=1, index=True)
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str] = mapped_column(String(300), default="Unspecified")
    cost: Mapped[str] = mapped_column(String(100), default="Unspecified")
    category: Mapped[str] = mapped_column(String(60), default="General")
    url: Mapped[str] = mapped_column(String(1000), default="")
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_favourited: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    calendar_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    google_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    origin: Mapped[str] = mapped_column(String(40), default="api")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceFetch(Base):
    __tablename__ = "source_fetches"

    url: Mapped[str] = mapped_column(String(1000), primary_key=True)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CalendarConnection(Base):
    __tablename__ = "calendar_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_cal_user_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(30), default="google")
    account_email: Mapped[str] = mapped_column(String(200), default="")
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_forward_favourites: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialAccount(Base):
    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", "username", name="uq_social_user_platform_username"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), default="instagram")
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    profile_url: Mapped[str] = mapped_column(String(500), default="")
    account_type: Mapped[str] = mapped_column(String(40), default="other")
    tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_mode: Mapped[str] = mapped_column(String(20), default="global")
    interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_config: Mapped[str] = mapped_column(Text, default="")
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    posts_processed: Mapped[int] = mapped_column(Integer, default=0)
    events_matched: Mapped[int] = mapped_column(Integer, default=0)
    events_discovered: Mapped[int] = mapped_column(Integer, default=0)
    posts_ignored: Mapped[int] = mapped_column(Integer, default=0)
    ocr_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialPost(Base):
    __tablename__ = "social_posts"
    __table_args__ = (
        UniqueConstraint("social_account_id", "external_post_id", name="uq_social_post_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    social_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("social_accounts.id"), nullable=False, index=True)
    external_post_id: Mapped[str] = mapped_column(String(120), nullable=False)
    post_url: Mapped[str] = mapped_column(String(1000), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    caption: Mapped[str] = mapped_column(Text, default="")
    media_type: Mapped[str] = mapped_column(String(40), default="")
    hashtags: Mapped[str] = mapped_column(Text, default="")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processing_status: Mapped[str] = mapped_column(String(40), default="pending")
    caption_text: Mapped[str] = mapped_column(Text, default="")
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    combined_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialPostMedia(Base):
    __tablename__ = "social_post_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    social_post_id: Mapped[int] = mapped_column(Integer, ForeignKey("social_posts.id"), nullable=False, index=True)
    media_url: Mapped[str] = mapped_column(String(2000), default="")
    media_type: Mapped[str] = mapped_column(String(40), default="image")
    position: Mapped[int] = mapped_column(Integer, default=0)
    local_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ocr_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventSocialLink(Base):
    __tablename__ = "event_social_links"
    __table_args__ = (
        UniqueConstraint("event_id", "social_post_id", name="uq_event_social_post"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    social_post_id: Mapped[int] = mapped_column(Integer, ForeignKey("social_posts.id"), nullable=False, index=True)
    link_kind: Mapped[str] = mapped_column(String(40), default="enrichment")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventDiscovery(Base):
    __tablename__ = "event_discoveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    social_post_id: Mapped[int] = mapped_column(Integer, ForeignKey("social_posts.id"), nullable=False, index=True)
    social_account_id: Mapped[int] = mapped_column(Integer, ForeignKey("social_accounts.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location: Mapped[str] = mapped_column(String(300), default="")
    event_type: Mapped[str] = mapped_column(String(100), default="")
    cost: Mapped[str] = mapped_column(String(100), default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    event_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("events.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventUpdate(Base):
    __tablename__ = "event_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(Integer, ForeignKey("events.id"), nullable=False, index=True)
    social_post_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("social_posts.id"), nullable=True)
    update_kind: Mapped[str] = mapped_column(String(40), default="info")
    summary: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
