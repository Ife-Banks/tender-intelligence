"""Unit tests for secure expiring download links (docs/08 §8.9, prompt 11 §18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tender_intelligence.mail.links import (
    DEFAULT_EXPIRY_DAYS,
    SecureLinkSigner,
    signer_from_secret,
)

SECRET = b"k" * 32
BASE = "https://archive.example.invalid"


def _signer(days: int = DEFAULT_EXPIRY_DAYS) -> SecureLinkSigner:
    return SecureLinkSigner(SECRET, BASE, default_expiry_days=days)


def _now() -> datetime:
    return datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class TestSign:
    def test_identity_based_url_never_storage_path(self) -> None:
        link = _signer().sign(tender_id=7, document_id=42, filename="a.pdf", now=_now())
        assert link.url.startswith(f"{BASE}/archive/7/42")
        assert "expires=" in link.url
        assert "sig=" in link.url
        assert "/objects/" not in link.url
        assert "/data/" not in link.url
        assert link.filename == "a.pdf"
        assert link.expiry_days == DEFAULT_EXPIRY_DAYS
        assert link.expires_at == _now() + timedelta(days=DEFAULT_EXPIRY_DAYS)

    def test_sign_with_default_expiry(self) -> None:
        link = _signer(days=7).sign(tender_id=1, document_id=2, filename="b.pdf", now=_now())
        assert link.expiry_days == 7
        assert link.expires_at == _now() + timedelta(days=7)

    def test_expiry_days_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            _signer().sign(tender_id=1, document_id=2, filename="a.pdf", expiry_days=0)

    def test_empty_secret_rejected(self) -> None:
        with pytest.raises(ValueError):
            SecureLinkSigner(b"", BASE)

    def test_signer_from_str_secret(self) -> None:
        signer = signer_from_secret("some-secret-string", BASE)
        link = signer.sign(tender_id=1, document_id=2, filename="a.pdf", now=_now())
        assert link.url.startswith(f"{BASE}/archive/1/2")


class TestVerify:
    def test_roundtrip_verifies(self) -> None:
        signer = _signer()
        link = signer.sign(tender_id=7, document_id=42, filename="a.pdf", now=_now())
        expires = int(link.expires_at.timestamp())
        assert signer.is_valid(
            tender_id=7, document_id=42, expires=expires, signature=link.url.split("sig=")[1]
        )

    def test_wrong_document_rejected(self) -> None:
        signer = _signer()
        link = signer.sign(tender_id=7, document_id=42, filename="a.pdf", now=_now())
        expires = int(link.expires_at.timestamp())
        sig = link.url.split("sig=")[1]
        assert not signer.is_valid(tender_id=7, document_id=43, expires=expires, signature=sig)

    def test_tampered_signature_rejected(self) -> None:
        signer = _signer()
        link = signer.sign(tender_id=7, document_id=42, filename="a.pdf", now=_now())
        expires = int(link.expires_at.timestamp())
        sig = link.url.split("sig=")[1]
        swapped = ("0" if sig[0] != "0" else "1") + sig[1:]
        assert not signer.is_valid(tender_id=7, document_id=42, expires=expires, signature=swapped)

    def test_wrong_expiry_rejected(self) -> None:
        signer = _signer()
        link = signer.sign(tender_id=7, document_id=42, filename="a.pdf", now=_now())
        expires = int(link.expires_at.timestamp())
        sig = link.url.split("sig=")[1]
        assert not signer.is_valid(tender_id=7, document_id=42, expires=expires + 1, signature=sig)

    def test_empty_signature_rejected(self) -> None:
        assert not _signer().is_valid(tender_id=1, document_id=2, expires=123, signature="")


class TestExpiry:
    def test_expired_link_detected(self) -> None:
        now = _now()
        link = _signer(days=14).sign(tender_id=1, document_id=2, filename="a.pdf", now=now)
        assert not SecureLinkSigner.is_expired(int(link.expires_at.timestamp()), now=now)
        assert SecureLinkSigner.is_expired(
            int(link.expires_at.timestamp()), now=now + timedelta(days=15)
        )

    def test_combined_verification_rejects_expiry(self) -> None:
        signer = _signer()
        link = signer.sign(tender_id=1, document_id=2, filename="a.pdf", now=_now())
        expires = int(link.expires_at.timestamp())
        signature = link.url.split("sig=")[1]
        assert signer.verify(
            tender_id=1,
            document_id=2,
            expires=expires,
            signature=signature,
            now=_now(),
        )
        assert not signer.verify(
            tender_id=1,
            document_id=2,
            expires=expires,
            signature=signature,
            now=_now() + timedelta(days=15),
        )

    def test_missing_expiry_is_treated_expired(self) -> None:
        assert SecureLinkSigner.is_expired(None, now=_now())
