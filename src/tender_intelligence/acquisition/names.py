""":mod:`tender_intelligence.acquisition.names` — safe storage naming (prompt 08 §3).

Remote filenames are untrusted and never used as filesystem paths. Storage keys are
checksum-scoped (``tenders/{tender_id}/attachments/{checksum}/{safe_name}``); this module
produces the final path component so it can never escape the storage root:

* keeps only the basename (a ``\\`` or ``/`` split first, so ``../`` and absolute paths die);
* drops path/traversal characters, Windows-invalid characters, nulls and control chars;
* neutralises Windows device names (``CON``, ``PRN``, …) and empty/dot names;

The *original* remote filename is preserved unchanged in ``Document.filename`` metadata;
only the local storage key component is replaced by its sanitised form.
"""

from __future__ import annotations

import posixpath
import re
from typing import Final

_UNSAFE_CHARS: Final[str] = '<>:"/\\|?*\x00'
_WINDOWS_DEVICE_NAMES: Final[frozenset[str]] = frozenset({"con", "prn", "aux", "nul"})
_MAX_KEY_NAME_LENGTH: Final[int] = 200
_CONTROL_CHARS: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")
_TRAILING_DOTS: Final[re.Pattern[str]] = re.compile(r"[. ]+$")


def sanitize_storage_name(filename: str) -> str:
    """Return a path-safe, single name component for *filename* (never empty).

    Conservative by default: when nothing safe remains the result is ``"file"`` so a
    storage key is always well-formed and unique within its checksum scope.
    """
    base = posixpath.basename(filename.replace("\\", "/"))
    base = _CONTROL_CHARS.sub("", base)
    cleaned = "".join(ch for ch in base if ch not in _UNSAFE_CHARS)
    cleaned = _TRAILING_DOTS.sub("", cleaned).strip()
    if cleaned.lower() in _WINDOWS_DEVICE_NAMES:
        cleaned = f"_{cleaned}"
    if cleaned in {"", ".", ".."}:
        cleaned = "file"
    if len(cleaned) > _MAX_KEY_NAME_LENGTH:
        cleaned = cleaned[:_MAX_KEY_NAME_LENGTH]
    return cleaned or "file"


def storage_key(tender_id: int, checksum: str, filename: str) -> str:
    """Checksum-scoped object key for an acquired document (collision-free by design)."""
    return f"tenders/{tender_id}/attachments/{checksum}/{sanitize_storage_name(filename)}"
