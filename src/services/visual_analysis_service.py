"""Durable visual-analysis execution, retry, recovery, and review handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import re
from pathlib import Path
from typing import Any, Awaitable, Callable
import uuid

from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only

from ..db.models import Scan, ScanFix, VisualAnalysis, VisualAnalysisAttempt
from .scan_fix_service import (
    invalidate_current_artifact_approvals,
    lock_scan_review_graph,
    review_digest_for,
)

VISUAL_SOURCE_KINDS = frozenset({"image", "chart"})
VISUAL_PURPOSES = frozenset(
    {
        "alt_text",
        "chart_description",
        "image_type",
        "alt_text_validation",
        "audio_description",
    }
)
SAFE_FAILURE_CATEGORIES = frozenset(
    {
        "source_unavailable",
        "source_unreadable",
        "provider_unavailable",
        "provider_timeout",
        "provider_rate_limited",
        "policy_denied",
        "invalid_provider_response",
        "worker_interrupted",
        "attempts_exhausted",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
_MAX_PROPOSAL_BYTES = 64 * 1024
_DEFAULT_LEASE_SECONDS = 300
_DEFAULT_REQUEST_FINGERPRINT = hashlib.sha256(
    b"aelira-visual-analysis-request-v1"
).hexdigest()
_LOCATOR_FIELDS = {
    "page_image": {"kind", "page_number", "image_xref"},
    "slide_shape": {"kind", "slide_number", "shape_id"},
    "media_frame": {"kind", "timestamp_ms"},
}
_PROPOSAL_FIELDS = {
    "alt_text": {
        "alt_text",
        "long_description",
        "is_decorative",
        "image_type",
    },
    "chart_description": {
        "chart_type",
        "title",
        "short_description",
        "detailed_description",
        "data_summary",
        "insights",
        "visual_elements",
        "accessibility_note",
    },
    "image_type": {
        "is_decorative",
        "image_purpose",
        "confidence",
        "reasoning",
        "recommended_alt",
        "visual_elements",
    },
    "alt_text_validation": {
        "is_accurate",
        "accuracy_score",
        "issues",
        "suggested_improvement",
        "reasoning",
        "existing_alt_text",
    },
    "audio_description": {
        "description",
        "scene_type",
        "importance",
        "timestamp_ms",
    },
}


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def canonical_visual_locator(value: object) -> dict[str, int | str]:
    """Validate and canonicalize a bounded locator without storage coordinates."""
    if not isinstance(value, dict) or type(value.get("kind")) is not str:
        raise ValueError("visual source locator is invalid")
    kind = value["kind"]
    expected = _LOCATOR_FIELDS.get(kind)
    if expected is None or set(value) != expected:
        raise ValueError("visual source locator is invalid")
    if kind == "page_image":
        page_number = value.get("page_number")
        image_xref = value.get("image_xref")
        if (
            type(page_number) is not int
            or not 1 <= page_number <= 1_000_000
            or type(image_xref) is not int
            or not 0 <= image_xref <= 2_147_483_647
        ):
            raise ValueError("visual source locator is invalid")
        return {
            "kind": kind,
            "page_number": page_number,
            "image_xref": image_xref,
        }
    if kind == "slide_shape":
        slide_number = value.get("slide_number")
        shape_id = value.get("shape_id")
        if (
            type(slide_number) is not int
            or not 1 <= slide_number <= 1_000_000
            or type(shape_id) is not int
            or not 0 <= shape_id <= 2_147_483_647
        ):
            raise ValueError("visual source locator is invalid")
        return {"kind": kind, "slide_number": slide_number, "shape_id": shape_id}
    timestamp_ms = value.get("timestamp_ms")
    if type(timestamp_ms) is not int or not 0 <= timestamp_ms <= 2_678_400_000:
        raise ValueError("visual source locator is invalid")
    return {"kind": kind, "timestamp_ms": timestamp_ms}


@dataclass(frozen=True)
class VisualAnalysisRequest:
    """Validated passive request identity; source bytes are never persisted."""

    department_id: str
    scan_id: str
    source_kind: str
    parent_artifact_sha256: str
    source_bytes: bytes | None
    source_locator: dict[str, Any]
    purpose: str
    request_fingerprint: str = _DEFAULT_REQUEST_FINGERPRINT
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.department_id, str) or not self.department_id:
            raise ValueError("department identity is invalid")
        if not isinstance(self.scan_id, str) or not self.scan_id:
            raise ValueError("scan identity is invalid")
        if self.source_kind not in VISUAL_SOURCE_KINDS:
            raise ValueError("visual source kind is invalid")
        if not _valid_sha256(self.parent_artifact_sha256):
            raise ValueError("parent artifact digest is invalid")
        if self.source_bytes is not None and type(self.source_bytes) is not bytes:
            raise ValueError("visual source bytes are invalid")
        if self.purpose not in VISUAL_PURPOSES:
            raise ValueError("visual analysis purpose is invalid")
        if not _valid_sha256(self.request_fingerprint):
            raise ValueError("visual analysis request fingerprint is invalid")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 20:
            raise ValueError("visual analysis max attempts is invalid")
        object.__setattr__(
            self, "source_locator", canonical_visual_locator(self.source_locator)
        )


@dataclass(frozen=True)
class VisualAnalysisClaim:
    analysis_id: str
    attempt_id: str
    claim_token: str


def visual_request_digest(request: VisualAnalysisRequest) -> str:
    """Bind tenant, scan, source, location, and purpose into retry identity."""
    source_sha256 = (
        hashlib.sha256(request.source_bytes).hexdigest()
        if request.source_bytes is not None
        else None
    )
    material = {
        "version": "visual-analysis-request-v1",
        "department_id": request.department_id,
        "scan_id": request.scan_id,
        "source_kind": request.source_kind,
        "parent_artifact_sha256": request.parent_artifact_sha256,
        "source_sha256": source_sha256,
        "source_locator": request.source_locator,
        "purpose": request.purpose,
        "request_fingerprint": request.request_fingerprint,
    }
    return hashlib.sha256(_canonical_json(material).encode("ascii")).hexdigest()


def _image_is_readable(content: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        return True
    except Exception:
        return False


def _scan_belongs_to_department(db: Any, scan_id: str, department_id: str) -> bool:
    return (
        db.query(Scan.id)
        .filter(Scan.id == scan_id, Scan.department_id == department_id)
        .scalar()
        is not None
    )


def enqueue_visual_analysis(
    db: Any,
    request: VisualAnalysisRequest,
    *,
    now: datetime | None = None,
) -> VisualAnalysis:
    """Create or return one tenant-scoped aggregate for a canonical request."""
    now = now or _utc_now()
    if not _scan_belongs_to_department(db, request.scan_id, request.department_id):
        raise ValueError("scan is outside the requested department")
    request_digest = visual_request_digest(request)
    existing = (
        db.query(VisualAnalysis)
        .filter(
            VisualAnalysis.department_id == request.department_id,
            VisualAnalysis.request_digest == request_digest,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing

    source_sha256 = (
        hashlib.sha256(request.source_bytes).hexdigest()
        if request.source_bytes is not None
        else None
    )
    failure_category = None
    if request.source_bytes is None:
        failure_category = "source_unavailable"
    elif not _image_is_readable(request.source_bytes):
        failure_category = "source_unreadable"
    terminal = failure_category is not None
    analysis = VisualAnalysis(
        id=str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"aelira:visual-analysis:{request.department_id}:{request_digest}",
            )
        ),
        department_id=request.department_id,
        scan_id=request.scan_id,
        source_kind=request.source_kind,
        parent_artifact_sha256=request.parent_artifact_sha256,
        source_sha256=source_sha256,
        source_locator=request.source_locator,
        purpose=request.purpose,
        request_digest=request_digest,
        status="terminal_failure" if terminal else "queued",
        failure_category=failure_category,
        max_attempts=request.max_attempts,
        attempt_count=1 if terminal else 0,
        completed_at=now if terminal else None,
        created_at=now,
    )
    try:
        with db.begin_nested():
            db.add(analysis)
            if terminal:
                db.add(
                    VisualAnalysisAttempt(
                        id=str(uuid.uuid4()),
                        analysis_id=analysis.id,
                        attempt_number=1,
                        purpose=analysis.purpose,
                        status="terminal_failure",
                        started_at=now,
                        finished_at=now,
                        failure_category=failure_category,
                    )
                )
            db.flush()
    except IntegrityError:
        existing = (
            db.query(VisualAnalysis)
            .filter(
                VisualAnalysis.department_id == request.department_id,
                VisualAnalysis.request_digest == request_digest,
            )
            .one_or_none()
        )
        if existing is None:
            raise
        return existing
    return analysis


def _locked_analysis(db: Any, analysis_id: str) -> VisualAnalysis:
    analysis = (
        db.query(VisualAnalysis)
        .filter(VisualAnalysis.id == analysis_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if analysis is None:
        raise ValueError("visual analysis does not exist")
    return analysis


def claim_visual_analysis(
    db: Any,
    analysis_id: str,
    *,
    now: datetime | None = None,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
) -> VisualAnalysisClaim:
    """Atomically claim queued or retryable work and open its next attempt."""
    now = now or _utc_now()
    if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
        raise ValueError("visual analysis lease is invalid")
    analysis = _locked_analysis(db, analysis_id)
    if analysis.status not in {"queued", "retryable_failure"}:
        raise ValueError("visual analysis is not eligible to claim")
    if analysis.attempt_count >= analysis.max_attempts:
        analysis.status = "terminal_failure"
        analysis.failure_category = "attempts_exhausted"
        analysis.completed_at = now
        db.flush()
        raise ValueError("visual analysis attempts are exhausted")

    token = str(uuid.uuid4())
    attempt_number = analysis.attempt_count + 1
    attempt = VisualAnalysisAttempt(
        id=str(uuid.uuid4()),
        analysis_id=analysis.id,
        attempt_number=attempt_number,
        purpose=analysis.purpose,
        status="running",
        started_at=now,
    )
    analysis.status = "running"
    analysis.attempt_count = attempt_number
    analysis.failure_category = None
    analysis.completed_at = None
    analysis.claim_token = token
    analysis.claimed_at = now
    analysis.heartbeat_at = now
    analysis.lease_expires_at = now + timedelta(seconds=lease_seconds)
    db.add(attempt)
    db.flush()
    return VisualAnalysisClaim(analysis.id, attempt.id, token)


def _owned_running_attempt(
    db: Any, claim: VisualAnalysisClaim
) -> tuple[VisualAnalysis, VisualAnalysisAttempt]:
    analysis = _locked_analysis(db, claim.analysis_id)
    if analysis.status != "running" or analysis.claim_token != claim.claim_token:
        raise ValueError("visual analysis ownership was lost")
    attempt = (
        db.query(VisualAnalysisAttempt)
        .filter(
            VisualAnalysisAttempt.id == claim.attempt_id,
            VisualAnalysisAttempt.analysis_id == analysis.id,
            VisualAnalysisAttempt.attempt_number == analysis.attempt_count,
            VisualAnalysisAttempt.status == "running",
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if attempt is None:
        raise ValueError("visual analysis ownership was lost")
    return analysis, attempt


def _bounded_identifier(value: object, *, field: str, limit: int) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= limit
        or _PROVIDER_ID_RE.fullmatch(value) is None
        or value.startswith("/")
        or ".." in value
        or "\\" in value
    ):
        raise ValueError(f"visual analysis {field} is invalid")
    return value


def _passive_json(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("visual proposal contains a non-finite number")
        return value
    if isinstance(value, list):
        return [_passive_json(item) for item in value[:100]]
    if isinstance(value, dict):
        if len(value) > 100 or any(type(key) is not str for key in value):
            raise ValueError("visual proposal is not bounded passive JSON")
        return {key: _passive_json(item) for key, item in value.items()}
    raise ValueError("visual proposal is not bounded passive JSON")


def _bounded_proposal(purpose: str, value: object) -> tuple[dict[str, Any], str]:
    if not isinstance(value, dict):
        raise ValueError("visual proposal must be an object")
    allowed = _PROPOSAL_FIELDS[purpose]
    proposal = {
        key: _passive_json(item) for key, item in value.items() if key in allowed
    }
    if not proposal:
        raise ValueError("visual proposal has no recognized fields")
    encoded = _canonical_json(proposal).encode("ascii")
    if len(encoded) > _MAX_PROPOSAL_BYTES:
        raise ValueError("visual proposal exceeds the size limit")
    return proposal, hashlib.sha256(encoded).hexdigest()


def validated_visual_proposal(purpose: str, value: object) -> dict[str, Any] | None:
    """Return only the bounded public proposal projection."""
    try:
        proposal, _digest = _bounded_proposal(purpose, value)
        return proposal
    except (KeyError, TypeError, ValueError):
        return None


def _review_content(analysis: VisualAnalysis, proposal: dict[str, Any]) -> str | None:
    if analysis.purpose == "alt_text":
        value = proposal.get("alt_text")
        if value == "" and proposal.get("is_decorative") is True:
            return ""
    elif analysis.purpose == "chart_description":
        value = proposal.get("short_description") or proposal.get(
            "detailed_description"
        )
    elif analysis.purpose == "audio_description":
        value = proposal.get("description")
    elif analysis.purpose == "image_type" and proposal.get("is_decorative") is True:
        return ""
    else:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _clear_claim(analysis: VisualAnalysis) -> None:
    analysis.claim_token = None
    analysis.claimed_at = None
    analysis.heartbeat_at = None
    analysis.lease_expires_at = None


def _review_fix_description(analysis: VisualAnalysis) -> str:
    if analysis.purpose == "chart_description":
        return "Machine-generated chart description proposal"
    if analysis.purpose == "audio_description":
        return "Machine-generated audio-description proposal"
    if analysis.purpose == "image_type":
        return "Machine-generated decorative-image classification proposal"
    return "Machine-generated image alt-text proposal"


def _upsert_review_fix(
    db: Any,
    analysis: VisualAnalysis,
    *,
    fixed_content: str,
    provider: str | None,
    model: str | None,
    now: datetime,
) -> ScanFix:
    existing = (
        db.query(ScanFix)
        .filter(
            ScanFix.scan_id == analysis.scan_id,
            ScanFix.occurrence_key == analysis.request_digest,
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    locator_json = _canonical_json(analysis.source_locator)
    if existing is None:
        fix = ScanFix(
            id=str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"aelira:visual-analysis-review:{analysis.id}",
                )
            ),
            scan_id=analysis.scan_id,
            issue_id=f"visual-analysis:{analysis.id}",
            occurrence_key=analysis.request_digest,
            category="alt_text",
            severity="high",
            description=_review_fix_description(analysis),
            location=locator_json,
            original_content=None,
            fixed_content=fixed_content,
            fix_method="ai_vision",
            provider_used=provider,
            model_used=model,
            confidence=0.5,
            needs_review=True,
            review_status="pending",
            wcag_criteria="1.1.1",
            page_number=analysis.source_locator.get("page_number"),
            created_at=now,
        )
        fix.review_digest = review_digest_for(fix)
        if fix.review_digest is None:
            raise ValueError("visual review digest could not be constructed")
        db.add(fix)
        db.flush()
        return fix

    before_digest = existing.review_digest
    before_status = existing.review_status
    before_approved_digest = existing.approved_review_digest
    existing.description = _review_fix_description(analysis)
    existing.location = locator_json
    existing.fixed_content = fixed_content
    existing.fix_method = "ai_vision"
    existing.provider_used = provider
    existing.model_used = model
    existing.confidence = 0.5
    existing.needs_review = True
    existing.wcag_criteria = "1.1.1"
    existing.page_number = analysis.source_locator.get("page_number")
    next_digest = review_digest_for(existing)
    if next_digest is None:
        raise ValueError("visual review digest could not be constructed")
    if next_digest != before_digest:
        graph = lock_scan_review_graph(db, analysis.scan_id)
        invalidate_current_artifact_approvals(
            db, graph, reason="visual_analysis_proposal_changed"
        )
        existing.review_status = "pending"
        existing.reviewed_by = None
        existing.reviewed_at = None
        existing.review_notes = None
        existing.approved_review_digest = None
    else:
        existing.review_status = before_status
        existing.approved_review_digest = before_approved_digest
    existing.review_digest = next_digest
    db.flush()
    return existing


def complete_visual_analysis(
    db: Any,
    claim: VisualAnalysisClaim,
    *,
    proposal: object,
    provider: object = None,
    model: object = None,
    now: datetime | None = None,
) -> VisualAnalysis:
    """Finalize a successful attempt and route remediating output to #303 review."""
    now = now or _utc_now()
    analysis, attempt = _owned_running_attempt(db, claim)
    safe_provider = _bounded_identifier(provider, field="provider", limit=64)
    safe_model = _bounded_identifier(model, field="model", limit=200)
    safe_proposal, proposal_sha256 = _bounded_proposal(analysis.purpose, proposal)
    review_content = _review_content(analysis, safe_proposal)

    attempt.status = "succeeded"
    attempt.finished_at = now
    attempt.provider = safe_provider
    attempt.model = safe_model
    attempt.proposal = safe_proposal
    attempt.proposal_sha256 = proposal_sha256
    analysis.proposal = safe_proposal
    analysis.proposal_sha256 = proposal_sha256
    analysis.failure_category = None
    analysis.completed_at = now
    analysis.status = "review_required" if review_content is not None else "succeeded"
    _clear_claim(analysis)
    if review_content is not None:
        fix = _upsert_review_fix(
            db,
            analysis,
            fixed_content=review_content,
            provider=safe_provider,
            model=safe_model,
            now=now,
        )
        analysis.review_fix_id = fix.id
    db.flush()
    return analysis


def fail_visual_analysis(
    db: Any,
    claim: VisualAnalysisClaim,
    *,
    failure_category: str,
    retryable: bool,
    provider: object = None,
    model: object = None,
    now: datetime | None = None,
) -> VisualAnalysis:
    """Close a claimed attempt with a safe retryable or terminal category."""
    now = now or _utc_now()
    if failure_category not in SAFE_FAILURE_CATEGORIES:
        raise ValueError("visual analysis failure category is invalid")
    analysis, attempt = _owned_running_attempt(db, claim)
    safe_provider = _bounded_identifier(provider, field="provider", limit=64)
    safe_model = _bounded_identifier(model, field="model", limit=200)
    can_retry = retryable and analysis.attempt_count < analysis.max_attempts
    status = "retryable_failure" if can_retry else "terminal_failure"
    attempt.status = status
    attempt.finished_at = now
    attempt.failure_category = failure_category
    attempt.provider = safe_provider
    attempt.model = safe_model
    analysis.status = status
    analysis.failure_category = failure_category
    analysis.completed_at = None if can_retry else now
    _clear_claim(analysis)
    db.flush()
    return analysis


def recover_stale_visual_analyses(db: Any, *, now: datetime | None = None) -> list[str]:
    """Reconcile expired running claims without disturbing live work."""
    now = now or _utc_now()
    rows = (
        db.query(VisualAnalysis)
        .options(
            load_only(
                VisualAnalysis.id,
                VisualAnalysis.status,
                VisualAnalysis.attempt_count,
                VisualAnalysis.max_attempts,
                VisualAnalysis.lease_expires_at,
                VisualAnalysis.claim_token,
                VisualAnalysis.claimed_at,
                VisualAnalysis.heartbeat_at,
                VisualAnalysis.completed_at,
                VisualAnalysis.failure_category,
            )
        )
        .filter(
            VisualAnalysis.status == "running",
            VisualAnalysis.lease_expires_at < now,
        )
        .order_by(VisualAnalysis.id.asc())
        .with_for_update()
        .populate_existing()
        .all()
    )
    recovered: list[str] = []
    for analysis in rows:
        attempt = (
            db.query(VisualAnalysisAttempt)
            .filter(
                VisualAnalysisAttempt.analysis_id == analysis.id,
                VisualAnalysisAttempt.attempt_number == analysis.attempt_count,
                VisualAnalysisAttempt.status == "running",
            )
            .with_for_update()
            .one_or_none()
        )
        if attempt is None:
            continue
        can_retry = analysis.attempt_count < analysis.max_attempts
        status = "retryable_failure" if can_retry else "terminal_failure"
        attempt.status = status
        attempt.finished_at = now
        attempt.failure_category = "worker_interrupted"
        analysis.status = status
        analysis.failure_category = "worker_interrupted"
        analysis.completed_at = None if can_retry else now
        _clear_claim(analysis)
        recovered.append(analysis.id)
    db.flush()
    return recovered


def classify_visual_failure(error: BaseException) -> tuple[str, bool]:
    """Map transport failures without persisting exception messages."""
    name = type(error).__name__.casefold()
    if "timeout" in name:
        return "provider_timeout", True
    if "ratelimit" in name or "rate_limit" in name:
        return "provider_rate_limited", True
    return "provider_unavailable", True


def _classify_result_failure(result: dict[str, Any]) -> tuple[str, bool]:
    error = str(result.get("error", "")).casefold()
    if (
        "policy" in error
        or "denied" in error
        or result.get("purpose_outcome") == ("denied_at_dispatch")
    ):
        return "policy_denied", False
    if "timeout" in error:
        return "provider_timeout", True
    if "429" in error or "rate" in error:
        return "provider_rate_limited", True
    if "unavailable" in error or "503" in error:
        return "provider_unavailable", True
    return "invalid_provider_response", False


class DurableVisualAnalysisRecorder:
    """Wrap existing provider calls in durable state without changing transport."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        department_id: str,
        scan_id: str,
        parent_artifact_sha256: str,
    ) -> None:
        if not _valid_sha256(parent_artifact_sha256):
            raise ValueError("parent artifact digest is invalid")
        self._session_factory = session_factory
        self.department_id = department_id
        self.scan_id = scan_id
        self.parent_artifact_sha256 = parent_artifact_sha256

    @staticmethod
    def _reported_result(analysis: VisualAnalysis) -> dict[str, Any]:
        proposal = analysis.proposal if isinstance(analysis.proposal, dict) else {}
        return {
            "success": analysis.status in {"succeeded", "review_required"},
            **proposal,
            "analysis_id": analysis.id,
            "analysis_status": analysis.status,
            "review_fix_id": analysis.review_fix_id,
            **(
                {"error": analysis.failure_category}
                if analysis.failure_category
                else {}
            ),
        }

    async def execute(
        self,
        *,
        purpose: str,
        source_kind: str,
        source_path: str,
        source_locator: dict[str, Any],
        request_fingerprint: str | None = None,
        invoke: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Persist before dispatch, then close with sanitized evidence."""
        try:
            source_bytes = Path(source_path).read_bytes()
        except (OSError, TypeError, ValueError):
            source_bytes = None
        request = VisualAnalysisRequest(
            department_id=self.department_id,
            scan_id=self.scan_id,
            source_kind=source_kind,
            parent_artifact_sha256=self.parent_artifact_sha256,
            source_bytes=source_bytes,
            source_locator=source_locator,
            purpose=purpose,
            request_fingerprint=(request_fingerprint or _DEFAULT_REQUEST_FINGERPRINT),
        )
        with self._session_factory() as db:
            analysis = enqueue_visual_analysis(db, request)
            if analysis.status == "running":
                recover_stale_visual_analyses(db)
                db.refresh(analysis)
            analysis_id = analysis.id
            existing_status = analysis.status
            reported = self._reported_result(analysis)
            db.commit()
        if existing_status in {
            "succeeded",
            "review_required",
            "terminal_failure",
        }:
            return reported
        if existing_status == "running":
            return {
                "success": False,
                "error": "analysis_in_progress",
                "analysis_id": analysis_id,
                "analysis_status": "running",
            }

        with self._session_factory() as db:
            claim = claim_visual_analysis(db, analysis_id)
            db.commit()
        try:
            result = await invoke()
        except Exception as error:
            failure_category, retryable = classify_visual_failure(error)
            with self._session_factory() as db:
                failed = fail_visual_analysis(
                    db,
                    claim,
                    failure_category=failure_category,
                    retryable=retryable,
                )
                reported = self._reported_result(failed)
                db.commit()
            return reported

        if not isinstance(result, dict) or not result.get("success"):
            safe_result = result if isinstance(result, dict) else {}
            failure_category, retryable = _classify_result_failure(safe_result)
            with self._session_factory() as db:
                failed = fail_visual_analysis(
                    db,
                    claim,
                    failure_category=failure_category,
                    retryable=retryable,
                    provider=safe_result.get("provider"),
                    model=safe_result.get("model"),
                )
                reported = self._reported_result(failed)
                db.commit()
            return reported

        try:
            with self._session_factory() as db:
                completed = complete_visual_analysis(
                    db,
                    claim,
                    proposal=result,
                    provider=result.get("provider"),
                    model=result.get("model"),
                )
                analysis_status = completed.status
                review_fix_id = completed.review_fix_id
                db.commit()
        except ValueError:
            with self._session_factory() as db:
                failed = fail_visual_analysis(
                    db,
                    claim,
                    failure_category="invalid_provider_response",
                    retryable=False,
                )
                reported = self._reported_result(failed)
                db.commit()
            return reported
        return {
            **result,
            "analysis_id": analysis_id,
            "analysis_status": analysis_status,
            "review_fix_id": review_fix_id,
        }
