"""Unit tests for structured JSON logging (P1.3, 2026-06-10)."""
from __future__ import annotations

import io
import json
import logging
import os

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_isolated_db]


def test_console_log_line_is_valid_json() -> None:
    """A stdlib log call must produce a parseable JSON line on stdout."""
    from app.core.logging import configure_logging

    # Redirect logging to a buffer so we can parse it
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.getLogger().handlers[0].formatter  # type: ignore[union-attr]
    )
    logger = logging.getLogger("test.json")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    configure_logging("INFO")
    # After reconfigure_logging the handler is reset. Set up fresh.
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.getLogger().handlers[0].formatter  # type: ignore[union-attr]
    )
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.info("hello world")

    line = buf.getvalue().strip().splitlines()[-1]
    obj = json.loads(line)
    assert obj["event"] == "hello world"
    assert "timestamp" in obj
    assert obj["level"] in ("info", "INFO")


def test_structlog_emits_json_with_context() -> None:
    """structlog.bind_contextvars propagates to the JSON output."""
    import structlog
    from app.core.logging import configure_logging

    configure_logging("INFO")
    log = structlog.get_logger("test.structlog")

    # Capture the rendered line
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.getLogger().handlers[0].formatter  # type: ignore[union-attr]
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="abc123", tenant_id="t1")
    log.info("user_login", user_id="u1")

    # Find the line with our event (multiple lines may be in the buffer
    # because configure_logging may emit its own startup logs)
    for line in reversed(buf.getvalue().strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "user_login":
            break
    else:
        raise AssertionError(f"no line with event=user_login in: {buf.getvalue()!r}")
    assert obj["request_id"] == "abc123"
    assert obj["tenant_id"] == "t1"
    assert obj["user_id"] == "u1"


def test_request_context_middleware_sets_x_request_id_header(client) -> None:
    """P1.3: every response carries X-Request-ID."""
    resp = client.get("/health")
    assert "X-Request-ID" in resp.headers
    # Server should mint one even if client didn't send
    assert len(resp.headers["X-Request-ID"]) >= 8


def test_request_context_middleware_echoes_client_supplied_id(client) -> None:
    """If the client sends X-Request-ID, the server echoes the same one."""
    custom = "trace-12345"
    resp = client.get("/health", headers={"X-Request-ID": custom})
    assert resp.headers["X-Request-ID"] == custom


def test_redaction_runs_inside_structlog_pipeline(monkeypatch) -> None:
    """PII inside a structlog field is redacted before the JSON render."""
    import structlog
    from app.core import logging as core_logging
    monkeypatch.setenv("LOG_REDACT_PII", "true")
    core_logging.configure_logging("INFO")

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(
        logging.getLogger().handlers[0].formatter  # type: ignore[union-attr]
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    log = structlog.get_logger("test.redact")
    log.info("phone_in_field", phone="0912345678")

    for line in reversed(buf.getvalue().strip().splitlines()):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "phone_in_field":
            break
    else:
        raise AssertionError(f"no matching line: {buf.getvalue()!r}")
    assert "[REDACTED-PHONE]" in obj["phone"]
    assert "0912345678" not in obj["phone"]
