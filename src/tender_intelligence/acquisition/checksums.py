""":mod:`tender_intelligence.acquisition.checksums` — content checksums (prompt 08 §6).

The authoritative specification requires a *checksum* per acquired document (docs/06 §6.1;
v1.1 §5.3) but does not name an algorithm; the ``Document.checksum`` column is
``String(64)`` (docs/03 §3.2; migration 0001), which is exactly the hex length of a
SHA-256 digest. SHA-256 is therefore the implemented algorithm; it is deterministic and
stable for identical bytes and is recorded after the bytes are actually acquired.
"""

from __future__ import annotations

import hashlib
from typing import Final

CHECKSUM_ALGORITHM: Final[str] = "sha256"


def checksum_of(data: bytes) -> str:
    """Return the hex SHA-256 of *data* (stable for identical bytes)."""
    return hashlib.sha256(data).hexdigest()
