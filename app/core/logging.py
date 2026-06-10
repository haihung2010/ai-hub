"""Structured stdlib logging configuration."""

import logging
import logging.config
from pathlib import Path
from typing import Any

from app.core.log_redaction import LogRedactionFilter


def configure_logging(level: str = "INFO", security_log_file: str | None = None) -> None:
    handlers: dict[str, dict[str, Any]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "filters": ["pii"],  # P0.3: log redaction filter
        },
    }
    security_handlers = ["console"]
    if security_log_file:
        security_path = Path(security_log_file)
        security_path.parent.mkdir(parents=True, exist_ok=True)
        handlers["security_file"] = {
            "class": "logging.FileHandler",
            "formatter": "default",
            "filename": str(security_path),
            "encoding": "utf-8",
            "filters": ["pii"],  # P0.3: same filter on security log
        }
        security_handlers.append("security_file")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
            },
            "filters": {
                # The filter is a no-op unless LOG_REDACT_PII is set, so
                # existing deployments see no behavior change.
                "pii": {"()": "app.core.log_redaction.LogRedactionFilter"},
            },
            "handlers": handlers,
            "loggers": {
                "app.security": {
                    "level": "WARNING",
                    "handlers": security_handlers,
                    "propagate": False,
                }
            },
            "root": {"level": level.upper(), "handlers": ["console"]},
        }
    )
