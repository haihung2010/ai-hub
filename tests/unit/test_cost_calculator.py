"""Unit tests for the cost calculator.

Validates per-model rate lookup, default zero behaviour, and rounding
behaviour for both local and cloud providers.
"""
from __future__ import annotations

import pytest

from app.utils.cost_calculator import calculate_cost_usd


@pytest.mark.unit
def test_local_providers_are_zero_cost() -> None:
    """Self-hosted llama.cpp must be free — that's the whole point of local."""
    assert calculate_cost_usd("llama_cpp", "gemma4-12b-q4", 1000, 500) == 0.0
    assert calculate_cost_usd("background", "e2b-q4", 1000, 500) == 0.0


@pytest.mark.unit
def test_openrouter_free_tier_is_zero_cost() -> None:
    assert calculate_cost_usd("openrouter", "openai/gpt-oss-20b:free", 10_000, 5_000) == 0.0


@pytest.mark.unit
def test_gemini_flash_rates_match_published_pricing() -> None:
    # Gemini 2.0 Flash: $0.075/M input, $0.30/M output (USD per 1K = 0.000075 / 0.0003)
    cost = calculate_cost_usd("gemini", "gemini-2.0-flash", 1_000_000, 500_000)
    expected = (1_000_000 / 1000) * 0.000075 + (500_000 / 1000) * 0.0003
    assert abs(cost - round(expected, 6)) < 1e-6


@pytest.mark.unit
def test_claude_via_9router_rates() -> None:
    cost = calculate_cost_usd("nine_router", "kr/claude-sonnet-4.5", 1_000_000, 1_000_000)
    expected = (1_000_000 / 1000) * 0.003 + (1_000_000 / 1000) * 0.015
    assert abs(cost - round(expected, 6)) < 1e-6


@pytest.mark.unit
def test_minimax_is_zero_cost() -> None:
    # Internal M3 fallback → 0 until a billed rate is configured.
    assert calculate_cost_usd("minimax", "MiniMax-M3", 1000, 500) == 0.0


@pytest.mark.unit
def test_synthetic_paths_are_zero_cost() -> None:
    assert calculate_cost_usd("memory", "history-recall", 0, 100) == 0.0
    assert calculate_cost_usd("risk_policy", "clarification", 100, 50) == 0.0


@pytest.mark.unit
def test_unknown_provider_falls_back_to_zero() -> None:
    # Better to write 0.0 than NULL for unknown providers (cost ranking needs numbers).
    assert calculate_cost_usd("mystery_provider", "mystery_model", 100, 50) == 0.0


@pytest.mark.unit
def test_none_or_negative_tokens_handled() -> None:
    # Negative/None inputs collapse to 0 (we never return negative cost).
    assert calculate_cost_usd("gemini", "gemini-2.0-flash", None, None) == 0.0
    assert calculate_cost_usd("gemini", "gemini-2.0-flash", -100, -50) == 0.0


@pytest.mark.unit
def test_cost_rounded_to_six_decimals() -> None:
    cost = calculate_cost_usd("gemini", "gemini-2.0-flash", 123, 456)
    # Round-trip should not lose precision past 6 decimals.
    assert cost == round(cost, 6)
