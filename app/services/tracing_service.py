"""Thin Langfuse wrapper for ai-hub (v4.x API).

Exports:
  - ``trace_chat(...)`` → starts a top-level observation for a chat request
  - ``span_rag(...)`` / ``span_llm(...)`` → context managers for sub-spans
  - ``record_span_metadata(...)`` / ``record_span_tokens(...)`` → mutate
  - ``shutdown()`` → flush pending spans on FastAPI shutdown
  - ``is_enabled()`` → public re-export for tests

When LANGFUSE_PUBLIC_KEY is empty (the default), every helper is a no-op
and zero network calls are made — so ai-hub continues to work without
tracing when env vars are unset.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

# The Langfuse client is process-local and NOT safe to share across event loops
# or threads concurrently when constructing (background flush threads bind to
# the creating thread). We guard creation with a lock and lazily build one
# client per process. Call reset() in tests that need a fresh client.
_langfuse_client: Any | None = None
_langfuse_lock: threading.Lock = threading.Lock()
_enabled: bool | None = None


def _is_enabled() -> bool:
    global _enabled
    if _enabled is None:
        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk = os.getenv("LANGFUSE_SECRET_KEY", "")
        _enabled = bool(pk) and bool(sk)
    return _enabled


def _get_client() -> Any:
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client
    with _langfuse_lock:
        if _langfuse_client is None:
            from langfuse import Langfuse  # local import — keeps startup fast when disabled

            _langfuse_client = Langfuse(
                public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
                secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
                host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
                flush_at=2,
                flush_interval=1.0,
            )
            logger.info("Langfuse tracing enabled host=%s", os.getenv("LANGFUSE_HOST"))
    return _langfuse_client


def reset() -> None:
    """Clear the cached Langfuse client. Intended for tests."""
    global _langfuse_client
    with _langfuse_lock:
        if _langfuse_client is not None:
            try:
                _langfuse_client.flush()
            except Exception:
                pass
        _langfuse_client = None


class TracingService:
    """Thin class wrapper exposing the module-level tracing helpers.

    The Langfuse client itself remains a process-local singleton (see module
    docstring) — this class is just a convenient namespace for callers that
    prefer dependency-injection over module-level functions.
    """

    def __init__(self) -> None:
        pass

    def is_enabled(self) -> bool:
        return _is_enabled()

    def trace_chat(self, **kwargs: Any) -> Any | None:
        return trace_chat(**kwargs)

    def finish_chat(self, span: Any | None) -> None:
        finish_chat(span)

    def shutdown(self) -> None:
        shutdown()

    def reset(self) -> None:
        reset()


def get_tracing_service() -> TracingService:
    """Return a process-wide TracingService instance."""
    return _tracing_service


_tracing_service: TracingService = TracingService()


def trace_chat(
    *,
    trace_id: str,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any | None:
    """Start a top-level chat observation. Returns a context-manager-style
    object that you should keep alive for the duration of the request.

    The simplest pattern: ``with trace_chat(...) as span: ...`` — but for
    routes that already use streaming/non-streaming branches, we expose
    ``enter``/``exit`` semantics via a thin wrapper that the caller
    closes via ``finish_chat``.

    Returns a small holder object that exposes ``update()`` and
    ``end()``; when tracing is disabled the holder's methods are no-ops.
    """
    if not _is_enabled():
        return _NullSpan()
    client = _get_client()
    # Langfuse v4 expects a context manager; we wrap it so the route can
    # use it without an explicit ``with`` block.
    span = client.start_as_current_observation(
        name="ai-hub.chat",
        as_type="span",
        trace_context={"trace_id": trace_id},
        metadata={
            **(metadata or {}),
            "user_id": user_id,
            "session_id": session_id,
        },
    )
    # ``span`` is a context manager — entering it sets the current span.
    # We enter immediately so the caller can update it; closing is the
    # caller's responsibility via finish_chat.
    span.__enter__()
    return span


def finish_chat(span: Any | None) -> None:
    """Close a span started by ``trace_chat``. No-op when tracing is off
    or the span is the null span."""
    if span is None or isinstance(span, _NullSpan):
        return
    try:
        span.__exit__(None, None, None)
    except Exception as exc:  # pragma: no cover
        logger.debug("finish_chat failed: %s", exc)


@contextmanager
def span_rag(
    parent: Any | None,
    *,
    name: str = "rag_retrieval",
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Span around the RAG retrieval step. No-op when tracing is off."""
    if parent is None or isinstance(parent, _NullSpan) or not _is_enabled():
        yield None
        return
    client = _get_client()
    with client.start_as_current_observation(
        name=name,
        as_type="retriever",
        metadata=metadata or {},
    ) as span:
        yield span


@contextmanager
def span_llm(
    parent: Any | None,
    *,
    name: str = "llm_call",
    model: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Span around the LLM call. No-op when tracing is off."""
    if parent is None or isinstance(parent, _NullSpan) or not _is_enabled():
        yield None
        return
    client = _get_client()
    with client.start_as_current_observation(
        name=name,
        as_type="generation",
        model=model,
        metadata=metadata or {},
    ) as span:
        yield span


def record_span_metadata(span: Any | None, **fields: Any) -> None:
    """Update metadata on a span. Best-effort — failures are logged but
    never raised so a tracing hiccup can never break the chat path."""
    if span is None or isinstance(span, _NullSpan) or not _is_enabled():
        return
    try:
        meta = {k: v for k, v in fields.items() if v is not None}
        if hasattr(span, "update"):
            span.update(metadata=meta)
    except Exception as exc:  # pragma: no cover
        logger.debug("record_span_metadata failed: %s", exc)


def record_span_tokens(
    span: Any | None,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    model: str | None = None,
) -> None:
    """Record token usage on a span. Langfuse generations have a
    dedicated ``usage_details`` field that surfaces as a cost column in
    the UI."""
    if span is None or isinstance(span, _NullSpan) or not _is_enabled():
        return
    try:
        usage: dict[str, int] = {}
        if prompt_tokens is not None:
            usage["input"] = prompt_tokens
        if completion_tokens is not None:
            usage["output"] = completion_tokens
        if hasattr(span, "update"):
            update_kwargs: dict[str, Any] = {}
            if usage:
                update_kwargs["usage_details"] = usage
            if model is not None:
                update_kwargs["model"] = model
            if update_kwargs:
                span.update(**update_kwargs)
    except Exception as exc:  # pragma: no cover
        logger.debug("record_span_tokens failed: %s", exc)


def shutdown() -> None:
    """Flush the Langfuse background queue on FastAPI shutdown."""
    if _langfuse_client is None or not _is_enabled():
        return
    try:
        _langfuse_client.flush()
    except Exception as exc:  # pragma: no cover
        logger.debug("Langfuse flush failed: %s", exc)
    _shutdown_otel_exporter()


def is_enabled() -> bool:
    return _is_enabled()


# === OpenTelemetry exporter (optional, additive) ============================
# When LANGFUSE_OTEL_ENDPOINT is set (e.g. http://otel-collector:4317), every
# trace is also exported via OTLP/gRPC to a generic OTel collector. This makes
# ai-hub traces visible in Jaeger / Tempo / SigNoz / Honeycomb alongside
# Langfuse — useful when you want both Langfuse's UI and a free backend.
# Disabled by default; pure additive — no effect on existing Langfuse flow.

_otel_tracer: Any | None = None
_otel_exporter_installed: bool = False


def _otel_endpoint() -> str:
    return os.getenv("LANGFUSE_OTEL_ENDPOINT", "")


def _install_otel_exporter() -> None:
    """Mirror Langfuse spans to an OTel collector via OTLP/gRPC. No-op if disabled."""
    global _otel_exporter_installed, _otel_tracer
    if _otel_exporter_installed or not _otel_endpoint():
        return
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-not-found]
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import-not-found]

        provider = TracerProvider(
            resource=Resource.create({"service.name": "ai-hub"})
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=_otel_endpoint(), insecure=True)))
        trace.set_tracer_provider(provider)
        _otel_tracer = trace.get_tracer("ai-hub")
        _otel_exporter_installed = True
        logger.info("OTel OTLP exporter enabled endpoint=%s", _otel_endpoint())
    except Exception as exc:  # pragma: no cover - optional dep
        logger.debug("OTel exporter init skipped: %s", exc)


def _shutdown_otel_exporter() -> None:
    if not _otel_exporter_installed:
        return
    try:
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
        from opentelemetry import trace as _trace  # type: ignore[import-not-found]
        provider = _trace.get_tracer_provider()
        if isinstance(provider, TracerProvider):
            provider.shutdown()
    except Exception:  # pragma: no cover
        pass


# Auto-install on first import when endpoint configured (after _is_enabled check).
if _is_enabled() and _otel_endpoint():
    _install_otel_exporter()


class _NullSpan:
    """No-op span returned when tracing is disabled or before init."""

    def update(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None

    def __enter__(self) -> "_NullSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        return None
