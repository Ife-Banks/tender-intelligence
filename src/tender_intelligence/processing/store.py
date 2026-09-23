""":mod:`tender_intelligence.processing.store` — extraction artifact persistence (prompt 09 §13).

Where extracted content lives, and why
--------------------------------------
docs/06 §6.3 specifies the reusable bundle be "persisted/referenced from
``Document.extracted_text_ref``, with an extraction-method and status per document", and
prompt 09 §13 forbids storing large extracted content in arbitrary database fields. docs/03 §3.2
gives ``Document`` exactly one reference column (``extracted_text_ref``, ``Text``) and no method,
page, table or bundle columns. So:

* each document's extraction is written as one JSON artifact in the configured object storage, and
  ``Document.extracted_text_ref`` holds its key (prompt 09 §13);
* the per-tender bundle is written to a deterministic key derived from the tender ID alone, so
  later stages can retrieve it without a new column and without a schema change (PROJECT_RULES #14);
* extraction method, versions, page/section boundaries and tables live **inside** the artifact,
  which is what docs/03 leaves room for.

Artifact keys are keyed by *content identity*, not by document ID, and carry no extraction method
segment: reprocessing the same bytes under a changed method overwrites the same key instead of
accumulating orphaned artifacts (prompt 09 §14 — "duplicate extraction artifacts").
"""

from __future__ import annotations

import json
import logging

from tender_intelligence.processing.representation import DocumentExtraction, TenderDocumentBundle
from tender_intelligence.processing.versions import (
    EXTRACTION_CONFIG_VERSION,
    PROCESSOR_VERSION,
    canonical_json,
)
from tender_intelligence.storage.interface import ObjectStorage

log = logging.getLogger("tender_intelligence.processing.store")

EXTRACTED_ROOT = "extracted"


def artifact_key(*, tender_id: int, segment: str, config_version: str) -> str:
    """Storage key for one document's extraction artifact."""
    return f"tenders/{tender_id}/{EXTRACTED_ROOT}/{segment}/v{config_version}.json"


def bundle_key(tender_id: int) -> str:
    """Storage key for a tender's reusable bundle (derivable from the tender ID alone)."""
    return f"tenders/{tender_id}/{EXTRACTED_ROOT}/bundle.json"


class ExtractionStore:
    """Reads and writes extraction artifacts and bundles through :class:`ObjectStorage`."""

    def __init__(
        self, storage: ObjectStorage, *, config_version: str = EXTRACTION_CONFIG_VERSION
    ) -> None:
        self._storage = storage
        self._config_version = config_version

    @property
    def config_version(self) -> str:
        return self._config_version

    def key_for(self, *, tender_id: int, segment: str) -> str:
        return artifact_key(
            tender_id=tender_id, segment=segment, config_version=self._config_version
        )

    def read_extraction(self, key: str) -> DocumentExtraction | None:
        """Load a persisted extraction, or ``None`` when absent or unreadable.

        An unreadable artifact is treated as absent so the document is reprocessed rather than
        the tender failing; the reason is logged without any document content (prompt 09 §18).
        """
        try:
            raw = self._storage.get(key)
        except KeyError:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
            return DocumentExtraction.from_dict(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning(
                "stored extraction artifact unreadable; it will be regenerated",
                extra={
                    "stage": "processing",
                    "status": "artifact_unreadable",
                    "artifact_key": key,
                    "exception": type(exc).__name__,
                },
            )
            return None

    def write_extraction(self, extraction: DocumentExtraction) -> str:
        """Persist *extraction* and return its storage key.

        Always overwrites the key for these bytes, so the artifact always mirrors the latest
        processing outcome and the row and artifact can never disagree. Because only
        ``extracted`` artifacts are reused (see :meth:`is_reusable`), a recorded failure never
        blocks a later retry: the next run reprocesses and rewrites the artifact.
        """
        key = self.key_for(
            tender_id=extraction.tender_id, segment=extraction.artifact_key_segment
        )
        self._storage.put(
            key, canonical_json(extraction.to_dict()).encode("utf-8"), "application/json"
        )
        return key

    def read_bundle(self, tender_id: int) -> TenderDocumentBundle | None:
        """Load a tender's persisted bundle, or ``None`` when absent or unreadable."""
        key = bundle_key(tender_id)
        try:
            raw = self._storage.get(key)
        except KeyError:
            return None
        try:
            return TenderDocumentBundle.from_dict(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning(
                "stored bundle unreadable",
                extra={
                    "stage": "processing",
                    "status": "bundle_unreadable",
                    "tender_id": tender_id,
                    "exception": type(exc).__name__,
                },
            )
            return None

    def write_bundle(self, bundle: TenderDocumentBundle) -> str:
        """Persist one reusable bundle per tender, overwriting the previous one (prompt 09 §12)."""
        key = bundle_key(bundle.tender_id)
        self._storage.put(
            key, canonical_json(bundle.to_dict()).encode("utf-8"), "application/json"
        )
        return key

    def is_reusable(self, extraction: DocumentExtraction) -> bool:
        """Whether a persisted extraction may be reused instead of reprocessed (prompt 09 §14, §15).

        Reuse requires a *successful* prior extraction produced by the same processor version and
        extraction configuration. Failures and skips are never reused, so a transient OCR failure
        or a newly installed OCR engine cannot permanently poison a document's extraction.
        """
        return (
            extraction.is_extracted
            and extraction.metadata.processor_version == PROCESSOR_VERSION
            and extraction.metadata.config_version == self._config_version
        )
