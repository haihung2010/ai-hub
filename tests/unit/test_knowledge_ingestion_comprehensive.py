"""Comprehensive tests for KnowledgeIngestionService — chunking, CRUD, edge cases."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.core.database import get_db_connection
from app.models.knowledge import KnowledgeCardCreate
from app.services.knowledge_ingestion_service import KnowledgeIngestionService


def _make_card_req(**overrides) -> KnowledgeCardCreate:
    defaults = dict(
        tenant_id="default",
        project_id="test",
        knowledge_domain="faq",
        title="Test Card",
        summary="",
        content="Test content for knowledge card.",
        source_type="manual",
        trust_level=3,
        status="active",
        version=1,
        tags=["test"],
    )
    defaults.update(overrides)
    return KnowledgeCardCreate(**defaults)


class TestChunkContent:
    def test_short_content_single_chunk(self):
        svc = KnowledgeIngestionService(chunk_chars=2000)
        chunks = svc._chunk_content("short text")
        assert len(chunks) == 1
        assert chunks[0] == "short text"

    def test_empty_content_returns_empty(self):
        svc = KnowledgeIngestionService(chunk_chars=2000)
        chunks = svc._chunk_content("")
        assert chunks == []

    def test_whitespace_only_returns_empty(self):
        svc = KnowledgeIngestionService(chunk_chars=2000)
        chunks = svc._chunk_content("   \n\n   ")
        assert chunks == []

    def test_long_paragraph_splits_at_chunk_size(self):
        svc = KnowledgeIngestionService(chunk_chars=50)
        long_text = "a" * 120
        chunks = svc._chunk_content(long_text)
        assert len(chunks) == 3
        assert all(len(c) <= 50 for c in chunks)

    def test_multiple_paragraphs_combine(self):
        svc = KnowledgeIngestionService(chunk_chars=200)
        text = "para one.\n\npara two.\n\npara three."
        chunks = svc._chunk_content(text)
        assert len(chunks) >= 1

    def test_paragraphs_exceeding_chunk_size_split(self):
        svc = KnowledgeIngestionService(chunk_chars=20)
        text = "short\n\n" + "x" * 50 + "\n\nend"
        chunks = svc._chunk_content(text)
        assert len(chunks) >= 3

    def test_respects_newline_paragraphs(self):
        svc = KnowledgeIngestionService(chunk_chars=100)
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = svc._chunk_content(text)
        assert len(chunks) == 1


class TestCreateCard:
    def test_create_and_retrieve(self):
        svc = KnowledgeIngestionService(chunk_chars=2000)
        req = _make_card_req(content="This is create test content.")
        card = svc.create_card(req)
        assert card.title == "Test Card"
        assert card.project_id == "test"

        retrieved = svc.get_card(card.id)
        assert retrieved is not None
        assert retrieved.id == card.id

    def test_create_card_truncates_long_content(self):
        svc = KnowledgeIngestionService(chunk_chars=2000, max_card_chars=100)
        long_content = "x" * 500
        req = _make_card_req(content=long_content)
        card = svc.create_card(req)
        assert len(card.content) <= 100

    def test_create_card_generates_chunks(self):
        svc = KnowledgeIngestionService(chunk_chars=50)
        content = "First paragraph.\n\n" + "x" * 60 + "\n\nThird paragraph."
        req = _make_card_req(content=content)
        card = svc.create_card(req)

        with get_db_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_card_chunks WHERE card_id = %s ORDER BY chunk_index",
                (card.id,),
            ).fetchall()
        assert len(rows) >= 2


class TestUpdateCard:
    def test_update_replaces_content_and_chunks(self):
        svc = KnowledgeIngestionService(chunk_chars=2000)
        req = _make_card_req(content="original content")
        card = svc.create_card(req)

        update_req = _make_card_req(content="updated content here", title="Updated Title")
        updated = svc.update_card(card.id, update_req)
        assert updated.title == "Updated Title"
        assert "updated content" in updated.content

    def test_update_replaces_old_chunks(self):
        svc = KnowledgeIngestionService(chunk_chars=20)
        req = _make_card_req(content="a" * 100)
        card = svc.create_card(req)

        with get_db_connection() as conn:
            old_chunks = conn.execute(
                "SELECT * FROM knowledge_card_chunks WHERE card_id = %s", (card.id,)
            ).fetchall()
        old_count = len(old_chunks)

        update_req = _make_card_req(content="short")
        svc.update_card(card.id, update_req)

        with get_db_connection() as conn:
            new_chunks = conn.execute(
                "SELECT * FROM knowledge_card_chunks WHERE card_id = %s", (card.id,)
            ).fetchall()
        assert len(new_chunks) < old_count


class TestListCards:
    def test_list_empty(self):
        svc = KnowledgeIngestionService()
        cards = svc.list_cards(tenant_id="default", project_id="empty_project")
        assert cards == []

    def test_list_returns_cards(self):
        svc = KnowledgeIngestionService()
        svc.create_card(_make_card_req(title="Card A"))
        svc.create_card(_make_card_req(title="Card B"))
        cards = svc.list_cards(tenant_id="default", project_id="test")
        assert len(cards) >= 2

    def test_list_filters_by_domain(self):
        svc = KnowledgeIngestionService()
        svc.create_card(_make_card_req(knowledge_domain="faq", title="FAQ Card"))
        svc.create_card(_make_card_req(knowledge_domain="policy", title="Policy Card"))
        faq_cards = svc.list_cards(tenant_id="default", project_id="test", knowledge_domain="faq")
        assert all(c.knowledge_domain == "faq" for c in faq_cards)


class TestFillMissingEmbeddings:
    def test_returns_error_when_no_embedding_service(self):
        svc = KnowledgeIngestionService(embedding_service=None)
        result = svc.fill_missing_embeddings()
        assert result["error"] == "no embedding service"

    def test_fills_embeddings(self):
        mock_embed = MagicMock()
        mock_embed.embed = MagicMock(return_value=b"\x00" * 384)
        svc = KnowledgeIngestionService(chunk_chars=2000, embedding_service=mock_embed)
        req = _make_card_req(content="embed me please")
        card = svc.create_card(req)

        with get_db_connection() as conn:
            conn.execute(
                "UPDATE knowledge_card_chunks SET embedding = NULL WHERE card_id = %s",
                (card.id,),
            )
            conn.commit()

        result = svc.fill_missing_embeddings(project_id="test")
        assert result["updated"] >= 1
