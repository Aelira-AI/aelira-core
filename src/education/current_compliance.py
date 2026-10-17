"""One current verified compliance state per enrolled document.

A ``Scan`` is an attempt, not a document identity.  Provider-backed documents
use their department-scoped ``CloudFile.id``.  Standalone uploads use their
SHA-256 content hash, website scans use a normalized URL, and new hashless
non-URL scans use an explicit opaque ``Scan.document_id``.  Legacy hashless
rows without a document ID remain history-only because their provider versus
standalone provenance cannot be recovered safely.  Filenames and storage paths
are deliberately never identity inputs.

Historical attempts remain in ``historical_scans``.  Current metrics use only
the newest verified result for each standalone identity and only
``CloudFile.last_scan_id`` for managed provider content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from math import isfinite
from numbers import Real
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from ..db.models import (
    CloudFile,
    CloudJobQueue,
    RemediationArtifact,
    Scan,
    ScanResult,
    ScanStatus,
    ScanType,
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


def _aware_timestamp(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _scan_order(scan: Any) -> tuple[float, str]:
    """Order attempts by immutable creation time, never workflow timestamps.

    Remediation updates ``Scan.completed_at`` on the original scan row.  Using
    that mutable value would allow remediating an older duplicate to make its
    stale result look newer than a later upload.
    """
    return (
        _aware_timestamp(getattr(scan, "created_at", None)),
        str(getattr(scan, "id", "")),
    )


def _normalized_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
    except (TypeError, ValueError):
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or not hostname:
        return None

    host = hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return None
    if port and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    return urlunsplit((scheme, host, parsed.path or "/", parsed.query, ""))


def _standalone_identity(scan: Any) -> tuple[str, str] | None:
    department_id = str(getattr(scan, "department_id", ""))
    if _enum_value(getattr(scan, "document_source", "")) == "cloud_file":
        # The CloudFile inventory is deleted on provider disconnect while scan
        # history remains. Persisted provenance keeps those orphaned attempts
        # from reappearing as current standalone documents.
        return None

    file_hash = getattr(scan, "file_hash", None)
    if isinstance(file_hash, str) and len(file_hash) == 64:
        normalized_hash = file_hash.lower()
        if all(character in "0123456789abcdef" for character in normalized_hash):
            return f"{department_id}:sha256:{normalized_hash}", "standalone_upload"

    if _enum_value(getattr(scan, "scan_type", "")) == ScanType.WEBSITE.value.lower():
        normalized_url = _normalized_url(str(getattr(scan, "file_name", "")))
        if normalized_url:
            return f"{department_id}:url:{normalized_url}", "website"

    document_id = getattr(scan, "document_id", None)
    if isinstance(document_id, str) and document_id:
        return f"{department_id}:document:{document_id}", "standalone_upload"

    # Before document_id existed, failed provider attempts were persisted as
    # hashless Scan rows but their job then raised and discarded scan_id.  Such
    # rows are indistinguishable from legacy hashless standalone failures. Keep
    # them in history instead of guessing and inflating current document stock.
    return None


def _is_verified(scan: Any | None, result: Any | None) -> bool:
    if scan is None or result is None:
        return False
    if (
        _enum_value(getattr(scan, "scan_type", ""))
        in {ScanType.VIDEO.value.lower(), ScanType.MULTIMEDIA.value.lower()}
        and public_compliance_score(scan, result) is None
    ):
        return False
    if _enum_value(getattr(scan, "status", "")) == ScanStatus.COMPLETED.value.lower():
        return True

    # Remediation reuses the original Scan row and can mark its workflow status
    # failed/manual-required.  A pre-existing ScanResult is still the verified
    # source measurement; the unverified remediation candidate is not promoted.
    return bool(
        getattr(scan, "remediation_outcome", None)
        and getattr(scan, "completed_at", None)
    )


def public_compliance_score(scan: Any | None, result: Any | None) -> float | None:
    """Mask unknown scores and legacy media scores without assessment provenance."""
    if scan is None or result is None:
        return None
    score = getattr(result, "compliance_score", None)
    if not isinstance(score, Real) or isinstance(score, bool):
        return None
    if not isfinite(score) or score < 0 or score > 100:
        return None
    if _enum_value(getattr(scan, "scan_type", "")) in {
        ScanType.VIDEO.value.lower(),
        ScanType.MULTIMEDIA.value.lower(),
    }:
        structure = getattr(result, "structure", None)
        if not isinstance(structure, dict):
            return None
        assessment = structure.get("flashing_assessment")
        if not (
            structure.get("media_type") == "audio"
            and isinstance(assessment, dict)
            and assessment.get("state") == "not_applicable"
            and assessment.get("version") == "unassessed-v1"
        ):
            return None
    return float(score)


_SEVERITIES = ("critical", "high", "medium", "low")
_RETIRED_FLASH_MESSAGES = (
    "Dangerous flashing content detected (",
    "Potentially problematic flashing detected (",
)


def is_retired_media_flash_issue(scan_type: Any, issue: Any) -> bool:
    """Identify only verdicts emitted by the withdrawn video flash heuristic."""
    return (
        _enum_value(scan_type)
        in {ScanType.VIDEO.value.lower(), ScanType.MULTIMEDIA.value.lower()}
        and isinstance(issue, dict)
        and issue.get("rule") == "WCAG 2.3.1"
        and isinstance(issue.get("message"), str)
        and issue["message"].startswith(_RETIRED_FLASH_MESSAGES)
    )


def _stored_issue_list(value: Any) -> list[Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        value = value.get("details")
    return value if isinstance(value, list) else None


def public_scan_issues(scan: Any | None, result: Any | None) -> list[Any] | None:
    """Project stored findings without publishing retired media flash verdicts."""
    if result is None:
        return None
    issues = _stored_issue_list(getattr(result, "issues", None))
    if issues is None:
        return None
    scan_type = getattr(scan, "scan_type", None)
    return [
        issue for issue in issues if not is_retired_media_flash_issue(scan_type, issue)
    ]


def retired_media_flash_scan_ids(
    scans: Sequence[Any], results: Sequence[Any]
) -> set[str]:
    """Identify stored media scans containing a withdrawn flash verdict."""
    types_by_id = {scan.id: scan.scan_type for scan in scans}
    return {
        result.scan_id
        for result in results
        if any(
            is_retired_media_flash_issue(types_by_id.get(result.scan_id), issue)
            for issue in (_stored_issue_list(getattr(result, "issues", None)) or [])
        )
    }


def is_ambiguous_retired_media_tracker(issue: Any, retired_scan_ids: set[str]) -> bool:
    """Recognize generic historic tracker rows that cannot be attributed.

    The old automatic mapper stored both rule/message-only caption findings and
    the retired flash verdict with this same identity. A separately described
    human finding remains active even when linked to that scan.
    """
    return (
        issue.scan_id in retired_scan_ids
        and issue.issue_type == "unknown"
        and issue.description == "No description"
        and issue.wcag_criterion is None
    )


def public_issue_counts(scan: Any | None, result: Any | None) -> dict[str, int | None]:
    """Return source severity counts, excluding withdrawn media verdicts."""
    if result is None:
        return {severity: None for severity in _SEVERITIES}
    scan_type = _enum_value(getattr(scan, "scan_type", None))
    if scan_type in {ScanType.VIDEO.value.lower(), ScanType.MULTIMEDIA.value.lower()}:
        issues = public_scan_issues(scan, result)
        if issues is not None:
            return {
                severity: sum(
                    isinstance(issue, dict) and issue.get("severity") == severity
                    for issue in issues
                )
                for severity in _SEVERITIES
            }
        # No parseable source list can establish which old counts came from the
        # retired detector. An explicit all-zero stored count remains zero.
        if any(getattr(result, f"{severity}_issues", None) for severity in _SEVERITIES):
            return {severity: None for severity in _SEVERITIES}
    return {
        severity: getattr(result, f"{severity}_issues", None)
        for severity in _SEVERITIES
    }


def public_issue_total(scan: Any | None, result: Any | None) -> int | None:
    counts = public_issue_counts(scan, result)
    return (
        sum(counts.values())
        if all(value is not None for value in counts.values())
        else None
    )


def public_cloud_file_score(db: Session, cloud_file: CloudFile) -> float | None:
    """Keep cached provider scores from reviving unassessed media results."""
    return public_cloud_file_scores(db, [cloud_file])[cloud_file.id]


def public_cloud_file_issue_totals(
    db: Session, cloud_files: Sequence[CloudFile]
) -> dict[str, int | None]:
    """Resolve active source findings for provider inventory in two queries."""
    scan_ids = {file.last_scan_id for file in cloud_files if file.last_scan_id}
    scans = db.query(Scan).filter(Scan.id.in_(scan_ids)).all() if scan_ids else []
    results = (
        db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        if scan_ids
        else []
    )
    scans_by_id = {scan.id: scan for scan in scans}
    results_by_scan = {result.scan_id: result for result in results}
    totals = {}
    for cloud_file in cloud_files:
        scan = scans_by_id.get(cloud_file.last_scan_id)
        result = results_by_scan.get(cloud_file.last_scan_id)
        if (
            scan is None
            or result is None
            or scan.department_id != cloud_file.department_id
            or getattr(scan, "document_id", None) not in (None, cloud_file.id)
        ):
            totals[cloud_file.id] = None
        else:
            totals[cloud_file.id] = public_issue_total(scan, result)
    return totals


def public_cloud_file_scores(
    db: Session, cloud_files: Sequence[CloudFile]
) -> dict[str, float | None]:
    """Resolve provider scores in bounded queries, with media provenance checked."""
    scan_ids = {file.last_scan_id for file in cloud_files if file.last_scan_id}
    scans = db.query(Scan).filter(Scan.id.in_(scan_ids)).all() if scan_ids else []
    scans_by_id = {scan.id: scan for scan in scans}
    media_scan_ids = {
        scan.id
        for scan in scans
        if _enum_value(scan.scan_type)
        in {ScanType.VIDEO.value.lower(), ScanType.MULTIMEDIA.value.lower()}
    }
    results = (
        db.query(ScanResult).filter(ScanResult.scan_id.in_(media_scan_ids)).all()
        if media_scan_ids
        else []
    )
    results_by_scan = {result.scan_id: result for result in results}

    scores: dict[str, float | None] = {}
    for cloud_file in cloud_files:
        cached = cloud_file.last_compliance_score
        if (
            not isinstance(cached, Real)
            or isinstance(cached, bool)
            or not isfinite(cached)
            or cached < 0
            or cached > 100
        ):
            scores[cloud_file.id] = None
            continue

        scan = scans_by_id.get(cloud_file.last_scan_id)
        if scan is not None and scan.id in media_scan_ids:
            scores[cloud_file.id] = public_compliance_score(
                scan, results_by_scan.get(scan.id)
            )
            continue

        file_type = str(getattr(cloud_file, "file_type", None) or "").lower()
        mime_type = str(getattr(cloud_file, "mime_type", None) or "").lower()
        if (
            (scan is None and file_type in {"", "file", "unknown"})
            or file_type
            in {
                "mp4",
                "mov",
                "avi",
                "mkv",
                "webm",
                "m4v",
                "wmv",
                "mp3",
                "wav",
                "m4a",
                "aac",
                "ogg",
                "flac",
                "video",
                "audio",
                "multimedia",
            }
            or mime_type.startswith(("video/", "audio/"))
        ):
            # A cached score alone does not establish a media assessment.
            scores[cloud_file.id] = None
        else:
            scores[cloud_file.id] = float(cached)
    return scores


def public_scan_job_results(
    db: Session, jobs: Sequence[CloudJobQueue]
) -> dict[str, dict[str, Any] | None]:
    """Mask cached scan-job scores using linked, department-owned scan evidence."""
    evidence_jobs = [
        job
        for job in jobs
        if _enum_value(job.job_type) == "scan"
        and isinstance(job.result_data, dict)
        and {"compliance_score", "issues_found"}.intersection(job.result_data)
    ]
    evidence_ids = {job.id for job in evidence_jobs}
    scan_ids = {
        job.result_data.get("scan_id")
        for job in evidence_jobs
        if isinstance(job.result_data.get("scan_id"), str)
    }
    scans = db.query(Scan).filter(Scan.id.in_(scan_ids)).all() if scan_ids else []
    scans_by_id = {scan.id: scan for scan in scans}
    results = (
        db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        if scan_ids
        else []
    )
    results_by_scan = {result.scan_id: result for result in results}

    public: dict[str, dict[str, Any] | None] = {}
    for job in jobs:
        blob = job.result_data
        if not isinstance(blob, dict):
            public[job.id] = blob
            continue
        safe = dict(blob)
        if job.id in evidence_ids:
            scan_id = blob.get("scan_id")
            scan = scans_by_id.get(scan_id) if isinstance(scan_id, str) else None
            result = results_by_scan.get(getattr(scan, "id", None))
            owns_scan = (
                scan is not None
                and scan.department_id == job.department_id
                and (job.cloud_file_id is None or scan.document_id == job.cloud_file_id)
            )
            if "compliance_score" in safe:
                safe["compliance_score"] = (
                    public_compliance_score(scan, result)
                    if owns_scan and _is_verified(scan, result)
                    else None
                )
            if "issues_found" in safe:
                safe["issues_found"] = (
                    public_issue_total(scan, result) if owns_scan else None
                )
        public[job.id] = safe
    return public


@dataclass(frozen=True)
class CurrentDocumentState:
    identity: str
    source_kind: str
    scan: Any | None
    result: Any | None
    latest_attempt: Any | None = None
    stale: bool = False

    @property
    def verified(self) -> bool:
        return _is_verified(self.scan, self.result)

    @property
    def scanned(self) -> bool:
        return self.latest_attempt is not None

    @property
    def failed(self) -> bool:
        return bool(
            self.latest_attempt is not None
            and _enum_value(getattr(self.latest_attempt, "status", ""))
            == ScanStatus.FAILED.value.lower()
        )


@dataclass(frozen=True)
class CurrentComplianceProjection:
    historical_scans: tuple[Any, ...]
    current_documents: tuple[CurrentDocumentState, ...]

    @property
    def historical_scan_count(self) -> int:
        return len(self.historical_scans)

    @property
    def enrolled_document_count(self) -> int:
        return len(self.current_documents)

    @property
    def verified_documents(self) -> tuple[CurrentDocumentState, ...]:
        return tuple(
            document for document in self.current_documents if document.verified
        )

    @property
    def verified_document_count(self) -> int:
        return len(self.verified_documents)

    @property
    def unverified_document_count(self) -> int:
        return self.enrolled_document_count - self.verified_document_count

    @property
    def scanned_document_count(self) -> int:
        return sum(document.scanned for document in self.current_documents)

    @property
    def stale_document_count(self) -> int:
        return sum(document.stale for document in self.current_documents)

    @property
    def failed_document_count(self) -> int:
        return sum(document.failed for document in self.current_documents)

    @property
    def average_compliance_score(self) -> float | None:
        scores = [
            float(document.result.compliance_score)
            for document in self.verified_documents
        ]
        return sum(scores) / len(scores) if scores else None

    @property
    def minimum_compliance_score(self) -> float | None:
        scores = [
            float(document.result.compliance_score)
            for document in self.verified_documents
        ]
        return min(scores) if scores else None

    @property
    def maximum_compliance_score(self) -> float | None:
        scores = [
            float(document.result.compliance_score)
            for document in self.verified_documents
        ]
        return max(scores) if scores else None

    @property
    def total_pages(self) -> int:
        return sum(
            int(getattr(document.scan, "pages", 0) or 0)
            for document in self.verified_documents
        )

    def issue_count(self, severity: str) -> int:
        return sum(
            int(public_issue_counts(document.scan, document.result)[severity] or 0)
            for document in self.verified_documents
        )

    @property
    def total_issues(self) -> int:
        return sum(
            self.issue_count(severity)
            for severity in ("critical", "high", "medium", "low")
        )

    def scan_type_count(self, scan_type: ScanType) -> int:
        return sum(
            1
            for document in self.verified_documents
            if _enum_value(getattr(document.scan, "scan_type", ""))
            == scan_type.value.lower()
        )

    def compliance_band_counts(
        self, *, compliant_at: float, needs_work_at: float
    ) -> tuple[int, int, int]:
        compliant = needs_work = critical = 0
        for document in self.verified_documents:
            score = float(document.result.compliance_score)
            if score >= compliant_at:
                compliant += 1
            elif score >= needs_work_at:
                needs_work += 1
            else:
                critical += 1
        return compliant, needs_work, critical


def project_current_documents(
    scans: Sequence[Any],
    results: Sequence[Any],
    cloud_files: Sequence[Any],
    *,
    provider_scan_ids: Iterable[str] = (),
) -> CurrentComplianceProjection:
    """Project scan history into one current state per stable document identity."""

    scans_by_id = {str(scan.id): scan for scan in scans}
    results_by_scan = {str(result.scan_id): result for result in results}
    managed_scan_ids = {str(scan_id) for scan_id in provider_scan_ids if scan_id}
    cloud_document_ids = {str(cloud_file.id) for cloud_file in cloud_files}
    managed_scan_ids.update(
        str(scan.id)
        for scan in scans
        if getattr(scan, "document_id", None)
        and str(scan.document_id) in cloud_document_ids
    )
    current_documents: list[CurrentDocumentState] = []

    for cloud_file in cloud_files:
        last_scan_id = getattr(cloud_file, "last_scan_id", None)
        if last_scan_id:
            managed_scan_ids.add(str(last_scan_id))
        scan = scans_by_id.get(str(last_scan_id)) if last_scan_id else None
        result = results_by_scan.get(str(last_scan_id)) if last_scan_id else None
        cloud_attempts = [
            candidate
            for candidate in scans
            if str(getattr(candidate, "document_id", "")) == str(cloud_file.id)
            and _enum_value(getattr(candidate, "document_source", "")) == "cloud_file"
        ]
        if scan is not None and scan not in cloud_attempts:
            cloud_attempts.append(scan)
        latest_attempt = (
            max(cloud_attempts, key=_scan_order) if cloud_attempts else None
        )
        if not _is_verified(scan, result):
            scan = None
            result = None
        current_documents.append(
            CurrentDocumentState(
                identity=(
                    f"{getattr(cloud_file, 'department_id', '')}:cloud:"
                    f"{getattr(cloud_file, 'id', '')}"
                ),
                source_kind="cloud_file",
                scan=scan,
                result=result,
                latest_attempt=latest_attempt,
                stale=bool(getattr(cloud_file, "needs_rescan", False)),
            )
        )

    standalone_groups: dict[str, tuple[str, list[Any]]] = {}
    for scan in scans:
        if str(scan.id) in managed_scan_ids:
            continue
        standalone_identity = _standalone_identity(scan)
        if standalone_identity is None:
            continue
        identity, source_kind = standalone_identity
        standalone_groups.setdefault(identity, (source_kind, []))[1].append(scan)

    for identity, (source_kind, attempts) in standalone_groups.items():
        verified_attempts = [
            scan
            for scan in attempts
            if _is_verified(scan, results_by_scan.get(str(scan.id)))
        ]
        scan = max(verified_attempts, key=_scan_order) if verified_attempts else None
        result = results_by_scan.get(str(scan.id)) if scan is not None else None
        current_documents.append(
            CurrentDocumentState(
                identity=identity,
                source_kind=source_kind,
                scan=scan,
                result=result,
                latest_attempt=max(attempts, key=_scan_order),
            )
        )

    return CurrentComplianceProjection(
        historical_scans=tuple(scans),
        current_documents=tuple(
            sorted(current_documents, key=lambda item: item.identity)
        ),
    )


def _provider_scan_ids(jobs: Sequence[Any], artifacts: Sequence[Any]) -> set[str]:
    scan_ids = {
        str(artifact.scan_id)
        for artifact in artifacts
        if getattr(artifact, "cloud_file_id", None)
        and getattr(artifact, "scan_id", None)
    }
    for job in jobs:
        if not getattr(job, "cloud_file_id", None):
            continue
        if _enum_value(getattr(job, "job_type", "")) not in {
            "scan",
            "remediate",
            "canvas_content",
        }:
            continue
        result_data = getattr(job, "result_data", None)
        if isinstance(result_data, Mapping) and result_data.get("scan_id"):
            scan_ids.add(str(result_data["scan_id"]))
    return scan_ids


def get_department_current_compliance(
    db: Session, department_id: str
) -> CurrentComplianceProjection:
    """Load and project one department before any cross-document grouping."""

    scans = db.query(Scan).filter(Scan.department_id == department_id).all()
    scan_ids = [scan.id for scan in scans]
    results = (
        db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
        if scan_ids
        else []
    )
    cloud_files = (
        db.query(CloudFile).filter(CloudFile.department_id == department_id).all()
    )
    jobs = (
        db.query(CloudJobQueue)
        .filter(
            CloudJobQueue.department_id == department_id,
            CloudJobQueue.cloud_file_id.is_not(None),
        )
        .all()
    )
    artifacts = (
        db.query(RemediationArtifact)
        .filter(
            RemediationArtifact.department_id == department_id,
            RemediationArtifact.cloud_file_id.is_not(None),
        )
        .all()
    )
    return project_current_documents(
        scans,
        results,
        cloud_files,
        provider_scan_ids=_provider_scan_ids(jobs, artifacts),
    )
