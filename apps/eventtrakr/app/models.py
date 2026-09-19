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
    cost: Mapped[str] = mapped_column(String(100), default="Free / Unspecified")
    category: Mapped[str] = mapped_column(String(60), default="General")
    url: Mapped[str] = mapped_column(String(1000), default="")
    image_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_favourited: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    calendar_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    google_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
