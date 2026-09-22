"""Provider seams shared by the worker pipeline (docs/02 §2.8–2.11).

Phase 0 defines the abstractions only — concrete source, document and LLM adapters
arrive with their implementing phases (docs/12). ``MailProvider`` lives in
``tender_intelligence.mail.provider``.
"""

from __future__ import annotations

from tender_intelligence.interfaces.document import (
    DocumentBundle,
    DocumentProcessor,
    ExtractedDocument,
)
from tender_intelligence.interfaces.llm import LLMClient, LLMMessage, LLMResponse
from tender_intelligence.interfaces.source import (
    SourceAdapter,
    SourceError,
    TenderAttachment,
    TenderDetail,
    TenderListing,
)

__all__ = [
    "DocumentBundle",
    "DocumentProcessor",
    "ExtractedDocument",
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "SourceAdapter",
    "SourceError",
    "TenderAttachment",
    "TenderDetail",
    "TenderListing",
]
