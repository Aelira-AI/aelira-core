"""
Encryption utilities for secure storage of sensitive data.

Provides Fernet symmetric encryption for API keys and other secrets.
Used primarily for BYOK (Bring Your Own Key) department API key storage.

SECURITY NOTES:
- BYOK_ENCRYPTION_KEY must be set in production environment
- Key should be generated using: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
- Store the key securely (environment variable, secrets manager)
- Never commit encryption keys to version control
"""

import os
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Environment variable for the encryption key
ENCRYPTION_KEY_ENV = "BYOK_ENCRYPTION_KEY"


class EncryptionError(Exception):
    """Raised when encryption/decryption operations fail."""
    pass


def get_encryption_key() -> bytes:
    """
    Get the encryption key from environment.

    Returns:
        The encryption key as bytes

    Raises:
        EncryptionError: If BYOK_ENCRYPTION_KEY is not set or invalid
    """
    key = os.getenv(ENCRYPTION_KEY_ENV)
    if not key:
        raise EncryptionError(
            f"{ENCRYPTION_KEY_ENV} environment variable not set. "
            "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    try:
        # Validate key format
        key_bytes = key.encode()
        Fernet(key_bytes)  # This validates the key format
        return key_bytes
    except Exception as e:
        raise EncryptionError(
            f"Invalid encryption key format: {e}. "
            "Key must be a valid Fernet key (44-character base64)."
        )


def encrypt_api_key(api_key: str) -> str:
    """
    Encrypt an API key for secure storage.

    Args:
        api_key: The plaintext API key to encrypt

    Returns:
        The encrypted API key as a base64 string

    Raises:
        EncryptionError: If encryption fails
    """
    if not api_key:
        raise EncryptionError("Cannot encrypt empty API key")

    try:
        key = get_encryption_key()
        f = Fernet(key)
        encrypted = f.encrypt(api_key.encode())
        return encrypted.decode()
    except EncryptionError:
        raise
    except Exception as e:
        logger.error(f"Failed to encrypt API key: {e}")
        raise EncryptionError(f"Encryption failed: {e}")


def decrypt_api_key(encrypted_key: str) -> str:
    """
    Decrypt a stored API key.

    Args:
        encrypted_key: The encrypted API key (base64 string)

    Returns:
        The decrypted plaintext API key

    Raises:
        EncryptionError: If decryption fails (invalid key, corrupted data, etc.)
    """
    if not encrypted_key:
        raise EncryptionError("Cannot decrypt empty encrypted key")

    try:
        key = get_encryption_key()
        f = Fernet(key)
        decrypted = f.decrypt(encrypted_key.encode())
        return decrypted.decode()
    except InvalidToken:
        logger.error("Failed to decrypt API key: Invalid token (wrong key or corrupted data)")
        raise EncryptionError(
            "Decryption failed: Invalid token. "
            "The encryption key may have changed or the data is corrupted."
        )
    except EncryptionError:
        raise
    except Exception as e:
        logger.error(f"Failed to decrypt API key: {e}")
        raise EncryptionError(f"Decryption failed: {e}")


def is_encryption_configured() -> bool:
    """
    Check if encryption is properly configured.

    Returns:
        True if BYOK_ENCRYPTION_KEY is set and valid, False otherwise
    """
    try:
        get_encryption_key()
        return True
    except EncryptionError:
        return False


def generate_encryption_key() -> str:
    """
    Generate a new Fernet encryption key.

    Returns:
        A new base64-encoded encryption key

    Note:
        This is a utility for generating keys during setup.
        Store the generated key securely and set as BYOK_ENCRYPTION_KEY.
    """
    return Fernet.generate_key().decode()


def rotate_encrypted_key(
    encrypted_key: str,
    old_key: Optional[str] = None,
    new_key: Optional[str] = None
) -> str:
    """
    Re-encrypt a key with a new encryption key (key rotation).

    Args:
        encrypted_key: The currently encrypted API key
        old_key: The old encryption key (defaults to current BYOK_ENCRYPTION_KEY)
        new_key: The new encryption key to use

    Returns:
        The API key encrypted with the new key

    Raises:
        EncryptionError: If rotation fails
    """
    if not new_key:
        raise EncryptionError("New encryption key must be provided for rotation")

    try:
        # Decrypt with old key
        if old_key:
            old_fernet = Fernet(old_key.encode())
            plaintext = old_fernet.decrypt(encrypted_key.encode()).decode()
        else:
            plaintext = decrypt_api_key(encrypted_key)

        # Re-encrypt with new key
        new_fernet = Fernet(new_key.encode())
        return new_fernet.encrypt(plaintext.encode()).decode()

    except Exception as e:
        logger.error(f"Key rotation failed: {e}")
        raise EncryptionError(f"Key rotation failed: {e}")
