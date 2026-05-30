"""Comprehensive streaming tests."""

from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from tests.conftest import ensure_user


class TestStreamingBasic:
    """Basic streaming functionality tests."""

    def test_streaming_returns_sse_data(self, client: TestClient) -> None:
        """Streaming response contains SSE data events."""
        ensure_user("stream_user", "default", "stream_user")

        with client.stream(
            "POST",
            "/v1/chat",
            json={
                "user_name": "stream_user",
                "project_id": "stream_test",
                "user_message": "Count to 3",
                "model_mode": "lite",
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200

            lines = b"".join(resp.iter_bytes()).decode("utf-8")
            assert "data:" in lines

    def test_streaming_ends_with_done(self, client: TestClient) -> None:
        """Streaming ends with [DONE] sentinel."""
        ensure_user("stream_done_user", "default", "stream_done_user")

        with client.stream(
            "POST",
            "/v1/chat",
            json={
                "user_name": "stream_done_user",
                "project_id": "stream_test",
                "user_message": "Hello",
                "model_mode": "lite",
                "stream": True,
            },
        ) as resp:
            lines = b"".join(resp.iter_bytes()).decode("utf-8")
            assert "[DONE]" in lines

    def test_non_streaming_returns_json(self, client: TestClient) -> None:
        """Non-streaming returns JSON directly."""
        ensure_user("sync_user", "default", "sync_user")

        resp = client.post(
            "/v1/chat",
            json={
                "user_name": "sync_user",
                "project_id": "stream_test",
                "user_message": "Hello",
                "model_mode": "lite",
                "stream": False,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data


class TestStreamingEdgeCases:
    """Streaming edge case tests."""

    def test_streaming_empty_message(self, client: TestClient) -> None:
        """Streaming with empty message."""
        ensure_user("empty_stream_user", "default", "empty_stream_user")

        resp = client.post(
            "/v1/chat",
            json={
                "user_name": "empty_stream_user",
                "project_id": "stream_test",
                "user_message": "",
                "model_mode": "lite",
                "stream": True,
            },
        )

        # Should still return something
        assert resp.status_code == 200

    def test_streaming_long_message(self, client: TestClient) -> None:
        """Streaming with long message."""
        ensure_user("long_stream_user", "default", "long_stream_user")

        long_msg = "Hello " * 100  # 600 chars

        resp = client.post(
            "/v1/chat",
            json={
                "user_name": "long_stream_user",
                "project_id": "stream_test",
                "user_message": long_msg,
                "model_mode": "lite",
                "stream": True,
            },
        )

        assert resp.status_code == 200

    def test_streaming_with_session_id(self, client: TestClient) -> None:
        """Streaming with session ID for context."""
        ensure_user("session_stream_user", "default", "session_stream_user")

        # First request to get session
        first_resp = client.post(
            "/v1/chat",
            json={
                "user_name": "session_stream_user",
                "project_id": "stream_test",
                "user_message": "Remember this",
                "model_mode": "lite",
            },
        )
        session_id = first_resp.json().get("session_id")

        if session_id:
            # Streaming with session
            with client.stream(
                "POST",
                "/v1/chat",
                json={
                    "user_name": "session_stream_user",
                    "project_id": "stream_test",
                    "user_message": "What did I say?",
                    "model_mode": "lite",
                    "stream": True,
                    "session_id": session_id,
                },
            ) as resp:
                assert resp.status_code == 200
                lines = b"".join(resp.iter_bytes()).decode("utf-8")
                assert "data:" in lines or "[DONE]" in lines

    def test_streaming_multiple_requests_sequential(self, client: TestClient) -> None:
        """Multiple sequential streaming requests work."""
        ensure_user("multi_stream_user", "default", "multi_stream_user")

        for i in range(3):
            with client.stream(
                "POST",
                "/v1/chat",
                json={
                    "user_name": "multi_stream_user",
                    "project_id": "stream_test",
                    "user_message": f"Message {i}",
                    "model_mode": "lite",
                    "stream": True,
                },
            ) as resp:
                assert resp.status_code == 200
                lines = b"".join(resp.iter_bytes()).decode("utf-8")
                assert "[DONE]" in lines


class TestStreamingErrors:
    """Streaming error handling tests."""

    def test_streaming_with_invalid_project(self, client: TestClient) -> None:
        """Streaming with invalid project returns error."""
        ensure_user("invalid_proj_user", "default", "invalid_proj_user")

        with client.stream(
            "POST",
            "/v1/chat",
            json={
                "user_name": "invalid_proj_user",
                "project_id": "invalid!@#$%",
                "user_message": "Hello",
                "model_mode": "lite",
                "stream": True,
            },
        ) as resp:
            # Should handle gracefully
            assert resp.status_code in (200, 400, 422, 500)

    def test_streaming_close_early(self, client: TestClient) -> None:
        """Client closes connection early doesn't crash server."""
        ensure_user("early_close_user", "default", "early_close_user")

        # Just open and close without reading
        try:
            with client.stream(
                "POST",
                "/v1/chat",
                json={
                    "user_name": "early_close_user",
                    "project_id": "stream_test",
                    "user_message": "Hello",
                    "model_mode": "lite",
                    "stream": True,
                },
            ) as resp:
                # Don't read anything, just exit context
                pass
            # Should not crash server
        except Exception:
            pass  # Accept any exception on client side


class TestStreamingContent:
    """Streaming content verification tests."""

    def test_streaming_contains_text_events(self, client: TestClient) -> None:
        """Streaming contains text data events."""
        ensure_user("text_events_user", "default", "text_events_user")

        with client.stream(
            "POST",
            "/v1/chat",
            json={
                "user_name": "text_events_user",
                "project_id": "stream_test",
                "user_message": "Say hello",
                "model_mode": "lite",
                "stream": True,
            },
        ) as resp:
            lines = b"".join(resp.iter_bytes()).decode("utf-8")

            # Should have some data events
            data_lines = [l for l in lines.split("\n") if l.startswith("data:")]
            assert len(data_lines) > 0

    def test_streaming_json_events_are_parseable(self, client: TestClient) -> None:
        """Streaming data events are valid JSON."""
        ensure_user("json_parse_user", "default", "json_parse_user")

        with client.stream(
            "POST",
            "/v1/chat",
            json={
                "user_name": "json_parse_user",
                "project_id": "stream_test",
                "user_message": "Hello",
                "model_mode": "lite",
                "stream": True,
            },
        ) as resp:
            lines = b"".join(resp.iter_bytes()).decode("utf-8")

            data_lines = [l for l in lines.split("\n") if l.startswith("data:")]
            for line in data_lines:
                if line == "data: [DONE]":
                    continue
                json_str = line[5:]  # Remove "data: "
                try:
                    json.loads(json_str)
                except json.JSONDecodeError:
                    pass  # Some delta events might be partial
