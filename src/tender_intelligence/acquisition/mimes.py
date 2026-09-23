""":mod:`tender_intelligence.acquisition.mimes` — neutral MIME handling (prompt 08 §5).

Where the HTTP response states a Content-Type, that actual media type is authoritative and
is recorded for the top-level document. For archive-inner members there is no response
header, so the stdlib ``mimetypes`` table is used as a *best guess*; when no mapping exists
the value stays ``None`` — unknown is recorded as unknown, never invented (docs/06 §6.2,
prompt 08 §5). No WAHO-specific mapping lives here.
"""

from __future__ import annotations

import mimetypes

_POSIX_MIME_MAP: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}


def guess_mime(filename: str | None) -> str | None:
    """Best-guess media type for *filename* via the stdlib table (+ common office types)."""
    if not filename:
        return None
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    explicit = _POSIX_MIME_MAP.get(ext)
    if explicit:
        return explicit
    return mimetypes.guess_type(filename)[0]


def bare_media_type(content_type: str | None) -> str | None:
    """Strip parameters (``charset=…`` etc.) from a Content-Type header value."""
    if not content_type:
        return None
    media = content_type.split(";", 1)[0].strip().lower()
    return media or None
