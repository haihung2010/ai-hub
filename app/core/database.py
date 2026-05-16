import logging
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"

_pool: ConnectionPool | None = None


def _get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            _get_database_url(),
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


@contextmanager
def get_db_connection():
    with _get_pool().connection() as conn:
        yield conn


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()
    return row is not None


def init_db() -> None:
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                project_id TEXT NOT NULL,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}',
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                user_id TEXT,
                is_summarized INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        """)

        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}',
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (tenant_id, name)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_boundaries (
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                boundary_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (tenant_id, user_id, project_id),
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS summaries (
                id BIGSERIAL PRIMARY KEY,
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_episodes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                start_message_id BIGINT NOT NULL,
                end_message_id BIGINT NOT NULL,
                source_text TEXT NOT NULL,
                event_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        """)

        conn.execute("""
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
                salience DOUBLE PRECISION NOT NULL DEFAULT 0,
                valid_from TIMESTAMP,
                valid_to TIMESTAMP,
                last_accessed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (episode_id) REFERENCES memory_episodes (id),
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        """)

        conn.execute("""
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS pinned_memories (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'user',
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                source_session_id TEXT,
                source_message_id BIGINT,
                confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                UNIQUE (tenant_id, project_id, user_id, key)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS prediction_records (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT NOT NULL,
                assistant_message_id BIGINT,
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

        conn.execute("""
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
                monthly_budget_usd DOUBLE PRECISION,
                expires_at TIMESTAMP,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (owner_user_id) REFERENCES users (id)
            )
        """)

        conn.execute("""
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
                cost_usd DOUBLE PRECISION,
                latency_ms DOUBLE PRECISION NOT NULL,
                status_code INTEGER,
                error_type TEXT,
                fallback_used INTEGER NOT NULL DEFAULT 0,
                queue_wait_ms DOUBLE PRECISION,
                route_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (api_key_id) REFERENCES api_keys (id),
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS failure_risk_events (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                user_id TEXT,
                session_id TEXT,
                risk_score DOUBLE PRECISION NOT NULL,
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_cards (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                project_id TEXT NOT NULL,
                knowledge_domain TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                source_type TEXT NOT NULL,
                trust_level INTEGER NOT NULL DEFAULT 3,
                status TEXT NOT NULL DEFAULT 'active',
                version INTEGER NOT NULL DEFAULT 1,
                effective_from TIMESTAMP,
                effective_to TIMESTAMP,
                tags TEXT NOT NULL DEFAULT '[]',
                owner TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_card_chunks (
                id TEXT PRIMARY KEY,
                card_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                project_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                token_estimate INTEGER NOT NULL DEFAULT 0,
                embedding BYTEA,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (card_id) REFERENCES knowledge_cards (id) ON DELETE CASCADE,
                UNIQUE (card_id, chunk_index)
            )
        """)

        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS fanpage_facts (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT '{DEFAULT_TENANT_ID}',
                user_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                fact TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'other',
                confidence FLOAT NOT NULL DEFAULT 0.5,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id),
                FOREIGN KEY (session_id) REFERENCES sessions (id)
            )
        """)

        # Create indexes
        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_messages_tenant_session ON messages (tenant_id, session_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_messages_user_unsummarized ON messages (user_id, is_summarized, id)",
            "CREATE INDEX IF NOT EXISTS idx_sessions_tenant_user_project ON sessions (tenant_id, user_id, project_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_users_tenant_name ON users (tenant_id, name)",
            "CREATE INDEX IF NOT EXISTS idx_summaries_tenant_user_project ON summaries (tenant_id, user_id, project_id)",
            "CREATE INDEX IF NOT EXISTS idx_memory_episodes_user_project_created ON memory_episodes (user_id, project_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_memory_items_user_project_type ON memory_items (user_id, project_id, memory_type)",
            "CREATE INDEX IF NOT EXISTS idx_memory_items_project_salience ON memory_items (project_id, salience)",
            "CREATE INDEX IF NOT EXISTS idx_pinned_memories_lookup ON pinned_memories (tenant_id, project_id, user_id, is_active, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_memory_consolidations_user_project_created ON memory_consolidations (user_id, project_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_prediction_records_tenant_project_user_symbol ON prediction_records (tenant_id, project_id, user_id, symbol, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_api_keys_hash_enabled ON api_keys (key_hash, enabled)",
            "CREATE INDEX IF NOT EXISTS idx_usage_events_tenant_created ON usage_events (tenant_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_usage_events_provider_model_created ON usage_events (provider, model, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_failure_risk_events_tenant_created ON failure_risk_events (tenant_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_failure_risk_events_project_level ON failure_risk_events (project_id, risk_level, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_cards_scope ON knowledge_cards (tenant_id, project_id, status, knowledge_domain, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_cards_project_status ON knowledge_cards (project_id, status, trust_level)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_scope ON knowledge_card_chunks (tenant_id, project_id, card_id, chunk_index)",
            "CREATE INDEX IF NOT EXISTS idx_fanpage_facts_user_project ON fanpage_facts (user_id, project_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_fanpage_facts_confidence ON fanpage_facts (project_id, confidence DESC)",
        ]:
            conn.execute(stmt)

        conn.commit()

        # Add is_admin column if it doesn't exist
        if not _column_exists(conn, "api_keys", "is_admin"):
            logger.info("Adding is_admin column to api_keys")
            conn.execute("ALTER TABLE api_keys ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            conn.commit()

    logger.info("Database initialized (PostgreSQL)")
