"""Structured stdlib logging configuration."""

import logging
import logging.config


def configure_logging(level: str = "INFO", security_log_file: str | None = None) -> None:
    handlers: dict[str, dict[str, str]] = {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    }
    security_handlers = ["console"]
    if security_log_file:
        handlers["security_file"] = {
            "class": "logging.FileHandler",
            "formatter": "default",
            "filename": security_log_file,
            "encoding": "utf-8",
        }
        security_handlers.append("security_file")

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
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
