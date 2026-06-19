"""Unit tests for ProviderRouter."""
import pytest
from app.services.provider_router import (
    TaskType,
    ProviderCapability,
    ProviderRouter,
    NoProviderError,
)


def test_task_type_enum_values():
    """TaskType enum exposes the 5 task categories."""
    assert TaskType.CHAT.value == "chat"
    assert TaskType.STRUCTMEM.value == "structmem"
    assert TaskType.SUMMARY.value == "summary"
    assert TaskType.CONTEXTUALIZE.value == "contextualize"
    assert TaskType.VISION.value == "vision"


def test_provider_capability_is_frozen():
    """ProviderCapability is immutable (frozen=True)."""
    cap = ProviderCapability(
        name="llama_cpp_12b",
        base_url="http://localhost:8080/v1",
        priority=1,
        supports={TaskType.CHAT, TaskType.CONTEXTUALIZE},
    )
    with pytest.raises((AttributeError, TypeError)):
        cap.priority = 99
