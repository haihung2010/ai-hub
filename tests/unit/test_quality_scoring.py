"""Unit tests for Vietnamese quality scoring module.

Pure (no DB) — opt out of isolated_db truncation.
"""
from __future__ import annotations

import pytest

from scripts.quality_scoring import (
    detect_hallucination,
    score_response,
    PROMPT_BANK,
)

pytestmark = pytest.mark.no_isolated_db


def test_detect_hallucination_clean():
    """Clean response has no hallucination markers."""
    assert detect_hallucination("Trời hôm nay nắng đẹp.") is False


def test_detect_hallucination_arraylist():
    """ArrayList is a known hallucination marker."""
    assert detect_hallucination("**Verdict:** ArrayList") is True


def test_detect_hallucination_class_normal():
    """CLASS-NORMAL is a known hallucination marker."""
    assert detect_hallucination("**Verdict:** CLASS-NORMAL") is True


def test_detect_hallucination_empty():
    """Empty response is suspicious."""
    assert detect_hallucination("") is True


def test_score_response_clean_relevant():
    """A clean, relevant response scores 7-10."""
    prompt = PROMPT_BANK[0]["prompt"]
    response = "Xin chào! Tôi là một trợ lý AI được huấn luyện bởi Google, có thể giúp bạn trả lời câu hỏi về nhiều chủ đề."
    score = score_response(prompt, response)
    assert score["total"] >= 7
    assert score["relevance"] >= 2


def test_score_response_irrelevant():
    """Off-topic response scores low relevance."""
    prompt = "Giải thích NEMA MG-1 voltage imbalance threshold"
    response = "Tôi thích ăn phở."
    score = score_response(prompt, response)
    assert score["relevance"] <= 1
    assert score["total"] < 5


def test_score_response_garbage_zero():
    """Hallucination tokens → automatic 0."""
    prompt = "Bất kỳ"
    response = "**Verdict:** ArrayList"
    score = score_response(prompt, response)
    assert score["total"] == 0


def test_prompt_bank_has_28_prompts():
    """Validate the prompt bank structure."""
    assert len(PROMPT_BANK) >= 28
    categories = set(p["category"] for p in PROMPT_BANK)
    assert "greeting" in categories
    assert "technical" in categories
    assert "code" in categories
