""":mod:`tender_intelligence.acquisition.service` — document acquisition orchestration (prompt 08).

Consumes the neutral :class:`TenderAttachment` set produced by the discovery stage and
turns it into stored bytes plus ``Document`` database rows, following the documented order
for safety (prompt 08 §2)::

    download temporary bytes
        ↓
    validate successful acquisition    (scheme/size guards, HTML-as-binary, ZIP proofing)
        ↓
    calculate checksum                 (SHA-256 of the actual acquired bytes)
        ↓
    atomically commit to storage       (ObjectStorage.put → checksum-scoped key)
        ↓
    update Document acquisition state  (checksum/storage_path/mime → download_status)

Behaviour contract (per feature):

* only the exact model statuses ``pending``/``downloaded``/``failed`` are ever written;
  ``extraction_status`` is never touched here (prompt 08 §14; prompt 09 owns extraction);
* one failed attachment/member never blocks its siblings (prompt 08 §11); partial vs
  complete acquisition is distinguishable via :meth:`AcquisitionResult.incomplete_inputs`;
* reruns are idempotent: an already-stored acquisition identity (tender, source_url,
  filename) whose object exists is silently reused, never re-downloaded and never
  duplicated, and the reuse is **reported** — a re-run returns the stored documents (or for
  an archive, the archive plus its members) instead of an empty result (prompt 08 §7); a
  first-time concurrent race on identical bytes is resolved by
  the ``(tender_id, checksum)`` unique constraint, reusing the winning row (prompt 08 §17);
* ZIP archives are expanded in memory under :class:`ZipLimits` and each member becomes its
  own flat ``Document`` row with its own checksum; the archive stays traceable to its
  source attachment (prompt 08 §8); unsafe/oversized members are rejected per member;
* database rows are only marked ``downloaded`` *after* the bytes are durably in storage, so
  state and storage cannot claim an acquisition that did not happen (prompt 08 §18).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import quote, unquote

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.acquisition.checksums import checksum_of
from tender_intelligence.acquisition.errors import (
    HTTP_ERROR,
    INVALID_RESPONSE,
    PERSISTENCE_FAILED,
    STORAGE_FAILED,
    DocumentAcquisitionError,
)
from tender_intelligence.acquisition.fetcher import DocumentFetcher, DocumentResponse
from tender_intelligence.acquisition.mimes import bare_media_type, guess_mime
from tender_intelligence.acquisition.names import storage_key
from tender_intelligence.acquisition.zip import (
    ZipArchiveReport,
    ZipLimits,
    recurse_depth_exceeded,
    safe_extract_archive,
)
from tender_intelligence.core.correlation import (
    correlation_context,
    get_correlation_id,
    new_correlation_id,
)
from tender_intelligence.core.errors import DOCUMENT_DOWNLOAD_FAILED
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.repositories import DocumentRepository
from tender_intelligence.interfaces.source import TenderAttachment
from tender_intelligence.storage.interface import ObjectStorage, StoredObject

log = logging.getLogger("tender_intelligence.acquisition.service")

# Files for which an HTML response is treated as an error page, not a valid document
# (prompt 08 §5: a "PDF" that arrives as an HTML error page must not be acquired).
_BINARY_SUFFIXES = frozenset(
    {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".zip",
        ".rar",
        ".7z",
        ".odt",
        ".ods",
        ".odp",
    }
)
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})

# Advertised-vs-actual size tolerance: a mismatch beyond this is logged as a warning (the
# page may legitimately be stale); it is never treated as an acquisition failure.
_SIZE_WARNING_ABSOLUTE_BYTES = 1024 * 1024
_SIZE_WARNING_RELATIVE = 0.25


@dataclass(frozen=True)
class AcquiredDocument:
    """A successfully acquired document (top-level or archive-inner), flat for downstream."""

    document_id: int
    filename: str
    source_url: str
    checksum: str
    storage_key: str
    size_bytes: int
    mime_type: str | None
    is_archive_inner: bool
    parent_filename: str | None = None


@dataclass(frozen=True)
class AcquisitionFailure:
    """One attachment/member that failed (acquisition continues; prompt 08 §11)."""

    source_url: str
    filename: str
    error_code: str
    category: str
    retryable: bool
    detail: str | None = None


@dataclass(frozen=True)
class AcquisitionResult:
    """Flat outcome of acquiring one tender's attachment set."""

    documents: list[AcquiredDocument] = field(default_factory=list)
    failures: list[AcquisitionFailure] = field(default_factory=list)

    @property
    def incomplete_inputs(self) -> bool:
        """True when anything failed: distinguishes partial from complete acquisition."""
        return bool(self.failures)


class DocumentAcquisitionService:
    """Acquires every :class:`TenderAttachment` into storage + ``Document`` rows."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        storage: ObjectStorage,
        fetcher: DocumentFetcher,
        zip_limits: ZipLimits | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._fetcher = fetcher
        self._zip_limits = zip_limits or ZipLimits()

    # ------------------------------------------------------------------ public

    def acquire(
        self,
        tender_id: int,
        attachments: list[TenderAttachment],
        *,
        correlation_id: str | None = None,
    ) -> AcquisitionResult:
        """Acquire *attachments* under *tender_id*; per-document tolerance (prompt 08 §11)."""
        active = correlation_id or get_correlation_id() or new_correlation_id()
        with correlation_context(active):
            documents: list[AcquiredDocument] = []
            failures: list[AcquisitionFailure] = []
            for attachment in attachments:
                block = self._acquire_attachment(tender_id, attachment, active)
                documents.extend(block.documents)
                failures.extend(block.failures)
            log.info(
                "acquisition complete: %d acquired, %d failed",
                len(documents),
                len(failures),
                extra={
                    "stage": "acquisition",
                    "status": "partial" if failures else "complete",
                    "correlation_id": active,
                    "tender_id": tender_id,
                    "acquired": len(documents),
                    "failed": len(failures),
                    "incomplete_inputs": bool(failures),
                },
            )
            return AcquisitionResult(documents=documents, failures=failures)

    # ------------------------------------------------------------------ top-level

    def _acquire_attachment(
        self,
        tender_id: int,
        attachment: TenderAttachment,
        correlation_id: str,
    ) -> AcquisitionResult:
        if self._lineage_acquired(tender_id, attachment.source_url):
            log.info(
                "attachment already acquired; reusing stored copy",
                extra={
                    "stage": "acquisition",
                    "status": "reused",
                    "correlation_id": correlation_id,
                    "tender_id": tender_id,
                    # Not "filename": that is a reserved LogRecord attribute (the source
                    # file name), and passing it in ``extra`` makes logging raise KeyError.
                    "attachment_filename": attachment.filename,
                },
            )
            return self._reused_lineage(
                tender_id, attachment.source_url, correlation_id=correlation_id
            )

        try:
            response = self._fetcher.fetch(attachment.source_url)
            self._validate_response(response, attachment)
            content = response.content

            if self._is_archive(attachment, response):
                return self._acquire_archive(
                    tender_id, attachment, content, response, correlation_id, depth=1
                )
            return self._acquire_file(
                tender_id,
                attachment.source_url,
                attachment.filename,
                content,
                mime=self._mime_for(response, attachment.filename),
                correlation_id=correlation_id,
            )
        except DocumentAcquisitionError as exc:
            self._mark_failed(
                tender_id, attachment.source_url, attachment.filename, exc, correlation_id
            )
            return AcquisitionResult(failures=[self._failure(exc, attachment)])

    # ------------------------------------------------------------------ files

    def _acquire_file(
        self,
        tender_id: int,
        source_url: str,
        filename: str,
        content: bytes,
        *,
        mime: str | None,
        correlation_id: str,
        parent_filename: str | None = None,
    ) -> AcquisitionResult:
        existing = self._find_acquired(tender_id, source_url, filename)
        if existing is not None:
            log.info(
                "document already acquired; reusing stored copy",
                extra={
                    "stage": "acquisition",
                    "status": "reused",
                    "correlation_id": correlation_id,
                    "tender_id": tender_id,
                    "attachment_filename": filename,
                    "document_id": existing.id,
                },
            )
            return AcquisitionResult(
                documents=[self._acquired_from_row(existing, correlation_id)]
            )

        checksum = checksum_of(content)
        key = storage_key(tender_id, checksum, filename)
        stored = self._store(key, content, mime)
        acquired = self._persist_acquired(
            tender_id,
            source_url=source_url,
            filename=filename,
            checksum=checksum,
            storage_path=key,
            mime_type=mime,
            size_bytes=stored.size_bytes,
            correlation_id=correlation_id,
            is_archive_inner=parent_filename is not None,
            parent_filename=parent_filename,
        )
        return AcquisitionResult(documents=[acquired])

    # ------------------------------------------------------------------ archives

    def _acquire_archive(
        self,
        tender_id: int,
        attachment: TenderAttachment,
        content: bytes,
        response: DocumentResponse,
        correlation_id: str,
        *,
        depth: int,
    ) -> AcquisitionResult:
        # Validate the archive BEFORE anything is stored (prompt 08 §2): a malformed/bomb
        # archive fails the attachment safely with no partial garbage persisted.
        report = safe_extract_archive(content, limits=self._zip_limits, depth=depth)

        archive_doc = self._acquire_file(
            tender_id,
            attachment.source_url,
            attachment.filename,
            content,
            mime=self._mime_for(response, attachment.filename),
            correlation_id=correlation_id,
        )

        results = list(archive_doc.documents)
        failures: list[AcquisitionFailure] = list(archive_doc.failures)
        failures.extend(self._member_failures(attachment.source_url, report, correlation_id))

        members = self._extract_members(
            tender_id,
            parent_url=attachment.source_url,
            parent_filename=attachment.filename,
            report=report,
            correlation_id=correlation_id,
            depth=depth,
        )
        results.extend(members.documents)
        failures.extend(members.failures)
        return AcquisitionResult(documents=results, failures=failures)

    def _extract_members(
        self,
        tender_id: int,
        *,
        parent_url: str,
        parent_filename: str,
        report: ZipArchiveReport,
        correlation_id: str,
        depth: int,
    ) -> AcquisitionResult:
        results: list[AcquiredDocument] = []
        failures: list[AcquisitionFailure] = []

        for entry in report.entries:
            inner_url = f"{parent_url}!/{quote(entry.name)}"
            try:
                if not entry.is_nested_zip:
                    inner = self._acquire_file(
                        tender_id,
                        inner_url,
                        entry.name,
                        entry.data,
                        mime=guess_mime(entry.name),
                        correlation_id=correlation_id,
                        parent_filename=parent_filename,
                    )
                    results.extend(inner.documents)
                    failures.extend(inner.failures)
                    continue

                # The nested archive itself is acquired as a document (its bytes arrived
                # and are stored), then expanded one more level if within the depth limit.
                nested = self._acquire_file(
                    tender_id,
                    inner_url,
                    entry.name,
                    entry.data,
                    mime=guess_mime(entry.name),
                    correlation_id=correlation_id,
                    parent_filename=parent_filename,
                )
                results.extend(nested.documents)
                failures.extend(nested.failures)

                if recurse_depth_exceeded(self._zip_limits, depth + 1):
                    log.info(
                        "nested archive kept as a document (recursion limit reached)",
                        extra={
                            "stage": "acquisition",
                            "status": "recursion_limited",
                            "correlation_id": correlation_id,
                            "tender_id": tender_id,
                            "member": entry.name,
                            "depth": depth + 1,
                        },
                    )
                    continue

                sub_report = safe_extract_archive(
                    entry.data, limits=self._zip_limits, depth=depth + 1
                )
                failures.extend(self._member_failures(inner_url, sub_report, correlation_id))
                sub = self._extract_members(
                    tender_id,
                    parent_url=inner_url,
                    parent_filename=entry.name,
                    report=sub_report,
                    correlation_id=correlation_id,
                    depth=depth + 1,
                )
                results.extend(sub.documents)
                failures.extend(sub.failures)
            except DocumentAcquisitionError as exc:
                # One bad member/nested archive must never block its siblings (§11).
                self._mark_failed(tender_id, inner_url, entry.name, exc, correlation_id)
                failures.append(
                    AcquisitionFailure(
                        source_url=inner_url,
                        filename=entry.name,
                        error_code=exc.error_code,
                        category=exc.category,
                        retryable=exc.retryable,
                        detail=self._failure_detail(exc),
                    )
                )
        return AcquisitionResult(documents=results, failures=failures)

    def _member_failures(
        self,
        parent_url: str,
        report: ZipArchiveReport,
        correlation_id: str,
    ) -> list[AcquisitionFailure]:
        out: list[AcquisitionFailure] = []
        for member in report.member_failures:
            log.warning(
                "archive member rejected: %s (%s)",
                member.member_name,
                member.reason,
                extra={
                    "stage": "acquisition",
                    "status": "member_rejected",
                    "correlation_id": correlation_id,
                    "member": member.member_name,
                    "reason": member.reason,
                },
            )
            out.append(
                AcquisitionFailure(
                    source_url=f"{parent_url}!/{quote(member.member_name)}",
                    filename=member.member_name,
                    error_code=DOCUMENT_DOWNLOAD_FAILED,
                    category="unsafe_archive_member",
                    retryable=False,
                    detail=member.reason,
                )
            )
        return out

    # ------------------------------------------------------------------ validation

    @staticmethod
    def _is_archive(attachment: TenderAttachment, response: DocumentResponse) -> bool:
        if attachment.is_zip:
            return True
        name = attachment.filename.lower()
        if name.endswith((".zip", ".zipx")):
            return True
        if response.content_type() in {
            "application/zip",
            "application/x-zip-compressed",
            "application/x-7z-compressed",
        }:
            return True
        return response.content[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

    @staticmethod
    def _validate_response(response: DocumentResponse, attachment: TenderAttachment) -> None:
        media = response.content_type()
        name = attachment.filename.lower()
        suffix = name.rsplit(".", 1)[-1] if "." in name else ""
        if media in _HTML_MEDIA_TYPES and f".{suffix}" in _BINARY_SUFFIXES:
            raise DocumentAcquisitionError(
                "response is HTML but the attachment is a binary document (error page?)",
                error_code=DOCUMENT_DOWNLOAD_FAILED,
                category=INVALID_RESPONSE,
                retryable=False,
                context={
                    "url": attachment.source_url,
                    "content_type": media,
                    "filename": attachment.filename,
                },
            )
        advertised = attachment.advertised_size_bytes
        if advertised is not None and media not in _HTML_MEDIA_TYPES:
            actual = len(response.content)
            delta = abs(actual - advertised)
            if delta > _SIZE_WARNING_ABSOLUTE_BYTES and (
                advertised == 0 or delta > advertised * _SIZE_WARNING_RELATIVE
            ):
                log.warning(
                    "advertised size (%d) and actual size (%d) differ beyond tolerance",
                    advertised,
                    actual,
                    extra={
                        "stage": "acquisition",
                        "status": "warning",
                        "attachment_filename": attachment.filename,
                        "advertised_size_bytes": advertised,
                        "actual_size_bytes": actual,
                    },
                )

    @staticmethod
    def _mime_for(response: DocumentResponse, filename: str) -> str | None:
        return bare_media_type(response.content_type()) or guess_mime(filename)

    # ------------------------------------------------------------------ storage

    def _store(self, key: str, content: bytes, mime: str | None) -> StoredObject:
        try:
            return self._storage.put(key, content, content_type=mime)
        except Exception as exc:  # noqa: BLE001 - backend errors become structured failures
            raise DocumentAcquisitionError(
                f"storage write failed: {type(exc).__name__}",
                error_code=DOCUMENT_DOWNLOAD_FAILED,
                category=STORAGE_FAILED,
                retryable=True,
                context={"key": key},
            ) from exc

    def _persist_acquired(
        self,
        tender_id: int,
        *,
        source_url: str,
        filename: str,
        checksum: str,
        storage_path: str,
        mime_type: str | None,
        size_bytes: int,
        correlation_id: str,
        is_archive_inner: bool,
        parent_filename: str | None,
    ) -> AcquiredDocument:
        try:
            with self._session_factory() as session:
                repo = DocumentRepository(session)
                row = self._find_row(session, tender_id, source_url, filename)
                if row is None:
                    row = repo.create(tender_id, source_url, filename, mime_type=mime_type)
                    session.flush()  # assign the primary key before the setters below
                else:
                    row.mime_type = mime_type
                repo.set_checksum(row.id, checksum)
                repo.set_storage_path(row.id, storage_path)
                repo.set_download_status(row.id, "downloaded")
                try:
                    session.commit()
                except IntegrityError:
                    # Concurrent first-time acquisition of identical bytes: the
                    # (tender_id, checksum) unique constraint wins — reuse the other row.
                    session.rollback()
                    existing = self._find_by_checksum(tender_id, checksum)
                    if existing is None:
                        raise
                    log.info(
                        "concurrent acquisition of identical bytes; reusing row %d",
                        existing.id,
                        extra={
                            "stage": "acquisition",
                            "status": "reused",
                            "correlation_id": correlation_id,
                            "tender_id": tender_id,
                            "document_id": existing.id,
                        },
                    )
                    return AcquiredDocument(
                        document_id=int(existing.id),
                        filename=str(existing.filename),
                        source_url=str(existing.source_url),
                        checksum=str(existing.checksum),
                        storage_key=str(existing.storage_path),
                        size_bytes=size_bytes,
                        mime_type=existing.mime_type,
                        is_archive_inner=is_archive_inner,
                        parent_filename=parent_filename,
                    )
                log.info(
                    "document acquired (%d bytes, %s)",
                    size_bytes,
                    checksum,
                    extra={
                        "stage": "acquisition",
                        "status": "downloaded",
                        "correlation_id": correlation_id,
                        "tender_id": tender_id,
                        "document_id": int(row.id),
                        "size_bytes": size_bytes,
                        "checksum": checksum,
                        "archive_inner": is_archive_inner,
                    },
                )
                return AcquiredDocument(
                    document_id=int(row.id),
                    filename=filename,
                    source_url=source_url,
                    checksum=checksum,
                    storage_key=storage_path,
                    size_bytes=size_bytes,
                    mime_type=mime_type,
                    is_archive_inner=is_archive_inner,
                    parent_filename=parent_filename,
                )
        except DocumentAcquisitionError:
            raise
        except Exception as exc:  # noqa: BLE001 - transactional failures become structured errors
            raise DocumentAcquisitionError(
                f"document persistence failed: {type(exc).__name__}",
                error_code=DOCUMENT_DOWNLOAD_FAILED,
                category=PERSISTENCE_FAILED,
                retryable=True,
                context={"tender_id": tender_id, "source_url": source_url},
            ) from exc

    # ------------------------------------------------------------------ reads/writes

    def _find_row(
        self, session: Session, tender_id: int, source_url: str, filename: str
    ) -> Document | None:
        return session.scalar(
            select(Document)
            .where(
                Document.tender_id == tender_id,
                Document.source_url == source_url,
                Document.filename == filename,
            )
            .order_by(Document.id.desc())
            .limit(1)
        )

    def _find_by_checksum(self, tender_id: int, checksum: str) -> Document | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(Document).where(
                    Document.tender_id == tender_id, Document.checksum == checksum
                )
            )
            if row is None:
                return None
            session.expunge(row)  # detach: the caller reads it outside this session
            return row

    def _row_is_acquired(self, row: Document) -> bool:
        """True when the row represents bytes that are durably stored and reusable.

        Mirrors prompt 08 §7: a row only counts as acquired when it is ``downloaded`` with a
        checksum, a storage path, and an object that actually exists — so a re-run never
        claims (or reuses) an acquisition whose bytes are gone.
        """
        return bool(
            row.download_status == "downloaded"
            and row.checksum
            and row.storage_path
            and self._storage.exists(row.storage_path)
        )

    def _find_acquired(
        self, tender_id: int, source_url: str, filename: str
    ) -> Document | None:
        """The stored row for this acquisition identity, or None when it is not reusable.

        Returns a detached row: the caller reads it outside this session (prompt 08 §7).
        """
        with self._session_factory() as session:
            row = self._find_row(session, tender_id, source_url, filename)
            if row is None or not self._row_is_acquired(row):
                return None
            session.expunge(row)
            return row

    def _lineage_rows(self, tender_id: int, source_url: str) -> list[Document]:
        """The attachment row plus every member row expanded from it, in creation order.

        Member rows carry the inner URL ``<attachment url>!/<name>`` (see
        :meth:`_extract_members`), so the lineage is exactly the prefix that construction
        produces — nothing else under this tender is claimed as this attachment's document
        set (prompt 08 §8). All rows are detached before being returned.
        """
        prefix = f"{source_url}!/"
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(Document)
                    .where(
                        Document.tender_id == tender_id,
                        or_(
                            Document.source_url == source_url,
                            Document.source_url.startswith(prefix, autoescape=True),
                        ),
                    )
                    .order_by(Document.id)
                ).all()
            )
            for row in rows:
                session.expunge(row)
            return rows

    def _lineage_acquired(self, tender_id: int, source_url: str) -> bool:
        """True when every stored row for *source_url* and its members is acquired.

        A single-file attachment has exactly one row. A partially-acquired archive — the
        archive stored but some members failed — is deliberately **not** considered acquired,
        so a re-run re-fetches and re-attempts the missing members (prompt 08 §8, §11)
        instead of silently skipping them behind an already-stored archive row.
        """
        rows = self._lineage_rows(tender_id, source_url)
        return bool(rows) and all(self._row_is_acquired(row) for row in rows)

    def _reused_lineage(
        self, tender_id: int, source_url: str, correlation_id: str
    ) -> AcquisitionResult:
        """Report an already-acquired attachment's stored documents (prompt 08 §7).

        The fix for the recorded re-run defect: a re-run of an already-acquired tender must
        return its documents, not an empty result — a caller that trusts the return value
        sees the acquisition that exists rather than an invented empty one.
        """
        rows = self._lineage_rows(tender_id, source_url)
        documents = [self._acquired_from_row(row, correlation_id) for row in rows]
        return AcquisitionResult(documents=documents)

    def _acquired_from_row(
        self, row: Document, correlation_id: str
    ) -> AcquiredDocument:
        """Rebuild the :class:`AcquiredDocument` DTO for an already-stored row.

        Size comes from the storage metadata (there is no size column on ``Document``);
        inner/archive provenance is recovered from the inner URL shape rather than invented.
        """
        storage_path = str(row.storage_path or "")
        is_inner = "!/" in str(row.source_url)
        parent = (
            unquote(str(row.source_url).rsplit("!/", 1)[0].rsplit("/", 1)[-1])
            if is_inner
            else None
        )
        return AcquiredDocument(
            document_id=int(row.id),
            filename=str(row.filename),
            source_url=str(row.source_url),
            checksum=str(row.checksum or ""),
            storage_key=storage_path,
            size_bytes=self._storage.size_of(storage_path),
            mime_type=row.mime_type,
            is_archive_inner=is_inner,
            parent_filename=parent,
        )

    def _mark_failed(
        self,
        tender_id: int,
        source_url: str,
        filename: str,
        exc: DocumentAcquisitionError,
        correlation_id: str,
    ) -> None:
        # Explicit, curated keys — never ``extra=dict(exc.context, ...)``. Two reasons:
        # the context is arbitrary, so a key such as ``filename`` collides with a reserved
        # LogRecord attribute and makes logging raise KeyError; and it can carry a raw
        # ``url``/``source_url``, which for a signed link is a credential and must not be
        # written to a log (PROJECT_RULES #12).
        log.error(
            "document acquisition failed (%s/%s): %s",
            exc.category,
            exc.error_code,
            exc.message,
            extra={
                "stage": "acquisition",
                "status": "failed",
                "correlation_id": correlation_id,
                "tender_id": tender_id,
                "attachment_filename": filename,
                "category": exc.category,
                "error_code": exc.error_code,
                "retryable": exc.retryable,
            },
        )
        try:
            with self._session_factory() as session:
                repo = DocumentRepository(session)
                row = self._find_row(session, tender_id, source_url, filename)
                if row is None:
                    row = repo.create(tender_id, source_url, filename)
                    session.flush()
                repo.set_download_status(row.id, "failed")
                session.commit()
        except Exception:  # noqa: BLE001
            log.exception(
                "failed to record document failure state",
                extra={
                    "stage": "acquisition",
                    "status": "warning",
                    "correlation_id": correlation_id,
                    "tender_id": tender_id,
                    "attachment_filename": filename,
                },
            )

    # ------------------------------------------------------------------ failures

    @staticmethod
    def _failure(exc: DocumentAcquisitionError, attachment: TenderAttachment) -> AcquisitionFailure:
        return AcquisitionFailure(
            source_url=attachment.source_url,
            filename=attachment.filename,
            error_code=exc.error_code,
            category=exc.category,
            retryable=exc.retryable,
            detail=DocumentAcquisitionService._failure_detail(exc),
        )

    @staticmethod
    def _failure_detail(exc: DocumentAcquisitionError) -> str | None:
        if exc.category == HTTP_ERROR:
            status = exc.context.get("status_code")
            if status is not None:
                return f"http {status}"
        reason = exc.context.get("reason")
        if reason is not None:
            return str(reason)
        return exc.category
