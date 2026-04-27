import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = Path("ai_hub.db")

DEFAULT_TENANT_ID = "default"


def _column_names(cursor: sqlite3.Cursor, table: str) -> set[str]:
    return {row["name"] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}


def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}',
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}',
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tenant_id, name)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                content TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE (user_id, project_id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_episodes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                start_message_id INTEGER NOT NULL,
                end_message_id INTEGER NOT NULL,
                source_text TEXT NOT NULL,
                event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_items (
                id TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                subject TEXT,
                predicate TEXT,
                object TEXT,
                content TEXT NOT NULL,
                salience REAL NOT NULL DEFAULT 0,
                valid_from TIMESTAMP,
                valid_to TIMESTAMP,
                last_accessed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (episode_id) REFERENCES memory_episodes (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_consolidations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                source_episode_ids TEXT NOT NULL,
                content TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        messages_cols = _column_names(cursor, "messages")
        if "tenant_id" not in messages_cols:
            cursor.execute(
                f"ALTER TABLE messages ADD COLUMN tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}'"
            )
            logger.info("Migrated messages table: added tenant_id column")
        if "user_id" not in messages_cols:
            cursor.execute("ALTER TABLE messages ADD COLUMN user_id TEXT")
            logger.info("Migrated messages table: added user_id column")
        if "is_summarized" not in messages_cols:
            cursor.execute(
                "ALTER TABLE messages ADD COLUMN is_summarized INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migrated messages table: added is_summarized column")

        sessions_cols = _column_names(cursor, "sessions")
        if "user_id" not in sessions_cols:
            cursor.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
            logger.info("Migrated sessions table: added user_id column")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_tenant_session "
            "ON messages (tenant_id, session_id, id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_unsummarized "
            "ON messages (user_id, is_summarized, id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_user "
            "ON sessions (user_id, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_tenant_name "
            "ON users (tenant_id, name)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_summaries_user_project "
            "ON summaries (user_id, project_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_episodes_user_project_created "
            "ON memory_episodes (user_id, project_id, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_items_user_project_type "
            "ON memory_items (user_id, project_id, memory_type)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_items_project_salience "
            "ON memory_items (project_id, salience)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_consolidations_user_project_created "
            "ON memory_consolidations (user_id, project_id, created_at)"
        )

        conn.commit()
    logger.info("Database initialized at %s", DB_PATH)

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
