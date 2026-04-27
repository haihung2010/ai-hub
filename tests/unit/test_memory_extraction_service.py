"""Unit tests for MemoryExtractionService pure methods (no DB)."""

from __future__ import annotations

import json

import pytest

from app.models.chat import Message
from app.services.memory_extraction_service import MemoryExtractionService


@pytest.fixture
def svc() -> MemoryExtractionService:
    return MemoryExtractionService()


MessagePair = tuple[int, Message]


def make_pair(msg_id: int, role: str, content: str) -> MessagePair:
    return (msg_id, Message(role=role, content=content))


class TestBuildSourceText:
    def test_concatenates_role_and_content(self, svc: MemoryExtractionService) -> None:
        pairs = [
            make_pair(1, "user", "Hello"),
            make_pair(2, "assistant", "Hi there"),
        ]
        text = svc._build_source_text(pairs)
        assert "user: Hello" in text
        assert "assistant: Hi there" in text

    def test_empty_messages_returns_empty_string(self, svc: MemoryExtractionService) -> None:
        assert svc._build_source_text([]) == ""

    def test_single_message(self, svc: MemoryExtractionService) -> None:
        pairs = [make_pair(1, "user", "Only one")]
        assert svc._build_source_text(pairs) == "user: Only one"


class TestBuildPrompt:
    def test_returns_two_messages(self, svc: MemoryExtractionService) -> None:
        pairs = [make_pair(1, "user", "Remember me")]
        prompt = svc._build_prompt(pairs)
        assert len(prompt) == 2

    def test_first_message_is_system(self, svc: MemoryExtractionService) -> None:
        pairs = [make_pair(1, "user", "x")]
        assert svc._build_prompt(pairs)[0].role == "system"

    def test_second_message_is_user(self, svc: MemoryExtractionService) -> None:
        pairs = [make_pair(1, "user", "x")]
        assert svc._build_prompt(pairs)[1].role == "user"

    def test_user_content_contains_source(self, svc: MemoryExtractionService) -> None:
        pairs = [make_pair(1, "user", "My name is Alice")]
        assert "Alice" in svc._build_prompt(pairs)[1].content


class TestParsePayload:
    def test_valid_json_all_keys(self, svc: MemoryExtractionService) -> None:
        payload = json.dumps({
            "episodic": [{"content": "ep1", "salience": 0.9}],
            "semantic": [{"content": "sem1", "salience": 0.5}],
            "relational": [],
            "procedural": [{"content": "proc1", "salience": 0.7}],
        })
        result = svc._parse_payload(payload)
        assert len(result["episodic"]) == 1
        assert len(result["semantic"]) == 1
        assert result["relational"] == []
        assert len(result["procedural"]) == 1

    def test_invalid_json_returns_empty_lists(self, svc: MemoryExtractionService) -> None:
        result = svc._parse_payload("not valid json {{{")
        assert result == {"episodic": [], "semantic": [], "relational": [], "procedural": []}

    def test_missing_keys_default_to_empty(self, svc: MemoryExtractionService) -> None:
        payload = json.dumps({"episodic": [{"content": "x"}]})
        result = svc._parse_payload(payload)
        assert result["semantic"] == []
        assert result["relational"] == []
        assert result["procedural"] == []

    def test_non_list_value_replaced_with_empty(self, svc: MemoryExtractionService) -> None:
        payload = json.dumps({
            "episodic": "should be a list",
            "semantic": [], "relational": [], "procedural": [],
        })
        assert svc._parse_payload(payload)["episodic"] == []

    def test_empty_json_object_all_empty(self, svc: MemoryExtractionService) -> None:
        result = svc._parse_payload("{}")
        assert all(result[k] == [] for k in ("episodic", "semantic", "relational", "procedural"))

    def test_salience_and_content_preserved(self, svc: MemoryExtractionService) -> None:
        payload = json.dumps({"episodic": [{"content": "Alice likes cats", "salience": 0.8, "subject": "Alice"}],
                              "semantic": [], "relational": [], "procedural": []})
        result = svc._parse_payload(payload)
        item = result["episodic"][0]
        assert item["content"] == "Alice likes cats"
        assert item["salience"] == 0.8
        assert item["subject"] == "Alice"
