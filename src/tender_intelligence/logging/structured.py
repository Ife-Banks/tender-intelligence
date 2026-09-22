""":mod:`tender_intelligence.logging.structured` — structured JSON logging setup."""

from __future__ import annotations

import json
import logging
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
