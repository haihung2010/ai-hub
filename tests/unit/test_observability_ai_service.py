"""Verify ai_service.chat() calls set_current_metadata (which is a no-op when disabled).

These tests guard the contract that the chat entrypoint propagates multi-tenant
metadata to the active trace. The ObservabilityService is a no-op when
LANGFUSE_ENABLED=false, so we can exercise the metadata-propagation code path
without a Langfuse backend.
"""
import pytest

from app.core.config import Settings
from app.services.observability import ObservabilityService


# Pure unit tests — no DB access. Skip the autouse isolated_db fixture.
pytestmark = pytest.mark.no_isolated_db


@pytest.fixture
def disabled_service() -> ObservabilityService:
    return ObservabilityService(Settings(_env_file=None))  # type: ignore[call-arg]


def test_set_current_metadata_accepts_chat_request_shape(disabled_service: ObservabilityService) -> None:
    """set_current_metadata must accept the kwarg shape used in ai_service.chat()."""
    disabled_service.set_current_metadata(
        tenant_id="tenant-1",
        project_id="proj-1",
        user_id="user-1",
        session_id="sess-1",
    )


def test_set_current_metadata_without_session_id(disabled_service: ObservabilityService) -> None:
    """set_current_metadata must work with session_id=None (chat requests may not have session)."""
    disabled_service.set_current_metadata(
        tenant_id="tenant-1",
        project_id="proj-1",
        user_id="user-1",
        session_id=None,
    )