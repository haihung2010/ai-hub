"""Token counting utilities for usage_events.

We use a model-aware tokenizer selection:

* For OpenAI/Claude/anthropic-style models we use ``tiktoken`` with the
  ``cl100k_base`` encoding (used by GPT-3.5/4 and a good-enough proxy for
  token counts on Claude / Gemini).
* For Gemma-family models we prefer a real sentencepiece tokenizer via
  ``transformers`` (small Gemma tokenizer is downloaded on first use). This
  avoids the ~10–20% undercounting that cl100k_base gives on non-Latin and
  whitespace-heavy text.
* If neither is available (e.g. slim venv, offline host), we fall back to a
  character-based heuristic ``len(text) * 0.27``.

Provider native counts are not exposed in our current streaming protocol —
these are best-effort estimates so cost analysis works without a separate
telemetry path.
"""
from __future__ import annotations

import logging
from typing import Iterable

import tiktoken

from app.models.chat import Message

logger = logging.getLogger(__name__)


_ENCODER = None
_GEMMA_TOKENIZER = None
_GEMMA_TOKENIZER_LOAD_FAILED = False
_FALLBACK_METHOD = "word_heuristic"
_TIKTOKEN_METHOD = "tiktoken_cl100k_base"
_GEMMA_METHOD = "transformers_gemma"


def _get_encoder():
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def _get_gemma_tokenizer():
    """Lazy-load a real Gemma tokenizer (sentencepiece via transformers).

    Returns ``None`` if ``transformers`` is not installed or the model
    cannot be fetched. Caller must fall back to tiktoken in that case.
    """
    global _GEMMA_TOKENIZER, _GEMMA_TOKENIZER_LOAD_FAILED
    if _GEMMA_TOKENIZER is not None:
        return _GEMMA_TOKENIZER
    if _GEMMA_TOKENIZER_LOAD_FAILED:
        return None
    try:
        from transformers import AutoTokenizer
        _GEMMA_TOKENIZER = AutoTokenizer.from_pretrained("google/gemma-2-2b")
        return _GEMMA_TOKENIZER
    except Exception as exc:
        logger.debug("gemma tokenizer unavailable, will fall back: %s", exc)
        _GEMMA_TOKENIZER_LOAD_FAILED = True
        return None


def _is_gemma_model(model: str | None) -> bool:
    if not model:
        return False
    return "gemma" in model.lower()


def count_text_tokens(text: str | None, model: str | None = None) -> int:
    """Count tokens in a single text. ``None``/empty -> 0.

    ``model`` is optional; if it contains ``gemma`` we attempt to use a
    sentencepiece Gemma tokenizer, otherwise we fall back to tiktoken
    cl100k_base. Final fallback is a char-based heuristic.
    """
    if not text:
        return 0

    if _is_gemma_model(model):
        tok = _get_gemma_tokenizer()
        if tok is not None:
            try:
                ids = tok.encode(text, add_special_tokens=False)
                return len(ids)
            except Exception:
                pass

    try:
        return len(_get_encoder().encode(text))
    except Exception:
        # Catastrophic fallback: ~0.27 tokens per char
        return max(1, int(len(text) * 0.27))


def count_messages_tokens(messages: Iterable[Message], model: str | None = None) -> int:
    """Estimate prompt tokens across a list of messages.

    Adds a small per-message overhead (4 tokens for role/delimiters) per
    the OpenAI cookbook's standard message-tokenizer formula. ``model`` is
    forwarded to :func:`count_text_tokens` for tokenizer selection.
    """
    total = 0
    for msg in messages:
        content = msg.content or ""
        total += 4  # role + delimiters
        total += count_text_tokens(content, model=model)
    total += 2  # priming tokens
    return total


def token_counting_method(model: str | None = None) -> str:
    """Return the name of the token-counting method in use for ``model``."""
    if _is_gemma_model(model) and _get_gemma_tokenizer() is not None:
        return _GEMMA_METHOD
    try:
        _get_encoder()
        return _TIKTOKEN_METHOD
    except Exception:
        return _FALLBACK_METHOD
