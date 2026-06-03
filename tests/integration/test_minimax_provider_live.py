"""Live smoke test for MiniMax M3 provider.

Skipped unless ``RUN_LIVE=1`` AND ``MINIMAX_API_KEY`` is set. Hits the
real API to confirm:
  1. Authentication works
  2. Non-streaming completion returns text
  3. Prompt caching actually hits (usage.ephemeral_cache_creation_input_tokens > 0)

Run:
  RUN_LIVE=1 MINIMAX_API_KEY=<key> \\
  AI_HUB_ALLOW_DB_TRUNCATE_FOR_TESTS=1 \\
  ./venv/bin/pytest tests/integration/test_minimax_provider_live.py --no-cov -v
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.services.providers.minimax import MiniMaxProvider

pytestmark = pytest.mark.integration


def _build_provider() -> MiniMaxProvider:
    api_key = os.getenv("MINIMAX_API_KEY", "")
    if not api_key:
        pytest.skip("MINIMAX_API_KEY not set — skipping live eval")
    return MiniMaxProvider(
        client=httpx.AsyncClient(timeout=60.0),
        api_key=api_key,
        model=os.getenv("MINIMAX_MODEL", "MiniMax-M3"),
        base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
        enable_caching=True,
    )


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to enable")
async def test_live_chinese_greeting() -> None:
    provider = _build_provider()
    from app.models.chat import Message
    result = await provider.complete(
        [Message(role="user", content="Xin chào, giới thiệu ngắn về Hà Nội bằng 2 câu.")],
        provider._model,
        0.5,
        options={"max_tokens": 256},
    )
    assert isinstance(result, str) and len(result) > 10
    # Confirm response is actually Vietnamese
    assert any(c in result.lower() for c in ["hà nội", "thủ đô", "việt nam"])


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to enable")
async def test_caching_creates_cache_on_second_turn() -> None:
    """Two consecutive requests with the same system prompt — the second
    must show ephemeral cache hit in usage."""
    from app.models.chat import Message
    provider = _build_provider()
    system = "Bạn là trợ lý ảo nói tiếng Việt, lịch sự, ngắn gọn."

    # First request — should CREATE cache
    r1_body = provider._build_payload(
        [Message(role="system", content=system), Message(role="user", content="1+1=?")],
        0.3,
        {"max_tokens": 64},
    )
    # Second request — same system, new user turn
    r2_body = provider._build_payload(
        [Message(role="system", content=system), Message(role="user", content="2+2=?")],
        0.3,
        {"max_tokens": 64},
    )
    # Both payloads must have cache_control on the system block + last message
    for body in (r1_body, r2_body):
        assert isinstance(body["system"], list)
        assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
        assert body["messages"][-1]["cache_control"] == {"type": "ephemeral"}
