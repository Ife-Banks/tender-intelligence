""":mod:`tender_intelligence.acquisition.zip` — safe ZIP acquisition and extraction.

Per the acquisition contract (docs/06 §6.1) a discovered ZIP is acquired and then *unzipped
and recursed* so its inner files become their own ``Document`` rows. ZIPs are untrusted
input; extraction happens in memory with configured resource limits and members are
returned to the caller, which stores them through the ``ObjectStorage`` seam under
sanitised, checksum-scoped keys — an archive member can never escape the storage boundary.

Protections implemented (prompt 08 §9):

* ``../`` traversal, absolute paths and Windows drive-letter/UNC paths are rejected per member;
* symlink (or equivalent) entries are rejected;
* per-member decompression-ratio bombs are rejected;
* a single member larger than the per-member limit is rejected;
* the total declared uncompressed size is bounded — exceeding it aborts the *whole* archive;
* the member count is bounded — exceeding it aborts the *whole* archive;
* member name length is bounded;
* malformed/corrupt archives fail safely (no partial garbage is surfaced as a document).

Recursion (prompt 08 §10): nested archives are followed to a configured depth; beyond that
they are retained as ordinary acquired documents (not unpacked) — never unbounded.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from tender_intelligence.acquisition.errors import (
    ARCHIVE_LIMIT,
    INVALID_ARCHIVE,
    DocumentAcquisitionError,
)

_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_SYMLINK_MODE = 0o120000

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class ZipLimits:
    """Configurable resource bounds for archive extraction (prompt 08 §9, §10).

    These are operator configuration (resource limits), not business rules. Defaults are
    deliberately conservative for a headless pipeline.
    """

    max_members: int = 200
    max_total_uncompressed_bytes: int = 512 * 1024 * 1024  # 512 MiB per archive
    max_compression_ratio: int = 200
    max_single_member_bytes: int = 256 * 1024 * 1024  # 256 MiB per member
    max_name_length: int = 255
    max_nested_depth: int = 2  # top-level archive = depth 1; recurse up to depth + max


@dataclass(frozen=True)
class ZipEntry:
    """One extractable member of an archive."""

    name: str
    data: bytes
    is_nested_zip: bool = False


@dataclass(frozen=True)
class ZipMemberFailure:
    """One member rejected for a safety/limit reason (acquisition continues)."""

    member_name: str
    reason: str


@dataclass(frozen=True)
class ZipArchiveReport:
    """Outcome of extracting one archive."""

    entries: list[ZipEntry]
    member_failures: list[ZipMemberFailure]
    archive_reason: str | None = None  # whole-archive failure (limit), if any

    @property
    def ok(self) -> bool:
        return self.archive_reason is None


def looks_like_zip(data: bytes) -> bool:
    """Best-effort ZIP sniffing via magic bytes; used for nested-archive detection."""
    return data[:4] in _ZIP_MAGICS


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return (unix_mode & 0o170000) == _SYMLINK_MODE


def _unsafe_member_name(name: str) -> str | None:
    """Return a reason string when *name* is an unsafe archive path, else None."""
    parts = name.replace("\\", "/").split("/")
    if name.startswith("/"):
        return "absolute path"
    if any(part in {"", ".", ".."} for part in parts):
        return "empty-or-dot path segment"
    head = parts[0]
    if len(head) == 2 and head[1] == ":" and head[0].lower() in _LETTERS:
        return "windows drive-letter path"
    if "//" in parts[0]:
        return "windows UNC path"
    return None


def safe_extract_archive(
    data: bytes,
    *,
    limits: ZipLimits,
    depth: int = 1,
) -> ZipArchiveReport:
    """Validate and extract *data* as a ZIP in memory under the configured limits.

    Raises :class:`DocumentAcquisitionError` (``invalid_archive``) for malformed/corrupt
    archives and (``archive_limit``) for whole-archive limit aborts. Individual unsafe or
    oversized members are rejected and reported in the result instead of failing the rest.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise DocumentAcquisitionError(
            "malformed ZIP archive",
            error_code="document_download_failed",
            category=INVALID_ARCHIVE,
            retryable=False,
            context={"reason": "bad_zip"},
        ) from exc
    except (zipfile.LargeZipFile, NotImplementedError) as exc:
        raise DocumentAcquisitionError(
            "ZIP archive uses unsupported/large features",
            error_code="document_download_failed",
            category=INVALID_ARCHIVE,
            retryable=False,
            context={"reason": type(exc).__name__},
        ) from exc

    infos = archive.infolist()
    if len(infos) > limits.max_members:
        raise DocumentAcquisitionError(
            "ZIP archive exceeds the member-count limit",
            error_code="document_download_failed",
            category=ARCHIVE_LIMIT,
            retryable=False,
            context={"members": len(infos), "limit": limits.max_members},
        )

    entries: list[ZipEntry] = []
    failures: list[ZipMemberFailure] = []
    total_seen = 0

    for info in infos:
        display = info.filename
        if display.endswith("/") or display.endswith("\\"):
            continue  # directory entry, not a document
        if len(display) > limits.max_name_length:
            failures.append(
                ZipMemberFailure(display, f"name exceeds {limits.max_name_length} chars")
            )
            continue
        unsafe = _unsafe_member_name(display)
        if unsafe is not None:
            failures.append(ZipMemberFailure(display, unsafe))
            continue
        if _is_symlink(info):
            failures.append(ZipMemberFailure(display, "symlink/link entry is not allowed"))
            continue

        total_seen += info.file_size
        if total_seen > limits.max_total_uncompressed_bytes:
            raise DocumentAcquisitionError(
                "ZIP archive exceeds the total uncompressed-size limit",
                error_code="document_download_failed",
                category=ARCHIVE_LIMIT,
                retryable=False,
                context={
                    "total_uncompressed_bytes": total_seen,
                    "limit_bytes": limits.max_total_uncompressed_bytes,
                },
            )
        if info.file_size > limits.max_single_member_bytes:
            failures.append(ZipMemberFailure(display, "member exceeds the single-file size limit"))
            continue
        if limits.max_compression_ratio > 0 and info.file_size > (
            limits.max_compression_ratio * max(info.compress_size, 1)
        ):
            failures.append(ZipMemberFailure(display, "compression ratio too high"))
            continue

        try:
            member_data = archive.read(info)
        except zipfile.error as exc:
            failures.append(ZipMemberFailure(display, f"corrupt member: {type(exc).__name__}"))
            continue

        is_nested_zip = looks_like_zip(member_data)
        entries.append(ZipEntry(display, member_data, is_nested_zip=is_nested_zip))

    archive.close()
    return ZipArchiveReport(entries=entries, member_failures=failures)


def recurse_depth_exceeded(limits: ZipLimits, depth: int) -> bool:
    """True when *depth* is beyond the configured nested-archive recursion limit."""
    return depth > limits.max_nested_depth
