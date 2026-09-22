"""Unit tests for the core primitives (correlation, error codes, stage results)."""

from __future__ import annotations

import re

import pytest

from tender_intelligence.core.correlation import (
    correlation_context,
    get_correlation_id,
    new_correlation_id,
)
from tender_intelligence.core.errors import (
    EMAIL_SEND_FAILED,
    SOURCE_UNREACHABLE,
    is_valid_error_code,
)
from tender_intelligence.core.result import StageResult


class TestCorrelation:
    def test_new_id_is_uuid4_hex(self):
        cid = new_correlation_id()
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", cid
        )
        assert len(cid) == 36

    def test_context_scopes_and_restores(self):
        assert get_correlation_id() is None
        outer = new_correlation_id()
        with correlation_context(outer) as active:
            assert active == outer
            assert get_correlation_id() == outer
            inner_ctx = correlation_context()
            with inner_ctx as inner_id:
                assert get_correlation_id() == inner_id
                assert inner_id != outer
            assert get_correlation_id() == outer
        assert get_correlation_id() is None

    def test_generates_when_none_supplied(self):
        with correlation_context() as cid:
            assert cid is not None
            assert get_correlation_id() == cid


class TestErrorCodes:
    def test_all_expected_codes_recognised(self):
        assert is_valid_error_code(SOURCE_UNREACHABLE)
        assert is_valid_error_code(EMAIL_SEND_FAILED)

    def test_invalid_code_rejected(self):
        assert not is_valid_error_code("not_an_error")
        assert not is_valid_error_code("")


class TestStageResult:
    def test_success(self):
        r = StageResult.success("fetch", correlation_id="c1")
        assert r.ok and r.status == "success" and r.error_code is None

    def test_failed_requires_valid_code(self):
        with pytest.raises(ValueError):
            StageResult.failed("fetch", "bogus")
        r = StageResult.failed("fetch", SOURCE_UNREACHABLE)
        assert r.status == "failed" and not r.ok

    def test_error_code_only_on_failure(self):
        with pytest.raises(ValueError):
            StageResult(stage="fetch", status="success", error_code=SOURCE_UNREACHABLE)

    def test_partial_and_skipped(self):
        assert StageResult.partial("x").status == "partial"
        assert StageResult.skipped("x").status == "skipped"
