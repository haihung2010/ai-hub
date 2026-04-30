"""Logging configuration tests."""

from __future__ import annotations

import logging

import pytest

from app.core.logging import configure_logging


@pytest.mark.unit
def test_configure_logging_creates_security_log_parent(tmp_path) -> None:
    security_log = tmp_path / "nested" / "security.log"

    configure_logging("INFO", str(security_log))
    logging.getLogger("app.security").warning("test security event")

    assert security_log.exists()
    assert "test security event" in security_log.read_text(encoding="utf-8")
