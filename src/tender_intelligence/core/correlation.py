"""Correlation-ID context for tender processing.

Every tender processing operation carries a correlation ID (PROJECT_RULES #15). The ID is
generated at pipeline entry and threaded through structured log records and stage results.
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator

from typing import Final

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

KEY: Final[str] = "correlation_id"


def new_correlation_id() -> str:
    """Generate a new correlation ID (UUID4 hex, string length 36)."""
    return str(uuid.uuid4())


def get_correlation_id() -> str | None:
    """Return the correlation ID active in the current context, if any."""
    return _current.get()


def set_correlation_id(correlation_id: str | None) -> None:
    """Set the correlation ID for the current context."""
    _current.set(correlation_id)


@contextmanager
def correlation_context(correlation_id: str | None = None) -> Iterator[str]:
    """Context manager that scopes a correlation ID to a block.

    If none is supplied, a fresh ID is generated. The previous value is restored on exit.
    """
    previous = _current.get()
    active = correlation_id if correlation_id is not None else new_correlation_id()
    token = _current.set(active)
    try:
        yield active
    finally:
        _current.reset(token)