"""Token counting utilities for usage_events.

We use ``tiktoken`` with the ``cl100k_base`` encoding (used by GPT-3.5/4 and
a good-enough proxy for token counts on Gemma / Claude / Gemini). Provider
native counts are not exposed in our current streaming protocol — these are
best-effort estimates so cost analysis works without a separate telemetry
path.

If ``tiktoken`` is unavailable (it ships in the venv, so this should only
happen on misconfigured hosts), we fall back to a character-based heuristic
``len(text) * 0.27`` which approximates English ~1.33 tokens/word.
"""
from __future__ import annotations

from typing import Iterable

import tiktoken

from app.models.chat import Message


_ENCODER = None
_FALLBACK_METHOD = "word_heuristic"
_TIKTOKEN_METHOD = "tiktoken_cl100k_base"


def _get_encoder():
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def count_text_tokens(text: str | None) -> int:
    """Count tokens in a single text. ``None``/empty -> 0.

    Uses tiktoken cl100k_base.
    """
    if not text:
        return 0
    try:
        return len(_get_encoder().encode(text))
    except Exception:
        # Catastrophic fallback: ~0.27 tokens per char
        return max(1, int(len(text) * 0.27))


def count_messages_tokens(messages: Iterable[Message]) -> int:
    """Estimate prompt tokens across a list of messages.

    Adds a small per-message overhead (4 tokens for role/delimiters) per
    the OpenAI cookbook's standard message-tokenizer formula.
    """
    total = 0
    for msg in messages:
        content = msg.content or ""
        total += 4  # role + delimiters
        total += count_text_tokens(content)
    total += 2  # priming tokens
    return total


def token_counting_method() -> str:
    """Return the name of the token-counting method in use."""
    try:
        _get_encoder()
        return _TIKTOKEN_METHOD
    except Exception:
        return _FALLBACK_METHOD
