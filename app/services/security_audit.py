"""PG audit writer for rate-limit + auth-failure events.

These tables (`rate_limit_buckets`, `auth_failures`) already exist in the
schema. They were created precisely to give us a durable audit trail
alongside the volatile Redis ZSETs and in-process deques. Until this
module shipped (2026-06-06, health-check Rank 7), they were empty because
nothing ever wrote to them.

Design constraints (per Rank-7 spec):
  - Never block the request path. Writes are dispatched to a small
    ThreadPoolExecutor and any error is logged + swallowed.
  - Do not change Redis behavior. This writer is purely additive.
  - Schema is fixed. We UPSERT (key is the PK) so repeated denials
    refresh the snapshot with the latest state.
  - Best-effort. PG outage must not 5xx the request — the primary
    rate-limit + auth-failure path still works via Redis.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

logger = logging.getLogger("app.security_audit")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sec-audit")
_enabled: bool = True
_lock = threading.Lock()


def _resolve_enabled() -> bool:
    """Read the runtime config flag. Falls back to True if config can't load."""
    try:
        from app.core.config import get_settings

        return bool(get_settings().security_pg_audit_enabled)
    except Exception:  # pragma: no cover - early import / missing env
        return True


def set_enabled(enabled: bool) -> None:
    """Test/admin hook to toggle the writer at runtime."""
    global _enabled
    with _lock:
        _enabled = enabled


def is_enabled() -> bool:
    with _lock:
        if not _enabled:
            return False
    # Layer 2: respect the config flag (test can still override above).
    return _resolve_enabled()


def _submit(coro_target) -> None:
    """Fire-and-forget submission. Catches all exceptions."""
    if not is_enabled():
        return
    try:
        _executor.submit(coro_target)
    except RuntimeError:
        # Executor was shut down (process exit). Drop silently.
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("security_audit: failed to submit task: %s", exc)


def _do_rate_limit_write(key: str, timestamps: Iterable[float], updated_at: float) -> None:
    try:
        from app.core.database import get_db_connection

        ts_json = json.dumps(list(timestamps))
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO rate_limit_buckets (key, timestamps_json, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                    SET timestamps_json = EXCLUDED.timestamps_json,
                        updated_at = EXCLUDED.updated_at
                """,
                (key, ts_json, updated_at),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - background thread
        logger.warning(
            "security_audit: rate_limit_buckets write failed for key=%s: %s",
            key,
            exc,
        )


def _do_auth_failure_write(
    key: str, failures: Iterable[float], blocked_until: float, updated_at: float
) -> None:
    try:
        from app.core.database import get_db_connection

        failures_json = json.dumps(list(failures))
        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO auth_failures (key, failures_json, blocked_until, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (key) DO UPDATE
                    SET failures_json = EXCLUDED.failures_json,
                        blocked_until = EXCLUDED.blocked_until,
                        updated_at = EXCLUDED.updated_at
                """,
                (key, failures_json, blocked_until, updated_at),
            )
            conn.commit()
    except Exception as exc:  # pragma: no cover - background thread
        logger.warning(
            "security_audit: auth_failures write failed for key=%s: %s",
            key,
            exc,
        )


def record_rate_limit(
    key: str,
    timestamps: Iterable[float] | None = None,
    now: float | None = None,
) -> None:
    """Snapshot a rate-limit denial into PG (fire-and-forget)."""
    current = now if now is not None else time.time()
    ts = list(timestamps) if timestamps is not None else [current]
    _submit(lambda: _do_rate_limit_write(key, ts, current))


def record_auth_failure(
    key: str,
    failures: Iterable[float] | None = None,
    blocked_until: float | None = None,
    now: float | None = None,
) -> None:
    """Snapshot an auth-failure event into PG (fire-and-forget)."""
    current = now if now is not None else time.time()
    fs = list(failures) if failures is not None else [current]
    bu = 0.0 if blocked_until is None else float(blocked_until)
    _submit(lambda: _do_auth_failure_write(key, fs, bu, current))


def shutdown(wait: bool = False) -> None:
    """Drain the executor on shutdown (call from FastAPI lifespan)."""
    try:
        _executor.shutdown(wait=wait, cancel_futures=not wait)
    except Exception:  # pragma: no cover
        pass
