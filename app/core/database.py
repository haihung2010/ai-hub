import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("DATABASE_PATH", "ai_hub.db"))

DEFAULT_TENANT_ID = "default"


def _column_names(cursor: sqlite3.Cursor, table: str) -> set[str]:
    return {row["name"] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}


def _has_unique_index(cursor: sqlite3.Cursor, table: str, columns: tuple[str, ...]) -> bool:
    for index in cursor.execute(f"PRAGMA index_list({table})").fetchall():
        if not index["unique"]:
            continue
        indexed_columns = tuple(
            row["name"]
            for row in cursor.execute(f"PRAGMA index_info({index['name']})").fetchall()
        )
        if indexed_columns == columns:
            return True
    return False


def _rebuild_summaries_table(cursor: sqlite3.Cursor) -> None:
    cursor.execute("ALTER TABLE summaries RENAME TO summaries_old")
    cursor.execute("""
        CREATE TABLE summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            user_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            content TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            UNIQUE (tenant_id, user_id, project_id)
        )
    """)
    cursor.execute(f"""
        INSERT OR IGNORE INTO summaries (
            id, tenant_id, user_id, project_id, content, version, updated_at
        )
        SELECT
            id,
            COALESCE(tenant_id, '{DEFAULT_TENANT_ID}'),
            user_id,
            project_id,
            content,
            version,
            updated_at
        FROM summaries_old
        ORDER BY updated_at DESC, id DESC
    """)
    cursor.execute("DROP TABLE summaries_old")


def init_db():
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
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
                tenant_id TEXT NOT NULL DEFAULT 'default',
                user_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                content TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE (tenant_id, user_id, project_id)
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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pinned_memories (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'user',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                source_session_id TEXT,
                source_message_id INTEGER,
                confidence REAL NOT NULL DEFAULT 1.0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE (tenant_id, project_id, user_id, key)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_records (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT NOT NULL,
                assistant_message_id INTEGER,
                symbol TEXT,
                horizon TEXT,
                prediction_text TEXT NOT NULL,
                confidence TEXT,
                inputs_json TEXT NOT NULL DEFAULT '{}',
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                actual_outcome TEXT,
                evaluated_at TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                owner_user_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                allowed_projects_json TEXT NOT NULL DEFAULT '[]',
                denied_projects_json TEXT NOT NULL DEFAULT '[]',
                allowed_models_json TEXT NOT NULL DEFAULT '[]',
                allow_external INTEGER NOT NULL DEFAULT 0,
                rpm_limit INTEGER NOT NULL DEFAULT 60,
                max_parallel_requests INTEGER NOT NULL DEFAULT 2,
                monthly_budget_usd REAL,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_user_id) REFERENCES users (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                api_key_id TEXT,
                user_id TEXT,
                project_id TEXT NOT NULL,
                session_id TEXT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                route_alias TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cost_usd REAL,
                latency_ms REAL NOT NULL,
                status_code INTEGER,
                error_type TEXT,
                fallback_used INTEGER NOT NULL DEFAULT 0,
                queue_wait_ms REAL,
                route_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (api_key_id) REFERENCES api_keys (id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS failure_risk_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT,
                risk_score REAL NOT NULL,
                risk_level TEXT NOT NULL,
                risk_types_json TEXT NOT NULL DEFAULT '[]',
                reasons_json TEXT NOT NULL DEFAULT '[]',
                recommended_action TEXT NOT NULL,
                applied_action TEXT NOT NULL,
                action_applied INTEGER NOT NULL DEFAULT 0,
                route_before TEXT,
                route_after TEXT,
                model_before TEXT,
                model_after TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (session_id) REFERENCES sessions (id)
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
        if "tenant_id" not in sessions_cols:
            cursor.execute(
                f"ALTER TABLE sessions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}'"
            )
            logger.info("Migrated sessions table: added tenant_id column")
        if "user_id" not in sessions_cols:
            cursor.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT")
            logger.info("Migrated sessions table: added user_id column")

        usage_cols = _column_names(cursor, "usage_events")
        if "queue_wait_ms" not in usage_cols:
            cursor.execute("ALTER TABLE usage_events ADD COLUMN queue_wait_ms REAL")
            logger.info("Migrated usage_events table: added queue_wait_ms column")
        if "route_reason" not in usage_cols:
            cursor.execute("ALTER TABLE usage_events ADD COLUMN route_reason TEXT")
            logger.info("Migrated usage_events table: added route_reason column")

        summaries_cols = _column_names(cursor, "summaries")
        if "tenant_id" not in summaries_cols:
            cursor.execute(
                f"ALTER TABLE summaries ADD COLUMN tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}'"
            )
            logger.info("Migrated summaries table: added tenant_id column")
        if not _has_unique_index(
            cursor, "summaries", ("tenant_id", "user_id", "project_id")
        ):
            _rebuild_summaries_table(cursor)
            logger.info("Migrated summaries table: rebuilt tenant-scoped unique constraint")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_tenant_session "
            "ON messages (tenant_id, session_id, id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_user_unsummarized "
            "ON messages (user_id, is_summarized, id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_tenant_user_project "
            "ON sessions (tenant_id, user_id, project_id, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_tenant_name "
            "ON users (tenant_id, name)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_summaries_tenant_user_project "
            "ON summaries (tenant_id, user_id, project_id)"
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
            "CREATE INDEX IF NOT EXISTS idx_pinned_memories_lookup "
            "ON pinned_memories (tenant_id, project_id, user_id, is_active, updated_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_consolidations_user_project_created "
            "ON memory_consolidations (user_id, project_id, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_prediction_records_tenant_project_user_symbol "
            "ON prediction_records (tenant_id, project_id, user_id, symbol, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_api_keys_hash_enabled "
            "ON api_keys (key_hash, enabled)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_tenant_created "
            "ON usage_events (tenant_id, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_events_provider_model_created "
            "ON usage_events (provider, model, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_failure_risk_events_tenant_created "
            "ON failure_risk_events (tenant_id, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_failure_risk_events_project_level "
            "ON failure_risk_events (project_id, risk_level, created_at)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_buckets (
                key TEXT PRIMARY KEY,
                timestamps_json TEXT NOT NULL DEFAULT '[]',
                updated_at REAL NOT NULL DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auth_failures (
                key TEXT PRIMARY KEY,
                failures_json TEXT NOT NULL DEFAULT '[]',
                blocked_until REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0
            )
        """)

        conn.commit()
    logger.info("Database initialized at %s", DB_PATH)

@contextmanager
def get_db_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
