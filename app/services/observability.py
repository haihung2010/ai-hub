"""Observability service — wraps Langfuse + OpenTelemetry for ai-hub.

When LANGFUSE_ENABLED=false (default), the service is a no-op so existing
tests and local dev work without a Langfuse stack. When enabled, it
exports OTLP/HTTP traces to Langfuse v3 at /api/public/otel.

The `@observe()` decorator is the primary public API. Multi-tenant
metadata (tenant_id, project_id, user_id, session_id) is propagated
via the current trace context, not as decorator arguments.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any, TypeVar

from app.core.config import Settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class ObservabilityService:
    """Singleton wrapper around Langfuse + OpenTelemetry.

    The singleton is created in app/main.py at startup via
    `init_observability(settings)`. Tests that need a fresh instance
    can construct one directly.
    """

    _instance: "ObservabilityService | None" = None

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._enabled = settings.langfuse_enabled
        self._otel_trace_provider = None
        self._otel_exporter = None
        self._otel_processor = None
        if self._enabled:
            self._init_otel()

    @classmethod
    def instance(cls) -> "ObservabilityService":
        """Return the process-wide singleton, or a no-op fallback if not initialized.

        Why a no-op fallback: the @observe() decorators at class-body level
        (ai_service, llama_cpp, contextualizer, etc.) call instance() at
        import time. Before main.py has run init_observability(), the
        singleton is None and env vars may not be set — constructing a real
        Settings(_env_file=None) in that state fails the app boot. Falling
        back to a no-op keeps the import-time decorator chain safe; the real
        service replaces it once main.py calls init_observability().
        """
        if cls._instance is None:
            from app.core.config import Settings
            try:
                # Let pydantic-settings auto-load .env from CWD. If env is
                # missing, the except branch keeps the app bootable.
                return cls(Settings())
            except Exception:
                # Bootstrap no-op — same shape, langfuse_enabled=False, so
                # observe() returns _noop_decorator and flush() is a no-op.
                noop = cls.__new__(cls)
                noop._settings = None  # type: ignore[attr-defined]
                noop._enabled = False
                noop._otel_trace_provider = None
                noop._otel_exporter = None
                noop._otel_processor = None
                return noop
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "ObservabilityService") -> None:
        cls._instance = instance

    def is_enabled(self) -> bool:
        return self._enabled

    def _init_otel(self) -> None:
        """Initialize OpenTelemetry OTLP/HTTP exporter to Langfuse."""
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create({"service.name": "ai-hub"})
            provider = TracerProvider(resource=resource)
            # Langfuse OTLP/HTTP auth is HTTP Basic with public_key:secret_key
            # (NOT Bearer). Verified against the v3.194 OTLP endpoint.
            import base64
            basic_token = base64.b64encode(
                f"{self._settings.langfuse_public_key}:{self._settings.langfuse_secret_key}".encode()
            ).decode()
            exporter = OTLPSpanExporter(
                endpoint=f"{self._settings.langfuse_otlp_endpoint}/v1/traces",
                headers={
                    "Authorization": f"Basic {basic_token}",
                },
            )
            processor = BatchSpanProcessor(
                exporter,
                schedule_delay_millis=int(self._settings.langfuse_flush_interval_seconds * 1000),
            )
            provider.add_span_processor(processor)
            trace.set_tracer_provider(provider)
            self._otel_trace_provider = provider
            self._otel_exporter = exporter
            self._otel_processor = processor
            logger.info("ObservabilityService: OTLP exporter initialized to %s", self._settings.langfuse_otlp_endpoint)
        except ImportError as exc:
            logger.warning("ObservabilityService: OpenTelemetry not installed (%s), disabling", exc)
            self._enabled = False

    def observe(self, name: str) -> Callable[[F], F]:
        """Decorator that wraps a function in an OpenTelemetry span.

        When disabled, this is a passthrough decorator (no overhead).
        """
        if not self._enabled:
            return self._noop_decorator
        return self._otel_observe(name)

    @staticmethod
    def _noop_decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        return wrapper  # type: ignore[return-value]

    def _otel_observe(self, name: str) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            from opentelemetry import trace

            tracer = trace.get_tracer("ai-hub")

            @wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with tracer.start_as_current_span(name) as span:
                    try:
                        result = func(*args, **kwargs)
                        span.set_status(trace.Status(trace.StatusCode.OK))
                        return result
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
                        raise

            return wrapper  # type: ignore[return-value]

        return decorator

    @contextmanager
    def span(self, name: str, **attributes: Any):
        """Context manager for manual span creation (e.g., inside non-decorated code)."""
        if not self._enabled:
            yield None
            return
        from opentelemetry import trace
        tracer = trace.get_tracer("ai-hub")
        with tracer.start_as_current_span(name) as span:
            for key, value in attributes.items():
                span.set_attribute(key, value)
            yield span

    def set_current_metadata(
        self,
        *,
        tenant_id: str,
        project_id: str,
        user_id: str | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Attach multi-tenant metadata to the current trace/span.

        Call this once at the start of a request (in ai_service.chat() or
        equivalent) so all child spans inherit the metadata.
        """
        if not self._enabled:
            return
        from opentelemetry import trace
        span = trace.get_current_span()
        span.set_attribute("ai_hub.tenant_id", tenant_id)
        span.set_attribute("ai_hub.project_id", project_id)
        if user_id is not None:
            span.set_attribute("ai_hub.user_id", user_id)
        if session_id is not None:
            span.set_attribute("ai_hub.session_id", session_id)
        if trace_id is not None:
            span.set_attribute("ai_hub.trace_id", trace_id)

    def flush(self) -> None:
        """Flush pending spans. Call at app shutdown."""
        if not self._enabled or self._otel_processor is None:
            return
        try:
            self._otel_processor.force_flush(timeout_millis=5000)
        except Exception as exc:
            logger.warning("ObservabilityService.flush failed: %s", exc)


def init_observability(settings: Settings) -> ObservabilityService:
    """Create and register the process-wide ObservabilityService singleton."""
    svc = ObservabilityService(settings)
    ObservabilityService.set_instance(svc)
    return svc
