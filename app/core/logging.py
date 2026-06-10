"""Structured stdlib + structlog configuration.

P1.3 (2026-06-10): switch to structlog with a JSON renderer so that
log output is machine-parseable (Splunk/Datadog/Loki friendly). The
P0.3 redaction filter is still active — it runs as a processor in
the structlog pipeline.

Reference: Stripe Canonical Log Lines
(https://stripe.com/blog/canonical-log-lines).

Stdlib logging still works (for libraries that haven't been
converted). The stdlib formatter is also JSON via the structlog
``ForeignLoggerLogfmt`` pattern.
"""
import logging
import logging.config
import sys
from pathlib import Path
from typing import Any

import structlog

from app.core.log_redaction import LogRedactionFilter, _redact_text


# P1.3: processors that run on every log event. The P0.3 redaction
# runs FIRST so any structured fields ("api_key", "tenant_id",
# "request_id") are scrubbed before the JSON renderer serialises.
def _redaction_processor(_logger, _method, event_dict):
    for k in list(event_dict.keys()):
        v = event_dict[k]
        if isinstance(v, str):
            event_dict[k] = _redact_text(v)
    return event_dict


def configure_logging(level: str = "INFO", security_log_file: str | None = None) -> None:
    # Configure stdlib first so loggers created before this call
    # (by libraries) pick up the JSON formatter.
    handlers: dict[str, dict[str, Any]] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    }
    security_handlers = ["console"]
    if security_log_file:
        security_path = Path(security_log_file)
        security_path.parent.mkdir(parents=True, exist_ok=True)
        handlers["security_file"] = {
            "class": "logging.FileHandler",
            "formatter": "json",
            "filename": str(security_path),
            "encoding": "utf-8",
        }
        security_handlers.append("security_file")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                # P1.3: JSON formatter. All log lines become valid JSON
                # with timestamp, level, logger, message, and any
                # structlog context (request_id, tenant_id, ...).
                "json": {
                    "()": "structlog.stdlib.ProcessorFormatter",
                    "processor": structlog.processors.JSONRenderer(),
                    "foreign_pre_chain": [
                        structlog.contextvars.merge_contextvars,
                        structlog.processors.TimeStamper(fmt="iso"),
                        structlog.processors.add_log_level,
                        _redaction_processor,
                    ],
                },
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

    # Configure structlog to interop with stdlib (so a `getLogger(__name__)`
    # call from app code emits the same JSON format).
    # We DO NOT include JSONRenderer here — the stdlib formatter
    # (ProcessorFormatter with JSONRenderer) does the final render.
    # Including both would double-encode the output. Instead we
    # hand off to wrap_for_formatter.
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            _redaction_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
