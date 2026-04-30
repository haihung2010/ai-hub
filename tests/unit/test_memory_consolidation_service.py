"""Unit tests for MemoryConsolidationService."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.memory_consolidation_service import MemoryConsolidationService


class _FakeProvider:
    name = "llama_cpp"

    def __init__(self, response: str = "- fact one\n- fact two"):
        self._response = response

    async def complete(self, messages, model, temperature, options=None):
        return self._response


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Each test gets its own SQLite file."""
    import app.core.database as db_module

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()


def _insert_user(user_id: str = "u1", tenant_id: str = "default", name: str = "alice"):
    from app.core.database import get_db_connection
    with get_db_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, tenant_id, name) VALUES (?, ?, ?)",
            (user_id, tenant_id, name),
        )
        conn.commit()


def _insert_episode(episode_id: str, user_id: str = "u1", tenant_id: str = "default", project_id: str = "test"):
    from app.core.database import get_db_connection
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO memory_episodes (id, user_id, tenant_id, project_id, session_id, start_message_id, end_message_id, source_text) "
            "VALUES (?, ?, ?, ?, 'sess1', 1, 2, 'some text')",
            (episode_id, user_id, tenant_id, project_id),
        )
        conn.commit()


def _insert_item(item_id: str, episode_id: str, memory_type: str = "semantic", user_id: str = "u1", tenant_id: str = "default", project_id: str = "test"):
    from app.core.database import get_db_connection
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO memory_items (id, episode_id, user_id, tenant_id, project_id, memory_type, content, salience) "
            "VALUES (?, ?, ?, ?, ?, ?, 'user likes Python', 0.8)",
            (item_id, episode_id, user_id, tenant_id, project_id, memory_type),
        )
        conn.commit()


@pytest.mark.asyncio
async def test_consolidate_returns_none_when_below_min_items():
    _insert_user()
    svc = MemoryConsolidationService()
    result = await svc.consolidate(
        user_id="u1", tenant_id="default", project_id="test",
        provider=_FakeProvider(), model="test-model", min_items=5,
    )
    assert result is None


@pytest.mark.asyncio
async def test_consolidate_writes_record():
    from app.core.database import get_db_connection

    _insert_user()
    _insert_episode("ep1")
    for i in range(5):
        _insert_item(f"item{i}", "ep1")

    svc = MemoryConsolidationService()
    record_id = await svc.consolidate(
        user_id="u1", tenant_id="default", project_id="test",
        provider=_FakeProvider("- fact one\n- fact two"), model="test-model", min_items=5,
    )
    assert record_id is not None

    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM memory_consolidations WHERE id = ?", (record_id,)
        ).fetchone()
    assert row is not None
    assert "fact one" in row["content"]
    assert row["version"] == 1
    assert row["scope_key"] == "default:test"


@pytest.mark.asyncio
async def test_consolidate_increments_version_on_second_run():
    from app.core.database import get_db_connection

    _insert_user()
    _insert_episode("ep1")
    for i in range(5):
        _insert_item(f"item{i}", "ep1")

    svc = MemoryConsolidationService()
    await svc.consolidate(
        user_id="u1", tenant_id="default", project_id="test",
        provider=_FakeProvider(), model="test-model", min_items=5,
    )
    await svc.consolidate(
        user_id="u1", tenant_id="default", project_id="test",
        provider=_FakeProvider("- updated fact"), model="test-model", min_items=5,
    )

    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT version, content FROM memory_consolidations WHERE user_id = 'u1' AND project_id = 'test'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["version"] == 2
    assert "updated fact" in rows[0]["content"]


@pytest.mark.asyncio
async def test_consolidate_returns_none_on_provider_error():
    _insert_user()
    _insert_episode("ep1")
    for i in range(5):
        _insert_item(f"item{i}", "ep1")

    class _ErrorProvider:
        name = "llama_cpp"
        async def complete(self, *a, **kw):
            raise RuntimeError("boom")

    svc = MemoryConsolidationService()
    result = await svc.consolidate(
        user_id="u1", tenant_id="default", project_id="test",
        provider=_ErrorProvider(), model="test-model", min_items=5,
    )
    assert result is None
