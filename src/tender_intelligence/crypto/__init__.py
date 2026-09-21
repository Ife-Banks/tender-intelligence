"""Encryption utilities for secrets at rest.

Secrets are encrypted with AES-256-GCM using a master key that lives OUTSIDE the database
(env var ``TI_MASTER_KEY``, base64 of 32 random bytes). Stored values are never plaintext;
the envelope format is ``aesgcm:v1:{nonce_b64}:{ciphertext_b64}`` (``docs/10`` security-spec,
PROJECT_RULES #12).
"""