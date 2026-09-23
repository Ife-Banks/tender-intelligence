""":mod:`tender_intelligence.db.repositories.documents` — Document persistence (prompt 06).

Document rows are created in the initial ``pending`` state before download/checksum
exists (prompt 06 §7; prompt 08 owns download + checksum computation). Download and
extraction status values are validated against the model-defined enums; the repository
never invents document statuses. Document *downloading/extraction* transition policy
belongs to prompts 08/09, so this layer only validates membership and persists state.
"""

from __future__ import annotations

from sqlalchemy import select

from tender_intelligence.core.errors import PERSISTENCE_NOT_FOUND
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.repositories.base import PersistenceError, Repository
from tender_intelligence.db.repositories.status import assert_valid_document_status


class DocumentRepository(Repository):
    """Row-level reads/writes for :class:`Document` (prompt 06 §4, §7, docs/03 §3.2)."""

    def create(
        self,
        tender_id: int,
        source_url: str,
        filename: str,
        *,
        mime_type: str | None = None,
        language: str | None = None,
        storage_path: str | None = None,
        extracted_text_ref: str | None = None,
    ) -> Document:
        doc = Document(
            tender_id=tender_id,
            filename=filename,
            source_url=source_url,
            storage_path=storage_path,
            mime_type=mime_type,
            language=language,
            checksum=None,
            extracted_text_ref=extracted_text_ref,
            download_status="pending",
            extraction_status="pending",
            extraction_error_code=None,
        )
        self.session.add(doc)
        return doc

    def get(self, document_id: int) -> Document | None:
        return self.session.get(Document, document_id)

    def list_by_tender(self, tender_id: int) -> list[Document]:
        return list(
            self.session.scalars(
                select(Document).where(Document.tender_id == tender_id).order_by(Document.id)
            ).all()
        )

    def _require(self, document_id: int) -> Document:
        doc = self.get(document_id)
        if doc is None:
            raise PersistenceError(
                f"document {document_id} not found",
                error_code=PERSISTENCE_NOT_FOUND,
                context={"document_id": document_id},
            )
        return doc

    def set_download_status(self, document_id: int, status: str) -> Document:
        """Validate + persist the model-defined download status (prompts 08 owns policy)."""
        doc = self._require(document_id)
        assert_valid_document_status(doc, "download_status", status)
        doc.download_status = status
        return doc

    def set_extraction_status(
        self, document_id: int, status: str, *, error_code: str | None = None
    ) -> Document:
        """Validate + persist the model-defined extraction status (prompt 09 owns policy)."""
        doc = self._require(document_id)
        assert_valid_document_status(doc, "extraction_status", status)
        doc.extraction_status = status
        doc.extraction_error_code = error_code
        return doc

    def set_language(self, document_id: int, language: str | None) -> Document:
        """Persist the best-guess document language (docs/03 §3.2; prompt 09 §10).

        ``Document.language`` is documented as a best guess and docs/03 enumerates no allowed
        values for it, so this deliberately does not invent a closed vocabulary to validate
        against (PROJECT_RULES #4, #6).
        """
        doc = self._require(document_id)
        doc.language = language
        return doc

    def set_checksum(self, document_id: int, checksum: str) -> Document:
        doc = self._require(document_id)
        doc.checksum = checksum
        return doc

    def set_storage_path(self, document_id: int, storage_path: str) -> Document:
        doc = self._require(document_id)
        doc.storage_path = storage_path
        return doc

    def set_extracted_text_ref(self, document_id: int, extracted_text_ref: str) -> Document:
        doc = self._require(document_id)
        doc.extracted_text_ref = extracted_text_ref
        return doc
