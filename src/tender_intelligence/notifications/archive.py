"""Signed archive access for notification links.

The route itself belongs to the later admin/deployment surface, but the access-control seam is
implemented here so a future HTTP handler cannot accidentally serve a storage path directly.
A caller supplies the identity/expiry/signature from a request and receives bytes only after
the HMAC and persisted document ownership checks pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.db.models.documents import Document
from tender_intelligence.mail.links import SecureLinkSigner
from tender_intelligence.storage.interface import ObjectStorage


class SecureLinkDenied(PermissionError):
    """A signed archive request is invalid, expired, or points at another tender."""


@dataclass(frozen=True)
class SecureDocument:
    document_id: int
    filename: str
    content: bytes
    content_type: str | None


class SecureDocumentAccess:
    """Verify a link and resolve the corresponding archived document by database ID."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage: ObjectStorage,
        signer: SecureLinkSigner,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._signer = signer

    def open(
        self,
        *,
        tender_id: int,
        document_id: int,
        expires: int,
        signature: str,
        now: datetime | None = None,
    ) -> SecureDocument:
        moment = now or datetime.now(UTC)
        if not self._signer.verify(
            tender_id=tender_id,
            document_id=document_id,
            expires=expires,
            signature=signature,
            now=moment,
        ):
            raise SecureLinkDenied("signed document link is invalid or expired")
        with self._session_factory() as session:
            document = session.get(Document, document_id)
            if document is None or document.tender_id != tender_id or not document.storage_path:
                raise SecureLinkDenied("document identity is not available")
            try:
                if not self._storage.exists(document.storage_path):
                    raise SecureLinkDenied("archived document is unavailable")
                content = self._storage.get(document.storage_path)
            except SecureLinkDenied:
                raise
            except (OSError, KeyError, ValueError) as exc:
                raise SecureLinkDenied("archived document is unavailable") from exc
        return SecureDocument(
            document_id=document_id,
            filename=document.filename,
            content=content,
            content_type=document.mime_type,
        )


__all__ = ["SecureDocument", "SecureDocumentAccess", "SecureLinkDenied"]
