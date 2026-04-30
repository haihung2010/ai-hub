"""HistoryService persists user-scoped sessions and unsummarized filters."""

from __future__ import annotations

import pytest

from app.services.history_service import HistoryService


@pytest.mark.unit
def test_create_session_persists_user_id() -> None:
    service = HistoryService()

    session_id = service.create_session("iot", user_id="user-123")
    messages = service.get_session_messages(session_id)

    assert session_id
    assert messages == []
    assert service.session_belongs_to(session_id, "iot", user_id="user-123")


@pytest.mark.unit
def test_session_belongs_to_rejects_wrong_tenant() -> None:
    service = HistoryService()

    session_id = service.create_session("stock_prediction", user_id="user-123", tenant_id="stock")

    assert service.session_belongs_to(session_id, "stock_prediction", tenant_id="stock", user_id="user-123")
    assert not service.session_belongs_to(session_id, "stock_prediction", tenant_id="other", user_id="user-123")


@pytest.mark.unit
def test_get_session_messages_filters_summarized_rows() -> None:
    service = HistoryService()
    session_id = service.create_session("iot", user_id="user-123")

    service.save_message(
        session_id,
        "user",
        "old summarized",
        user_id="user-123",
        is_summarized=True,
    )
    service.save_message(
        session_id,
        "assistant",
        "recent live",
        user_id="user-123",
        is_summarized=False,
    )

    all_messages = service.get_session_messages(session_id)
    live_messages = service.get_session_messages(session_id, only_unsummarized=True)

    assert [message.content for message in all_messages] == [
        "old summarized",
        "recent live",
    ]
    assert [message.content for message in live_messages] == ["recent live"]


@pytest.mark.unit
def test_get_session_messages_limit_returns_most_recent_messages_in_chronological_order() -> None:
    service = HistoryService()
    session_id = service.create_session("iot", user_id="user-recent-history")

    for idx in range(5):
        service.save_message(
            session_id,
            "user",
            f"message {idx}",
            user_id="user-recent-history",
        )

    messages = service.get_session_messages(session_id, limit=2)

    assert [message.content for message in messages] == ["message 3", "message 4"]
