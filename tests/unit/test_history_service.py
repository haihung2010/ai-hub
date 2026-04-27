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
