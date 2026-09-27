"""Unit tests for the WAHO detail-page and attachment-discovery adapter (prompt 07).

All tests run against offline fixtures and a fake fetcher — no live WAHO access, no network
(prompt 07 §8, §9). Coverage maps to prompt 07 §9 items 1–16.
"""

from __future__ import annotations

import logging
import os
import urllib.parse
from datetime import UTC, datetime

import pytest

from tender_intelligence.core.correlation import correlation_context
from tender_intelligence.core.errors import PARSER_MISMATCH, SOURCE_UNREACHABLE
from tender_intelligence.interfaces.source import (
    SourceError,
    TenderAttachment,
    TenderDetail,
)
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse
from tender_intelligence.sources.waho import WahoPaginatedAdapter

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures",
    "sources",
    "waho",
)

LISTING_URL = "https://data.wahooas.org/tenders/tenders/list"


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def _detail_url(tender_id: str) -> str:
    return urllib.parse.urljoin(LISTING_URL, f"/tenders/tenders/{tender_id}/list")


DETAIL_EN = _detail_url("167")
DETAIL_FR = _detail_url("163")
DETAIL_PT = _detail_url("162")
DETAIL_MISSING_DEADLINE = _detail_url("164")
DETAIL_MISSING_OPTIONAL = _detail_url("165")
DETAIL_MALFORMED = _detail_url("180")
DETAIL_EN_DIR = "https://data.wahooas.org/uploads/tenders/167/"


def _adapter(page_map: dict[str, str], *, reject_others: bool = False) -> WahoPaginatedAdapter:
    def fetch(url: str) -> HttpResponse:
        if url in page_map:
            return HttpResponse(
                status_code=200,
                headers={"content-type": "text/html; charset=utf-8"},
                text=_fixture(page_map[url]),
                url=url,
            )
        if reject_others:
            raise SourceError(
                f"unexpected fetch for {url} (discovery must not download attachments)",
                error_code=SOURCE_UNREACHABLE,
                context={"url": url},
            )
        raise SourceError(
            f"No fixture for {url}",
            error_code=SOURCE_UNREACHABLE,
            context={"url": url},
        )

    return WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=fetch,
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )


EN_DETAIL_MAP = {DETAIL_EN: "detail_en_complete.html"}


# ---------------------------------------------------------------------------
# get_detail — neutral TenderDetail (§9.1)
# ---------------------------------------------------------------------------


def test_get_detail_en_returns_neutral_detail() -> None:
    detail = _adapter(EN_DETAIL_MAP).get_detail("167")
    assert isinstance(detail, TenderDetail)
    assert detail.listing.external_id == "167"
    assert detail.listing.url == DETAIL_EN
    assert detail.listing.title.startswith("RECRUTEMENT D'UN CABINET DE CONSEIL")
    assert detail.reference_numbers == ["P-Z1-BZ0-012/C"]
    assert detail.procuring_body == "West African Health Organisation (WAHO)"
    assert "traceability" in (detail.scope or "")
    assert detail.requirements_hints
    assert "at least three senior consultants" in detail.requirements_hints[0]
    assert detail.raw_html
    assert "trix-content" in detail.raw_html
    assert detail.listing.raw_metadata["detail_page_url"] == DETAIL_EN
    assert detail.listing.raw_metadata["language"] == "en"


def test_get_detail_french_fixture_parses() -> None:
    detail = _adapter({DETAIL_FR: "detail_fr.html"}).get_detail("163")
    assert detail.listing.title.startswith("AVIS D'APPEL A CANDIDATURES")
    assert detail.reference_numbers == ["P-Z1-CON-901"]
    assert "OOAS" in (detail.procuring_body or "")
    assert detail.listing.raw_metadata["language"] == "fr"
    assert detail.listing.deadline_at == datetime(2026, 11, 12, 14, 0, tzinfo=UTC)
    assert detail.listing.deadline_timezone == "GMT"


def test_get_detail_portuguese_fixture_parses() -> None:
    detail = _adapter({DETAIL_PT: "detail_pt.html"}).get_detail("162")
    assert detail.listing.title.startswith("PUBLICAÇÃO DO PLANO DE AQUISIÇÕES")
    assert detail.reference_numbers == ["P-Z1-ABR-220"]
    assert "OOAS" in (detail.procuring_body or "")
    assert detail.scope
    assert detail.requirements_hints
    assert detail.listing.deadline_at == datetime(2026, 12, 31, 15, 0, tzinfo=UTC)
    assert detail.listing.raw_metadata["language"] == "pt"


def test_detail_embeds_attachments_from_same_page() -> None:
    detail = _adapter(EN_DETAIL_MAP).get_detail("167")
    assert len(detail.attachments) == 8


# ---------------------------------------------------------------------------
# Deadline / timezone rules (§9.3, §9.4)
# ---------------------------------------------------------------------------


def test_deadline_timezone_preserved_and_normalized() -> None:
    detail = _adapter(EN_DETAIL_MAP).get_detail("167")
    assert detail.listing.deadline_timezone == "GMT"
    assert detail.listing.deadline_at == datetime(2026, 9, 24, 13, 0, tzinfo=UTC)
    assert detail.listing.deadline_at.tzinfo is not None
    assert detail.listing.raw_metadata["deadline_raw"] is not None


def test_missing_deadline_component_never_yields_naive() -> None:
    """A date without time/zone must not become a naive local timestamp."""
    detail = _adapter(
        {DETAIL_MISSING_DEADLINE: "detail_missing_deadline_component.html"}
    ).get_detail("164")
    assert detail.listing.deadline_at is None
    assert detail.listing.deadline_timezone is None
    assert (detail.listing.raw_metadata["deadline_raw"] or "").endswith("30 November 2026")


# ---------------------------------------------------------------------------
# Optional fields / malformed / missing (§9.5, §9.6)
# ---------------------------------------------------------------------------


def test_missing_optional_metadata_does_not_crash() -> None:
    detail = _adapter({DETAIL_MISSING_OPTIONAL: "detail_missing_optional.html"}).get_detail("165")
    assert detail.reference_numbers == []
    assert detail.procuring_body is None
    assert detail.scope is None
    assert detail.requirements_hints == []
    assert detail.attachments == []
    assert detail.listing.published_at == datetime(2026, 9, 10, 8, 0, tzinfo=UTC)


def test_malformed_detail_raises_parser_mismatch() -> None:
    adapter = _adapter({DETAIL_MALFORMED: "detail_malformed.html"})
    with pytest.raises(SourceError) as excinfo:
        adapter.get_detail("180")
    assert excinfo.value.error_code == PARSER_MISMATCH


def test_missing_detail_404_raises_source_unreachable() -> None:
    def fetch(url: str) -> HttpResponse:
        return HttpResponse(404, {}, "", url)

    adapter = WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=fetch,
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )
    with pytest.raises(SourceError) as excinfo:
        adapter.get_detail("999")
    assert excinfo.value.error_code == SOURCE_UNREACHABLE
    assert excinfo.value.context.get("status_code") == 404


# ---------------------------------------------------------------------------
# get_attachments — complete enumeration (§9.7)
# ---------------------------------------------------------------------------


def test_get_attachments_returns_all_exposed_links() -> None:
    attachments = _adapter(EN_DETAIL_MAP).get_attachments("167")
    assert len(attachments) == 8
    by_url = {a.source_url: a for a in attachments}
    assert (
        by_url[DETAIL_EN_DIR + "rfp_p-z1-bz0-012_c_en.pdf"].filename == "rfp_p-z1-bz0-012_c_en.pdf"
    )
    assert (
        by_url[DETAIL_EN_DIR + "tor_p-z1-bz0-012_c_en.docx"].filename
        == "tor_p-z1-bz0-012_c_en.docx"
    )
    assert by_url[DETAIL_EN_DIR + "budget_template_en.xlsx"].filename == "budget_template_en.xlsx"
    assert (
        by_url[DETAIL_EN_DIR + "annexes_p-z1-bz0-012_c_en.zip"].filename
        == "annexes_p-z1-bz0-012_c_en.zip"
    )


def test_duplicate_looking_filenames_from_different_urls_kept() -> None:
    attachments = _adapter(EN_DETAIL_MAP).get_attachments("167")
    same_name = [a for a in attachments if a.filename == "Annexe 1.pdf"]
    assert len(same_name) == 2
    assert {a.source_url for a in same_name} == {
        DETAIL_EN_DIR + "en/Annexe 1.pdf",
        DETAIL_EN_DIR + "fr/Annexe 1.pdf",
    }


def test_unfamiliar_extension_not_discarded() -> None:
    attachments = _adapter(EN_DETAIL_MAP).get_attachments("167")
    weird = [a for a in attachments if a.filename == "supporting_data.hwb"]
    assert len(weird) == 1
    assert weird[0].mime_type is None


# ---------------------------------------------------------------------------
# ZIP awareness (§9.8, §9.9)
# ---------------------------------------------------------------------------


def test_zip_link_marked_as_top_level_zip() -> None:
    attachments = _adapter(EN_DETAIL_MAP).get_attachments("167")
    zips = [a for a in attachments if a.is_zip]
    assert len(zips) == 1
    assert zips[0].filename == "annexes_p-z1-bz0-012_c_en.zip"
    assert zips[0].mime_type == "application/zip"


def test_no_zip_contents_enumerated_no_downloads() -> None:
    """Discovery performs zero fetches for attachments and zero inner-file enumeration."""
    adapter = _adapter(EN_DETAIL_MAP, reject_others=True)
    attachments = adapter.get_attachments("167")
    assert all(type(a) is TenderAttachment for a in attachments)
    assert {a.source_url for a in attachments} == {
        DETAIL_EN_DIR + f
        for f in (
            "rfp_p-z1-bz0-012_c_en.pdf",
            "tor_p-z1-bz0-012_c_en.docx",
            "budget_template_en.xlsx",
            "annexes_p-z1-bz0-012_c_en.zip",
            "supporting_data.hwb",
            "en/Annexe 1.pdf",
            "fr/Annexe 1.pdf",
        )
    } | {"https://data.wahooas.org/uploads/tenders/167/download?id=4128"}


# ---------------------------------------------------------------------------
# Metadata only when available (§9.10, §9.11)
# ---------------------------------------------------------------------------


def test_mime_and_advertised_size_only_when_available() -> None:
    attachments = _adapter(EN_DETAIL_MAP).get_attachments("167")
    sized = {a.filename: a.advertised_size_bytes for a in attachments if a.advertised_size_bytes}
    assert sized == {
        "rfp_p-z1-bz0-012_c_en.pdf": 1_258_291,  # 1.2 MB
        "annexes_p-z1-bz0-012_c_en.zip": 3_565_158,  # 3.4 MB
    }
    untyped = {a.filename for a in attachments if a.mime_type is None}
    assert untyped == {"supporting_data.hwb", "Download prosecution annex"}


def test_no_checksum_invented() -> None:
    """The discovery DTO exposes no checksum; nothing may fabricate one before bytes."""
    assert "checksum" not in TenderAttachment.__dataclass_fields__


# ---------------------------------------------------------------------------
# Correlation ID on results/errors (§9.13)
# ---------------------------------------------------------------------------


def test_correlation_id_propagates_to_records(caplog) -> None:
    records: list[logging.LogRecord] = []

    class Recorder(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("tender_intelligence.sources.waho")
    handler = Recorder()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with correlation_context("waho-detail-001"):
            _adapter(EN_DETAIL_MAP).get_detail("167")
            _adapter(EN_DETAIL_MAP).get_attachments("167")
    finally:
        logger.removeHandler(handler)

    assert records
    for record in records:
        assert getattr(record, "correlation_id", None) == "waho-detail-001"


def test_error_context_carries_correlation_id() -> None:
    def fetch(url: str) -> HttpResponse:
        return HttpResponse(404, {}, "", url)

    adapter = WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=fetch,
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )
    with correlation_context("waho-detail-404"), pytest.raises(SourceError) as excinfo:
        adapter.get_attachments("999")
    assert excinfo.value.error_code == SOURCE_UNREACHABLE
    assert excinfo.value.context.get("correlation_id") == "waho-detail-404"


# ---------------------------------------------------------------------------
# Boundary (§9.14)
# ---------------------------------------------------------------------------


def test_no_waho_types_leak_through_boundary() -> None:
    detail = _adapter(EN_DETAIL_MAP).get_detail("167")
    assert type(detail) is TenderDetail
    assert type(detail).__module__ == "tender_intelligence.interfaces.source"
    assert all(type(a) is TenderAttachment for a in detail.attachments)


def test_get_attachments_malformed_page_raises_parser_mismatch() -> None:
    adapter = _adapter({DETAIL_MALFORMED: "detail_malformed.html"})
    with pytest.raises(SourceError) as excinfo:
        adapter.get_attachments("180")
    assert excinfo.value.error_code == PARSER_MISMATCH
    assert excinfo.value.context.get("tender_id") == "180"


def test_grouped_size_never_mis_attributed_and_multiple_blocks_enumerated() -> None:
    """A group-level ``(5 MB)`` belongs to no single file; a second attachment section counts."""
    detail_url = _detail_url("181")
    adapter = _adapter({detail_url: "detail_grouped_size.html"})
    attachments = adapter.get_detail("181").attachments
    assert {a.filename for a in attachments} == {
        "per_file_spec.pdf",
        "shared_pack_one.docx",
        "shared_pack_two.zip",
        "Download memo",
        "supporting_archive.zip",
    }
    sizes = {a.filename: a.advertised_size_bytes for a in attachments}
    assert sizes["per_file_spec.pdf"] == 1_258_291  # per-file (1,2 MB) on its own link
    assert sizes["shared_pack_one.docx"] is None  # 5 MB is shared across two links → None
    assert sizes["shared_pack_two.zip"] is None
    assert sizes["Download memo"] is None
    memo = next(a for a in attachments if a.filename == "Download memo")
    assert memo.source_url.endswith("/uploads/tenders/181/memo-without-extension")
    assert memo.mime_type is None
    zips = [a for a in attachments if a.is_zip]
    assert {a.filename for a in zips} == {"shared_pack_two.zip", "supporting_archive.zip"}


DETAIL_EN_DIR = "https://data.wahooas.org/uploads/tenders/167/"


# ── URL template regression tests (Prompt 16C FIX #2) ─────────────────────


class TestDetailUrlTemplate:
    """Generated detail URLs must be clean — no ° or %C2%B0 corruption."""

    def test_normal_tender_id_produces_clean_url(self):
        adapter = WahoPaginatedAdapter("https://data.wahooas.org/tenders/tenders/list")
        url = adapter._detail_url("162")
        assert "\u00b0" not in url
        assert "%C2%B0" not in url
        assert url == "https://data.wahooas.org/tenders/tenders/162/list"

    def test_url_does_not_contain_degree_characters(self):
        adapter = WahoPaginatedAdapter("https://data.wahooas.org/tenders/tenders/list")
        for tender_id in ["1", "42", "999", "12345"]:
            url = adapter._detail_url(tender_id)
            assert "\u00b0" not in url, f"° found in URL for tender {tender_id}: {url}"
            assert "%C2%B0" not in url, f"%C2%B0 found in URL for tender {tender_id}: {url}"


# ---------------------------------------------------------------------------
# Fix verification — degree-symbol href rejection (Prompt 16C FIX #2)
# ---------------------------------------------------------------------------

DETAIL_DEGREE = _detail_url("162")
_DEGREE_FIXTURE = "detail_degree_href.html"


def _adapter_for_degree() -> WahoPaginatedAdapter:
    """Adapter backed by the degree-href fixture, with default (clean) parser config."""
    return _adapter({DETAIL_DEGREE: _DEGREE_FIXTURE})


# Test G — Clean WAHO attachment href produces the correct absolute URL
def test_clean_attachment_href_produces_correct_absolute_url() -> None:
    """Test G: A normal /uploads/ href resolves to the correct absolute URL."""
    attachments = _adapter_for_degree().get_attachments("162")
    urls = {a.source_url for a in attachments}
    assert "https://data.wahooas.org/uploads/tenders/162/plano_aquisicoes_2026.pdf" in urls


# Test H — A degree-symbol href does not produce %C2%B0 in any attachment source_url
def test_degree_href_does_not_produce_percent_c2_b0_in_url() -> None:
    """Test H: href values containing ° (U+00B0) are rejected before URL construction."""
    attachments = _adapter_for_degree().get_attachments("162")
    for a in attachments:
        assert "%C2%B0" not in a.source_url, (
            f"Corrupted source_url found: {a.source_url!r}"
        )
        assert "\u00b0" not in a.source_url, (
            f"Literal ° found in source_url: {a.source_url!r}"
        )


# Test I — No repeated ° appended to valid attachment URLs
def test_no_degree_characters_appended_to_valid_attachment_urls() -> None:
    """Test I: The clean upload URL is not affected by the degree-href on the same page."""
    attachments = _adapter_for_degree().get_attachments("162")
    clean = next(
        (a for a in attachments if "plano_aquisicoes_2026.pdf" in a.source_url),
        None,
    )
    assert clean is not None, "Clean attachment should still be discovered"
    assert "\u00b0" not in clean.source_url
    assert "°" not in clean.source_url
    assert "%C2%B0" not in clean.source_url


# Test J — Existing valid attachment URLs from the PT fixture remain unchanged
def test_existing_valid_pt_attachment_urls_unchanged() -> None:
    """Test J: The PT fixture's clean URL is unchanged — the fix is purely additive."""
    detail = _adapter({DETAIL_PT: "detail_pt.html"}).get_detail("162")
    attachments = detail.attachments
    assert len(attachments) == 1
    assert attachments[0].source_url == (
        "https://data.wahooas.org/uploads/tenders/162/plano_aquisicoes_2026.pdf"
    )
    assert "\u00b0" not in attachments[0].source_url


# Test K — Only the clean document is returned; the degree-href link is silently dropped
def test_degree_href_link_is_excluded_exactly_one_clean_attachment_returned() -> None:
    """Test K: degree href is dropped; exactly the one clean /uploads/ link is returned."""
    attachments = _adapter_for_degree().get_attachments("162")
    # Exactly one attachment: the clean PDF; the CMS degree-href link is excluded
    assert len(attachments) == 1
    assert attachments[0].filename == "plano_aquisicoes_2026.pdf"
    assert attachments[0].source_url.endswith("/uploads/tenders/162/plano_aquisicoes_2026.pdf")
