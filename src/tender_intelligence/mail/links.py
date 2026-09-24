""":mod:`tender_intelligence.mail.links` — secure expiring download links (docs/08 §8.9).

Links identify documents by their database identity (``tender_id`` / ``document_id``), never
by storage or filesystem paths. Each URL carries a short expiry timestamp and an HMAC-SHA256
signature so a link is usable only until it expires and only with a secret that never lives in
the database (prompt 11 §18; docs/10 security-spec).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

DEFAULT_EXPIRY_DAYS = 14

_ALG = "sha256"
_SIG_BYTES = hashlib.sha256().digest_size


def _epoch(instant: datetime) -> int:
    """Whole seconds since the epoch for *instant* (UTC)."""
    return int(instant.astimezone(UTC).timestamp())


@dataclass(frozen=True)
class SecureLink:
    """A signed, expiring URL plus its display metadata.

    Attributes:
        document_id: The ``Document`` row identity the URL resolves to (never a path).
        filename: Display name for the email body / notes footer.
        url: The signed URL (includes ``expires`` and ``sig`` query parameters).
        expires_at: Expiry instant (UTC), echoed into emails so readers know the window.
    """

    document_id: int
    filename: str
    url: str
    expires_at: datetime
    expiry_days: int


class SecureLinkSigner:
    """Generate and verify identity-based, expiring signed URLs.

    ``secret`` is the HMAC key (the master key or a dedicated ``TI_LINK_SIGNING_SECRET``,
    always outside the database). ``base_url`` is the archive access root; tests inject a
    harmless value because no network is involved — the signer only builds URLs.
    """

    def __init__(
        self,
        secret: bytes,
        base_url: str,
        default_expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> None:
        if not secret:
            raise ValueError("secure-link signing secret must not be empty")
        if default_expiry_days < 1:
            raise ValueError("default_expiry_days must be >= 1")
        self._secret = bytes(secret)
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("secure-link base_url must be an http(s) URL")
        self.base_url = base_url.rstrip("/")
        self.default_expiry_days = int(default_expiry_days)

    def sign(
        self,
        *,
        tender_id: int,
        document_id: int,
        filename: str,
        expiry_days: int | None = None,
        now: datetime | None = None,
    ) -> SecureLink:
        """Sign a download link for *document_id* under *tender_id*.

        The signature covers ``archive/{tender_id}/{document_id}`` together with the expiry
        epoch, so neither the identity nor the freshness can be forged without the secret.
        *now* is injectable for deterministic tests.
        """
        if tender_id < 1 or document_id < 1:
            raise ValueError("tender_id and document_id must be positive")
        instant = (now or datetime.now(UTC)).astimezone(UTC)
        days = expiry_days if expiry_days is not None else self.default_expiry_days
        if days < 1:
            raise ValueError("expiry_days must be >= 1")
        expires = _epoch(instant) + days * 24 * 60 * 60
        signature = self._digest(tender_id, document_id, expires)
        url = f"{self.base_url}/archive/{tender_id}/{document_id}?expires={expires}&sig={signature}"
        expires_at = datetime.fromtimestamp(expires, tz=UTC)
        return SecureLink(
            document_id=document_id,
            filename=filename,
            url=url,
            expires_at=expires_at,
            expiry_days=days,
        )

    def is_valid(
        self,
        *,
        tender_id: int,
        document_id: int,
        expires: int,
        signature: str,
        now: datetime | None = None,
    ) -> bool:
        """Return True when *signature* matches the identity/expiry payload.

        Constant-time comparison. Expiry is checked as part of validation so an old but
        genuine signature cannot render a dead link live again.
        """
        if not signature or self.is_expired(expires, now=now):
            return False
        try:
            expected = self._digest(tender_id, document_id, expires)
        except (TypeError, ValueError):
            return False
        return hmac.compare_digest(expected, signature)

    def verify(
        self,
        *,
        tender_id: int,
        document_id: int,
        expires: int,
        signature: str,
        now: datetime | None = None,
    ) -> bool:
        """Verify both signature and expiry for an archive request."""

        return self.is_valid(
            tender_id=tender_id,
            document_id=document_id,
            expires=expires,
            signature=signature,
            now=now,
        )

    @staticmethod
    def is_expired(expires: int | None, now: datetime | None = None) -> bool:
        """Return True when the *expires* epoch has passed at *now* (UTC)."""
        if expires is None:
            return True
        return expires <= _epoch(now or datetime.now(UTC))

    def _digest(self, tender_id: int, document_id: int, expires: int) -> str:
        payload = f"archive/{tender_id}/{document_id}:{expires}".encode()
        raw = hmac.new(self._secret, payload, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")[:_SIG_BYTES]
        return encoded.decode()


def signer_from_secret(
    secret: str | bytes,
    base_url: str,
    default_expiry_days: int = DEFAULT_EXPIRY_DAYS,
) -> SecureLinkSigner:
    """Build a signer from a str/bytes secret (env values arrive as ``str``)."""
    secret_bytes = secret.encode() if isinstance(secret, str) else secret
    return SecureLinkSigner(secret_bytes, base_url, default_expiry_days=default_expiry_days)
