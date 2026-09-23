""":mod:`tender_intelligence.storage.interface` — object-storage contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredObject:
    """A stored object and its metadata.

    Attributes:
        key: Stable object key (e.g. ``tenders/{tender_id}/{checksum}.pdf``).
        size_bytes: Byte size of the stored content.
        content_type: MIME type recorded at write time.
    """

    key: str
    size_bytes: int
    content_type: str | None = None


class ObjectStorage(ABC):
    """Minimal persistent-storage contract used by the document archive.

    Implementations must be safe for concurrent workers and must not return secret-bearing
    content in metadata. Secure-link serving is layered on top in a later phase (docs/13 O10).
    """

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        """Store *data* under *key*, overwriting any existing object."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the bytes stored under *key*; raise ``KeyError`` when absent."""

    @abstractmethod
    def size_of(self, key: str) -> int:
        """Return the byte size of the object under *key*; raise ``KeyError`` when absent.

        Metadata-only: implementations must not return content, and callers must treat the
        value as untrusted input (acquisition reports it without trusting it).
        """

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True when an object exists under *key*."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete the object under *key*; missing keys are a no-op."""

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]:
        """Return all object keys under *prefix* in stable order."""
