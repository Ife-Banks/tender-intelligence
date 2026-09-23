""":mod:`tender_intelligence.processing.versions` — extraction versioning (prompt 09 §15).

Extraction behaviour changes when libraries or configuration change, so every persisted
extraction records enough processing metadata to answer "how was this produced?" and to
distinguish *same bytes + same processor* from *same bytes + changed processor*.

Reuse decisions compare :data:`PROCESSOR_VERSION` and the configured extraction config version.
Recorded library versions are evidence for a human, not part of the reuse key: patch-level
library versions differ per environment, so keying reuse on them would cause non-deterministic
reprocessing churn across machines. This is a documented engineering choice (prompt 09 §15),
and :data:`PROCESSOR_VERSION` is the declared lever for an intentional implementation change.
"""

from __future__ import annotations

import hashlib
import json
from importlib import metadata
from typing import Any

#: Declared implementation version of the extraction pipeline. Bump on any change to extraction
#: behaviour that should invalidate previously persisted extractions.
PROCESSOR_VERSION = "1.0.0"

#: Version of the extraction *configuration* (render DPI, sufficiency threshold, OCR language).
#: Bump when a default changes in a way that alters output.
EXTRACTION_CONFIG_VERSION = "1"

PROCESSOR_NAME = "TenderDocumentProcessor"

#: Schema version of the persisted bundle/artifact JSON. Bump only on an incompatible shape change.
BUNDLE_SCHEMA_VERSION = 1

_TRACKED_DISTRIBUTIONS: tuple[tuple[str, str], ...] = (
    ("pymupdf", "pymupdf"),
    ("python-docx", "python-docx"),
    ("pillow", "pillow"),
    ("pytesseract", "pytesseract"),
)


def library_versions() -> dict[str, str | None]:
    """Return the installed versions of the extraction libraries (``None`` when absent)."""
    versions: dict[str, str | None] = {}
    for label, distribution in _TRACKED_DISTRIBUTIONS:
        try:
            versions[label] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[label] = None
    return versions


def canonical_json(payload: Any) -> str:
    """Serialise *payload* deterministically (sorted keys, no insignificant whitespace)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_fingerprint(payload: Any) -> str:
    """SHA-256 over the canonical form of *payload* (prompt 09 §14 determinism).

    Callers pass content only — never timestamps, versions or correlation IDs — so that the same
    bytes extracted by the same processor always produce the same fingerprint.
    """
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
