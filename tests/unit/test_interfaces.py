"""Unit tests for the Phase 0 provider interfaces (src/tender_intelligence/interfaces)."""

from __future__ import annotations

from tender_intelligence.core.errors import AI_CALL_TIMEOUT
from tender_intelligence.interfaces.document import (
    DocumentBundle,
    DocumentProcessingError,
    DocumentProcessor,
    ExtractedDocument,
)
from tender_intelligence.interfaces.llm import LLMClient, LLMError, LLMMessage, LLMResponse
from tender_intelligence.interfaces.source import (
    SourceAdapter,
    SourceError,
    TenderAttachment,
    TenderDetail,
    TenderListing,
)


class TestSourceInterface:
    def test_listing_fields(self):
        listing = TenderListing(external_id="waho-42", title="EOI", url="https://e")
        assert listing.title == "EOI"
        assert listing.raw_metadata == {}

    def test_detail_embeds_listing_and_attachments(self):
        listing = TenderListing(external_id="waho-42", title="EOI", url="https://e")
        detail = TenderDetail(
            listing=listing,
            attachments=[TenderAttachment(source_url="https://a.pdf", filename="a.pdf")],
        )
        assert detail.listing.external_id == "waho-42"
        assert detail.attachments[0].filename == "a.pdf"

    def test_source_error_has_structured_code(self):
        err = SourceError("boom", context={"url": "https://e"})
        assert err.error_code == "source_unreachable"
        assert err.context["url"] == "https://e"

    def test_adapter_abstract_methods_present(self):
        assert "list_new_tenders" in SourceAdapter.__abstractmethods__
        assert "get_detail" in SourceAdapter.__abstractmethods__
        assert "get_attachments" in SourceAdapter.__abstractmethods__


class TestDocumentInterface:
    def test_bundle_failed_documents_only(self):
        bundle = DocumentBundle(
            documents=[
                ExtractedDocument(
                    filename="ok.txt",
                    source_url="https://o",
                    mime_type="text/plain",
                    language="en",
                    checksum="c1",
                    extraction_method="native_pdf",
                    text="body",
                ),
                ExtractedDocument(
                    filename="bad.pdf",
                    source_url="https://b",
                    mime_type="application/pdf",
                    language=None,
                    checksum="c2",
                    extraction_method="ocr",
                    text="",
                    error_code="ocr_failed",
                ),
            ]
        )
        assert [d.filename for d in bundle.failed_documents] == ["bad.pdf"]
        assert bundle.incomplete_inputs is False

    def test_processing_error_code_defaults(self):
        err = DocumentProcessingError("broken")
        assert err.error_code == "ocr_failed"

    def test_processor_abstract_method_present(self):
        assert "process" in DocumentProcessor.__abstractmethods__


class TestLLMInterface:
    def test_response_and_usage(self):
        resp = LLMResponse(content="go", profile_name="p", model="m")
        assert resp.content == "go"
        assert resp.usage.prompt_tokens == 0

    def test_chat_error_code(self):
        err = LLMError("timeout")
        assert err.error_code == AI_CALL_TIMEOUT

    def test_client_abstract_method_present(self):
        assert "chat" in LLMClient.__abstractmethods__


def test_message_roles():
    assert LLMMessage(role="user", content="hi").role == "user"
