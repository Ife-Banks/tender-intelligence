""":mod:`tender_intelligence.notifications.test_mode` — Test Mode policy."""

from __future__ import annotations

TEST_PREFIX = "[TEST]"


class TestModePolicy:
    """Policy gate that prevents tender emails from reaching business recipients while
    Test Mode is enabled (PROJECT_RULES #11).

    The caller passes a plain ``bool`` (read from the DB ``Settings.test_mode``), so this
    class stays free of any storage dependency. Test Mode ON is the default in the schema.
    """

    def __init__(self, test_mode: bool) -> None:
        self._enabled = bool(test_mode)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def guard_tender_recipients(self, *, list_types: list[str]) -> bool:
        """Return True when a send is permitted for the requested recipient list types.

        In Test Mode, ``tender`` recipients are never permitted; ``dev_alert`` is. Any other
        list type is rejected (defensive).
        """
        if not self._enabled:
            return True
        allowed = {t for t in list_types if t == "dev_alert"}
        return bool(allowed) and not any(t == "tender" for t in list_types)

    def apply_subject_prefix(self, subject: str) -> str:
        """Prefix *subject* with ``[TEST]`` exactly once."""
        if not self._enabled:
            return subject
        if subject.startswith(TEST_PREFIX):
            return subject
        return f"{TEST_PREFIX} {subject}"