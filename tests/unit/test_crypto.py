"""Unit tests for the AES-GCM secret envelope (docs/10 security, PROJECT_RULES #12)."""

from __future__ import annotations

import base64

import pytest

from tender_intelligence.crypto.secrets import (
    ENVELOPE_PREFIX,
    SecretError,
    decrypt_secret,
    encrypt_secret,
    generate_master_key,
    get_master_key,
)


class TestSecretRoundTrip:
    def test_roundtrip(self):
        key = generate_master_key()
        envelope = encrypt_secret("sk-live-123", key=key)
        assert envelope.startswith(ENVELOPE_PREFIX)
        assert "sk-live-123" not in envelope
        assert decrypt_secret(envelope, key=key) == "sk-live-123"

    def test_envelope_detached(self):
        """Ciphertext must not contain plaintext even in the envelope string."""
        envelope = encrypt_secret("AKIAIVERYLONGACCESSKEY", key=generate_master_key())
        assert "AKIAIVERYLONGACCESSKEY" not in envelope
        payload = envelope[len(ENVELOPE_PREFIX):]
        # payload is exactly ``nonce_b64:ct_b64`` — one separator, no further colons
        assert payload.count(":") == 1

    def test_wrong_key_fails(self):
        envelope = encrypt_secret("secret", key=generate_master_key())
        with pytest.raises(SecretError):
            decrypt_secret(envelope, key=generate_master_key())

    def test_empty_plaintext_refused(self):
        with pytest.raises(SecretError):
            encrypt_secret("", key=generate_master_key())

    def test_non_envelope_rejected(self):
        with pytest.raises(SecretError):
            decrypt_secret("plain:stuff:here", key=generate_master_key())


class TestMasterKey:
    def test_generate_produces_32_bytes(self):
        key = generate_master_key()
        assert len(key) == 32

    def test_master_key_from_env(self, monkeypatch):
        key = generate_master_key()
        monkeypatch.setenv("TI_MASTER_KEY", base64.b64encode(key).decode("ascii"))
        assert get_master_key() == key

    def test_missing_master_key_raises(self, monkeypatch):
        monkeypatch.delenv("TI_MASTER_KEY", raising=False)
        with pytest.raises(SecretError):
            get_master_key()
