""":mod:`tender_intelligence.processing.service` — document understanding (prompt 09).

Answers prompt 09's core question — *"what does each acquired document contain?"* — for one
tender, by turning the ``Document`` rows acquisition already persisted (prompt 08) into a single
reusable :class:`TenderDocumentBundle`. No verdicting, scoring or triage happens here: this stage
produces deterministic, reusable content and stops (prompt 09 §1, §17).

Inputs, and what is deliberately *not* an input
-----------------------------------------------
Documents are consumed from the database (``DocumentRepository.list_by_tender``) and their bytes
from the configured storage abstraction via ``Document.storage_path``. Source URLs are never
re-fetched and never re-discovered: acquisition owns that, and prompt 09 §3 requires stored-bytes
processing so a later stage cannot trigger a second download (prompt 09 §2, §13).

Per-document failure semantics (§16)
------------------------------------
One bad document never aborts the tender. Each document is processed in its own transaction and
its outcome is a *result* — ``extracted``, ``failed`` (with a machine-readable error code) or
``skipped`` (with a recorded reason) — never an exception that escapes the loop. The bundle always
records every document, including the ones that produced nothing, so nothing is silently hidden.

``skipped`` and ``failed`` are kept distinct on purpose:

* **failed** — the document should have yielded content but did not (corrupt bytes, parse failure,
  OCR required but unavailable, stored bytes missing). It is absent from the AI stage's inputs and
  it sets ``incomplete_inputs`` (docs/06 §6.5), so a verdict reached without it is visibly partial.
* **skipped** — the document was never a content source in the first place: an archive container
  whose members are separate rows, or a format the specification does not require extracting.
  These do not by themselves make the bundle incomplete. The unsupported-format case is
  business-visible (it drives the email footer per docs/04 §4.6), so it is recorded as
  docs/13-open-decisions.md **O22** rather than decided silently here.

Reuse and idempotency (§14, §15)
--------------------------------
Re-running for a tender reprocesses nothing that is already usable: an existing extraction
artifact for the same bytes is reused when its processor and configuration versions match. Every
write is keyed deterministically, so a repeat run produces the same keys and the same content
rather than duplicate artifacts.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.core.correlation import (
    correlation_context,
    get_correlation_id,
    new_correlation_id,
)
from tender_intelligence.core.errors import DOCUMENT_DOWNLOAD_FAILED, PARSE_FAILED
from tender_intelligence.db.repositories import DocumentRepository
from tender_intelligence.interfaces.document import (
    DocumentBundle,
    DocumentProcessor,
    ExtractedDocument,
)
from tender_intelligence.processing.docx import extract_docx
from tender_intelligence.processing.errors import (
    SKIP_ACQUISITION_FAILED,
    SKIP_ARCHIVE_CONTAINER,
    SKIP_SOURCE_BYTES_UNAVAILABLE,
    SKIP_STORAGE_REFERENCE_MISSING,
    SKIP_UNSUPPORTED_FORMAT,
    ProcessingError,
)
from tender_intelligence.processing.languages import bundle_languages, detect_language
from tender_intelligence.processing.ocr import OcrEngine
from tender_intelligence.processing.pdf import (
    DEFAULT_RENDER_DPI,
    MIN_NATIVE_TEXT_CHARS_PER_PAGE,
    extract_pdf,
)
from tender_intelligence.processing.representation import (
    DocumentExtraction,
    ExtractedPage,
    ExtractedSection,
    ExtractedTable,
    ProcessingMetadata,
    TenderDocumentBundle,
)
from tender_intelligence.processing.store import ExtractionStore
from tender_intelligence.processing.versions import (
    EXTRACTION_CONFIG_VERSION,
    PROCESSOR_NAME,
    PROCESSOR_VERSION,
    content_fingerprint,
    library_versions,
)
from tender_intelligence.storage.interface import ObjectStorage

log = logging.getLogger("tender_intelligence.processing.service")

_DOWNLOAD_FAILED = "failed"
_STATUS_EXTRACTED = "extracted"
_STATUS_FAILED = "failed"
_STATUS_SKIPPED = "skipped"

_METHOD_NONE = "none"
_FORMAT_ARCHIVE = "archive"
_FORMAT_PDF = "pdf"
_FORMAT_DOCX = "docx"
_FORMAT_UNKNOWN = "unknown"

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"
_ARCHIVE_SUFFIXES = (".zip",)
_DOCX_SUFFIXES = (".docx",)
_ARCHIVE_MIMES = ("application/zip", "application/x-zip-compressed", "multipart/x-zip")
_DOCX_MIMES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-word.document.12",
)

#: The part every WordprocessingML package carries. Its presence in a ZIP archive is what makes the
#: archive a DOCX, whatever the file happens to be named.
_OOXML_DOCUMENT_PART = "word/document.xml"


def _is_ooxml_package(data: bytes) -> bool:
    """Whether these ZIP bytes are a WordprocessingML package (a ``.docx``).

    Read from the central directory rather than trusted from the filename: prompt 08 stores
    attachments under whatever name the source served, and a source that serves a DOCX as
    ``document.pdf`` — or with no usable MIME type — must still be read as the DOCX it is. A
    container that cannot be opened at all is not an OOXML package; the caller falls back to the
    declared type so the extractor can report the corruption.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return _OOXML_DOCUMENT_PART in archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def detect_format(*, mime_type: str | None = None, filename: str = "", data: bytes = b"") -> str:
    """Identify a document's container format from its bytes, then its declared type.

    Content wins over declaration: a file named ``.pdf`` that is not a PDF must be reported as
    unparseable rather than trusted, and prompt 09 §20 requires exactly that distinction between
    "declared format is wrong" and "format is not supported". Suffixes are only consulted when the
    bytes are inconclusive, because tenders do serve attachments with missing or wrong MIME types.
    """
    name = (filename or "").lower()
    mime = (mime_type or "").lower().split(";")[0].strip()
    head = data[:8]

    if head.startswith(_PDF_MAGIC):
        return _FORMAT_PDF
    if head.startswith(_ZIP_MAGIC):
        # A DOCX and a plain archive are both ZIP containers, so the bytes alone do not separate
        # them — but the archive's own contents do, and that is checked before the name. A ZIP the
        # source *called* .docx is still routed to the DOCX reader even when it is not a package,
        # so the malformed file is reported as a parse failure rather than quietly skipped (§20).
        if _is_ooxml_package(data):
            return _FORMAT_DOCX
        if name.endswith(_DOCX_SUFFIXES) or mime in _DOCX_MIMES:
            return _FORMAT_DOCX
        if name.endswith(_ARCHIVE_SUFFIXES) or mime in _ARCHIVE_MIMES:
            return _FORMAT_ARCHIVE
        return _FORMAT_ARCHIVE

    if name.endswith(_DOCX_SUFFIXES) or mime in _DOCX_MIMES:
        return _FORMAT_DOCX
    if name.endswith(".pdf") or mime == "application/pdf":
        return _FORMAT_PDF
    if name.endswith(_ARCHIVE_SUFFIXES) or mime in _ARCHIVE_MIMES:
        return _FORMAT_ARCHIVE
    return _FORMAT_UNKNOWN


@dataclass(frozen=True)
class DocumentProcessingConfig:
    """Extraction configuration recorded in processing metadata (prompt 09 §15).

    Every value here can change extracted output, which is why changing any of them must be
    accompanied by a new :data:`EXTRACTION_CONFIG_VERSION`: reuse is keyed on that version, so
    bumping it is what makes already-processed documents reprocess (prompt 09 §14).
    """

    config_version: str = EXTRACTION_CONFIG_VERSION
    render_dpi: int = DEFAULT_RENDER_DPI
    min_native_chars_per_page: int = MIN_NATIVE_TEXT_CHARS_PER_PAGE
    ocr_lang: str = "eng"
    reuse_extractions: bool = True


@dataclass(frozen=True)
class DocumentSnapshot:
    """One ``Document`` row, detached from its session (prompt 09 §3).

    Read inside a session and carried as a plain value so the extraction loop never holds a
    database session open across file I/O or OCR (prompt 09 §13).
    """

    document_id: int
    tender_id: int
    filename: str
    source_url: str
    download_status: str
    extraction_status: str
    mime_type: str | None = None
    checksum: str | None = None
    storage_path: str | None = None
    language: str | None = None

    @property
    def download_failed(self) -> bool:
        return self.download_status == _DOWNLOAD_FAILED

    @property
    def artifact_segment(self) -> str:
        return self.checksum or f"doc-{self.document_id}"

    @property
    def archive_parent_url(self) -> str | None:
        """The container URL when these bytes came out of an archive (prompt 08 provenance).

        Prompt 08 records an extracted member's provenance as ``"{parent_url}!/{member}"`` in
        ``source_url``, so the parent is recoverable without a second download (prompt 09 §13).
        """
        if "!/" not in self.source_url:
            return None
        parent, _, _member = self.source_url.partition("!/")
        return parent or None


@dataclass(frozen=True)
class ExtractionOutcome:
    """The result of extracting one document's bytes, before identity is attached."""

    status: str
    extraction_method: str = _METHOD_NONE
    text: str = ""
    pages: list[ExtractedPage] = field(default_factory=list)
    sections: list[ExtractedSection] = field(default_factory=list)
    error_code: str | None = None
    skip_reason: str | None = None
    ocr_engine: str | None = None
    ocr_engine_version: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


class TenderDocumentProcessor(DocumentProcessor):
    """Storage-driven implementation of the document-processing seam (docs/02 §2.10).

    ``DocumentProcessor.process`` takes storage *keys*, which is how this seam is exercised
    outside the pipeline: each key is read through the same :class:`ObjectStorage` abstraction and
    run through the same extractors the tender-level service uses, so there is exactly one
    extraction code path. The database-aware entry point is
    :meth:`DocumentProcessingService.process_tender`.
    """

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        ocr: OcrEngine | None = None,
        config: DocumentProcessingConfig | None = None,
    ) -> None:
        self._storage = storage
        self._ocr = ocr
        self._config = config or DocumentProcessingConfig()
        self._library_versions = library_versions()

    def process(self, document_paths: list[str]) -> DocumentBundle:
        """Extract every key in *document_paths* into one bundle (docs/06 §6.2, §6.4).

        A key that cannot be read or extracted is recorded on its ``ExtractedDocument`` with an
        error code rather than raising, so one bad file never blocks the rest (docs/06 §6.4).
        """
        documents: list[ExtractedDocument] = []
        for path in document_paths:
            documents.append(self._process_path(path))
        return DocumentBundle(
            documents=documents,
            incomplete_inputs=any(document.error_code is not None for document in documents),
        )

    def _process_path(self, path: str) -> ExtractedDocument:
        filename = path.rsplit("/", 1)[-1]
        try:
            data = self._storage.get(path)
        except KeyError:
            return ExtractedDocument(
                filename=filename,
                source_url="",
                mime_type=None,
                language=None,
                checksum=_checksum_hint(path),
                extraction_method=_METHOD_NONE,
                text="",
                error_code=DOCUMENT_DOWNLOAD_FAILED,
            )

        outcome = self._extract(
            data,
            filename=filename,
            mime_type=None,
            document_id=path,
        )
        language = None
        if outcome.status == _STATUS_EXTRACTED:
            detected, _confidence = detect_language(outcome.text)
            language = detected

        return ExtractedDocument(
            filename=filename,
            source_url="",
            mime_type=None,
            language=language,
            checksum=_checksum_hint(path),
            extraction_method=outcome.extraction_method,
            text=outcome.text,
            pages=[page.text for page in outcome.pages],
            tables=[table.to_dict() for table in _tables_of(outcome)],
            error_code=outcome.error_code,
        )

    def _extract(
        self, data: bytes, *, filename: str, mime_type: str | None, document_id: int | str
    ) -> ExtractionOutcome:
        """Run the format-appropriate extractor, converting failures into outcomes (§16)."""
        fmt = detect_format(mime_type=mime_type, filename=filename, data=data)
        if fmt == _FORMAT_ARCHIVE:
            return ExtractionOutcome(
                status=_STATUS_SKIPPED, skip_reason=SKIP_ARCHIVE_CONTAINER
            )
        if fmt == _FORMAT_UNKNOWN:
            return ExtractionOutcome(
                status=_STATUS_SKIPPED, skip_reason=SKIP_UNSUPPORTED_FORMAT
            )

        try:
            if fmt == _FORMAT_PDF:
                pdf_result = extract_pdf(
                    data,
                    ocr=self._ocr,
                    ocr_lang=self._config.ocr_lang,
                    render_dpi=self._config.render_dpi,
                    min_native_chars=self._config.min_native_chars_per_page,
                )
                first_ocr = next((page for page in pdf_result.pages if page.was_ocr), None)
                return ExtractionOutcome(
                    status=_STATUS_EXTRACTED,
                    extraction_method=pdf_result.method,
                    text=pdf_result.text,
                    pages=pdf_result.pages,
                    ocr_engine=first_ocr.ocr_engine if first_ocr else None,
                    ocr_engine_version=first_ocr.ocr_engine_version if first_ocr else None,
                )

            docx_result = extract_docx(data)
            return ExtractionOutcome(
                status=_STATUS_EXTRACTED,
                extraction_method=docx_result.method,
                text=docx_result.text,
                sections=docx_result.sections,
            )
        except ProcessingError as exc:
            log.warning(
                "document extraction failed",
                extra={
                    "stage": "processing",
                    "document_id": document_id,
                    "error_code": exc.error_code,
                    "failure_category": exc.category,
                    **exc.context,
                },
            )
            return ExtractionOutcome(
                status=_STATUS_FAILED,
                error_code=exc.error_code,
                skip_reason=exc.category,
            )
        except Exception as exc:  # one bad document must never abort the tender (§16)
            log.error(
                "unexpected extraction failure",
                extra={
                    "stage": "processing",
                    "document_id": document_id,
                    "error_code": None,
                    "failure_category": "unexpected",
                    "exception": type(exc).__name__,
                },
            )
            return ExtractionOutcome(
                status=_STATUS_FAILED,
                error_code=PARSE_FAILED,
                skip_reason="unexpected_error",
            )


def _tables_of(outcome: ExtractionOutcome) -> list[ExtractedTable]:
    collected: list[ExtractedTable] = []
    for page in outcome.pages:
        collected.extend(page.tables)
    for section in outcome.sections:
        collected.extend(section.tables)
    return collected


def _checksum_hint(key: str) -> str:
    """Best-effort checksum from a storage key laid out as ``.../attachments/{checksum}/{name}``."""
    parts = key.split("/")
    if len(parts) >= 3:
        candidate = parts[-2]
        if len(candidate) == 64 and all(char in "0123456789abcdef" for char in candidate.lower()):
            return candidate
    return ""


class DocumentProcessingService:
    """Per-tender document understanding (prompt 09 §2, §3, §11, §12)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        storage: ObjectStorage,
        ocr: OcrEngine | None = None,
        config: DocumentProcessingConfig | None = None,
        store: ExtractionStore | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._ocr = ocr
        self._config = config or DocumentProcessingConfig()
        self._store = store or ExtractionStore(
            storage, config_version=self._config.config_version
        )
        self._extractor = TenderDocumentProcessor(
            storage=storage, ocr=ocr, config=self._config
        )
        # Probed once per service: library_versions reads distribution metadata, and the answer
        # cannot change while the process runs (prompt 09 §15).
        self._library_versions = library_versions()

    @property
    def config(self) -> DocumentProcessingConfig:
        return self._config

    def process_tender(
        self, tender_id: int, *, correlation_id: str | None = None
    ) -> TenderDocumentBundle:
        """Produce the reusable bundle for *tender_id* and persist it (prompt 09 §12, §13).

        Returns the bundle for the caller to hand to the AI stage; this method performs no
        triage or verdicting (prompt 09 §17).
        """
        correlation = correlation_id or get_correlation_id() or new_correlation_id()
        with correlation_context(correlation):
            snapshots = self._load_documents(tender_id)
            documents = [self._process_snapshot(snapshot, correlation) for snapshot in snapshots]

            bundle = TenderDocumentBundle(
                tender_id=tender_id,
                documents=documents,
                incomplete_inputs=_incomplete(documents),
                languages=bundle_languages([document.language for document in documents]),
                metadata=self._bundle_metadata(correlation),
                created_at=_utcnow_iso(),
            )
            self._store.write_bundle(bundle)
            log.info(
                "tender document bundle built",
                extra={
                    "stage": "processing",
                    "status": "bundle_complete",
                    "tender_id": tender_id,
                    "correlation_id": correlation,
                    "document_count": bundle.document_count,
                    "extracted_count": len(bundle.extracted_documents),
                    "failed_count": len(bundle.failed_documents),
                    "skipped_count": len(bundle.skipped_documents),
                    "incomplete_inputs": bundle.incomplete_inputs,
                },
            )
            return bundle

    # ------------------------------------------------------------------ internals

    def _load_documents(self, tender_id: int) -> list[DocumentSnapshot]:
        """Read this tender's document rows as plain values, in a stable order (prompt 09 §3)."""
        with self._session_factory() as session:
            rows = DocumentRepository(session).list_by_tender(tender_id)
            return [
                DocumentSnapshot(
                    document_id=row.id,
                    tender_id=row.tender_id,
                    filename=row.filename,
                    source_url=row.source_url,
                    download_status=row.download_status,
                    extraction_status=row.extraction_status,
                    mime_type=row.mime_type,
                    checksum=row.checksum,
                    storage_path=row.storage_path,
                    language=row.language,
                )
                for row in rows
            ]

    def _process_snapshot(self, snapshot: DocumentSnapshot, correlation: str) -> DocumentExtraction:
        """Resolve one document's outcome, persist its artifact, and update its row."""
        if snapshot.download_failed:
            return self._finalize(
                snapshot,
                ExtractionOutcome(
                    status=_STATUS_SKIPPED,
                    error_code=DOCUMENT_DOWNLOAD_FAILED,
                    skip_reason=SKIP_ACQUISITION_FAILED,
                ),
                correlation,
            )

        if not snapshot.storage_path:
            return self._finalize(
                snapshot,
                ExtractionOutcome(
                    status=_STATUS_FAILED,
                    error_code=DOCUMENT_DOWNLOAD_FAILED,
                    skip_reason=SKIP_STORAGE_REFERENCE_MISSING,
                ),
                correlation,
            )

        key = self._store.key_for(
            tender_id=snapshot.tender_id, segment=snapshot.artifact_segment
        )
        if self._config.reuse_extractions:
            cached = self._store.read_extraction(key)
            if cached is not None and self._store.is_reusable(cached):
                self._persist_row(snapshot, cached, ref_key=key)
                return cached

        try:
            data = self._storage.get(snapshot.storage_path)
        except KeyError:
            # Stored bytes are gone. Prompt 09 §3: an explicit processing failure, never a silent
            # re-download from the source URL.
            return self._finalize(
                snapshot,
                ExtractionOutcome(
                    status=_STATUS_FAILED,
                    error_code=DOCUMENT_DOWNLOAD_FAILED,
                    skip_reason=SKIP_SOURCE_BYTES_UNAVAILABLE,
                ),
                correlation,
            )

        outcome = self._extractor._extract(
            data,
            filename=snapshot.filename,
            mime_type=snapshot.mime_type,
            document_id=snapshot.document_id,
        )
        return self._finalize(snapshot, outcome, correlation)

    def _finalize(
        self, snapshot: DocumentSnapshot, outcome: ExtractionOutcome, correlation: str
    ) -> DocumentExtraction:
        """Attach identity, detect language, persist the artifact, and update the row."""
        language = snapshot.language
        confidence: float | None = None
        source = "unknown"
        if outcome.status == _STATUS_EXTRACTED:
            detected, detected_confidence = detect_language(outcome.text)
            if detected is not None:
                language = detected
                confidence = detected_confidence
                source = "detected"

        extraction = DocumentExtraction(
            document_id=snapshot.document_id,
            tender_id=snapshot.tender_id,
            filename=snapshot.filename,
            source_url=snapshot.source_url,
            extraction_status=outcome.status,
            extraction_method=outcome.extraction_method,
            text=outcome.text,
            mime_type=snapshot.mime_type,
            checksum=snapshot.checksum,
            storage_path=snapshot.storage_path,
            language=language,
            language_confidence=confidence,
            language_source=source,
            error_code=outcome.error_code,
            pages=outcome.pages,
            sections=outcome.sections,
            archive_parent_url=snapshot.archive_parent_url,
            metadata=ProcessingMetadata(
                processor=PROCESSOR_NAME,
                processor_version=PROCESSOR_VERSION,
                config_version=self._config.config_version,
                library_versions=dict(self._library_versions),
                ocr_engine=outcome.ocr_engine,
                ocr_engine_version=outcome.ocr_engine_version,
                processed_at=_utcnow_iso(),
                correlation_id=correlation,
                skip_reason=outcome.skip_reason,
            ),
        )

        # Every outcome is persisted, including failures and skips: prompt 09 §16 requires the
        # record of what happened, and the row's reference then points at an artifact that agrees
        # with it instead of at a stale success.
        key = self._store.write_extraction(extraction)
        self._persist_row(snapshot, extraction, ref_key=key)
        return extraction

    def _persist_row(
        self, snapshot: DocumentSnapshot, extraction: DocumentExtraction, *, ref_key: str
    ) -> None:
        """Record the extraction outcome on the document row in its own transaction.

        A persistence failure propagates: it is not a document failure but a system failure, and
        continuing would return a bundle whose statuses were never recorded (prompt 09 §22).
        """
        with self._session_factory() as session:
            repository = DocumentRepository(session)
            repository.set_extraction_status(
                snapshot.document_id,
                extraction.extraction_status,
                error_code=extraction.error_code,
            )
            repository.set_extracted_text_ref(snapshot.document_id, ref_key)
            if extraction.language is not None:
                repository.set_language(snapshot.document_id, extraction.language)
            session.commit()

    def _bundle_metadata(self, correlation: str) -> ProcessingMetadata:
        return ProcessingMetadata(
            processor=PROCESSOR_NAME,
            processor_version=PROCESSOR_VERSION,
            config_version=self._config.config_version,
            library_versions=dict(self._library_versions),
            ocr_engine=self._ocr.name if self._ocr is not None else None,
            ocr_engine_version=self._ocr.version() if self._ocr is not None else None,
            processed_at=_utcnow_iso(),
            correlation_id=correlation,
        )


def _incomplete(documents: list[DocumentExtraction]) -> bool:
    """Whether any document that should have contributed content did not (docs/06 §6.5).

    A document failed to extract, or failed to download. Documents skipped because they were never
    content sources (an archive container, an unsupported format) do not make the inputs incomplete
    — see docs/13-open-decisions.md O22 for the unsupported-format case.
    """
    for document in documents:
        if document.is_failed:
            return True
        if document.metadata.skip_reason == SKIP_ACQUISITION_FAILED:
            return True
    return False


def bundle_fingerprint(bundle: TenderDocumentBundle) -> str:
    """Deterministic fingerprint of a bundle's *content* (prompt 09 §14).

    Excludes timestamps, versions and correlation IDs, so two runs over unchanged inputs produce
    the same fingerprint — which is what makes reuse verifiable rather than merely asserted.
    """
    return content_fingerprint(
        [
            {
                "document_id": document.document_id,
                "extraction_status": document.extraction_status,
                "content_fingerprint": document.content_fingerprint,
            }
            for document in sorted(bundle.documents, key=lambda item: item.document_id)
        ]
    )
