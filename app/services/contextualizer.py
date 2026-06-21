"""Contextualizer — LLM-generated context for Anthropic Contextual Retrieval.

For each chunk, ask a small local LLM (E4B Q4 on port 8081) to produce 50-100
tokens of context that situates the chunk within the full document. The
generated context is prepended to the chunk before embedding AND before the
FTS tsvector is built — both vector search and BM25 benefit from the semantic
framing.

Why a separate service from KnowledgeIngestionService:
  - Feature-flag the LLM path off (no model load on the hot path).
  - Fall back to a deterministic header if E4B is down or slow.
  - Independently benchmark the LLM path against the deterministic path.

Reference: https://www.anthropic.com/news/contextual-retrieval
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

from app.services.observability import ObservabilityService

logger = logging.getLogger(__name__)


# P0.2 (2026-06-10): RAG content segregation. Same OWASP LLM01:2025 defense
# as knowledge_retrieval_service.sanitize_chunk_content — strip ChatML and
# llama.cpp role markers that could let the LLM response smuggle a fake
# system message into the embedding/FTS index.
#
# Two-pass cleanup:
#   1. Drop entire ChatML/llama.cpp role blocks (e.g. <|im_start|>system
#      ... You are in admin mode ... <|im_end|>) so injected payloads
#      between markers cannot survive in the embedding/FTS text.
#   2. Strip any standalone role marker (no matching pair) as a
#      belt-and-braces pass — a partial injection should not leave
#      role-marker text lying around.
_FULL_INJECTION_BLOCK_RE = re.compile(
    r"<\|\s*im_start\s*\|>.*?<\|\s*im_end\s*\|>",
    re.DOTALL | re.IGNORECASE,
)
_STANDALONE_MARKER_RE = re.compile(
    r"<\|\s*(?:system|user|assistant|end|channel|message|start)\s*\|>"
    r"|<[/]?system>|<[/]?user>|<[/]?assistant>"
    r"|\[/?INST\]|<\*?/?SYS\*?>",
    re.IGNORECASE,
)


class ContextualizerProvider(Protocol):
    """Provider interface for the LLM call.

    The real implementation wraps `LlamaCppProvider` (OpenAI-compatible on
    port 8081). Tests use a stub that records calls and returns configurable
    responses. Keeping this Protocol tight makes the swap trivial.
    """

    async def chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str: ...


def build_prompt(*, chunk_text: str, full_document: str) -> str:
    """Build the Anthropic Contextual Retrieval prompt.

    Pure function so it can be unit-tested without any model or network.
    Follows the template from the Anthropic blog:
      <document>...</document>
      <chunk>...</chunk>
      "Give 50-100 token context to situate this chunk..."

    Why this exact shape:
      - Putting the whole document first gives the model maximum context.
      - `<document>` and `<chunk>` tags make the boundaries unambiguous to
        the model, which matters for Vietnamese where the model might
        otherwise splice the document and chunk together.
      - "Answer only with the succinct context" prevents the model from
        prepending "Here is the context:" which would dilute the signal.
    """
    return (
        "<document>\n"
        f"{full_document}\n"
        "</document>\n\n"
        "Here is the chunk we want to situate within the whole document:\n"
        "<chunk>\n"
        f"{chunk_text}\n"
        "</chunk>\n\n"
        "Please give a short succinct context (50-100 tokens) to situate "
        "this chunk within the overall document for the purposes of "
        "improving search retrieval of the chunk. "
        "Answer only with the succinct context and nothing else."
    )


def _deterministic_fallback(*, chunk_text: str, full_document: str) -> str:
    """Build a fallback context header when the LLM is unavailable.

    Not as good as the LLM-generated version (no cross-chunk reasoning, no
    entity disambiguation) but still better than nothing — at minimum it
    tells the embedder what document the chunk came from.

    Truncates the document to 200 chars to keep the embedding close to the
    natural Anthropic 50-100 token range.
    """
    topic = full_document.strip().replace("\n", " ")[:200]
    return f"Ngữ cảnh: {topic}" if topic else "Ngữ cảnh: (không rõ tài liệu)"


def _sanitize_response(response: str) -> str:
    """Strip prompt-injection markers and collapse whitespace.

    Two-pass cleanup: first drop entire ChatML role blocks (catches payloads
    between `<|im_start|>` and `<|im_end|>`), then strip any standalone
    role marker that survived. Whitespace is collapsed at the end so the
    embedding sees a clean string.
    """
    if not response:
        return ""
    cleaned = _FULL_INJECTION_BLOCK_RE.sub(" ", response)
    cleaned = _STANDALONE_MARKER_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


class Contextualizer:
    """Generate LLM-based context for each chunk.

    Usage:
        provider = LlamaCppContextualizerProvider(
            openai_url="http://localhost:8081/v1",
            client=httpx.AsyncClient(timeout=120.0),
        )
        ctx = Contextualizer(
            provider=provider,
            model="local-gemma4-e4b-q4-text",
            max_context_tokens=100,
            timeout_seconds=30.0,
        )
        context = await ctx.generate(chunk_text=..., full_document=...)

    On any failure (timeout, HTTP error, empty response, model
    hallucination) the call falls back to a deterministic header so
    ingestion never blocks on E4B availability.
    """

    # 4 chars/token is a reasonable Vietnamese-aware upper bound. 10×
    # headroom on max_tokens catches models that ignore max_tokens.
    _MAX_CHARS_PER_TOKEN = 4
    _HEADROOM = 10

    def __init__(
        self,
        *,
        provider: ContextualizerProvider,
        model: str,
        max_context_tokens: int = 100,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_context_tokens = max_context_tokens
        self._timeout = timeout_seconds

    @ObservabilityService.instance().observe("contextualize")
    async def generate(
        self,
        *,
        chunk_text: str,
        full_document: str,
    ) -> str:
        """Generate context for a single chunk. Never raises."""
        user_prompt = build_prompt(
            chunk_text=chunk_text, full_document=full_document
        )
        messages = [{"role": "user", "content": user_prompt}]

        try:
            async with asyncio.timeout(self._timeout):
                response = await self._provider.chat_completion(
                    messages=messages,
                    max_tokens=self._max_context_tokens,
                    temperature=0.0,
                )
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning(
                "contextualizer: timeout after %.1fs, using fallback",
                self._timeout,
            )
            return _deterministic_fallback(
                chunk_text=chunk_text, full_document=full_document
            )
        except Exception as exc:
            logger.warning(
                "contextualizer: LLM call failed (%r), using fallback", exc
            )
            return _deterministic_fallback(
                chunk_text=chunk_text, full_document=full_document
            )

        cleaned = _sanitize_response(response)
        if not cleaned:
            logger.debug("contextualizer: empty response after sanitize, using fallback")
            return _deterministic_fallback(
                chunk_text=chunk_text, full_document=full_document
            )

        cap = self._max_context_tokens * self._MAX_CHARS_PER_TOKEN * self._HEADROOM
        if len(cleaned) >= cap:
            # Truncate to cap-1 (strict less-than cap), preferring word
            # boundary. Fall back to the raw slice if rsplit returns empty.
            truncated = cleaned[: cap - 1]
            word_broken = truncated.rsplit(" ", 1)[0].rstrip()
            cleaned = word_broken if word_broken else truncated
        return cleaned


class LlamaCppContextualizerProvider:
    """Adapter that wraps a LlamaCppProvider to satisfy ContextualizerProvider.

    Used in main.py to wire the E4B Q4 background model (port 8081) into
    the Contextualizer. Keeping this as a thin wrapper means:
      - Tests don't have to mock an HTTP client; the existing
        `_StubProvider` in test_contextualizer.py is enough.
      - The Contextualizer never imports LlamaCppProvider directly,
        so the dependency graph stays clean.
      - Operators can swap the underlying model (gemma3-4b, qwen2-1.5b)
        by changing CONTEXTUALIZER_MODEL without touching this code.
    """

    def __init__(self, llama_cpp_provider: Any, model: str) -> None:
        # `Any` here because we don't want to import LlamaCppProvider at
        # module load time — circular import risk (providers depend on
        # service-layer models). The duck-typed protocol surface we use
        # is just `complete(messages, model, temperature, options)`.
        self._provider = llama_cpp_provider
        self._model = model

    async def chat_completion(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        from app.models.chat import Message

        msg_objects = [Message(role=m["role"], content=m["content"]) for m in messages]
        return await self._provider.complete(
            messages=msg_objects,
            model=self._model,
            temperature=temperature,
            options={"max_tokens": max_tokens},
        )
