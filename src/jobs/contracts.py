"""Typed, JSON-safe contracts for durable job execution."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping

ProgressReporter = Callable[[int, str | None], Awaitable[bool]]
OwnershipChecker = Callable[[], Awaitable[None]]
ExternalEffectBeginner = Callable[[], Awaitable[str]]

_CREDENTIAL_MATERIAL_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "client_secret",
        "credentials",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)


def reject_credential_material(value: Any) -> None:
    """Reject credential-shaped keys recursively at durable JSON boundaries."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _CREDENTIAL_MATERIAL_KEYS:
                raise ValueError("credential_material_forbidden")
            reject_credential_material(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            reject_credential_material(item)


def remove_credential_material(value: Any) -> Any:
    """Remove credential-shaped entries from failure evidence before persistence."""
    if isinstance(value, Mapping):
        cleaned = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _CREDENTIAL_MATERIAL_KEYS:
                continue
            cleaned[key] = remove_credential_material(item)
        return cleaned
    if isinstance(value, (list, tuple, set, frozenset)):
        return [remove_credential_material(item) for item in value]
    return value


async def _assume_owned() -> None:
    """Compatibility default for direct/legacy handler invocation."""


async def _external_effect_checkpoint_unavailable() -> str:
    """Fail closed when a non-durable caller attempts an external mutation."""
    raise RuntimeError("external effect checkpoint unavailable")


class LostJobOwnership(RuntimeError):
    """Raised when a domain checkpoint no longer owns its queue claim."""


class FailureKind(str, Enum):
    RETRYABLE = "retryable"
    DETERMINISTIC = "deterministic"
    INDETERMINATE = "indeterminate"


def sanitize_json(value: Any, *, _depth: int = 0) -> Any:
    """Return bounded, serialization-safe data without leaking repr strings."""
    if _depth > 12:
        return "<max-depth>"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "<non-finite-number>"
    if isinstance(value, Mapping):
        return {
            str(key)[:256]: sanitize_json(item, _depth=_depth + 1)
            for key, item in list(value.items())[:256]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize_json(item, _depth=_depth + 1) for item in list(value)[:256]]
    return "<non-json-value>"


_PUBLIC_JOB_RESULT_FIELDS = frozenset(
    {
        "artifact_id",
        "ai_used",
        "compliance_improvement",
        "download_available",
        "external_ai_used",
        "failed_count",
        "fixed_count",
        "manual_count",
        "original_compliance_score",
        "remediated_compliance_score",
        "scan_id",
        "skipped_count",
        "status",
        "success",
        "total_issues",
        "providers",
        "purpose_decisions",
    }
)
_PUBLIC_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PUBLIC_COUNT_FIELDS = frozenset(
    {"failed_count", "fixed_count", "manual_count", "skipped_count", "total_issues"}
)
_PUBLIC_SCORE_FIELDS = frozenset(
    {
        "compliance_improvement",
        "original_compliance_score",
        "remediated_compliance_score",
    }
)

_PUBLIC_JOB_ERROR_CODES = frozenset(
    {
        "alt_text_manual_required",
        "download_failed",
        "invalid_job_payload",
        "invalid_job_scope",
        "job_execution_timeout",
        "job_handler_exception",
        "job_lease_expired",
        "managed_artifact_required",
        "malformed_handler_result",
        "manual_required",
        "policy_not_permitted",
        "remediation_artifact_unavailable",
        "remediation_artifact_retryable",
        "remediation_completion_retryable",
        "remediation_failed",
        "remediation_unsupported",
        "report_generation_failed",
        "report_payload_invalid",
        "report_storage_unavailable",
        "scan_not_found",
        "scan_results_unavailable",
        "source_file_unavailable",
        "unsupported_lms_remediation",
        "unregistered_job_type",
    }
)


def public_job_result(value: Any) -> dict[str, Any] | None:
    """Project internal result data onto the path-free public contract."""
    if not isinstance(value, Mapping):
        return None
    result: dict[str, Any] = {}
    for key in _PUBLIC_JOB_RESULT_FIELDS:
        if key not in value:
            continue
        item = value[key]
        if key in {"success", "download_available"}:
            if type(item) is bool:
                result[key] = item
        elif key in {"ai_used", "external_ai_used"}:
            if item is None or type(item) is bool:
                result[key] = item
        elif key in {"artifact_id", "scan_id"}:
            if isinstance(item, str) and _PUBLIC_IDENTIFIER_RE.fullmatch(item):
                result[key] = item
        elif key in _PUBLIC_COUNT_FIELDS:
            if type(item) is int and 0 <= item <= 1_000_000:
                result[key] = item
        elif key in _PUBLIC_SCORE_FIELDS:
            if (
                type(item) in {int, float}
                and math.isfinite(float(item))
                and -100.0 <= float(item) <= 100.0
            ):
                result[key] = item
        elif key == "status":
            if item in {"completed", "manual_required", "no_op", "failed"}:
                result[key] = item
        elif key == "providers":
            if item is None:
                result[key] = None
            elif isinstance(item, list):
                result[key] = [
                    provider
                    for provider in item[:2]
                    if provider
                    in {"anthropic", "gemini", "local", "ollama", "openai", "xai"}
                ]
        elif key == "purpose_decisions":
            if item is None:
                result[key] = None
            elif isinstance(item, Mapping):
                result[key] = {
                    purpose: decision
                    for purpose, decision in item.items()
                    if purpose in {"remediation", "alt_text"}
                    and decision
                    in {
                        "not_requested",
                        "allowed_not_used",
                        "manual_required",
                        "denied_at_dispatch",
                        "attempted_failed",
                        "used",
                    }
                }
    return result or None


def public_job_error_code(value: Any) -> str | None:
    """Return only an explicitly stable public remediation error code."""
    return (
        value if isinstance(value, str) and value in _PUBLIC_JOB_ERROR_CODES else None
    )


def validate_json_object(value: Any, *, max_bytes: int = 262_144) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("job JSON value must be an object")
    sanitized = sanitize_json(value)
    if not isinstance(sanitized, dict):
        raise ValueError("job JSON value must be an object")
    if len(json.dumps(sanitized, separators=(",", ":")).encode()) > max_bytes:
        raise ValueError("job JSON value exceeds size limit")
    return sanitized


@dataclass(frozen=True)
class JobSuccess:
    result: dict[str, Any] = field(default_factory=dict)
    handler_committed: bool = False

    def __post_init__(self) -> None:
        reject_credential_material(self.result)
        object.__setattr__(self, "result", validate_json_object(self.result))


@dataclass(frozen=True)
class JobFailure:
    code: str
    kind: FailureKind
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        safe_code = (
            self.code.strip()[:128] if isinstance(self.code, str) else "job_failed"
        )
        object.__setattr__(self, "code", safe_code or "job_failed")
        object.__setattr__(
            self,
            "details",
            validate_json_object(remove_credential_material(self.details)),
        )

    @classmethod
    def retryable(
        cls, code: str, details: Mapping[str, Any] | None = None
    ) -> "JobFailure":
        return cls(code, FailureKind.RETRYABLE, dict(details or {}))

    @classmethod
    def deterministic(
        cls, code: str, details: Mapping[str, Any] | None = None
    ) -> "JobFailure":
        return cls(code, FailureKind.DETERMINISTIC, dict(details or {}))

    @classmethod
    def indeterminate(
        cls, code: str, details: Mapping[str, Any] | None = None
    ) -> "JobFailure":
        return cls(code, FailureKind.INDETERMINATE, dict(details or {}))


@dataclass(frozen=True)
class JobContext:
    job_id: str
    job_type: str
    payload: Mapping[str, Any]
    claim_token: str
    worker_id: str
    attempt_count: int
    report_progress: ProgressReporter
    assert_owned: OwnershipChecker = _assume_owned
    begin_external_effect: ExternalEffectBeginner = (
        _external_effect_checkpoint_unavailable
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "payload", MappingProxyType(validate_json_object(self.payload))
        )


JobResult = JobSuccess | JobFailure
JobHandler = Callable[[JobContext, Any, Any], Awaitable[JobResult]]
