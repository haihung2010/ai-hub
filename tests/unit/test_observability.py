"""Unit tests for observability.ObservabilityService.

The service must be a no-op when LANGFUSE_ENABLED=false (default) so
existing tests and local dev work without a Langfuse stack.
"""
import pytest

from app.core.config import Settings
from app.services.observability import ObservabilityService


# Pure unit tests — no DB access. Skip the autouse isolated_db fixture.
pytestmark = pytest.mark.no_isolated_db


@pytest.fixture
def disabled_settings(monkeypatch) -> Settings:
    for key in (
        "LANGFUSE_ENABLED", "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY", "LANGFUSE_OTLP_ENDPOINT",
        "LANGFUSE_FLUSH_INTERVAL_SECONDS", "LANGFUSE_SAMPLE_RATE",
    ):
        monkeypatch.delenv(key, raising=False)
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def disabled_service(disabled_settings: Settings) -> ObservabilityService:
    return ObservabilityService(disabled_settings)


def test_disabled_service_is_noop(disabled_service: ObservabilityService) -> None:
    """When LANGFUSE_ENABLED=false, observe() must be a passthrough decorator."""
    assert disabled_service.is_enabled() is False

    @disabled_service.observe("test.span")
    def add(a: int, b: int) -> int:
        return a + b

    # No-op decorator must preserve return value
    assert add(1, 2) == 3


def test_disabled_service_flush_is_safe(disabled_service: ObservabilityService) -> None:
    """flush() on a disabled service must not raise."""
    disabled_service.flush()  # should not raise


def test_disabled_service_does_not_require_opentelemetry(disabled_service: ObservabilityService) -> None:
    """Disabled service must not crash even if opentelemetry is missing."""
    # The whole point of no-op mode: don't touch opentelemetry at all
    # (we only import it in _init_otel which is skipped when disabled).
    assert disabled_service._otel_trace_provider is None
    assert disabled_service._otel_exporter is None
    assert disabled_service._otel_processor is None


def test_set_current_metadata_noop_when_disabled(disabled_service: ObservabilityService) -> None:
    """set_current_metadata on disabled service must not raise."""
    disabled_service.set_current_metadata(
        tenant_id="t1", project_id="p1", user_id="u1", session_id="s1"
    )


def test_span_context_manager_noop_when_disabled(disabled_service: ObservabilityService) -> None:
    """span() context manager on disabled service must yield None safely."""
    with disabled_service.span("test.span") as maybe_span:
        assert maybe_span is None
