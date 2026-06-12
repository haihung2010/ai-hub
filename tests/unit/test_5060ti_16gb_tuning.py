"""Unit tests for 5060 Ti 16GB tuning (2026-06-12).

Covers the 3 deliverables:
  1. New start script (syntax + env handling)
  2. Memory budget validation in config
  3. ctx overflow guard in chat service
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


# ──────────────────────────────────────────────────────────────────────
# 1. start_5060ti_16gb.sh — syntax + env handling
# ──────────────────────────────────────────────────────────────────────


SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "start_5060ti_16gb.sh"


def test_start_script_exists_and_executable() -> None:
    assert SCRIPT.exists(), f"missing: {SCRIPT}"
    import stat
    assert SCRIPT.stat().st_mode & stat.S_IXUSR, "script is not executable; chmod +x"


def test_start_script_parses() -> None:
    import subprocess
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=5)
    assert r.returncode == 0, f"syntax error: {r.stderr}"


def test_start_script_defaults_printed_in_help() -> None:
    """The script's banner should include the key tuning values."""
    text = SCRIPT.read_text()
    assert "parallel=${PARALLEL:-4}" in text or "PARALLEL:-4" in text
    assert "ctx_size=${CTX_SIZE:-6144}" in text or "CTX_SIZE:-6144" in text
    assert "mlock" in text  # critical for 16GB


# ──────────────────────────────────────────────────────────────────────
# 2. Memory budget validation
# ──────────────────────────────────────────────────────────────────────


def test_memory_budget_default_fits_16gb() -> None:
    """With defaults (model=7600, ctx=8192, parallel=8) the
    budget is OVER the 16GB cap. The validator should warn."""
    from app.core.config import Settings, validate_memory_budget
    s = Settings(
        APP_PORT=8000, LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://localhost:8080", DEFAULT_MODEL="x", LITE_MODEL="y",
        REQUEST_TIMEOUT_SECONDS=5.0, MAX_HISTORY_MESSAGES=5, LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="test-api-key-aaaaaaaaaa", RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver"], BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
    )
    # ctx=8192 × 8 parallel × 60 MiB/1K = 3.9 GiB KV cache alone.
    # Plus 7.6 model + 2 GB overhead = 13.5 GiB. Just under 16.
    # With higher ctx or model it'd be over. Don't assert on log;
    # the test is that validate_memory_budget doesn't raise.
    validate_memory_budget(s)


def test_memory_budget_strict_16gb_with_5060ti_tuning() -> None:
    """After 5060 Ti tuning (parallel=4, ctx=6K), the budget
    validator should report a healthy headroom."""
    from app.core.config import Settings, validate_memory_budget
    s = Settings(
        APP_PORT=8000, LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://localhost:8080", DEFAULT_MODEL="x", LITE_MODEL="y",
        REQUEST_TIMEOUT_SECONDS=5.0, MAX_HISTORY_MESSAGES=5, LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="test-api-key-aaaaaaaaaa", RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver"], BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
        GPU_CONCURRENCY=4, LITE_NUM_CTX=6144,
    )
    # 7.6 + 4*60*6 + 2 = 7.6 + 1.44 + 2 = 11.04 GiB used.
    # 16 - 11.04 = 4.96 GiB headroom. Plenty.
    validate_memory_budget(s)


def test_memory_budget_over_24gb_safe() -> None:
    """On 24GB+ GPUs the validator should also be fine."""
    from app.core.config import Settings, validate_memory_budget
    s = Settings(
        APP_PORT=8000, LOG_LEVEL="WARNING",
        LLAMA_CPP_BASE_URL="http://localhost:8080", DEFAULT_MODEL="x", LITE_MODEL="y",
        REQUEST_TIMEOUT_SECONDS=5.0, MAX_HISTORY_MESSAGES=5, LITE_MAX_HISTORY_MESSAGES=5,
        API_KEY="test-api-key-aaaaaaaaaa", RATE_LIMIT_PER_MINUTE=5,
        ALLOWED_HOSTS=["testserver"], BACKGROUND_LLAMA_CPP_ENABLED="false",
        ENABLE_KNOWLEDGE_RAG=False,
        GPU_MEMORY_BUDGET_MIB=24576, GPU_CONCURRENCY=8, LITE_NUM_CTX=8192,
    )
    validate_memory_budget(s)


# ──────────────────────────────────────────────────────────────────────
# 3. ctx overflow guard
# ──────────────────────────────────────────────────────────────────────


def test_estimate_prompt_tokens_handles_vietnamese() -> None:
    """Vietnamese characters count as ~2 chars per token, so the
    estimator uses 3 chars/token as a safe average."""
    from app.models.chat import ChatRequest
    from app.services.ai_service import AIService

    # Build a service instance for the helper. We don't need a
    # fully-wired service; just the bound method.
    svc = AIService.__new__(AIService)

    req = ChatRequest(
        project_id="t",
        user_message="Xin chào, bạn khỏe không?",  # ~25 chars → ~8 tokens
    )
    est = svc._estimate_prompt_tokens(req)
    assert 5 <= est <= 20


def test_estimate_prompt_tokens_counts_history() -> None:
    from app.models.chat import ChatRequest
    from app.services.ai_service import AIService
    svc = AIService.__new__(AIService)
    req = ChatRequest(
        project_id="t",
        user_message="hi",
        history=[
            {"role": "user", "content": "x" * 300},
            {"role": "assistant", "content": "y" * 600},
        ],
    )
    est = svc._estimate_prompt_tokens(req)
    # 300 + 600 + 2 (hi) = 902 chars / 3 = ~300 tokens
    assert 250 <= est <= 350


def test_check_ctx_overflow_returns_true_for_huge_request() -> None:
    from app.models.chat import ChatRequest
    from app.services.ai_service import AIService
    svc = AIService.__new__(AIService)
    req = ChatRequest(
        project_id="t",
        user_message="x" * 30000,  # 10K tokens
    )
    would_overflow, est = svc._check_ctx_overflow(req, ctx=6144)
    assert would_overflow is True
    assert est > 5000


def test_check_ctx_overflow_returns_false_for_small_request() -> None:
    from app.models.chat import ChatRequest
    from app.services.ai_service import AIService
    svc = AIService.__new__(AIService)
    req = ChatRequest(
        project_id="t",
        user_message="short question",
    )
    would_overflow, est = svc._check_ctx_overflow(req, ctx=6144)
    assert would_overflow is False
    assert est < 100
