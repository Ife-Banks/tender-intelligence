"""Integration tests for document acquisition (prompt 08 §2, §7, §8–§11, §17, §18).

Real fetcher + real filesystem storage + migrated SQLite: only the network bytes are
faked (``httpx2.MockTransport``), exactly like the prompt-10 harness. Covers the
download→validate→checksum→storage→row ordering, ZIP expansion/safety, per-document
failure tolerance, idempotent re-runs (incl. the recorded re-run defect), hostile
filenames, storage-failure isolation and correlation tracing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from httpx2 import MockTransport, Request, Response
from sqlalchemy import select

from support.documents import text_pdf, zip_bytes
from tender_intelligence.acquisition.checksums import checksum_of
from tender_intelligence.acquisition.errors import STORAGE_FAILED
from tender_intelligence.acquisition.fetcher import DocumentFetcher
from tender_intelligence.acquisition.service import DocumentAcquisitionService
from tender_intelligence.acquisition.zip import ZipLimits
from tender_intelligence.db.models.documents import Document
from tender_intelligence.interfaces.source import TenderAttachment
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.storage.interface import ObjectStorage, StoredObject
from tender_intelligence.storage.local import LocalFileSystemStorage


@dataclass
class DownloadSite:
    """An in-process download host: attachment bytes by URL substring."""

    attachments: dict[str, tuple[bytes, str | None]] = field(default_factory=dict)
    fail_urls: set[str] = field(default_factory=set)
    requests: list[str] = field(default_factory=list)

    def handler(self, request: Request) -> Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.fail_urls:
            return Response(404, text="injected 404")
        for needle, (content, content_type) in self.attachments.items():
            if needle in url:
                headers = {"content-type": content_type} if content_type else {}
                return Response(200, content=content, headers=headers)
        return Response(404, text=f"no fixture for {url}")


def _make_service(session_factory, storage_dir: str, site: DownloadSite) -> tuple[
    DocumentAcquisitionService, ObjectStorage
]:
    policy = CrawlPolicy(
        request_interval_seconds=0.0,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        max_retries=2,
    )
    fetcher = DocumentFetcher(policy, transport=MockTransport(site.handler))
    storage: ObjectStorage = LocalFileSystemStorage(storage_dir)
    service = DocumentAcquisitionService(
        session_factory, storage=storage, fetcher=fetcher, zip_limits=ZipLimits()
    )
    return service, storage


def _rows(session_factory, tender_id: int) -> list[Document]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(Document).where(Document.tender_id == tender_id)
                .order_by(Document.id)
            ).all()
        )


SUMMARY_TEXT = b"summary of the tender"


def _summary_attachment(tender_id: int = 7) -> TenderAttachment:
    return TenderAttachment(
        source_url=f"https://src.example/t{tender_id}/summary",
        filename="summary.txt",
        advertised_size_bytes=len(SUMMARY_TEXT),
    )


class TestBasicAcquisition:
    def test_bytes_stored_and_row_updated(self, session_factory_gr, tmp_storage) -> None:
        pdf = text_pdf(["first page of the RFP"])
        site = DownloadSite(
            attachments={
                "summary": (SUMMARY_TEXT, "text/plain"),
                "rfp": (pdf, "application/pdf"),
            }
        )
        service, storage = _make_service(session_factory_gr, str(tmp_storage), site)

        result = service.acquire(
            7,
            [
                _summary_attachment(),
                TenderAttachment(source_url="https://src.example/t7/rfp", filename="rfp.pdf"),
            ],
            correlation_id="corr-basic",
        )

        assert not result.failures
        assert not result.incomplete_inputs
        assert len(result.documents) == 2
        summary, rfp = result.documents

        # Original filename and source URL preserved.
        assert summary.filename == "summary.txt"
        assert summary.source_url == "https://src.example/t7/summary"
        assert rfp.filename == "rfp.pdf"

        # Checksum, size and storage-path contract.
        assert summary.checksum == checksum_of(SUMMARY_TEXT)
        assert summary.size_bytes == len(SUMMARY_TEXT)
        assert summary.storage_key.startswith("tenders/7/attachments/")
        assert storage.exists(summary.storage_key)
        assert storage.get(summary.storage_key) == SUMMARY_TEXT
        assert storage.size_of(summary.storage_key) == len(SUMMARY_TEXT)

        # Response MIME recorded for top-level documents.
        assert summary.mime_type == "text/plain"
        assert rfp.mime_type == "application/pdf"

        rows = _rows(session_factory_gr, 7)
        assert len(rows) == 2
        by_url = {row.source_url: row for row in rows}
        sum_row = by_url["https://src.example/t7/summary"]
        assert sum_row.download_status == "downloaded"
        assert sum_row.checksum == checksum_of(SUMMARY_TEXT)
        assert sum_row.storage_path == summary.storage_key
        assert sum_row.extraction_status == "pending"  # prompt 08 never touches extraction

    def test_mime_falls_back_to_guess(self, session_factory_gr, tmp_storage) -> None:
        site = DownloadSite(attachments={"notice": (text_pdf(["notice"]), None)})
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)
        result = service.acquire(
            8,
            [TenderAttachment(source_url="https://src.example/t8/notice", filename="notice.pdf")],
        )
        assert len(result.documents) == 1
        assert result.documents[0].mime_type == "application/pdf"

    def test_advertised_size_mismatch_warns_but_succeeds(
        self, session_factory_gr, tmp_storage, caplog
    ) -> None:
        site = DownloadSite(attachments={"summary": (SUMMARY_TEXT, "text/plain")})
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)
        attachment = TenderAttachment(
            source_url="https://src.example/t9/summary",
            filename="summary.txt",
            advertised_size_bytes=5_000_000,  # far larger than the actual ~21 bytes
        )
        with caplog.at_level(logging.WARNING, logger="tender_intelligence.acquisition.service"):
            result = service.acquire(9, [attachment], correlation_id="corr-warn")
        assert len(result.documents) == 1
        assert not result.failures
        assert any("advertised" in record.message for record in caplog.records)

    def test_html_error_page_rejected_for_binary(self, session_factory_gr, tmp_storage) -> None:
        site = DownloadSite(
            attachments={"spec": (b"<html><body>not found</body></html>", "text/html")}
        )
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)
        result = service.acquire(
            1,
            [TenderAttachment(source_url="https://src.example/t1/spec", filename="spec.pdf")],
        )
        assert len(result.documents) == 0
        assert len(result.failures) == 1
        failure = result.failures[0]
        assert failure.category == "invalid_response"
        assert failure.retryable is False
        assert failure.filename == "spec.pdf"
        assert result.incomplete_inputs
        rows = _rows(session_factory_gr, 1)
        assert rows[0].download_status == "failed"


class TestZipAcquisition:
    def test_zip_expanded_flat_with_correct_checksums(
        self, session_factory_gr, tmp_storage
    ) -> None:
        spec_pdf = text_pdf(["tender specification"])  # built once: pdf bytes embed timestamps
        outer = zip_bytes(
            {
                "spec.pdf": spec_pdf,
                "readme.txt": b"annex notes",
                "forms/data.csv": b"a,b\n1,2",
            }
        )
        site = DownloadSite(attachments={"bundle": (outer, "application/zip")})
        service, storage = _make_service(session_factory_gr, str(tmp_storage), site)
        result = service.acquire(
            2,
            [TenderAttachment(source_url="https://src.example/t2/bundle", filename="bundle.zip")],
        )

        # Flat downstream document set: the archive itself + every inner member.
        assert not result.failures
        assert len(result.documents) == 4
        archive_doc, *members = result.documents
        assert archive_doc.filename == "bundle.zip"
        assert archive_doc.is_archive_inner is False
        assert all(member.is_archive_inner for member in members)

        by_name = {member.filename: member for member in members}
        assert set(by_name) == {"spec.pdf", "readme.txt", "forms/data.csv"}
        assert by_name["spec.pdf"].source_url == "https://src.example/t2/bundle!/spec.pdf"
        assert by_name["spec.pdf"].checksum == checksum_of(spec_pdf)
        assert by_name["spec.pdf"].parent_filename == "bundle.zip"
        assert by_name["forms/data.csv"].source_url == "https://src.example/t2/bundle!/forms/data.csv"

        # Every document row is stored: 4 rows, all downloaded, all under the tender key space.
        rows = _rows(session_factory_gr, 2)
        assert len(rows) == 4
        assert {row.download_status for row in rows} == {"downloaded"}
        keys = storage.list_keys("tenders/2")
        assert len(keys) == 4

    def test_unsafe_members_rejected_but_archive_and_siblings_acquired(
        self, session_factory_gr, tmp_storage
    ) -> None:
        outer = zip_bytes(
            {
                "good.txt": b"ok",
                "../evil.txt": b"x",
                "a/../../escape": b"y",
            }
        )
        site = DownloadSite(attachments={"bundle": (outer, "application/zip")})
        service, storage = _make_service(session_factory_gr, str(tmp_storage), site)
        result = service.acquire(
            3,
            [TenderAttachment(source_url="https://src.example/t3/bundle", filename="bundle.zip")],
        )

        # Archive + the one safe member; the two hostile members rejected per member.
        assert len(result.documents) == 2
        assert len(result.failures) == 2
        assert {failure.filename for failure in result.failures} == {
            "../evil.txt",
            "a/../../escape",
        }
        assert all(failure.category == "unsafe_archive_member" for failure in result.failures)
        assert result.incomplete_inputs
        assert all(failure.retryable is False for failure in result.failures)

        rows = _rows(session_factory_gr, 3)
        assert len(rows) == 2
        assert {row.download_status for row in rows} == {"downloaded"}

    def test_malformed_zip_fails_the_attachment(self, session_factory_gr, tmp_storage) -> None:
        site = DownloadSite(attachments={"bundle": (b"definitely not a zip", "application/zip")})
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)
        result = service.acquire(
            4,
            [TenderAttachment(source_url="https://src.example/t4/bundle", filename="bundle.zip")],
        )
        assert len(result.documents) == 0
        assert len(result.failures) == 1
        assert result.failures[0].category == "invalid_archive"
        assert result.incomplete_inputs
        rows = _rows(session_factory_gr, 4)
        assert rows[0].download_status == "failed"


class TestFailureTolerance:
    def test_five_document_fixture_isolates_failure(
        self, session_factory_gr, tmp_storage
    ) -> None:
        urls = {
            "A": "https://src.example/t5/a",
            "B": "https://src.example/t5/b",
            "C": "https://src.example/t5/c",
            "D": "https://src.example/t5/d",
            "E": "https://src.example/t5/e",
        }
        site = DownloadSite(
            attachments={url: (f"docs {prefix}".encode(), None) for prefix, url in urls.items()}
        )
        site.attachments.pop(urls["C"])
        site.fail_urls.add(urls["C"])
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)

        result = service.acquire(
            5,
            [
                TenderAttachment(source_url=url, filename=f"{prefix.lower()}.txt")
                for prefix, url in urls.items()
            ],
        )

        assert {doc.filename for doc in result.documents} == {
            "a.txt",
            "b.txt",
            "d.txt",
            "e.txt",
        }
        assert len(result.failures) == 1
        assert result.failures[0].filename == "c.txt"
        assert result.failures[0].category == "http_error"
        assert result.failures[0].retryable is False
        assert result.incomplete_inputs

        rows = _rows(session_factory_gr, 5)
        by_file = {row.filename: row for row in rows}
        assert by_file["a.txt"].download_status == "downloaded"
        assert by_file["b.txt"].download_status == "downloaded"
        assert by_file["c.txt"].download_status == "failed"
        assert by_file["d.txt"].download_status == "downloaded"
        assert by_file["e.txt"].download_status == "downloaded"

    def test_failed_attempt_followed_by_successful_retry(
        self, session_factory_gr, tmp_storage
    ) -> None:
        site = DownloadSite(attachments={"summary": (SUMMARY_TEXT, "text/plain")})
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)
        attachment = _summary_attachment(11)
        site.fail_urls.add(attachment.source_url)

        first = service.acquire(11, [attachment], correlation_id="corr-fail")
        assert len(first.failures) == 1
        assert len(_rows(session_factory_gr, 11)) == 1

        site.fail_urls.clear()
        second = service.acquire(11, [attachment], correlation_id="corr-retry")
        assert not second.failures
        assert len(second.documents) == 1
        rows = _rows(session_factory_gr, 11)
        assert len(rows) == 1  # updated in place, never duplicated
        assert rows[0].download_status == "downloaded"
        assert rows[0].checksum == checksum_of(SUMMARY_TEXT)


class TestIdempotencyAndRerun:
    def test_rerun_reports_existing_documents_not_empty(
        self, session_factory_gr, tmp_storage
    ) -> None:
        """Regression for the recorded re-run defect (docs/09): a re-run of an already
        acquired tender must return its documents, never an empty :class:`AcquisitionResult`."""
        site = DownloadSite(attachments={"summary": (SUMMARY_TEXT, "text/plain")})
        service, storage = _make_service(session_factory_gr, str(tmp_storage), site)
        attachment = _summary_attachment(10)

        first = service.acquire(10, [attachment], correlation_id="corr-run1")
        assert len(first.documents) == 1
        keys_after_first = sorted(storage.list_keys())

        second = service.acquire(10, [attachment], correlation_id="corr-run2")

        # The defect: `second.documents` used to be empty (service returned
        # `AcquisitionResult()` on the already-acquired path). Now the stored
        # document(s) are reported, so a caller can trust the return value.
        assert len(second.documents) == 1
        assert [doc.document_id for doc in second.documents] == [
            doc.document_id for doc in first.documents
        ]
        assert second.documents[0].storage_key == first.documents[0].storage_key
        assert not second.failures
        assert not second.incomplete_inputs

        # Nothing re-downloaded, nothing duplicated.
        assert len(_rows(session_factory_gr, 10)) == 1
        assert sorted(storage.list_keys()) == keys_after_first
        assert len(site.requests) == 1  # the second run reused the stored copy, no fetch

    def test_rerun_does_not_refetch(self, session_factory_gr, tmp_storage) -> None:
        site = DownloadSite(attachments={"summary": (SUMMARY_TEXT, "text/plain")})
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)
        attachment = _summary_attachment(12)
        service.acquire(12, [attachment])
        requests_before = len(site.requests)
        service.acquire(12, [attachment])
        assert len(site.requests) == requests_before  # reuse path never re-fetches

    def test_partial_archive_rerun_reattempts_failed_member(
        self, session_factory_gr, tmp_storage
    ) -> None:
        """A partially-acquired archive (one corrupt member) is not silently skipped on
        re-run: the failed member is re-attempted once its bytes become valid."""

        def corrupted_nested() -> bytes:
            # Starts with ZIP magic (so a nested-archive is detected) but is not decodable.
            return b"PK\x03\x04" + b"\xff" * 32

        def broken_outer() -> bytes:
            return zip_bytes({"good.txt": b"ok", "bad.zip": corrupted_nested()})

        def fixed_outer() -> bytes:
            return zip_bytes({"good.txt": b"ok", "bad.zip": zip_bytes({"inner.txt": b"deep"})})

        site = DownloadSite(attachments={"bundle": (broken_outer(), "application/zip")})
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)
        attachment = TenderAttachment(
            source_url="https://src.example/t13/bundle", filename="bundle.zip"
        )

        first = service.acquire(13, [attachment], correlation_id="corr-p1")
        # Archive + good.txt + the nested zip itself are stored; the corrupt member is a failure.
        assert len(first.documents) == 3
        assert len(first.failures) == 1
        assert first.failures[0].category == "invalid_archive"
        rows = _rows(session_factory_gr, 13)
        by_file = {row.filename: row for row in rows}
        assert by_file["bad.zip"].download_status == "failed"

        site.attachments["bundle"] = (fixed_outer(), "application/zip")
        second = service.acquire(13, [attachment], correlation_id="corr-p2")

        # Now the nested zip and its inner file are both acquired; nothing re-downloads
        # the already-stored archive or good.txt, and nothing is duplicated.
        assert not second.failures
        assert len(second.documents) == 4
        rows = _rows(session_factory_gr, 13)
        assert len(rows) == 4
        assert {row.download_status for row in rows} == {"downloaded"}


class TestHardening:
    def test_hostile_filename_cannot_escape_storage(self, session_factory_gr, tmp_storage) -> None:
        site = DownloadSite(attachments={"evil": (b"payload", "text/plain")})
        service, storage = _make_service(session_factory_gr, str(tmp_storage), site)
        result = service.acquire(
            5,
            [
                TenderAttachment(
                    source_url="https://src.example/t5/evil",
                    filename="../../outside.pdf",
                )
            ],
        )
        assert len(result.documents) == 1
        document = result.documents[0]
        # Original name preserved as metadata on the row…
        row = _rows(session_factory_gr, 5)[0]
        assert row.filename == "../../outside.pdf"
        # …while the storage key is a plain basename under the checksum scope.
        assert document.storage_key == (
            f"tenders/5/attachments/{document.checksum}/outside.pdf"
        )
        assert storage.exists(document.storage_key)
        assert storage.get(document.storage_key) == b"payload"
        # Nothing escaped the storage root.
        assert set(storage.list_keys()) == {document.storage_key}

    def test_storage_failure_is_isolated_and_recorded(
        self, session_factory_gr, tmp_storage
    ) -> None:
        class BrokenStorage(ObjectStorage):
            def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
                raise OSError("simulated disk failure")

            def get(self, key: str) -> bytes:
                raise KeyError(key)

            def size_of(self, key: str) -> int:
                raise KeyError(key)

            def exists(self, key: str) -> bool:
                return False

            def delete(self, key: str) -> None:
                return None

            def list_keys(self, prefix: str = "") -> list[str]:
                return []

        site = DownloadSite(attachments={"summary": (SUMMARY_TEXT, "text/plain")})
        policy = CrawlPolicy(
            request_interval_seconds=0.0,
            backoff_base_seconds=0.0,
            backoff_max_seconds=0.0,
            max_retries=2,
        )
        fetcher = DocumentFetcher(policy, transport=MockTransport(site.handler))
        service = DocumentAcquisitionService(
            session_factory_gr, storage=BrokenStorage(), fetcher=fetcher
        )

        result = service.acquire(6, [_summary_attachment(6)])
        assert len(result.documents) == 0
        assert len(result.failures) == 1
        assert result.failures[0].category == STORAGE_FAILED
        assert result.failures[0].retryable is True
        assert _rows(session_factory_gr, 6)[0].download_status == "failed"

    def test_correlation_id_traced_through_logs(
        self, session_factory_gr, tmp_storage, caplog
    ) -> None:
        site = DownloadSite(attachments={"summary": (SUMMARY_TEXT, "text/plain")})
        service, _ = _make_service(session_factory_gr, str(tmp_storage), site)
        with caplog.at_level(logging.INFO, logger="tender_intelligence.acquisition.service"):
            service.acquire(9, [_summary_attachment(9)], correlation_id="corr-unique-xyz")
        assert any(
            getattr(record, "correlation_id", None) == "corr-unique-xyz"
            and getattr(record, "stage", None) == "acquisition"
            for record in caplog.records
        )
        assert any(
            getattr(record, "status", None) == "complete"
            for record in caplog.records
        )
