"""Unit tests for the Test Mode policy and recipient guards."""

from __future__ import annotations

from tender_intelligence.notifications.test_mode import TEST_PREFIX, TestModePolicy


class TestTestModePolicy:
    def test_default_on(self):
        policy = TestModePolicy(test_mode=True)
        assert policy.enabled

    def test_blocks_tender_recipients_when_on(self):
        policy = TestModePolicy(test_mode=True)
        assert not policy.guard_tender_recipients(list_types=["tender"])
        assert not policy.guard_tender_recipients(list_types=["tender", "dev_alert"])

    def test_allows_dev_alert_when_on(self):
        policy = TestModePolicy(test_mode=True)
        assert policy.guard_tender_recipients(list_types=["dev_alert"])

    def test_allows_everything_when_off(self):
        policy = TestModePolicy(test_mode=False)
        assert policy.guard_tender_recipients(list_types=["tender"])

    def test_unknown_list_rejected_when_on(self):
        policy = TestModePolicy(test_mode=True)
        assert not policy.guard_tender_recipients(list_types=["operations"])

    def test_subject_prefix(self):
        policy = TestModePolicy(test_mode=True)
        assert policy.apply_subject_prefix("WAHO Tender") == f"{TEST_PREFIX} WAHO Tender"
        assert policy.apply_subject_prefix("[TEST] WAHO") == "[TEST] WAHO"  # idempotent

    def test_no_prefix_when_off(self):
        assert TestModePolicy(test_mode=False).apply_subject_prefix("Real") == "Real"
