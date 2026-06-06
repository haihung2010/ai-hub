"""Tests for the auto-fact extraction service (Mem0 pattern).

The service consumes recent chat messages, asks the local LLM to extract
atomic user facts ("Tôi sống ở Hà Nội", "Tôi thích cà phê đen"), dedupes
against existing pinned_memories, and writes new ones with scope="auto".

We stub out the LLM client so tests do not require a running llama.cpp.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from app.core.database import get_db_connection
from app.models.chat import Message
from app.services.fact_extraction_service import (
    ExtractedFact,
    PinnedMemoryAutoExtractor,
    _parse_llm_facts,
    _dedupe_against_existing,
)
from app.services.pinned_memory_service import PinnedMemoryService


# ── Pure-function helpers (no LLM, no DB) ──────────────────────────────


class TestParseLlmFacts:
    @pytest.mark.unit
    def test_parses_clean_json_array(self) -> None:
        text = '[{"key": "city", "value": "Hà Nội", "confidence": 0.9}]'
        facts = _parse_llm_facts(text)
        assert len(facts) == 1
        assert facts[0].key == "city"
        assert facts[0].value == "Hà Nội"
        assert facts[0].confidence == pytest.approx(0.9)

    @pytest.mark.unit
    def test_parses_json_embedded_in_prose(self) -> None:
        text = (
            "Tôi đã đọc tin nhắn, đây là các facts:\n"
            '[{"key": "city", "value": "Hà Nội", "confidence": 0.95},'
            ' {"key": "drink", "value": "cà phê đen", "confidence": 0.8}]\n'
            "Hết."
        )
        facts = _parse_llm_facts(text)
        assert len(facts) == 2
        assert {f.key for f in facts} == {"city", "drink"}

    @pytest.mark.unit
    def test_empty_response_yields_empty_list(self) -> None:
        assert _parse_llm_facts("") == []
        assert _parse_llm_facts("Không có thông tin cá nhân nào.") == []
        assert _parse_llm_facts("[]") == []

    @pytest.mark.unit
    def test_skips_invalid_entries_but_keeps_valid(self) -> None:
        text = (
            '[{"key":"a","value":"b","confidence":0.9},'
            '{"key":"","value":"","confidence":0.5},'   # empty key/value → drop
            '{"key":"c","value":""},'                    # missing confidence → drop
            '{"key":"d","value":"e","confidence":2.0}]' # >1 confidence → clamp
        )
        facts = _parse_llm_facts(text)
        # a passes, d passes (clamped), b/c dropped
        keys = {f.key for f in facts}
        assert "a" in keys
        assert "d" in keys
        assert "b" not in keys
        assert "c" not in keys
        # d's confidence must be clamped to <= 1.0
        d = next(f for f in facts if f.key == "d")
        assert 0.0 <= d.confidence <= 1.0

    @pytest.mark.unit
    def test_garbage_response_does_not_raise(self) -> None:
        # LLM sometimes returns malformed JSON
        assert _parse_llm_facts("not json at all") == []
        assert _parse_llm_facts("[broken json") == []
        assert _parse_llm_facts("null") == []


class TestDedupe:
    @pytest.mark.unit
    def test_drops_exact_key_match(self) -> None:
        facts = [
            ExtractedFact(key="city", value="Hà Nội", confidence=0.9),
        ]
        existing = [
            ("city", "TP HCM"),  # same key, different value
        ]
        kept, dropped = _dedupe_against_existing(facts, existing)
        # Same key → assume same fact, just lower confidence takes precedence
        assert len(kept) == 0
        assert len(dropped) == 1

    @pytest.mark.unit
    def test_keeps_unseen_keys(self) -> None:
        facts = [
            ExtractedFact(key="city", value="Hà Nội", confidence=0.9),
            ExtractedFact(key="drink", value="cà phê", confidence=0.8),
        ]
        existing = [("hobby", "đá bóng")]
        kept, dropped = _dedupe_against_existing(facts, existing)
        assert len(kept) == 2
        assert len(dropped) == 0

    @pytest.mark.unit
    def test_empty_existing_keeps_all(self) -> None:
        facts = [ExtractedFact(key="x", value="y", confidence=0.5)]
        kept, dropped = _dedupe_against_existing(facts, [])
        assert len(kept) == 1
        assert len(dropped) == 0


# ── Integration: service writes to pinned_memory ──────────────────────


class _FakeLlm:
    """Stub LLM client: returns a pre-canned JSON string."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[str] = []

    def extract(self, messages: list[Message]) -> str:  # noqa: ARG002
        self.calls.append("called")
        return self._response


def _seed_user(tenant_id: str = "default", project_id: str = "default", user_id: str = "u1") -> None:
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO users (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (user_id, tenant_id, user_id),
        )
        conn.commit()


def _msgs(*pairs: tuple[str, str]) -> list[Message]:
    """Build a flat list of alternating user/assistant messages."""
    out: list[Message] = []
    for role, content in pairs:
        out.append(Message(role=role, content=content))
    return out


class TestFactExtractionServicePersist:
    @pytest.mark.unit
    def test_persists_new_facts_via_pinned_memory(self) -> None:
        _seed_user(user_id="u-fresh")
        llm = _FakeLlm(
            json.dumps(
                [
                    {"key": "city", "value": "Hà Nội", "confidence": 0.95},
                    {"key": "drink", "value": "cà phê đen", "confidence": 0.8},
                ]
            )
        )
        svc = PinnedMemoryAutoExtractor(
            llm=llm,
            pinned=PinnedMemoryService(),
        )
        result = svc.extract_and_persist(
            tenant_id="default",
            project_id="default",
            user_id="u-fresh",
            messages=_msgs(
                ("user", "Tôi sống ở Hà Nội, thích uống cà phê đen buổi sáng"),
                ("assistant", "Chào anh!"),
            ),
        )
        assert result.added == 2
        assert result.dropped == 0
        assert llm.calls == ["called"]

        # Verify DB state
        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT key, value, scope, confidence FROM pinned_memories "
                "WHERE user_id = %s ORDER BY key",
                ("u-fresh",),
            ).fetchall()
        assert [r["key"] for r in rows] == ["city", "drink"]
        for r in rows:
            assert r["scope"] == "auto"  # tagged as auto-extracted

    @pytest.mark.unit
    def test_dedupes_against_existing_pinned_memory(self) -> None:
        _seed_user(user_id="u-dedupe")
        pinned = PinnedMemoryService()
        # Pre-seed one fact manually
        pinned.upsert_memory(
            tenant_id="default",
            project_id="default",
            user_id="u-dedupe",
            key="city",
            value="Hà Nội",
            scope="user",
            confidence=1.0,
        )

        llm = _FakeLlm(
            json.dumps(
                [
                    {"key": "city", "value": "Hà Nội", "confidence": 0.9},
                    {"key": "sport", "value": "bóng đá", "confidence": 0.7},
                ]
            )
        )
        svc = PinnedMemoryAutoExtractor(llm=llm, pinned=pinned)
        result = svc.extract_and_persist(
            tenant_id="default",
            project_id="default",
            user_id="u-dedupe",
            messages=_msgs(("user", "Tôi thích bóng đá")),
        )
        assert result.added == 1  # only "sport" is new
        assert result.dropped == 1  # "city" already exists

        # Existing manual "city" must NOT be overwritten (manual > auto)
        with get_db_connection() as conn:
            row = conn.execute(
                "SELECT scope, confidence FROM pinned_memories "
                "WHERE user_id = %s AND key = %s",
                ("u-dedupe", "city"),
            ).fetchone()
        assert row["scope"] == "user"  # still manual
        assert row["confidence"] == 1.0  # unchanged

    @pytest.mark.unit
    def test_chitchat_with_no_facts_returns_zero_added(self) -> None:
        _seed_user(user_id="u-chitchat")
        llm = _FakeLlm("[]")  # LLM says no personal facts
        svc = PinnedMemoryAutoExtractor(llm=llm, pinned=PinnedMemoryService())
        result = svc.extract_and_persist(
            tenant_id="default",
            project_id="default",
            user_id="u-chitchat",
            messages=_msgs(
                ("user", "Hôm nay thời tiết thế nào?"),
                ("assistant", "Trời nắng nhẹ."),
            ),
        )
        assert result.added == 0
        assert result.dropped == 0

    @pytest.mark.unit
    def test_malformed_llm_response_is_handled_gracefully(self) -> None:
        _seed_user(user_id="u-malformed")
        llm = _FakeLlm("this is not json [[[")
        svc = PinnedMemoryAutoExtractor(llm=llm, pinned=PinnedMemoryService())
        result = svc.extract_and_persist(
            tenant_id="default",
            project_id="default",
            user_id="u-malformed",
            messages=_msgs(("user", "Hello?")),
        )
        # Must not raise; just record zero added
        assert result.added == 0
        assert result.dropped == 0
        # And no pinned memory was created
        with get_db_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM pinned_memories WHERE user_id = %s",
                ("u-malformed",),
            ).fetchone()["c"]
        assert count == 0

    @pytest.mark.unit
    def test_existing_keys_query_hits_db(self) -> None:
        """Internal _existing_keys() must return the active pinned keys
        for dedupe lookup, scoped to the right tenant/project/user."""
        _seed_user(user_id="u-keys")
        pinned = PinnedMemoryService()
        pinned.upsert_memory(
            "default", "default", "u-keys", "favorite_color", "blue"
        )
        pinned.upsert_memory(
            "default", "default", "u-keys", "pet_name", "Miu"
        )
        # Deactivate favorite_color specifically
        mems = pinned.list_memories("default", "default", "u-keys")
        for mem in mems:
            if mem.key == "favorite_color":
                pinned.deactivate_memory(mem.id)
                break

        svc = PinnedMemoryAutoExtractor(
            llm=_FakeLlm("[]"),
            pinned=pinned,
        )
        keys = [k for k, _ in svc._existing_keys("default", "default", "u-keys")]
        # Only active keys — favorite_color is deactivated
        assert "pet_name" in keys
        assert "favorite_color" not in keys

    @pytest.mark.unit
    def test_prompt_includes_recent_messages(self) -> None:
        """The LLM prompt must include the recent user+assistant turns so
        the extractor has the context to find personal facts."""
        _seed_user(user_id="u-prompt")
        captured: list[str] = []

        class _CapturingLlm:
            def extract(self_inner, messages: list[Message]) -> str:
                # Pydantic Message — use model_dump_json for round-trip
                captured.append(json.dumps([m.model_dump() for m in messages], ensure_ascii=False))
                return "[]"

        svc = PinnedMemoryAutoExtractor(
            llm=_CapturingLlm(),  # type: ignore[arg-type]
            pinned=PinnedMemoryService(),
        )
        svc.extract_and_persist(
            tenant_id="default",
            project_id="default",
            user_id="u-prompt",
            messages=_msgs(
                ("user", "Tôi tên An, sống ở Đà Nẵng"),
                ("assistant", "Chào An!"),
            ),
        )
        assert captured, "LLM.extract was never called"
        prompt_dump = captured[0]
        assert "An" in prompt_dump
        assert "Đà Nẵng" in prompt_dump
