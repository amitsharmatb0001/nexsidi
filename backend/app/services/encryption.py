"""At-rest field encryption using Fernet (AES-128-CBC + HMAC-SHA256).

Used for sensitive DB fields that must be stored encrypted:
- User.totp_secret_enc — TOTP shared secrets (32 bytes base32 encoded)

Key derivation:
  TOTP_ENCRYPTION_KEY env var → 32-byte Fernet key (base64url-encoded).
  If not set, derives deterministically from jwt_secret_key via HKDF-SHA256
  so existing deployments work without new secrets.

NEVER log the raw secret or the key.
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache

import structlog

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _get_fernet():
    """Return a cached Fernet instance, building the key at first call."""
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "cryptography package is required for field encryption. "
            "Add 'cryptography>=42' to pyproject.toml dependencies."
        ) from exc

    # Prefer explicit TOTP_ENCRYPTION_KEY env var (32 bytes base64url-encoded).
    raw_key = os.environ.get("TOTP_ENCRYPTION_KEY", "").strip()
    if raw_key:
        try:
            # Validate it is a valid Fernet key (32 bytes base64url-padded = 44 chars)
            decoded = base64.urlsafe_b64decode(raw_key + "==")  # tolerant padding
            if len(decoded) != 32:
                raise ValueError(f"Fernet key must be 32 bytes, got {len(decoded)}")
            key = raw_key.encode() if isinstance(raw_key, str) else raw_key
        except Exception as exc:
            raise RuntimeError(
                f"Invalid TOTP_ENCRYPTION_KEY: {exc}. "
                "Generate a valid key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc
    else:
        # Derive from jwt_secret_key via HKDF-SHA256 so existing deployments
        # don't need a new secret. Not ideal (using auth key for encryption) but
        # safe for the TOTP use case — TOTP secrets are low-sensitivity compared
        # to auth tokens.
        from app.config import get_settings
        settings = get_settings()
        ikm = settings.jwt_secret_key.encode("utf-8")
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"nexsidi-totp-enc-v1",
            info=b"totp-field-encryption",
        )
        raw_32 = hkdf.derive(ikm)
        key = base64.urlsafe_b64encode(raw_32)
        logger.warning(
            "totp_encryption_key_derived",
            source="jwt_secret_key",
            hint="Set TOTP_ENCRYPTION_KEY env var for independent key rotation",
        )

    return Fernet(key)


def encrypt_totp_secret(plaintext: str) -> str:
    """Encrypt a TOTP secret for storage in the database.

    Args:
        plaintext: The raw TOTP secret (e.g., pyotp.random_base32() output).

    Returns:
        A Fernet token (base64url-encoded string) safe to store in TEXT column.
    """
    fernet = _get_fernet()
    ciphertext_bytes = fernet.encrypt(plaintext.encode("utf-8"))
    return ciphertext_bytes.decode("ascii")


def decrypt_totp_secret(ciphertext: str) -> str:
    """Decrypt a TOTP secret retrieved from the database.

    Args:
        ciphertext: The Fernet token string stored in DB.

    Returns:
        The original plaintext TOTP secret.

    Raises:
        cryptography.fernet.InvalidToken: If ciphertext is tampered or key changed.
    """
    fernet = _get_fernet()
    plaintext_bytes = fernet.decrypt(ciphertext.encode("ascii"))
    return plaintext_bytes.decode("utf-8")
