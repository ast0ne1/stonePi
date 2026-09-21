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
    from app.services.users import migrate_multi_user

    migrate_multi_user()


def _table_names(conn) -> set[str]:
    return {
        row[0]
        for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    }


def _table_columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def _ensure_schema() -> None:
    with engine.begin() as conn:
        tables = _table_names(conn)
        if "feeds" in tables:
            feed_cols = _table_columns(conn, "feeds")
            if "schedule_mode" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN schedule_mode VARCHAR(20) DEFAULT 'global'"))
                conn.execute(text("UPDATE feeds SET schedule_mode = 'global' WHERE schedule_mode IS NULL"))
            if "interval_minutes" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN interval_minutes INTEGER"))
            if "summarize" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN summarize BOOLEAN DEFAULT 1"))
                conn.execute(text("UPDATE feeds SET summarize = 1 WHERE summarize IS NULL"))
            if "favicon_name" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN favicon_name VARCHAR(200)"))
            if "translate" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN translate BOOLEAN DEFAULT 0"))
                conn.execute(text("UPDATE feeds SET translate = 0 WHERE translate IS NULL"))
            if "translate_provider" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN translate_provider VARCHAR(20) DEFAULT 'global'"))
                conn.execute(
                    text(
                        "UPDATE feeds SET translate_provider = 'global' "
                        "WHERE translate_provider IS NULL OR translate_provider = ''"
                    )
                )
            if "muted_until" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN muted_until DATETIME"))
            if "keyword_include" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN keyword_include TEXT DEFAULT ''"))
                conn.execute(text("UPDATE feeds SET keyword_include = '' WHERE keyword_include IS NULL"))
            if "keyword_exclude" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN keyword_exclude TEXT DEFAULT ''"))
                conn.execute(text("UPDATE feeds SET keyword_exclude = '' WHERE keyword_exclude IS NULL"))
            if "last_status_code" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN last_status_code INTEGER"))
            if "last_item_count" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN last_item_count INTEGER"))
            if "empty_since" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN empty_since DATETIME"))
            if "paywall_skip" not in feed_cols:
                conn.execute(text("ALTER TABLE feeds ADD COLUMN paywall_skip BOOLEAN DEFAULT 0"))
                conn.execute(text("UPDATE feeds SET paywall_skip = 0 WHERE paywall_skip IS NULL"))
        if "users" in tables:
            user_cols = _table_columns(conn, "users")
            if "auth_user_id" not in user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN auth_user_id VARCHAR(36)"))
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_users_auth_user_id ON users(auth_user_id)"))
        if "stories" in tables:
            story_cols = _table_columns(conn, "stories")
            if "favourited" not in story_cols:
                conn.execute(text("ALTER TABLE stories ADD COLUMN favourited BOOLEAN DEFAULT 0"))
                conn.execute(text("UPDATE stories SET favourited = 0 WHERE favourited IS NULL"))
            if "saved" not in story_cols:
                conn.execute(text("ALTER TABLE stories ADD COLUMN saved BOOLEAN DEFAULT 0"))
                conn.execute(text("UPDATE stories SET saved = 0 WHERE saved IS NULL"))
            if "expires_at" not in story_cols:
                conn.execute(text("ALTER TABLE stories ADD COLUMN expires_at DATETIME"))
            if "saved_origin" not in story_cols:
                conn.execute(text("ALTER TABLE stories ADD COLUMN saved_origin VARCHAR(20)"))
            if "importance" not in story_cols:
                conn.execute(text("ALTER TABLE stories ADD COLUMN importance INTEGER"))
            if "content_lang" not in story_cols:
                conn.execute(text("ALTER TABLE stories ADD COLUMN content_lang VARCHAR(8)"))
        if "sync_tasks" in tables:
            task_cols = _table_columns(conn, "sync_tasks")
            if "kind" not in task_cols:
                conn.execute(text("ALTER TABLE sync_tasks ADD COLUMN kind VARCHAR(20) DEFAULT 'x3'"))
                conn.execute(text("UPDATE sync_tasks SET kind = 'x3' WHERE kind IS NULL OR kind = ''"))
            if "error_message" not in task_cols:
                conn.execute(text("ALTER TABLE sync_tasks ADD COLUMN error_message TEXT"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
