from collections.abc import Generator
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import DATA_DIR, env
from app.models import Base

DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    env.database_url,
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_schema()
    from app.services.categories import backfill_event_source_categories, seed_default_categories
    from app.services.users import ensure_admin_user, seed_default_catalog
    ensure_admin_user()
    seed_default_catalog()
    with SessionLocal() as db:
        seed_default_categories(db)
        backfill_event_source_categories(db)


def _ensure_schema() -> None:
    with engine.begin() as conn:
        # Check users table
        user_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        if "is_public" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_public BOOLEAN DEFAULT 0"))
        if "default_location" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN default_location VARCHAR(200) DEFAULT 'Copenhagen, Denmark'"))
        if "favourites_public" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN favourites_public BOOLEAN DEFAULT 0"))
        if "auth_user_id" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN auth_user_id VARCHAR(36)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_auth_user_id ON users(auth_user_id)"))

        # Check events table
        event_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(events)")).fetchall()}
        if "is_favourited" not in event_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN is_favourited BOOLEAN DEFAULT 0"))
        if "calendar_synced" not in event_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN calendar_synced BOOLEAN DEFAULT 0"))
        if "google_event_id" not in event_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN google_event_id VARCHAR(200)"))
        if "is_cancelled" not in event_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN is_cancelled BOOLEAN DEFAULT 0"))

        # Check catalog_sources table
        catalog_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(catalog_sources)")).fetchall()}
        if "favicon_path" not in catalog_cols:
            conn.execute(text("ALTER TABLE catalog_sources ADD COLUMN favicon_path VARCHAR(300)"))
        if "favicon_checked_at" not in catalog_cols:
            conn.execute(text("ALTER TABLE catalog_sources ADD COLUMN favicon_checked_at DATETIME"))

        # Check event_sources table
        source_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(event_sources)")).fetchall()}
        if "favicon_path" not in source_cols:
            conn.execute(text("ALTER TABLE event_sources ADD COLUMN favicon_path VARCHAR(300)"))
        if "favicon_checked_at" not in source_cols:
            conn.execute(text("ALTER TABLE event_sources ADD COLUMN favicon_checked_at DATETIME"))
        if "category" not in source_cols:
            conn.execute(text("ALTER TABLE event_sources ADD COLUMN category VARCHAR(60)"))
            if "category_filter" in source_cols:
                # Carry the old free-text value over verbatim; categories
                # service resolves/normalizes it against the managed list
                # afterwards (see backfill_event_source_categories).
                conn.execute(text("UPDATE event_sources SET category = category_filter WHERE category IS NULL"))
        if "category_filter" in source_cols:
            # Drop the renamed-away legacy column outright -- it's still
            # NOT NULL and the ORM model no longer sets it, so leaving it in
            # place makes every new insert fail once the app is upgraded
            # past the rename (existing rows survive only because they were
            # written back when this column was still required).
            conn.execute(text("ALTER TABLE event_sources DROP COLUMN category_filter"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
