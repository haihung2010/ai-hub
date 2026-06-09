"""Cost calculation utilities for usage_events.

Per-model rates (USD per 1K tokens) for cloud providers. Local llama.cpp
inference is treated as zero-cost (self-hosted electricity is not billed
to tenants in this MVP).

Rates are kept here as a small static table so cost analysis works without
needing to hit the live provider. Override via env in the future.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UNKNOWN_MODELS_WARNED: set[tuple[str, str]] = set()

# USD per 1K tokens. Keys are (provider_name, model_substring) tuples;
# the first matching substring wins. A catch-all ``("*", "*")`` returns 0
# for any unrecognised model so we never silently fail.

# Source: public pricing pages as of 2026-06-06.
# input/output rates are USD per 1K tokens.
_RATES: list[tuple[tuple[str, str], tuple[float, float]]] = [
    # (provider_substring, model_substring) -> (input_per_1k, output_per_1k)
    (("llama_cpp", "*"), (0.0, 0.0)),            # local, self-hosted
    (("background", "*"), (0.0, 0.0)),            # local background models
    (("openrouter", ":free"), (0.0, 0.0)),         # free tier
    (("openrouter", "gpt-oss-20b:free"), (0.0, 0.0)),
    (("openrouter", "gpt-4o-mini"), (0.00015, 0.0006)),
    (("openrouter", "gpt-4o"), (0.005, 0.015)),
    (("openrouter", "claude-3-5-sonnet"), (0.003, 0.015)),
    (("openrouter", "claude-sonnet-4"), (0.003, 0.015)),
    (("nine_router", "claude-sonnet"), (0.003, 0.015)),
    (("nine_router", "*"), (0.003, 0.015)),       # 9router default
    (("gemini", "2.0-flash"), (0.000075, 0.0003)),
    (("gemini", "1.5-flash"), (0.000075, 0.0003)),
    (("gemini", "1.5-pro"), (0.00125, 0.005)),
    (("gemini", "*"), (0.0001, 0.0004)),
    (("minimax", "*"), (0.0, 0.0)),               # internal M3 — treat as zero until billed
    (("minimax", "m3"), (0.0, 0.0)),
    (("cloud", "*"), (0.003, 0.015)),             # generic cloud default
    (("memory", "*"), (0.0, 0.0)),                # /memory recall — local
    (("risk_policy", "*"), (0.0, 0.0)),           # synthetic policy reply
    # NOTE: no `("*", "*")` catch-all — unknown provider/model combinations
    # now log a WARNING (once per pair) and return 0.0 so silent zero-cost
    # regressions are visible in logs.
]


def _match_rate(provider: str, model: str) -> tuple[float, float] | None:
    """Find first matching (input_per_1k, output_per_1k) for provider+model.

    Matching is substring-based: provider_substring must appear in the
    lowercased provider name, and model_substring (or "*") in the lowercased
    model string. Returns ``None`` if nothing matched (the catch-all is no
    longer implicit so callers can warn on genuinely unknown models).
    """
    p = (provider or "").lower()
    m = (model or "").lower()
    for (p_pat, m_pat), rate in _RATES:
        if p_pat == "*" or p_pat in p:
            if m_pat == "*" or m_pat in m:
                return rate
    return None


def calculate_cost_usd(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Compute USD cost for a single chat completion.

    Returns 0.0 for local providers / unrecognised models (better than NULL
    for cost ranking). The value is rounded to 6 decimal places.
    """
    if prompt_tokens is None and completion_tokens is None:
        return 0.0
    p = max(0, int(prompt_tokens or 0))
    c = max(0, int(completion_tokens or 0))
    rate = _match_rate(provider, model)
    if rate is None:
        key = (provider or "", model or "")
        if key not in _UNKNOWN_MODELS_WARNED:
            logger.warning(
                "cost_calculator: unknown model provider=%s model=%s — returning 0 cost",
                provider, model,
            )
            _UNKNOWN_MODELS_WARNED.add(key)
        return 0.0
    in_rate, out_rate = rate
    cost = (p / 1000.0) * in_rate + (c / 1000.0) * out_rate
    return round(cost, 6)
