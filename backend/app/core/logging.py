"""Logging configuration.

Uses the standard library only. ``configure_logging`` is called once from the
application factory so that application logs and uvicorn's own logs share a
single format and level.
"""

import logging
from logging.config import dictConfig

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

DEFAULT_LEVEL = "INFO"


def _resolve_level(level: str) -> str:
    """Normalise a configured level name, falling back to ``INFO``."""
    candidate = level.strip().upper()
    if candidate in logging.getLevelNamesMapping():
        return candidate
    return DEFAULT_LEVEL


def configure_logging(level: str = DEFAULT_LEVEL) -> None:
    """Install the process-wide logging configuration.

    Safe to call more than once; the configuration is fully declarative and
    replaces whatever was there before.
    """
    resolved = _resolve_level(level)

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": LOG_FORMAT,
                    "datefmt": DATE_FORMAT,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "stream": "ext://sys.stderr",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": resolved,
            },
            # Let uvicorn's loggers fall through to the root handler above so
            # every line in the process looks the same.
            "loggers": {
                "uvicorn": {"handlers": [], "propagate": True, "level": resolved},
                "uvicorn.error": {"handlers": [], "propagate": True, "level": resolved},
                "uvicorn.access": {"handlers": [], "propagate": True, "level": resolved},
            },
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module logger. Thin wrapper kept for a single import site."""
    return logging.getLogger(name)
