"""Test doubles for the prompt-09 suite.

The OCR stub exists because the Tesseract binary is not installed in this environment. It lets the
scanned-PDF *path* be verified — that a page with no text layer is rendered, sent to OCR, mapped
back to its page number, and recorded with its engine and version — without pretending that real
OCR accuracy has been verified. Whether Tesseract reads a real scan correctly is an environment
question (and part of the docs/13 O18 vision-vs-OCR decision), not something a stub can answer.
"""

from __future__ import annotations

from typing import ClassVar

from tender_intelligence.processing.errors import OcrFailedError, OcrUnavailableError
from tender_intelligence.processing.ocr import OcrEngine

#: Long enough that a document whose only OCR'd page carries it is still detectable.
STUB_OCR_TEXT = (
    "This annex is supplied as a scanned image. Bidders shall confirm the delivery schedule, the "
    "payment terms and the evaluation methodology described in the main document. The contracting "
    "authority reserves the right to seek clarification on any proposal before award."
)


class StubOcrEngine(OcrEngine):
    """Deterministic OCR engine: returns fixed text and counts how often it was invoked."""

    name: ClassVar[str] = "stub-ocr"

    def __init__(
        self,
        *,
        text: str = STUB_OCR_TEXT,
        version: str | None = "9.9.9-test",
        available: bool = True,
        fail: bool = False,
    ) -> None:
        self._text = text
        self._version = version
        self._available = available
        self._fail = fail
        self.calls = 0

    def version(self) -> str | None:
        return self._version

    def is_available(self) -> bool:
        return self._available

    def image_to_text(self, image: bytes, *, lang: str | None = None) -> str:
        self.calls += 1
        if not self._available:
            raise OcrUnavailableError("stub OCR is not available")
        if self._fail:
            raise OcrFailedError("stub OCR failed")
        return self._text
