"""RFC 6238 Time-based One-Time Password (TOTP) Service for Authenticator Apps.

Zero-dependency standard library implementation (RFC 6238 / RFC 4226) for
multi-factor authentication with Google Authenticator, Microsoft Authenticator,
Duo, 1Password, and other compatible authenticators.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import struct
import time
from urllib.parse import quote
from typing import Optional, List, Tuple, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from cryptography.fernet import Fernet, InvalidToken

from src.db.models import User, TOTPCredential
from src.config.settings import get_settings

logger = logging.getLogger(__name__)


def _get_encryption_fernet() -> Fernet:
    """Return a Fernet cipher instance using BYOK_ENCRYPTION_KEY or derived key."""
    env_key = os.getenv("BYOK_ENCRYPTION_KEY")
    if env_key:
        try:
            return Fernet(env_key.encode("utf-8"))
        except Exception:
            pass
    # Deterministic fallback key derived from application secret
    settings = get_settings()
    seed = (settings.jwt_secret or "aelira-totp-fallback-key-seed").encode("utf-8")
    derived_seed = hashlib.sha256(seed).digest()
    fernet_key = base64.urlsafe_b64encode(derived_seed)
    return Fernet(fernet_key)


def encrypt_totp_secret(secret: str) -> str:
    """Encrypt base32 TOTP secret for storage at rest."""
    f = _get_encryption_fernet()
    return f.encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_totp_secret(encrypted_secret: str) -> str:
    """Decrypt stored base32 TOTP secret."""
    f = _get_encryption_fernet()
    try:
        return f.decrypt(encrypted_secret.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise ValueError("Decryption failed: invalid encryption token or key mismatch")


class TOTPEngine:
    """Pure Python RFC 6238 / RFC 4226 implementation."""

    INTERVAL = 30
    DIGITS = 6

    @classmethod
    def generate_secret(cls, byte_length: int = 20) -> str:
        """Generate cryptographically random 160-bit base32 secret."""
        random_bytes = secrets.token_bytes(byte_length)
        return base64.b32encode(random_bytes).decode("ascii").rstrip("=")

    @classmethod
    def get_provisioning_uri(
        cls, secret: str, email: str, issuer: str = "Aelira"
    ) -> str:
        """Construct otpauth:// provisioning URI."""
        clean_email = email.strip()
        clean_issuer = issuer.strip()
        label = f"{quote(clean_issuer)}:{quote(clean_email)}"
        params = (
            f"secret={secret}"
            f"&issuer={quote(clean_issuer)}"
            f"&algorithm=SHA1"
            f"&digits={cls.DIGITS}"
            f"&period={cls.INTERVAL}"
        )
        return f"otpauth://totp/{label}?{params}"

    @classmethod
    def generate_code(
        cls, secret: str, for_time: Optional[float] = None, interval: int = INTERVAL
    ) -> str:
        """Generate a 6-digit TOTP code for a given timestamp."""
        if for_time is None:
            for_time = time.time()
        counter = int(for_time // interval)

        # Pad base32 string to multiple of 8 if necessary
        padded_secret = secret.upper()
        remainder = len(padded_secret) % 8
        if remainder:
            padded_secret += "=" * (8 - remainder)

        key_bytes = base64.b32decode(padded_secret, casefold=True)
        counter_bytes = struct.pack(">Q", counter)

        mac = hmac.new(key_bytes, counter_bytes, hashlib.sha1).digest()
        offset = mac[-1] & 0x0F
        binary = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
        code_int = binary % (10**cls.DIGITS)
        return f"{code_int:0{cls.DIGITS}d}"

    @classmethod
    def verify_code(
        cls,
        secret: str,
        code: str,
        window: int = 1,
        for_time: Optional[float] = None,
        interval: int = INTERVAL,
    ) -> bool:
        """Verify code against secret with clock drift window in constant time."""
        clean_code = code.strip().replace(" ", "").replace("-", "")
        if len(clean_code) != cls.DIGITS or not clean_code.isdigit():
            return False

        if for_time is None:
            for_time = time.time()

        # Check current step and adjacent steps in window
        matched = False
        for drift in range(-window, window + 1):
            t = for_time + drift * interval
            expected = cls.generate_code(secret, for_time=t, interval=interval)
            if hmac.compare_digest(expected, clean_code):
                matched = True

        return matched

    @classmethod
    def generate_backup_codes(cls, count: int = 8) -> Tuple[List[str], List[str]]:
        """Generate single-use backup recovery codes and their SHA-256 hashes."""
        plain_codes: List[str] = []
        hashed_codes: List[str] = []
        for _ in range(count):
            part1 = secrets.token_hex(2)
            part2 = secrets.token_hex(2)
            code = f"{part1}-{part2}".lower()
            plain_codes.append(code)
            hashed_codes.append(cls.hash_backup_code(code))
        return plain_codes, hashed_codes

    @classmethod
    def hash_backup_code(cls, code: str) -> str:
        """Hash a backup recovery code using SHA-256."""
        clean = code.strip().replace(" ", "").replace("-", "").lower()
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    @classmethod
    def verify_and_consume_backup_code(
        cls, hashed_codes: List[str], code: str
    ) -> Tuple[bool, List[str]]:
        """Verify backup code against stored hashes and remove if matched."""
        if not hashed_codes:
            return False, hashed_codes

        target_hash = cls.hash_backup_code(code)
        match_index = -1
        for idx, h in enumerate(hashed_codes):
            if hmac.compare_digest(h, target_hash):
                match_index = idx
                break

        if match_index >= 0:
            remaining = [h for i, h in enumerate(hashed_codes) if i != match_index]
            return True, remaining
        return False, hashed_codes


class TOTPService:
    """Service layer for user TOTP enrollment, validation, and lifecycle."""

    @classmethod
    def begin_enrollment(
        cls, user: User, db: Session, issuer: str = "Aelira"
    ) -> Dict[str, str]:
        """Begin TOTP enrollment by generating an unconfirmed secret."""
        secret = TOTPEngine.generate_secret()
        encrypted = encrypt_totp_secret(secret)

        existing = (
            db.query(TOTPCredential).filter(TOTPCredential.user_id == user.id).first()
        )
        if existing:
            existing.encrypted_secret = encrypted
            existing.is_confirmed = False
            existing.backup_codes = []
            db.commit()
        else:
            credential = TOTPCredential(
                user_id=user.id,
                encrypted_secret=encrypted,
                is_confirmed=False,
                backup_codes=[],
                friendly_name="Authenticator App",
            )
            db.add(credential)
            db.commit()

        provisioning_uri = TOTPEngine.get_provisioning_uri(
            secret=secret,
            email=user.email,
            issuer=issuer,
        )

        return {
            "secret": secret,
            "otpauth_uri": provisioning_uri,
        }

    @classmethod
    def confirm_enrollment(cls, user: User, code: str, db: Session) -> Dict[str, Any]:
        """Confirm enrollment by verifying the user's first 6-digit TOTP code."""
        credential = (
            db.query(TOTPCredential).filter(TOTPCredential.user_id == user.id).first()
        )
        if not credential:
            raise ValueError("No TOTP enrollment in progress. Please start enrollment.")

        secret = decrypt_totp_secret(credential.encrypted_secret)
        if not TOTPEngine.verify_code(secret, code):
            raise ValueError(
                "Invalid verification code. Check your authenticator app time sync and try again."
            )

        plain_backup_codes, hashed_backup_codes = TOTPEngine.generate_backup_codes(
            count=8
        )

        credential.is_confirmed = True
        credential.backup_codes = hashed_backup_codes
        credential.last_used_at = func.now()
        db.commit()

        return {
            "status": "confirmed",
            "backup_codes": plain_backup_codes,
            "message": "Authenticator app enrolled successfully. Store your backup recovery codes securely.",
        }

    @classmethod
    def verify_login(
        cls, user_id: str, code: str, db: Session
    ) -> Tuple[bool, str, Optional[User]]:
        """Verify TOTP code or backup code during authentication."""
        user = (
            db.query(User)
            .filter(User.id == user_id, User.is_active == True)  # noqa: E712
            .first()
        )
        if not user:
            return False, "User not found or inactive", None

        credential = (
            db.query(TOTPCredential)
            .filter(
                TOTPCredential.user_id == user.id,
                TOTPCredential.is_confirmed == True,  # noqa: E712
            )
            .first()
        )
        if not credential:
            return False, "Authenticator app MFA is not configured for this user", None

        secret = decrypt_totp_secret(credential.encrypted_secret)

        # 1. Try 6-digit TOTP code
        if TOTPEngine.verify_code(secret, code):
            credential.last_used_at = func.now()
            db.commit()
            return True, "totp", user

        # 2. Try single-use backup recovery code
        stored_hashes = credential.backup_codes or []
        matched, remaining = TOTPEngine.verify_and_consume_backup_code(
            stored_hashes, code
        )
        if matched:
            credential.backup_codes = remaining
            credential.last_used_at = func.now()
            db.commit()
            return True, "backup_code", user

        return False, "Invalid authenticator code or backup recovery code", None

    @classmethod
    def revoke(cls, user: User, code: str, db: Session) -> bool:
        """Revoke TOTP MFA after verifying code."""
        credential = (
            db.query(TOTPCredential).filter(TOTPCredential.user_id == user.id).first()
        )
        if not credential:
            return True

        # If already confirmed, require valid code before revoking
        if credential.is_confirmed:
            secret = decrypt_totp_secret(credential.encrypted_secret)
            if not TOTPEngine.verify_code(secret, code):
                # Also check backup code
                matched, _ = TOTPEngine.verify_and_consume_backup_code(
                    credential.backup_codes or [], code
                )
                if not matched:
                    raise ValueError(
                        "Invalid verification code. Cannot revoke authenticator."
                    )

        db.delete(credential)
        db.commit()
        return True

    @classmethod
    def get_status(cls, user: User, db: Session) -> Dict[str, Any]:
        """Check user's TOTP MFA enrollment status."""
        credential = (
            db.query(TOTPCredential).filter(TOTPCredential.user_id == user.id).first()
        )
        if not credential or not credential.is_confirmed:
            return {
                "enrolled": False,
                "friendly_name": None,
                "created_at": None,
                "last_used_at": None,
                "remaining_backup_codes": 0,
            }

        return {
            "enrolled": True,
            "friendly_name": credential.friendly_name,
            "created_at": (
                credential.created_at.isoformat() if credential.created_at else None
            ),
            "last_used_at": (
                credential.last_used_at.isoformat() if credential.last_used_at else None
            ),
            "remaining_backup_codes": len(credential.backup_codes or []),
        }
