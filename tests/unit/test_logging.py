"""Unit tests for the structured logging formatter: JSON shape, correlation injection,
secret scubbing and error-code fields.
"""

from __future__ import annotations

import json

from tender_intelligence.core.correlation import correlation_context
from tender_intelligence.logging.structured import JsonFormatter, REDACTED


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
        record = self._record(extra={"api_key": "SK-123", "safe": "value", "nested": {"token": "t"}})
        line = JsonFormatter().format(record)
        data = json.loads(line)
        assert data["extra"]["api_key"] == REDACTED
        assert data["extra"]["safe"] == "value"
        assert data["extra"]["nested"]["token"] == REDACTED
        assert "SK-123" not in line
        assert "t" not in line