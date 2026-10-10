""":mod:`tender_intelligence.logging.structured` — structured JSON logging setup."""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from tender_intelligence.core.correlation import get_correlation_id

SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "private_key",
        "access_key",
    }
)
REDACTED = "[REDACTED]"

#: ``key=value`` / ``key: value`` inside free text. Key-based scrubbing cannot see these: a
#: message like "connect failed (password=hunter2)" has no dict key to match, and exception
#: text and tracebacks are exactly the place such strings turn up (connection URLs, request
#: reprs). Logging a traceback therefore has to scrub content as well as structure, or
#: PROJECT_RULES #12 ("no secrets in logs") would be satisfied only until the first failure.
_TEXT_SECRET = re.compile(
    r"(?i)\b(" + "|".join(sorted(SENSITIVE_KEYS)) + r")\b(\s*[=:]\s*)"
    r"""("[^"]*"|'[^']*'|[^\s,;)\]}]+)"""
)


def _scrub_text(text: str) -> str:
    """Redact secret-looking ``key=value`` pairs embedded in a message or traceback."""

    def replace(match: re.Match[str]) -> str:
        key, separator = match.group(1), match.group(2)
        return f"{key}{separator}{REDACTED}"

    return _TEXT_SECRET.sub(replace, text)


def _scrub_value(value: Any, key: str | None = None) -> Any:
    """Recurse through a value, redacting any key/leaf that may carry a secret."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if k.lower() in SENSITIVE_KEYS else _scrub_value(v, k))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(v, key) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v, key) for v in value)
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    return value


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON.

    Every record: ``ts``, ``level``, ``logger``, ``message``, optional ``correlation_id``,
    ``stage``, ``status``, ``error_code``, plus ``**extra`` (scrubbed).
    """

    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = getattr(record, "correlation_id", None) or get_correlation_id()
        if correlation_id:
            base["correlation_id"] = correlation_id
        for field in ("stage", "status", "error_code"):
            value = getattr(record, field, None)
            if value:
                base[field] = value
        extra = getattr(record, "extra", None)
        if extra:
            base["extra"] = _scrub_value(extra)
        if record.exc_info:
            # A failure whose cause is unlogged cannot be acted on, which is exactly the "fail
            # loudly" promise v1.1 §2.9 makes. This formatter previously dropped `exc_info`
            # entirely, so every `logger.exception(...)` in the codebase — the scheduler poll,
            # the run coordinator, acquisition, notification — announced that something broke
            # and then discarded why.
            exc_type, exc_value, _ = record.exc_info
            base["error_type"] = getattr(exc_type, "__name__", str(exc_type))
            base["error_message"] = _scrub_text(str(exc_value))
            base["traceback"] = _scrub_text(self.formatException(record.exc_info))
        try:
            return json.dumps(base, separators=(",", ":"), default=str)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return json.dumps({"level": record.levelname, "message": repr(base)})


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure a root logger that emits structured JSON to stdout.

    The correlation-ID context is picked up live from ``core.correlation``; extra fields
    passed via ``logger.info(..., extra={"stage": ..., "status": ...})`` are scrubbed.
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    return root


def get_logger(name: str = "tender_intelligence") -> logging.Logger:
    """Return a module logger; extra records are scrubbed by the formatter."""
    return logging.getLogger(name)
