"""Logging configuration.

Uses the standard library only. ``configure_logging`` is called once from the
application factory so that application logs and uvicorn's own logs share a
single format and level.
"""

import logging
import re
from collections.abc import Iterable
from logging.config import dictConfig

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

DEFAULT_LEVEL = "INFO"

REDACTED = "***REDACTED***"

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Groq API keys, wherever they surface -- our code, a third-party
    # traceback, or SDK debug output.
    (re.compile(r"gsk_[A-Za-z0-9_\-]{8,}"), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), f"Bearer {REDACTED}"),
    # scheme://user:password@host -- a database URL leaks differently from an
    # API key, and SQLAlchemy is not the only thing that might print one.
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]+):([^\s@/]+)@"),
        rf"\1:{REDACTED}@",
    ),
    # No leading \b: it would fail between the underscore and the name in
    # GROQ_API_KEY. An identifier prefix is consumed instead, so GROQ_API_KEY,
    # x-api-key, and a bare authorization all match.
    (
        re.compile(r"(?i)([A-Za-z0-9_-]*(?:authorization|api[_-]?key))(\s*[:=]\s*)\S+"),
        rf"\1\2{REDACTED}",
    ),
)


class SecretRedactingFilter(logging.Filter):
    """Strips credentials out of log records before they are emitted.

    Attached to the handler rather than to a logger, so it covers every logger
    in the process -- ours, the provider SDK's, and the HTTP client's alike.

    Redaction happens on the *formatted* message. A secret passed as a ``%s``
    argument never appears in ``record.msg``, so filtering the template alone
    would miss it; the rendered message is substituted back and the arguments
    cleared.

    This is a backstop, not the primary control. Nothing in the codebase is
    supposed to log a credential, a prompt, or model output in the first place.
    """

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._literals = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - never drop a record over this
            return True
        redacted = self.redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True

    def redact(self, text: str) -> str:
        """Return ``text`` with known credential shapes replaced."""
        for literal in self._literals:
            text = text.replace(literal, REDACTED)
        for pattern, replacement in _SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        return text


def _resolve_level(level: str) -> str:
    """Normalise a configured level name, falling back to ``INFO``."""
    candidate = level.strip().upper()
    if candidate in logging.getLevelNamesMapping():
        return candidate
    return DEFAULT_LEVEL


def configure_logging(
    level: str = DEFAULT_LEVEL, secrets: Iterable[str] = ()
) -> None:
    """Install the process-wide logging configuration.

    ``secrets`` are literal values to scrub from every record -- the configured
    provider API key, typically. Pattern-based redaction runs regardless.

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
            "filters": {
                "redact_secrets": {
                    "()": SecretRedactingFilter,
                    "secrets": tuple(secrets),
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "standard",
                    "stream": "ext://sys.stderr",
                    "filters": ["redact_secrets"],
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
