""":mod:`tender_intelligence.crypto.secrets` — AES-256-GCM secret envelope."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tender_intelligence.core.errors import is_valid_error_code  # noqa: F401  (re-exported for callers)

ENVELOPE_PREFIX = "aesgcm:v1:"
SECRET_ENV_VAR = "TI_MASTER_KEY"

__all__ = ["SecretError", "encrypt_secret", "decrypt_secret", "generate_master_key", "get_master_key"]


class SecretError(Exception):
    """Raised when a secret cannot be encrypted/decrypted."""


def generate_master_key() -> bytes:
    """Return a new 32-byte random master key."""
    return os.urandom(32)


def get_master_key() -> bytes:
    """Read the master key from ``TI_MASTER_KEY`` (env).

    Must live outside the database and never be logged (PROJECT_RULES #12).
    """
    encoded = os.environ.get(SECRET_ENV_VAR)
    if not encoded:
        raise SecretError(f"{SECRET_ENV_VAR} is not set; cannot encrypt/decrypt secrets")
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:  # binascii.Error / ValueError
        raise SecretError(f"{SECRET_ENV_VAR} is not valid base64") from exc


def encrypt_secret(plaintext: str, key: bytes | None = None) -> str:
    """Encrypt a plaintext secret into the ``aesgcm:v1:{nonce}:{ct}`` envelope.

    Returns the envelope string; the plaintext never appears in the result and
    must not be logged (PROJECT_RULES #12).
    """
    if not isinstance(plaintext, str):
        raise SecretError("plaintext must be a str")
    if plaintext == "":
        raise SecretError("refusing to encrypt empty secret")
    key = key if key is not None else get_master_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    ct_b64 = base64.b64encode(ct).decode("ascii")
    return f"{ENVELOPE_PREFIX}{nonce_b64}:{ct_b64}"


def decrypt_secret(envelope: str, key: bytes | None = None) -> str:
    """Decrypt an ``aesgcm:v1`` envelope back to plaintext.

    Captured secrets may surface; callers must transfer them to the target
    destination without logging.
    """
    if not envelope.startswith(ENVELOPE_PREFIX):
        raise SecretError("not an aesgcm:v1 envelope")
    payload = envelope[len(ENVELOPE_PREFIX):]
    try:
        nonce_b64, ct_b64 = payload.split(":", 1)
        nonce = base64.b64decode(nonce_b64, validate=True)
        ct = base64.b64decode(ct_b64, validate=True)
    except Exception as exc:
        raise SecretError("malformed envelope payload") from exc
    if len(nonce) != 12:
        raise SecretError("invalid nonce length")
    key = key if key is not None else get_master_key()
    try:
        pt = AESGCM(key).decrypt(nonce, ct, None)
    except Exception as exc:
        raise SecretError("decryption failed (wrong key or corrupted ciphertext)") from exc
    return pt.decode("utf-8")