"""At-rest field encryption using Fernet (AES-128-CBC + HMAC-SHA256).

Used for:
- User.totp_secret_enc — TOTP shared secrets (32 bytes base32 encoded)
- Agent Message Bus payloads — per-pipeline-run envelope encryption (Phase 1A)

Key derivation:
  TOTP_ENCRYPTION_KEY env var → 32-byte Fernet key (base64url-encoded).
  If not set, derives deterministically from jwt_secret_key via HKDF-SHA256
  so existing deployments work without new secrets.

Agent message encryption uses a separate HKDF derivation with
salt="nexsidi-agent-msg-v1" so it's cryptographically independent of TOTP.

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


# ── Phase 1A: Agent Message Encryption ──────────────────────────────
#
# Envelope encryption for inter-agent communication:
#   - Master key: derived from jwt_secret_key via HKDF (salt="nexsidi-agent-msg-v1")
#   - Per-run key: random Fernet key, encrypted with master key, stored in memory
#   - Message payloads: encrypted with per-run key before storage in Valkey
#
# This ensures that even if Valkey is compromised, message contents are
# unreadable without the master key. Per-run keys limit blast radius:
# compromising one run's key doesn't expose other runs.
# ─────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_message_master_fernet():
    """Return a Fernet instance for the agent message encryption master key.

    Derived independently from TOTP encryption — uses a different HKDF salt
    so the two keys are cryptographically independent even when both derive
    from jwt_secret_key.
    """
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    except ImportError as exc:
        raise RuntimeError(
            "cryptography package is required for message encryption. "
            "Add 'cryptography>=42' to pyproject.toml dependencies."
        ) from exc

    # Check for explicit MESSAGE_ENCRYPTION_KEY env var first
    raw_key = os.environ.get("MESSAGE_ENCRYPTION_KEY", "").strip()
    if raw_key:
        try:
            decoded = base64.urlsafe_b64decode(raw_key + "==")
            if len(decoded) != 32:
                raise ValueError(f"Fernet key must be 32 bytes, got {len(decoded)}")
            key = raw_key.encode() if isinstance(raw_key, str) else raw_key
        except Exception as exc:
            raise RuntimeError(
                f"Invalid MESSAGE_ENCRYPTION_KEY: {exc}. "
                "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from exc
    else:
        # Derive from jwt_secret_key with agent-message-specific salt
        from app.config import get_settings
        settings = get_settings()
        ikm = settings.jwt_secret_key.encode("utf-8")
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"nexsidi-agent-msg-v1",
            info=b"agent-message-encryption",
        )
        raw_32 = hkdf.derive(ikm)
        key = base64.urlsafe_b64encode(raw_32)
        logger.info(
            "message_encryption_key_derived",
            source="jwt_secret_key",
            hint="Set MESSAGE_ENCRYPTION_KEY env var for independent rotation",
        )

    return Fernet(key)


class MessageEncryptor:
    """Envelope encryption for inter-agent communication payloads.

    Usage::

        enc = MessageEncryptor()

        # At pipeline start: generate a per-run key
        run_key = enc.generate_run_key()
        encrypted_run_key = enc.encrypt_run_key(run_key)
        # Store encrypted_run_key in memory (or a secure store)

        # Encrypting a message
        ciphertext = enc.encrypt("Hello agent", run_key)

        # Decrypting
        plaintext = enc.decrypt(ciphertext, run_key)

        # Recovering run key from encrypted form
        run_key = enc.decrypt_run_key(encrypted_run_key)
    """

    @staticmethod
    def generate_run_key() -> bytes:
        """Generate a fresh Fernet key for a single pipeline run.

        Returns:
            32-byte URL-safe base64-encoded Fernet key.
        """
        from cryptography.fernet import Fernet
        return Fernet.generate_key()

    @staticmethod
    def encrypt_run_key(run_key: bytes) -> str:
        """Encrypt a per-run key with the master key for safe storage.

        Args:
            run_key: The per-run Fernet key bytes.

        Returns:
            Master-encrypted Fernet token (string) wrapping the run key.
        """
        master = _get_message_master_fernet()
        return master.encrypt(run_key).decode("ascii")

    @staticmethod
    def decrypt_run_key(encrypted_run_key: str) -> bytes:
        """Recover a per-run key from its master-encrypted form.

        Args:
            encrypted_run_key: The master-encrypted Fernet token string.

        Returns:
            The original per-run Fernet key bytes.

        Raises:
            cryptography.fernet.InvalidToken: If tampered or master key changed.
        """
        master = _get_message_master_fernet()
        return master.decrypt(encrypted_run_key.encode("ascii"))

    @staticmethod
    def encrypt(plaintext: str, run_key: bytes) -> str:
        """Encrypt a message payload with the per-run key.

        Args:
            plaintext: The message content to encrypt.
            run_key: The per-run Fernet key.

        Returns:
            Fernet ciphertext token (string).
        """
        from cryptography.fernet import Fernet
        f = Fernet(run_key)
        return f.encrypt(plaintext.encode("utf-8")).decode("ascii")

    @staticmethod
    def decrypt(ciphertext: str, run_key: bytes) -> str:
        """Decrypt a message payload with the per-run key.

        Args:
            ciphertext: The Fernet token string.
            run_key: The per-run Fernet key.

        Returns:
            The original plaintext message.

        Raises:
            cryptography.fernet.InvalidToken: If tampered or wrong key.
        """
        from cryptography.fernet import Fernet
        f = Fernet(run_key)
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")

    @staticmethod
    def is_encrypted(value: str) -> bool:
        """Heuristic check: is this value a Fernet ciphertext?

        Fernet tokens are base64url-encoded and always start with 'gAAAAA'.
        This is not a cryptographic check — just helps decide whether to decrypt.
        """
        return bool(value and value.startswith("gAAAAA") and len(value) > 100)


# Singleton for convenience
_message_encryptor: MessageEncryptor | None = None


def get_message_encryptor() -> MessageEncryptor:
    """Return the MessageEncryptor singleton."""
    global _message_encryptor
    if _message_encryptor is None:
        _message_encryptor = MessageEncryptor()
    return _message_encryptor
