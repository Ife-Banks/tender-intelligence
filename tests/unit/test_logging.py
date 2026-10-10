"""Unit tests for the structured logging formatter: JSON shape, correlation injection,
secret scubbing and error-code fields.
"""

from __future__ import annotations

import json

from tender_intelligence.core.correlation import correlation_context
from tender_intelligence.logging.structured import REDACTED, JsonFormatter


class TestJsonFormatter:
    def _record(self, msg="hello", **extra):
        import logging

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_basic_shape(self):
        line = JsonFormatter().format(self._record())
        data = json.loads(line)
        assert data["message"] == "hello"
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert "ts" in data

    def test_correlation_flow(self):
        with correlation_context("abc-123") as cid:
            line = JsonFormatter().format(self._record())
        data = json.loads(line)
        assert data["correlation_id"] == cid == "abc-123"

    def test_stage_status_error_code(self):
        line = JsonFormatter().format(
            self._record(stage="fetch", status="failed", error_code="source_unreachable")
        )
        data = json.loads(line)
        assert data["stage"] == "fetch"
        assert data["status"] == "failed"
        assert data["error_code"] == "source_unreachable"

    def test_extra_scrubbed(self):
        record = self._record(
            extra={
                "api_key": "SK-SECRET-123",
                "safe": "value",
                "nested": {"token": "TOKEN-SECRET-456"},
            }
        )
        line = JsonFormatter().format(record)
        data = json.loads(line)
        assert data["extra"]["api_key"] == REDACTED
        assert data["extra"]["safe"] == "value"
        assert data["extra"]["nested"]["token"] == REDACTED
        assert "SK-SECRET-123" not in line
        assert "TOKEN-SECRET-456" not in line

    def _raised(self, message="boom"):
        import logging
        import sys

        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="scheduler poll failed", args=(), exc_info=None,
        )
        try:
            raise RuntimeError(message)
        except RuntimeError:
            record.exc_info = sys.exc_info()
        return record

    def test_exception_detail_is_logged_not_discarded(self):
        """A failure whose cause is unlogged cannot be acted on (v1.1 §2.9).

        Regression guard: the formatter ignored ``record.exc_info`` entirely, so every
        ``logger.exception(...)`` in the codebase announced a failure and then threw away why.
        """
        data = json.loads(JsonFormatter().format(self._raised()))
        assert data["error_type"] == "RuntimeError"
        assert "boom" in data["error_message"]
        assert "Traceback (most recent call last)" in data["traceback"]
        assert "raise RuntimeError" in data["traceback"]

    def test_record_without_exception_gains_no_error_keys(self):
        data = json.loads(JsonFormatter().format(self._record()))
        assert set(data) == {"ts", "level", "logger", "message"}

    def test_exception_text_is_scrubbed(self):
        """A traceback must not become a secret-leak path (PROJECT_RULES #12)."""
        data = json.loads(
            JsonFormatter().format(self._raised("connect failed password=hunter2"))
        )
        assert "hunter2" not in data["error_message"]
        assert "hunter2" not in data["traceback"]
