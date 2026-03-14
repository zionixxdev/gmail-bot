"""
services/encryption.py — Fernet symmetric encryption for sensitive data.

All Gmail refresh tokens are encrypted before persisting to the database
and decrypted only when an API call requires them.

Key generation (one-time, run once):
    from cryptography.fernet import Fernet
    print(Fernet.generate_key().decode())

Store the output as ENCRYPTION_KEY in your .env file.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from config import ENCRYPTION_KEY

logger = logging.getLogger(__name__)


# ─── Cipher singleton ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """Return a cached Fernet instance built from the env key."""
    if not ENCRYPTION_KEY:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set in .env. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(ENCRYPTION_KEY.encode())
    except Exception as exc:
        raise RuntimeError(f"Invalid ENCRYPTION_KEY: {exc}") from exc


# ─── Public helpers ──────────────────────────────────────────────────────────

def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string and return a base64-encoded ciphertext string.

    Args:
        plaintext: The string to encrypt (e.g., a refresh token).

    Returns:
        URL-safe base64-encoded encrypted bytes as a UTF-8 string.

    Raises:
        RuntimeError: If the encryption key is invalid.
    """
    cipher = _get_fernet()
    token_bytes = cipher.encrypt(plaintext.encode("utf-8"))
    return token_bytes.decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext string back to plaintext.

    Args:
        ciphertext: The encrypted string produced by :func:`encrypt`.

    Returns:
        The original plaintext string.

    Raises:
        ValueError: If the token is invalid or has been tampered with.
        RuntimeError: If the encryption key is invalid.
    """
    cipher = _get_fernet()
    try:
        plaintext_bytes = cipher.decrypt(ciphertext.encode("utf-8"))
        return plaintext_bytes.decode("utf-8")
    except InvalidToken as exc:
        logger.error("Failed to decrypt token — InvalidToken: %s", exc)
        raise ValueError("Decryption failed: token is invalid or corrupted.") from exc
