"""Live integration test for the auto-fact extraction pipeline.

Sends a real Vietnamese conversation to the local LLM (E2B Q4 port 8081)
and verifies the extracted facts land in pinned_memories.

Run:
  AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 \
  ./venv/bin/pytest tests/integration/test_fact_extraction_live.py --no-cov -v
"""

from __future__ import annotations

import pytest

from app.core.database import get_db_connection
from app.models.chat import Message
from app.services.fact_extraction_service import (
    LocalLlamaCppFactExtractor,
    PinnedMemoryAutoExtractor,
)
from app.services.pinned_memory_service import PinnedMemoryService

pytestmark = pytest.mark.integration


def _seed_user(user_id: str = "u-live-fact") -> None:
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO users (id, tenant_id, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (user_id, "default", user_id),
        )
        conn.commit()


@pytest.mark.integration
def test_live_extraction_writes_to_pinned_memory() -> None:
    """End-to-end: real LLM → real DB."""
    _seed_user()
    extractor = PinnedMemoryAutoExtractor(
        llm=LocalLlamaCppFactExtractor(),
        pinned=PinnedMemoryService(),
    )
    result = extractor.extract_and_persist(
        tenant_id="default",
        project_id="default",
        user_id="u-live-fact",
        messages=[
            Message(role="user", content="Tôi tên An, sống ở Đà Nẵng, thích uống cà phê đen buổi sáng và chơi bóng đá cuối tuần."),
            Message(role="assistant", content="Chào An! Cà phê đen Đà Nẵng nổi tiếng đó."),
        ],
    )

    assert result.raw_count >= 1, f"LLM should have extracted at least 1 fact, got {result.raw_count}"

    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT key, value, scope, confidence FROM pinned_memories "
            "WHERE user_id = %s ORDER BY key",
            ("u-live-fact",),
        ).fetchall()

    # We expect at least 1 fact; specific keys depend on LLM output
    assert rows, f"pinned_memories should have rows, got {result}"
    keys = {r["key"] for r in rows}
    # The LLM may extract any subset of: city/Hà Nội/ten/etc — but MUST
    # be Vietnamese and MUST be auto-tagged.
    for r in rows:
        assert r["scope"] == "auto", f"all auto-extracted facts must have scope='auto', got {r['scope']}"
        assert r["value"], "value must not be empty"
        assert r["confidence"] >= 0.6, f"below threshold: {r['confidence']}"
    # Sanity: at least one of the well-known facts is captured
    assert any(
        "đà nẵng" in r["value"].lower() for r in rows
    ), f"expected 'Đà Nẵng' in some fact, got keys: {keys}"
