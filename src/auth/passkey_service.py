"""
WebAuthn / Passkey Authentication Service

Provides functions for:
- Challenge generation and lifecycle management (Redis backed with memory fallback)
- WebAuthn registration (options generation & credential verification)
- WebAuthn authentication (assertion options & verification)
- Dynamic Relying Party (RP) ID and Origin resolution
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from fastapi import Request
from sqlalchemy.orm import Session

import webauthn
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    options_to_json,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticationCredential,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    RegistrationCredential,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..config.settings import get_settings
from ..db.models import User, WebAuthnCredential
from .redis_rate_limiter import get_redis_client

logger = logging.getLogger(__name__)


class WebAuthnVerificationError(ValueError):
    """Raised when WebAuthn registration or authentication fails verification."""


class WebAuthnChallengeStorage:
    """Storage for short-lived cryptographic WebAuthn challenges."""

    CHALLENGE_PREFIX = "webauthn:challenge:"
    CHALLENGE_TTL_SECONDS = 300  # 5 minutes

    _memory_lock = threading.Lock()
    _memory_challenges: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def store_challenge(cls, challenge_id: str, challenge_data: Dict[str, Any]) -> None:
        """Store challenge with TTL in Redis or in-memory fallback."""
        redis_client = get_redis_client()
        if redis_client is not None:
            try:
                key = f"{cls.CHALLENGE_PREFIX}{challenge_id}"
                redis_client.setex(
                    key,
                    cls.CHALLENGE_TTL_SECONDS,
                    json.dumps(challenge_data),
                )
                return
            except Exception as e:
                logger.warning(
                    "Redis store challenge failed, falling back to memory: %s", e
                )

        # In-memory fallback
        with cls._memory_lock:
            cls._cleanup_expired_memory()
            cls._memory_challenges[challenge_id] = {
                **challenge_data,
                "expires_at": datetime.now(timezone.utc)
                + timedelta(seconds=cls.CHALLENGE_TTL_SECONDS),
            }

    @classmethod
    def consume_challenge(cls, challenge_id: str) -> Optional[Dict[str, Any]]:
        """Atomically retrieve and delete challenge (one-time use)."""
        if not challenge_id:
            return None

        redis_client = get_redis_client()
        if redis_client is not None:
            try:
                key = f"{cls.CHALLENGE_PREFIX}{challenge_id}"
                data_json = redis_client.getdel(key)
                if data_json:
                    return json.loads(data_json)
                return None
            except Exception as e:
                logger.warning(
                    "Redis consume challenge failed, checking memory fallback: %s", e
                )

        with cls._memory_lock:
            cls._cleanup_expired_memory()
            entry = cls._memory_challenges.pop(challenge_id, None)
            if entry is not None:
                if datetime.now(timezone.utc) < entry.get(
                    "expires_at", datetime.min.replace(tzinfo=timezone.utc)
                ):
                    return {k: v for k, v in entry.items() if k != "expires_at"}
        return None

    @classmethod
    def _cleanup_expired_memory(cls) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            cid
            for cid, data in cls._memory_challenges.items()
            if data.get("expires_at", datetime.min.replace(tzinfo=timezone.utc)) < now
        ]
        for cid in expired:
            del cls._memory_challenges[cid]


def resolve_rp_and_origin(request: Request) -> Tuple[str, str, str]:
    """
    Resolve Relying Party ID, RP Name, and expected Origin from the request.

    - RP ID is the domain without scheme or port (e.g. 'aelira.ai' or 'localhost').
    - Origin is full scheme + host (+ port if non-standard).
    """
    settings = get_settings()
    rp_name = "Aelira"

    # Prefer Origin header from CORS / browser request
    origin_header = request.headers.get("origin")
    if origin_header:
        parsed_origin = urlparse(origin_header)
        rp_id = parsed_origin.hostname or "localhost"
        return rp_id, rp_name, origin_header

    # Fallback to configured magic_link_base_url or request host
    base_url = settings.magic_link_base_url or str(request.base_url).rstrip("/")
    parsed = urlparse(base_url)
    rp_id = parsed.hostname or "localhost"
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return rp_id, rp_name, origin


class PasskeyService:
    """Service handling WebAuthn passkey registration and verification."""

    @staticmethod
    def generate_registration_options(
        db: Session,
        user: User,
        request: Request,
    ) -> Tuple[str, str]:
        """
        Generate PublicKeyCredentialCreationOptions for an authenticated user.

        Returns (options_json, challenge_id).
        """
        rp_id, rp_name, expected_origin = resolve_rp_and_origin(request)

        # Existing credentials to exclude (prevents enrolling the same physical key twice)
        existing_creds = (
            db.query(WebAuthnCredential)
            .filter(WebAuthnCredential.user_id == user.id)
            .all()
        )
        exclude_credentials = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(cred.credential_id),
                transports=cred.transports or None,
            )
            for cred in existing_creds
        ]

        # Use user.id as user_handle (UUID bytes)
        user_handle = user.id.encode("utf-8")

        options = webauthn.generate_registration_options(
            rp_id=rp_id,
            rp_name=rp_name,
            user_id=user_handle,
            user_name=user.email,
            user_display_name=user.name or user.email,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            supported_pub_key_algs=[
                COSEAlgorithmIdentifier.ECDSA_SHA_256,
                COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
                COSEAlgorithmIdentifier.EDDSA,
            ],
            exclude_credentials=exclude_credentials,
        )

        challenge_id = secrets.token_urlsafe(32)
        challenge_data = {
            "challenge": bytes_to_base64url(options.challenge),
            "user_id": user.id,
            "rp_id": rp_id,
            "expected_origin": expected_origin,
            "ceremony": "registration",
        }
        WebAuthnChallengeStorage.store_challenge(challenge_id, challenge_data)

        return options_to_json(options), challenge_id

    @staticmethod
    def verify_registration(
        db: Session,
        user: User,
        challenge_id: str,
        credential_data: Dict[str, Any],
        friendly_name: Optional[str] = None,
    ) -> WebAuthnCredential:
        """
        Verify RegistrationCredential and persist WebAuthnCredential in the database.
        """
        challenge_entry = WebAuthnChallengeStorage.consume_challenge(challenge_id)
        if not challenge_entry or challenge_entry.get("ceremony") != "registration":
            raise WebAuthnVerificationError(
                "Invalid or expired registration challenge."
            )

        if challenge_entry.get("user_id") != user.id:
            raise WebAuthnVerificationError(
                "Challenge does not match authenticated user."
            )

        expected_challenge = base64url_to_bytes(challenge_entry["challenge"])
        expected_origin = challenge_entry["expected_origin"]
        expected_rp_id = challenge_entry["rp_id"]

        try:
            credential = RegistrationCredential.parse_raw(json.dumps(credential_data))
            verification = webauthn.verify_registration_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_origin=expected_origin,
                expected_rp_id=expected_rp_id,
                require_user_verification=False,
            )
        except Exception as e:
            logger.warning("WebAuthn registration verification failed: %s", e)
            raise WebAuthnVerificationError(f"Registration verification failed: {e}")

        # Check for credential_id collisions
        b64_credential_id = bytes_to_base64url(verification.credential_id)
        existing = (
            db.query(WebAuthnCredential)
            .filter(WebAuthnCredential.credential_id == b64_credential_id)
            .first()
        )
        if existing:
            raise WebAuthnVerificationError(
                "This passkey credential is already registered."
            )

        # Persist new credential
        new_cred = WebAuthnCredential(
            user_id=user.id,
            credential_id=b64_credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            aaguid=verification.aaguid if verification.aaguid else None,
            friendly_name=friendly_name or "Passkey",
            backup_eligible=verification.credential_device_type is not None,
            backup_state=False,
            last_used_at=datetime.now(timezone.utc),
        )
        db.add(new_cred)
        db.commit()
        db.refresh(new_cred)

        logger.info(
            "Registered WebAuthn passkey %s for user %s",
            new_cred.id,
            user.id,
        )
        return new_cred

    @staticmethod
    def generate_authentication_options(
        db: Session,
        request: Request,
        email: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Generate PublicKeyCredentialRequestOptions for passkey login.

        If email is provided, restrict to user's registered keys.
        If email is omitted, allow any resident key (Conditional UI).
        """
        rp_id, _, expected_origin = resolve_rp_and_origin(request)

        allow_credentials = None
        user_id = None

        if email:
            normalized_email = email.lower().strip()
            user = (
                db.query(User)
                .filter(User.email == normalized_email, User.is_active == True)
                .first()
            )  # noqa: E712
            if user:
                user_id = user.id
                user_creds = (
                    db.query(WebAuthnCredential)
                    .filter(WebAuthnCredential.user_id == user.id)
                    .all()
                )
                if user_creds:
                    allow_credentials = [
                        PublicKeyCredentialDescriptor(
                            id=base64url_to_bytes(c.credential_id),
                            transports=c.transports or None,
                        )
                        for c in user_creds
                    ]

        options = webauthn.generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.PREFERRED,
        )

        challenge_id = secrets.token_urlsafe(32)
        challenge_data = {
            "challenge": bytes_to_base64url(options.challenge),
            "user_id": user_id,
            "rp_id": rp_id,
            "expected_origin": expected_origin,
            "ceremony": "authentication",
        }
        WebAuthnChallengeStorage.store_challenge(challenge_id, challenge_data)

        return options_to_json(options), challenge_id

    @staticmethod
    def verify_authentication(
        db: Session,
        challenge_id: str,
        credential_data: Dict[str, Any],
    ) -> Tuple[User, WebAuthnCredential]:
        """
        Verify AuthenticationCredential assertion, detect replay attacks, and return User.
        """
        challenge_entry = WebAuthnChallengeStorage.consume_challenge(challenge_id)
        if not challenge_entry or challenge_entry.get("ceremony") != "authentication":
            raise WebAuthnVerificationError(
                "Invalid or expired authentication challenge."
            )

        expected_challenge = base64url_to_bytes(challenge_entry["challenge"])
        expected_origin = challenge_entry["expected_origin"]
        expected_rp_id = challenge_entry["rp_id"]

        try:
            credential = AuthenticationCredential.parse_raw(json.dumps(credential_data))
        except Exception as e:
            raise WebAuthnVerificationError(f"Malformed credential data: {e}")

        b64_credential_id = bytes_to_base64url(credential.raw_id)
        stored_cred = (
            db.query(WebAuthnCredential)
            .filter(WebAuthnCredential.credential_id == b64_credential_id)
            .first()
        )
        if not stored_cred:
            raise WebAuthnVerificationError("Unknown passkey credential.")

        user = (
            db.query(User)
            .filter(User.id == stored_cred.user_id, User.is_active == True)  # noqa: E712
            .first()
        )
        if not user:
            raise WebAuthnVerificationError("User account is inactive or disabled.")

        try:
            verification = webauthn.verify_authentication_response(
                credential=credential,
                expected_challenge=expected_challenge,
                expected_origin=expected_origin,
                expected_rp_id=expected_rp_id,
                credential_public_key=stored_cred.public_key,
                credential_current_sign_count=stored_cred.sign_count,
                require_user_verification=False,
            )
        except Exception as e:
            logger.warning("WebAuthn assertion verification failed: %s", e)
            raise WebAuthnVerificationError(f"Authentication failed: {e}")

        # Replay counter protection
        if (
            stored_cred.sign_count > 0
            and verification.new_sign_count <= stored_cred.sign_count
        ):
            logger.critical(
                "Potential replay attack detected on WebAuthn credential %s: sign_count did not advance (%d -> %d)",
                stored_cred.id,
                stored_cred.sign_count,
                verification.new_sign_count,
            )
            raise WebAuthnVerificationError(
                "Security violation: passkey clone or replay detected."
            )

        stored_cred.sign_count = verification.new_sign_count
        stored_cred.last_used_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(stored_cred)

        logger.info(
            "User %s successfully authenticated with passkey %s",
            user.id,
            stored_cred.id,
        )
        return user, stored_cred


_passkey_service = PasskeyService()


def get_passkey_service() -> PasskeyService:
    """Get the singleton PasskeyService instance."""
    return _passkey_service
