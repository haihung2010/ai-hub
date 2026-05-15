"""Comprehensive tests for MemoryExtractionService — edge cases and DB integration."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.database import get_db_connection
from app.models.chat import Message
from app.services.memory_extraction_service import MemoryExtractionService


class TestBuildSourceText:
    def test_empty_messages(self):
        svc = MemoryExtractionService()
        result = svc._build_source_text([])
        assert result == ""

    def test_single_message(self):
        svc = MemoryExtractionService()
        msgs = [(1, Message(role="user", content="hello"))]
        result = svc._build_source_text(msgs)
        assert result == "user: hello"

    def test_multi_message_preserves_order(self):
        svc = MemoryExtractionService()
        msgs = [
            (1, Message(role="user", content="hi")),
            (2, Message(role="assistant", content="hello")),
            (3, Message(role="user", content="bye")),
        ]
        result = svc._build_source_text(msgs)
        lines = result.split("\n")
        assert len(lines) == 3
        assert lines[0] == "user: hi"
        assert lines[1] == "assistant: hello"
        assert lines[2] == "user: bye"

    def test_multiline_content(self):
        svc = MemoryExtractionService()
        msgs = [(1, Message(role="user", content="line1\nline2\nline3"))]
        result = svc._build_source_text(msgs)
        assert "line1\nline2\nline3" in result


class TestBuildPrompt:
    def test_returns_two_messages(self):
        svc = MemoryExtractionService()
        msgs = [(1, Message(role="user", content="test"))]
        prompt = svc._build_prompt(msgs)
        assert len(prompt) == 2
        assert prompt[0].role == "system"
        assert prompt[1].role == "user"

    def test_system_message_contains_extraction_instructions(self):
        svc = MemoryExtractionService()
        msgs = [(1, Message(role="user", content="test"))]
        prompt = svc._build_prompt(msgs)
        assert "JSON" in prompt[0].content
        assert "episodic" in prompt[0].content


class TestParsePayload:
    def test_valid_full_json(self):
        svc = MemoryExtractionService()
        payload = json.dumps({
            "episodic": [{"content": "e1", "salience": 0.8}],
            "semantic": [{"content": "s1", "salience": 0.9}],
            "relational": [],
            "procedural": [{"content": "p1"}],
        })
        result = svc._parse_payload(payload)
        assert len(result["episodic"]) == 1
        assert len(result["semantic"]) == 1
        assert len(result["relational"]) == 0
        assert len(result["procedural"]) == 1

    def test_invalid_json_returns_empty(self):
        svc = MemoryExtractionService()
        result = svc._parse_payload("not json at all {{{")
        assert result == {"episodic": [], "semantic": [], "relational": [], "procedural": []}

    def test_missing_keys_default_empty(self):
        svc = MemoryExtractionService()
        result = svc._parse_payload('{"episodic": [{"content": "x"}]}')
        assert len(result["episodic"]) == 1
        assert result["semantic"] == []
        assert result["relational"] == []
        assert result["procedural"] == []

    def test_non_list_values_replaced_with_empty(self):
        svc = MemoryExtractionService()
        result = svc._parse_payload('{"episodic": "not a list", "semantic": 42}')
        assert result["episodic"] == []
        assert result["semantic"] == []

    def test_empty_json_object(self):
        svc = MemoryExtractionService()
        result = svc._parse_payload("{}")
        for key in ("episodic", "semantic", "relational", "procedural"):
            assert result[key] == []

    def test_extra_keys_ignored(self):
        svc = MemoryExtractionService()
        result = svc._parse_payload('{"episodic": [], "unknown_key": [1,2,3]}')
        assert "unknown_key" not in result


@pytest.mark.unit
class TestInsertEpisode:
    def test_insert_and_retrieve_episode(self):
        from tests.conftest import ensure_user
        ensure_user("ext_user_1")
        with get_db_connection() as conn:
            conn.execute("INSERT INTO sessions (id, tenant_id, project_id, user_id) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", ("sess_1", "default", "test", "ext_user_1"))
            conn.commit()
        svc = MemoryExtractionService()
        episode_id = svc._insert_episode(
            user_id="ext_user_1",
            tenant_id="default",
            project_id="test",
            session_id="sess_1",
            start_message_id=1,
            end_message_id=5,
            source_text="user: hello\nassistant: hi",
        )
        assert episode_id is not None
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT * FROM memory_episodes WHERE id = %s", (episode_id,)
            ).fetchone()
            assert row is not None
            assert row["user_id"] == "ext_user_1"


@pytest.mark.unit
class TestInsertMemoryItems:
    def test_inserts_valid_items_skips_empty(self):
        from tests.conftest import ensure_user
        ensure_user("ext_user_2")
        with get_db_connection() as conn:
            conn.execute("INSERT INTO sessions (id, tenant_id, project_id, user_id) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", ("sess_2", "default", "test", "ext_user_2"))
            conn.commit()
        svc = MemoryExtractionService()
        episode_id = svc._insert_episode(
            user_id="ext_user_2",
            tenant_id="default",
            project_id="test",
            session_id="sess_2",
            start_message_id=1,
            end_message_id=3,
            source_text="test",
        )
        extracted = {
            "episodic": [
                {"content": "valid item", "subject": "user", "predicate": "likes", "object": "coffee", "salience": 0.7},
                {"content": "", "subject": "x"},
            ],
            "semantic": [
                {"content": "fact: water boils at 100C"},
            ],
            "relational": [],
            "procedural": [],
        }
        svc._insert_memory_items(
            episode_id=episode_id,
            user_id="ext_user_2",
            tenant_id="default",
            project_id="test",
            extracted=extracted,
        )
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_items WHERE episode_id = %s", (episode_id,)
            ).fetchall()
            assert len(rows) == 2


class TestExtractAndStore:
    @pytest.mark.asyncio
    async def test_returns_none_for_empty_messages(self):
        svc = MemoryExtractionService()
        provider = AsyncMock()
        result = await svc.extract_and_store(
            user_id="u1", project_id="p1", session_id="s1",
            messages=[], provider=provider, model="test",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_full_extraction_flow(self):
        from tests.conftest import ensure_user
        ensure_user("ext_user_3")
        with get_db_connection() as conn:
            conn.execute("INSERT INTO sessions (id, tenant_id, project_id, user_id) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", ("sess_3", "default", "test", "ext_user_3"))
            conn.commit()
        svc = MemoryExtractionService()
        provider = AsyncMock()
        provider.complete = AsyncMock(return_value=json.dumps({
            "episodic": [{"content": "user asked about weather", "salience": 0.6}],
            "semantic": [],
            "relational": [],
            "procedural": [],
        }))
        msgs = [
            (10, Message(role="user", content="what's the weather?")),
            (11, Message(role="assistant", content="it's sunny")),
        ]
        result = await svc.extract_and_store(
            user_id="ext_user_3",
            tenant_id="default",
            project_id="test",
            session_id="sess_3",
            messages=msgs,
            provider=provider,
            model="test-model",
        )
        assert result is not None
        provider.complete.assert_called_once()
