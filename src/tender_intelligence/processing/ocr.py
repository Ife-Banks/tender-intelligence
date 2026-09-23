""":mod:`tender_intelligence.processing.ocr` — OCR engine seam (prompt 09 §4.2).

OCR is required for scanned/image-only PDFs (docs/06 §6.2: "WAHO TORs and AMI notices are
frequently scanned"; "no OCR vendor mandated"). The engine sits behind an abstraction so the
scanned-PDF path can be exercised and page-mapped without depending on a specific binary, and so
the vision-vs-OCR choice left open in docs/13-open-decisions.md O18 can be revisited without
rewriting the PDF pipeline.

Availability is probed, never assumed: with no Tesseract binary present the engine reports
unavailable and the caller records ``ocr_failed`` for that document rather than fabricating text
(prompt 09 §5, §20).
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from typing import ClassVar

from tender_intelligence.processing.errors import OcrFailedError, OcrUnavailableError


class OcrEngine(ABC):
    """Converts a rendered page image into text."""

    name: ClassVar[str] = "ocr"

    @abstractmethod
    def version(self) -> str | None:
        """Engine version string, or ``None`` when the engine is unavailable."""

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this engine can currently perform OCR."""

    @abstractmethod
    def image_to_text(self, image: bytes, *, lang: str | None = None) -> str:
        """OCR a PNG/JPEG *image* into text.

        Raises :class:`OcrUnavailableError` when the engine is not available and
        :class:`OcrFailedError` when it ran but failed. Neither message may contain image or
        document content (prompt 09 §18).
        """


class TesseractOcrEngine(OcrEngine):
    """Tesseract via :mod:`pytesseract` — the non-vision default for scanned PDFs.

    ``lang`` is a Tesseract traineddata code (``eng``/``fra``/``por``). Language cannot be detected
    before OCR, so the configured default is used and the language is then detected from the OCR'd
    text and recorded as document metadata (prompt 09 §10).
    """

    name: ClassVar[str] = "tesseract"

    def __init__(self, *, lang: str = "eng", config: str = "") -> None:
        self._lang = lang
        self._config = config
        self._probed = False
        self._version: str | None = None

    def version(self) -> str | None:
        """Cached probe of the Tesseract binary version; ``None`` when absent."""
        if not self._probed:
            self._probed = True
            try:
                import pytesseract

                self._version = str(pytesseract.get_tesseract_version())
            except Exception:  # any probe failure means "unavailable", not a crash
                self._version = None
        return self._version

    def is_available(self) -> bool:
        return self.version() is not None

    def image_to_text(self, image: bytes, *, lang: str | None = None) -> str:
        if not self.is_available():
            raise OcrUnavailableError(
                "tesseract binary is not available",
                context={"engine": self.name},
            )
        try:
            import pytesseract
            from PIL import Image

            with Image.open(io.BytesIO(image)) as opened:
                return str(
                    pytesseract.image_to_string(
                        opened, lang=lang or self._lang, config=self._config
                    )
                )
        except OcrUnavailableError:
            raise
        except Exception as exc:  # engine faults are library-specific and numerous; a
            # per-document failure must be recorded, never abort the tender (prompt 09 §16).
            raise OcrFailedError(
                f"tesseract OCR failed: {type(exc).__name__}",
                context={"engine": self.name, "exception": type(exc).__name__},
            ) from exc
