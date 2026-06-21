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


def _get_effective_sslmode(url: str) -> str:
    """Parse the sslmode query param out of a psycopg URL.

    Returns "disable" if the param is missing or the URL is unparseable.
    P2.3 fix: we surface this at startup so operators can verify the
    effective security level without having to read the URL by hand.
    """
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        return (params.get("sslmode") or ["disable"])[0]
    except Exception:
        return "disable"


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        # min_size kept small for cold start; max_size bumped from 10 → 20
        # to handle 200 RPM without connection timeouts (2026-06-09 tune).
        # Override via DB_POOL_MAX_SIZE env var if needed.
        import os
        max_size = int(os.environ.get("DB_POOL_MAX_SIZE", "20"))
        url = _get_database_url()
        sslmode = _get_effective_sslmode(url)
        if sslmode == "disable":
            logger.warning(
                "DATABASE_URL has no sslmode param — connection is "
                "UNENCRYPTED. Add ?sslmode=require (dev) or ?sslmode=verify-full (prod)."
            )
        else:
            logger.info("DATABASE_URL sslmode=%s", sslmode)
        _pool = ConnectionPool(
            url,
            min_size=2,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        try:
            with _pool.connection() as conn:
                conn.execute("SELECT 1").fetchone()
            logger.info("PostgreSQL pool warmed up successfully (sslmode=%s)", sslmode)
        except Exception as exc:
            _pool = None
            raise RuntimeError(
                f"Failed to warm up PostgreSQL pool — check DATABASE_URL "
                f"and ensure the server is reachable. Underlying error: {exc}"
            ) from exc
    return _pool


@contextmanager
def get_db_connection(tenant_id: str | None = None):
    """Yield a connection. If ``tenant_id`` is set, the connection's
    ``app.current_tenant`` GUC is bound for the duration of the
    transaction, so any table with RLS enabled will filter rows by
    tenant.

    Without a ``tenant_id`` arg, behavior is unchanged — every
    caller (admin queries, tests, etc.) sees everything. P2.2
    (2026-06-10) — see ``docs/security/rls.md`` for the policy.
    """
    with _get_pool().connection() as conn:
        if tenant_id is not None:
            # SET LOCAL is per-transaction and released on COMMIT/ROLLBACK.
            # We don't issue a COMMIT here — the caller's context
            # manager does that. The ``app.current_tenant`` name is
            # arbitrary; we use the ``app.`` prefix to signal it's
            # not a built-in Postgres GUC.
            #
            # NB: SET LOCAL doesn't accept parameter binding, so we
            # have to interpolate the value. We use psycopg's
            # sql.Identifier-style escaping is not available here;
            # instead we use a strict whitelist (alphanumeric +
            # hyphen/underscore) to defang SQL injection.
            if not all(c.isalnum() or c in "-_." for c in tenant_id):
                raise ValueError(
                    f"invalid tenant_id for SET LOCAL: {tenant_id!r}"
                )
            conn.execute(f"SET LOCAL app.current_tenant = '{tenant_id}'")
        yield conn


# P2.2 (2026-06-10) — list of user-scoped tables that get RLS
# policies. Adding a new tenant-scoped table? Add it here + create
# a matching policy in init_db().
RLS_TABLES: list[str] = [
    "messages",
    "memory_items",
    "memory_episodes",
    "memory_consolidations",
    "memory_boundaries",
    "pinned_memories",
    "summaries",
    "sessions",
    "usage_events",
    "failure_risk_events",
    "prediction_records",
    "fanpage_facts",
    "api_keys",
    "skills",
]


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
        conn.execute(
            "ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS content_hash TEXT"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_items_user_content_hash "
            "ON memory_items (user_id, content_hash)"
        )

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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_links (
                id TEXT PRIMARY KEY,
                source_card_id TEXT NOT NULL,
                target_card_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                score DOUBLE PRECISION NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_card_id) REFERENCES knowledge_cards (id) ON DELETE CASCADE,
                FOREIGN KEY (target_card_id) REFERENCES knowledge_cards (id) ON DELETE CASCADE,
                UNIQUE (source_card_id, target_card_id, relation)
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

        conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                project_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                trigger_patterns_json TEXT NOT NULL DEFAULT '[]',
                prompt_template TEXT NOT NULL DEFAULT '',
                expected_behavior TEXT NOT NULL DEFAULT '',
                test_cases_json TEXT NOT NULL DEFAULT '[]',
                version INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_evaluated_at TIMESTAMP,
                eval_score FLOAT NOT NULL DEFAULT 0.0,
                UNIQUE (tenant_id, project_id, name)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ihi_rag_cases (
                id SERIAL PRIMARY KEY,
                device_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                symptom TEXT NOT NULL DEFAULT '',
                pattern JSONB NOT NULL DEFAULT '{}',
                description TEXT NOT NULL DEFAULT '',
                resolution TEXT,
                confirmed_by TEXT,
                match_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ihi_feedback (
                id SERIAL PRIMARY KEY,
                case_id TEXT NOT NULL,
                feedback TEXT NOT NULL,
                rating INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS ihi_rollups (
                id TEXT PRIMARY KEY,
                window_start TIMESTAMP NOT NULL,
                window_end TIMESTAMP NOT NULL,
                summary TEXT NOT NULL,
                model TEXT NOT NULL,
                source_window_count INTEGER NOT NULL DEFAULT 0,
                source_token_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Orders + return requests (added 2026-06-13 for e-commerce test)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                order_code TEXT UNIQUE NOT NULL,
                product_name TEXT NOT NULL,
                size TEXT,
                color TEXT,
                price INTEGER,
                purchase_date TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS return_requests (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                product_serial TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
                resolved_at TIMESTAMP,
                resolution_note TEXT
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
            "CREATE INDEX IF NOT EXISTS idx_knowledge_links_source ON knowledge_links (source_card_id, score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_links_target ON knowledge_links (target_card_id)",
            "CREATE INDEX IF NOT EXISTS idx_fanpage_facts_user_project ON fanpage_facts (user_id, project_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_fanpage_facts_confidence ON fanpage_facts (project_id, confidence DESC)",
            "CREATE INDEX IF NOT EXISTS idx_skills_tenant_project_active ON skills (tenant_id, project_id, is_active)",
            "CREATE INDEX IF NOT EXISTS idx_skills_eval_score ON skills (eval_score DESC)",
            "CREATE INDEX IF NOT EXISTS idx_ihi_rag_cases_severity ON ihi_rag_cases (severity DESC, match_count DESC)",
            "CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(tenant_id, user_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_code ON orders(order_code)",
            "CREATE INDEX IF NOT EXISTS idx_returns_order ON return_requests(tenant_id, order_id)",
        ]:
            conn.execute(stmt)

        # P1.2: A2A audit log. Every JSON-RPC call lands a row here so
        # operators can reconstruct the full call history for a tenant
        # (compliance + forensic). Kept separate from usage_events so
        # the schema for the two remains clean.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS a2a_audit_log (
                id BIGSERIAL PRIMARY KEY,
                ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                tenant_id TEXT,
                api_key_id TEXT,
                rpc_method TEXT NOT NULL,
                request_id TEXT,
                task_id TEXT,
                status_code INT NOT NULL,
                latency_ms INT NOT NULL,
                err_id TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_a2a_audit_tenant_ts ON a2a_audit_log (tenant_id, ts DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_a2a_audit_method_ts ON a2a_audit_log (rpc_method, ts DESC)"
        )

        conn.commit()

        # Add is_admin column if it doesn't exist
        if not _column_exists(conn, "api_keys", "is_admin"):
            logger.info("Adding is_admin column to api_keys")
            conn.execute("ALTER TABLE api_keys ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
            conn.commit()

        # P2.4 (2026-06-10) — secret rotation policy. Track when each
        # API key was last rotated so the scheduler can warn operators
        # before the rotation deadline passes. Default NULL means
        # "never rotated" → effectively (now - created_at) days old.
        if not _column_exists(conn, "api_keys", "last_rotated_at"):
            logger.info("Adding last_rotated_at column to api_keys")
            conn.execute(
                "ALTER TABLE api_keys ADD COLUMN last_rotated_at TIMESTAMP"
            )
            conn.commit()
        # Backfill: for rows that pre-date the migration, treat
        # last_rotated_at as the row's created_at (so existing keys
        # start their rotation clock from when they were created,
        # not from today).
        conn.execute(
            "UPDATE api_keys SET last_rotated_at = created_at "
            "WHERE last_rotated_at IS NULL"
        )
        conn.commit()

        # P2.6 (2026-06-10) — GDPR data export + erasure.
        # gdpr_delete_requested_at: when the user (or admin) requested
        #   the deletion. NULL = no pending request.
        # gdpr_delete_scheduled_for: when the hard-delete will run.
        #   Set to requested_at + 30 days by default; admin can
        #   cancel any time before this fires.
        # gdpr_deleted_at: when the hard-delete actually ran. Once
        #   set, the user_id can never be referenced again (foreign
        #   keys are no longer meaningful because the row is gone).
        if not _column_exists(conn, "users", "gdpr_delete_requested_at"):
            logger.info("Adding gdpr columns to users")
            conn.execute(
                "ALTER TABLE users ADD COLUMN gdpr_delete_requested_at TIMESTAMP"
            )
            conn.commit()
        if not _column_exists(conn, "users", "gdpr_delete_scheduled_for"):
            conn.execute(
                "ALTER TABLE users ADD COLUMN gdpr_delete_scheduled_for TIMESTAMP"
            )
            conn.commit()
        if not _column_exists(conn, "users", "gdpr_deleted_at"):
            conn.execute(
                "ALTER TABLE users ADD COLUMN gdpr_deleted_at TIMESTAMP"
            )
            conn.commit()

        if not _column_exists(conn, "knowledge_cards", "linked_card_ids"):
            logger.info("Adding linked_card_ids column to knowledge_cards")
            conn.execute("ALTER TABLE knowledge_cards ADD COLUMN linked_card_ids TEXT NOT NULL DEFAULT '[]'")
            conn.commit()

        if not _column_exists(conn, "knowledge_card_chunks", "content_tsv"):
            logger.info("Adding content_tsv column to knowledge_card_chunks")
            conn.execute("ALTER TABLE knowledge_card_chunks ADD COLUMN content_tsv tsvector")
            conn.execute("UPDATE knowledge_card_chunks SET content_tsv = to_tsvector('simple', COALESCE(content, '')) WHERE content_tsv IS NULL")
            conn.commit()

        # Anthropic Contextual Retrieval (2026-06-19): LLM-generated context.
        # `contextual_text` is the full text that gets embedded AND indexed
        # in the FTS tsvector. The trigger below prefers it over raw `content`
        # so lexical search (BM25) benefits from the semantic framing.
        # Existing rows (pre-migration) leave contextual_text NULL → the
        # COALESCE in the trigger falls back to raw content, preserving
        # their current FTS index until the reindex job backfills.
        if not _column_exists(conn, "knowledge_card_chunks", "contextual_text"):
            logger.info("Adding contextual_text column to knowledge_card_chunks")
            conn.execute("ALTER TABLE knowledge_card_chunks ADD COLUMN contextual_text TEXT NOT NULL DEFAULT ''")
            conn.commit()
        if not _column_exists(conn, "knowledge_card_chunks", "contextual_model_version"):
            logger.info("Adding contextual_model_version column to knowledge_card_chunks")
            conn.execute("ALTER TABLE knowledge_card_chunks ADD COLUMN contextual_model_version TEXT NOT NULL DEFAULT ''")
            conn.commit()

        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_content_tsv ON knowledge_card_chunks USING gin(content_tsv)")
            conn.execute("""
                CREATE OR REPLACE FUNCTION update_chunk_tsv() RETURNS trigger AS $$
                BEGIN
                  -- Prefer LLM-generated contextual_text when set; otherwise
                  -- fall back to raw chunk content (existing rows, ingestion
                  -- with contextualizer disabled).
                  NEW.content_tsv := to_tsvector('simple', COALESCE(NULLIF(NEW.contextual_text, ''), NEW.content, ''));
                  RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
            """)
            conn.execute("DROP TRIGGER IF EXISTS trg_chunk_tsv ON knowledge_card_chunks")
            conn.execute("""
                CREATE TRIGGER trg_chunk_tsv
                BEFORE INSERT OR UPDATE OF content, contextual_text ON knowledge_card_chunks
                FOR EACH ROW EXECUTE FUNCTION update_chunk_tsv()
            """)
            conn.commit()
        except Exception as exc:
            logger.warning("Could not initialize knowledge FTS support: %s", exc)
            conn.rollback()

        try:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
            if not _column_exists(conn, "knowledge_card_chunks", "embedding_vec"):
                logger.info("Adding embedding_vec column to knowledge_card_chunks")
                conn.execute("ALTER TABLE knowledge_card_chunks ADD COLUMN embedding_vec vector(384)")
                conn.commit()
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON knowledge_card_chunks USING hnsw (embedding_vec vector_cosine_ops) WITH (m = 16, ef_construction = 200)")
            conn.commit()
        except Exception as exc:
            logger.warning(
                "Could not initialize pgvector support — vector extension unavailable. "
                "RAG will fall back to token-overlap search. Error: %s",
                exc,
            )
            conn.rollback()

    # P2.2 (2026-06-10) — enable Row Level Security on tenant-scoped
    # tables. Each table gets a policy that lets rows through only
    # when ``current_setting('app.current_tenant', true)`` matches
    # the row's tenant_id. The second arg ``true`` to current_setting
    # makes it return NULL (not raise) if the GUC isn't set — so
    # superusers and code paths that forget to bind the GUC see
    # zero rows instead of the whole table.
    try:
        with get_db_connection() as _rls_conn:
            for _table in RLS_TABLES:
                # Idempotent: ENABLE ROW LEVEL SECURITY is a no-op
                # if already enabled.
                _rls_conn.execute(f"ALTER TABLE {_table} ENABLE ROW LEVEL SECURITY")
                # CREATE POLICY IF NOT EXISTS isn't a thing in PG
                # pre-15, so we use DO $$ BEGIN ... EXCEPTION ...
                # to skip the duplicate-policy error. PG 15+ has
                # CREATE POLICY ... IF NOT EXISTS.
                _rls_conn.execute(
                    f"""
                    DO $$
                    BEGIN
                        CREATE POLICY {_table}_tenant_isolation ON {_table}
                            USING (tenant_id = current_setting('app.current_tenant', true));
                    EXCEPTION
                        WHEN duplicate_object THEN NULL;
                    END $$;
                    """
                )
            _rls_conn.commit()
            logger.info("Row Level Security enabled on %d tables", len(RLS_TABLES))
    except Exception as exc:
        logger.warning("RLS enablement failed (RLS not active): %s", exc)

    logger.info("Database initialized (PostgreSQL)")
