"""A fully-wired, offline 04 → 09 pipeline for the prompt-10 suite.

Nothing about the pipeline itself is faked here. Real discovery, real deduplication, real
persistence, real acquisition, real extraction and real ``RunHistory`` run against a migrated
SQLite schema, the real filesystem storage, and real PDF/DOCX/ZIP bytes. The only seam is
*transport*: an ``httpx2.MockTransport`` stands in for the network, so WAHO's own listing and
detail fixtures, and the attachment bytes they link to, are served in-process by the real
:class:`~tender_intelligence.sources.polite.PoliteHttpClient` and the real
:class:`~tender_intelligence.acquisition.fetcher.DocumentFetcher` — robots handling, pacing,
retries, size guards, MIME checks and error classification all still execute.

That distinction matters for prompt 10 §18: mocks are used for *where the bytes come from*,
never for *what the pipeline does with them*.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from httpx2 import MockTransport, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from support.documents import (
    ENGLISH,
    EVALUATION_MATRIX,
    FRENCH,
    docx_bytes,
    mixed_pdf,
    text_pdf,
    zip_bytes,
)
from tender_intelligence.acquisition.fetcher import DocumentFetcher
from tender_intelligence.acquisition.service import DocumentAcquisitionService
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.sources import Source
from tender_intelligence.dedup.service import DedupService
from tender_intelligence.orchestrator.alerts import NullAlertHook
from tender_intelligence.orchestrator.config import ConfigLoader
from tender_intelligence.orchestrator.coordinator import RunCoordinator
from tender_intelligence.orchestrator.registry import AdapterRegistry
from tender_intelligence.orchestrator.retry import RetryPolicy
from tender_intelligence.orchestrator.scheduler import SourceScheduler
from tender_intelligence.orchestrator.worker import Worker
from tender_intelligence.processing.ocr import OcrEngine
from tender_intelligence.processing.service import DocumentProcessingService
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import PoliteHttpClient
from tender_intelligence.storage.local import LocalFileSystemStorage

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sources" / "waho"

BASE_URL = "https://data.wahooas.org"
LISTING_URL = f"{BASE_URL}/tenders/tenders/list"
DETAIL_PATH = "/tenders/tenders/{tender_id}/list"

#: The three tenders ``listing_page_1.html`` / ``listing_page_2.html`` advertise.
LISTING_EXTERNAL_IDS = ("167", "166", "165")

#: A download link on the detail fixture carries a query string, so its URL cannot be
#: reconstructed from a filename alone. The bytes served for it are a PDF.
_QUERY_ATTACHMENT_PATH = "/tenders/tenders/167/download"

ROBOTS_TXT = "User-agent: *\nDisallow:\n"


def fixture(name: str) -> str:
    """Read one WAHO HTML fixture."""
    with open(os.fspath(FIXTURES / name), encoding="utf-8") as handle:
        return handle.read()


# --------------------------------------------------------------------------- payloads
#
# Real bytes, generated per attachment URL: a genuine PDF with a native text layer, a genuine
# OOXML package carrying a table, a genuine SpreadsheetML package, and a genuine ZIP holding a
# real member.
#
# Each generator is keyed by a *label* derived from the source path, because two WAHO
# attachments legitimately share a basename — ``.../167/en/Annexe 1.pdf`` and
# ``.../167/fr/Annexe 1.pdf`` are different documents. Serving one payload for both would make
# prompt 08 collapse them by checksum, and the fixture would then be asserting a behaviour the
# real site never produces.


def _label_for(path: str) -> str:
    """A stable, human-readable token identifying one attachment URL.

    Keeps the parent segment so same-basename attachments in different language folders stay
    distinct.
    """
    parts = [unquote(segment) for segment in path.strip("/").split("/")]
    return "/".join(parts[-2:]) if len(parts) >= 2 else path


@functools.lru_cache(maxsize=32)
def _pdf_bytes(label: str) -> bytes:
    return text_pdf([f"{ENGLISH}\n\nAttachment: {label}"])


@functools.lru_cache(maxsize=32)
def _annex_pdf_bytes(label: str) -> bytes:
    return text_pdf([f"{ENGLISH}\n\nAnnexe 1 for {label}."])


@functools.lru_cache(maxsize=32)
def _docx_bytes(label: str) -> bytes:
    return docx_bytes([f"Attachment: {label}", FRENCH, EVALUATION_MATRIX])


@functools.lru_cache(maxsize=32)
def _zip_bytes(label: str) -> bytes:
    return zip_bytes(
        {
            "annexe-1.pdf": _annex_pdf_bytes(label),
            "readme.txt": f"annex readme for {label}".encode(),
        }
    )


@functools.lru_cache(maxsize=32)
def _hwb_bytes(label: str) -> bytes:
    return f"HWB supporting data for {label}\n".encode()


@functools.lru_cache(maxsize=1)
def _mixed_pdf_bytes() -> bytes:
    """A native page plus a scanned page: prompt 09's mixed-document fixture (prompt 10 §19 G)."""
    return mixed_pdf(native_pages=1, scanned_pages=1)


# A minimal but genuinely valid SpreadsheetML package. The site really does serve ``.xlsx``
# budget templates, so serving a valid OOXML container is the honest fixture — a bare ZIP magic
# header would only prove that prompt 08 rejects corrupt archives, which is already covered.

_XML_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_RELS_CT = "application/vnd.openxmlformats-package.relationships+xml"
_MAIN_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_SHEET_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
_OFFICE_REL_CT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _xml_file(*lines: str) -> bytes:
    """Join XML fragments into one file: newline per fragment, trailing newline."""
    return ("\n".join(lines) + "\n").encode()


_CONTENT_TYPES_XML = _xml_file(
    _XML_DECL.decode(),
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
    f'<Default Extension="rels" ContentType="{_RELS_CT}"/>',
    '<Default Extension="xml" ContentType="application/xml"/>',
    f'<Override PartName="/xl/workbook.xml" ContentType="{_MAIN_CT}"/>',
    f'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="{_SHEET_CT}"/>',
    "</Types>",
)

_ROOT_RELS_XML = _xml_file(
    _XML_DECL.decode(),
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    f'<Relationship Id="rId1" Type="{_OFFICE_REL_CT}/officeDocument" '
    'Target="xl/workbook.xml"/>',
    "</Relationships>",
)

_WORKBOOK_XML = _xml_file(
    _XML_DECL.decode(),
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    f'xmlns:r="{_OFFICE_REL_CT}">',
    '<sheets><sheet name="Budget" sheetId="1" r:id="rId1"/></sheets>',
    "</workbook>",
)

_WORKBOOK_RELS_XML = _xml_file(
    _XML_DECL.decode(),
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    f'<Relationship Id="rId1" Type="{_OFFICE_REL_CT}/worksheet" '
    'Target="worksheets/sheet1.xml"/>',
    "</Relationships>",
)


def _sheet_xml(label: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>'
        f"{label}"
        "</t></is></c></row></sheetData></worksheet>\n"
    ).encode()


@functools.lru_cache(maxsize=32)
def _xlsx_bytes(label: str) -> bytes:
    return zip_bytes(
        {
            "[Content_Types].xml": _CONTENT_TYPES_XML,
            "_rels/.rels": _ROOT_RELS_XML,
            "xl/workbook.xml": _WORKBOOK_XML,
            "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS_XML,
            "xl/worksheets/sheet1.xml": _sheet_xml(label),
        }
    )


# --------------------------------------------------------------------------- transport


@dataclass
class OfflineSite:
    """An in-process WAHO: listing pages, detail pages and attachment bytes.

    Records every URL it was asked for, so a test can assert what the pipeline *did* and did
    not request — the admin-app test (§19 D) reads that list to prove no admin endpoint was
    ever contacted.
    """

    listing_page_1: str | None = None
    detail_by_tender: dict[str, str] = field(default_factory=dict)
    attachment_bytes: dict[str, bytes] = field(default_factory=dict)
    #: URL substring -> HTTP status, for injecting transport-level failures.
    fail_urls: dict[str, int] = field(default_factory=dict)
    #: URL substring -> number of remaining times to fail before succeeding.
    transient_failures: dict[str, int] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def handler(self, request: Request) -> Response:
        url = str(request.url)
        self.requests.append(url)
        path = request.url.path

        for needle, status in list(self.fail_urls.items()):
            if needle in url:
                return Response(status, json={"error": "injected failure"})

        for needle, remaining in list(self.transient_failures.items()):
            if needle in url and remaining > 0:
                self.transient_failures[needle] = remaining - 1
                return Response(503, json={"error": "temporarily unavailable"})

        if path == "/robots.txt":
            return Response(200, text=ROBOTS_TXT)

        if url == LISTING_URL:
            html = self.listing_page_1 if self.listing_page_1 is not None else _page1()
            return _html(html)
        if url == f"{LISTING_URL}?page=2":
            return _html(_page2())

        for tender_id, html in self.detail_by_tender.items():
            if path == DETAIL_PATH.format(tender_id=tender_id):
                return _html(html)

        payload = self._attachment_for(path)
        if payload is not None:
            data, content_type = payload
            return Response(200, content=data, headers={"content-type": content_type})

        # Anything else is a fixture gap, and saying so beats a confusing parse failure.
        return Response(404, text=f"no fixture for {url}")

    def _attachment_for(self, path: str) -> tuple[bytes, str] | None:
        for needle, data in self.attachment_bytes.items():
            if needle in path:
                return data, _content_type_for(path)
        if not (path.startswith("/uploads/") or path == _QUERY_ATTACHMENT_PATH):
            return None
        label = _label_for(path)
        if path.endswith(".docx"):
            return _docx_bytes(label), _content_type_for(path)
        if path.endswith(".zip"):
            return _zip_bytes(label), "application/zip"
        if path.endswith(".xlsx"):
            return _xlsx_bytes(label), _content_type_for(path)
        if path.endswith(".pdf") or path == _QUERY_ATTACHMENT_PATH:
            return _pdf_bytes(label), "application/pdf"
        return _hwb_bytes(label), "application/octet-stream"


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _content_type_for(path: str) -> str:
    if path.endswith(".pdf"):
        return "application/pdf"
    if path.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if path.endswith(".zip"):
        return "application/zip"
    if path.endswith(".xlsx"):
        return _XLSX_MIME
    return "application/octet-stream"


def _html(body: str) -> Response:
    return Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


@functools.lru_cache(maxsize=1)
def _page1() -> str:
    return fixture("listing_page_1.html")


@functools.lru_cache(maxsize=1)
def _page2() -> str:
    return fixture("listing_page_2.html")


def _detail_html() -> str:
    return fixture("detail_en_complete.html")


# --------------------------------------------------------------------------- harness


def test_policy(**overrides: Any) -> CrawlPolicy:
    """A crawl policy that never sleeps: pacing and backoff are asserted, not waited for."""
    base = CrawlPolicy(
        request_interval_seconds=0.0, backoff_base_seconds=0.0, backoff_max_seconds=0.0
    )
    return replace(base, **overrides) if overrides else base


@dataclass
class Harness:
    """Everything one prompt-10 integration test needs, already wired together."""

    session_factory: sessionmaker[Session]
    storage: LocalFileSystemStorage
    site: OfflineSite
    registry: AdapterRegistry
    dedup: DedupService
    acquisition: DocumentAcquisitionService
    processing: DocumentProcessingService
    scheduler: SourceScheduler
    config_loader: ConfigLoader
    coordinator: RunCoordinator
    worker: Worker
    alerts: NullAlertHook

    def seed_source(
        self,
        *,
        name: str = "wahoo",
        source_type: str = "wahoo",
        listing_url: str | None = LISTING_URL,
        base_url: str = BASE_URL,
        active: bool = True,
        crawl_frequency_minutes: int | None = None,
        parser_config: dict[str, Any] | None = None,
    ) -> int:
        """Configure one source row, the way the admin app would."""
        with self.session_factory() as session:
            source = Source(
                name=name,
                source_type=source_type,
                base_url=base_url,
                listing_url=listing_url,
                active=active,
                crawl_frequency_minutes=crawl_frequency_minutes,
                parser_config=parser_config,
            )
            session.add(source)
            session.commit()
            return int(source.id)

    def ensure_settings(self, *, test_mode: bool = True) -> int:
        """Make sure the settings singleton exists; return its version."""
        with self.session_factory() as session:
            row = session.scalars(select(Setting)).one_or_none()
            if row is None:
                row = Setting.seed_default()
                row.test_mode = test_mode
                session.add(row)
            session.commit()
            return int(row.version or 0)

    def change_configuration(self, *, test_mode: bool, reason: str) -> int:
        """Flip a configuration value and bump its version, as the admin app would."""
        with self.session_factory() as session:
            row = session.scalars(select(Setting)).one_or_none()
            assert row is not None, "seed the settings row first"
            row.test_mode = test_mode
            row.test_mode_reason = reason
            row.version = int(row.version or 0) + 1
            session.commit()
            return int(row.version)

    def stored_keys(self) -> list[str]:
        return self.storage.list_keys()


def build_harness(
    session_factory: sessionmaker[Session],
    storage_root: str | Path,
    *,
    site: OfflineSite | None = None,
    ocr: OcrEngine | None = None,
    retry_attempts: int = 2,
    sleeper: Any = None,
    coordinator_overrides: dict[str, Any] | None = None,
) -> Harness:
    """Wire the whole prompt-10 pipeline over an offline transport.

    The *same* transport feeds discovery and acquisition, so one request log covers both and a
    test can assert exactly which URLs the run touched.
    """
    resolved_site = site or OfflineSite(
        detail_by_tender={tender_id: _detail_html() for tender_id in LISTING_EXTERNAL_IDS}
    )
    transport = MockTransport(resolved_site.handler)
    policy = test_policy()

    storage = LocalFileSystemStorage(Path(storage_root) / "objects")
    discovery_client = PoliteHttpClient.build(policy, transport=transport)
    fetcher = DocumentFetcher(policy, transport=transport)

    registry = AdapterRegistry.default(policy=policy, fetcher=discovery_client.get)
    dedup = DedupService(session_factory)
    acquisition = DocumentAcquisitionService(
        session_factory, storage=storage, fetcher=fetcher
    )
    processing = DocumentProcessingService(session_factory, storage=storage, ocr=ocr)
    scheduler = SourceScheduler(session_factory)
    config_loader = ConfigLoader(session_factory)
    alerts = NullAlertHook()

    coordinator_kwargs: dict[str, Any] = {
        "session_factory": session_factory,
        "registry": registry,
        "dedup": dedup,
        "acquisition": acquisition,
        "processing": processing,
        "scheduler": scheduler,
        "config_loader": config_loader,
        "alert_hook": alerts,
        "retry_policy": RetryPolicy(attempts=retry_attempts, backoff_base_seconds=0.0),
    }
    if sleeper is not None:
        coordinator_kwargs["sleeper"] = sleeper
    coordinator_kwargs.update(coordinator_overrides or {})
    coordinator = RunCoordinator(**coordinator_kwargs)
    worker = Worker(coordinator=coordinator, scheduler=scheduler)

    return Harness(
        session_factory=session_factory,
        storage=storage,
        site=resolved_site,
        registry=registry,
        dedup=dedup,
        acquisition=acquisition,
        processing=processing,
        scheduler=scheduler,
        config_loader=config_loader,
        coordinator=coordinator,
        worker=worker,
        alerts=alerts,
    )


__all__ = [
    "BASE_URL",
    "FIXTURES",
    "Harness",
    "LISTING_EXTERNAL_IDS",
    "LISTING_URL",
    "OfflineSite",
    "build_harness",
    "fixture",
    "test_policy",
]
