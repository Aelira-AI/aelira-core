"""Pure, fail-closed LMS AI authorization policy resolution."""

from dataclasses import dataclass
from typing import Literal

LMS_AI_POLICY_VERSION = 1
LMS_AI_PROVIDERS = frozenset({"ollama", "gemini", "openai", "anthropic", "xai"})
LMS_AI_PURPOSES = frozenset({"remediation", "alt_text"})


@dataclass(frozen=True)
class LMSAIPolicyDecision:
    """Immutable authorization decision; it contains no provider client state."""

    enabled: bool
    allowed: bool
    provider: str | None
    locality: Literal["local", "remote"] | None
    purpose: str | None
    reason: str
    version: int = LMS_AI_POLICY_VERSION


def _deny(*, enabled: bool, purpose: str | None, reason: str) -> LMSAIPolicyDecision:
    return LMSAIPolicyDecision(
        enabled=enabled,
        allowed=False,
        provider=None,
        locality=None,
        purpose=purpose,
        reason=reason,
    )


def resolve_lms_ai_policy(department: object, purpose: object) -> LMSAIPolicyDecision:
    """Resolve one purpose without initializing providers or performing I/O."""

    enabled = getattr(department, "lms_ai_enabled", None) is True
    normalized_purpose = purpose if isinstance(purpose, str) else None
    if normalized_purpose not in LMS_AI_PURPOSES:
        return _deny(
            enabled=enabled, purpose=normalized_purpose, reason="invalid_purpose"
        )
    if not enabled:
        return _deny(enabled=False, purpose=normalized_purpose, reason="disabled")

    provider = getattr(department, "lms_ai_provider", None)
    purposes = getattr(department, "lms_ai_purposes", None)
    if (
        not isinstance(provider, str)
        or provider not in LMS_AI_PROVIDERS
        or not isinstance(purposes, list)
        or not purposes
        or any(
            not isinstance(item, str) or item not in LMS_AI_PURPOSES
            for item in purposes
        )
        or len(set(purposes)) != len(purposes)
    ):
        return _deny(enabled=True, purpose=normalized_purpose, reason="invalid_policy")
    if normalized_purpose not in purposes:
        return _deny(
            enabled=True, purpose=normalized_purpose, reason="purpose_not_enabled"
        )

    return LMSAIPolicyDecision(
        enabled=True,
        allowed=True,
        provider=provider,
        locality="local" if provider == "ollama" else "remote",
        purpose=normalized_purpose,
        reason="allowed",
    )
