"""Bounded review-evidence export generator.

Generates bounded audit evidence in JSON, CSV, and PDF formats for
the review workflow. Used by the GET /reviews/{scan_id}/audit/export endpoint.

PDF reports include:
- Executive Summary with bounded review-state counts
- Machine observations
- Reviewer decisions
- Review History (chronological audit log)
- Matterhorn Protocol results (for PDF scans)
- Scope and limitations
"""

import csv
import io
import logging
import os
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.config.settings import get_settings

logger = logging.getLogger(__name__)

# Aelira brand colors
_BLUE_PRIMARY = "#2563EB"
_BLUE_DARK = "#1E40AF"
_DARK_GRAY = "#1F2937"
_MEDIUM_GRAY = "#4B5563"
_LIGHT_GRAY = "#F3F4F6"

# Severity row colors
_SEVERITY_COLORS = {
    "critical": "#FEE2E2",
    "serious": "#FED7AA",
    "moderate": "#FEF3C7",
    "minor": "#D1FAE5",
}

ACCEPTED_REVIEW_STATUSES = frozenset({"approved", "edited", "auto_approved"})
_EXPORTED_REVIEW_STATUSES = (
    "pending",
    "approved",
    "rejected",
    "edited",
    "auto_approved",
    "unresolved",
    "unavailable",
)
_EXPORTED_DEFERRAL_STATUSES = ("active", "expired", "revoked", "resolved")


def _isoformat(dt: Any) -> str:
    """Safely convert a datetime to ISO format string."""
    if dt is None:
        return ""
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def _safe_str(value: Any, default: str = "") -> str:
    """Convert value to string, returning default if None.

    Handles enum types by extracting .value to avoid 'ScanType.WEBSITE' style output.
    """
    if value is None:
        return default
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


def _recorded(value: Any, missing: str = "not recorded") -> Any:
    """Return recorded evidence without manufacturing a substitute."""
    if value is None or value == "":
        return missing
    if hasattr(value, "value"):
        return value.value
    return value


def _review_status(value: Any) -> str:
    """Map persisted status to the bounded public evidence vocabulary."""
    if value is None or not str(value).strip():
        return "unavailable"
    normalized = str(value).strip().lower()
    if normalized in _EXPORTED_REVIEW_STATUSES:
        return normalized
    return "unresolved"


def _review_status_counts(fixes: list) -> dict[str, int]:
    counts = {status: 0 for status in _EXPORTED_REVIEW_STATUSES}
    for fix in fixes:
        counts[_review_status(getattr(fix, "review_status", None))] += 1
    return counts


def _deferral_lifecycle(fix: Any, *, now: datetime | None = None) -> str | None:
    stored_status = getattr(fix, "deferral_status", None)
    if stored_status is None:
        return None
    if stored_status in {"revoked", "resolved"}:
        return stored_status
    if stored_status != "active":
        return None
    expires_at = getattr(fix, "deferral_expires_at", None)
    if not isinstance(expires_at, datetime):
        return None
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return "expired" if expires_at <= (now or datetime.now(timezone.utc)) else "active"


def _deferral_evidence(fix: Any) -> dict[str, Any] | None:
    lifecycle = _deferral_lifecycle(fix)
    if lifecycle is None:
        return None
    return {
        "lifecycle": lifecycle,
        "owner": getattr(fix, "deferral_owner", None),
        "reason": getattr(fix, "deferral_reason", None),
        "expires_at": _isoformat(getattr(fix, "deferral_expires_at", None)),
        "created_at": _isoformat(getattr(fix, "deferral_created_at", None)),
        "updated_at": _isoformat(getattr(fix, "deferral_updated_at", None)),
        "closed_at": (_isoformat(getattr(fix, "deferral_closed_at", None)) or None),
    }


def _deferral_status_counts(fixes: list) -> dict[str, int]:
    counts = {status: 0 for status in _EXPORTED_DEFERRAL_STATUSES}
    for fix in fixes:
        lifecycle = _deferral_lifecycle(fix)
        if lifecycle is not None:
            counts[lifecycle] += 1
    return counts


def _machine_observation(fix: Any) -> dict[str, Any]:
    return {
        "id": fix.id,
        "category": fix.category,
        "severity": fix.severity,
        "description": fix.description,
        "fix_method": fix.fix_method,
        "confidence": fix.confidence,
        "wcag_criteria": _recorded(getattr(fix, "wcag_criteria", None)),
        "page_number": _recorded(getattr(fix, "page_number", None)),
        "source_kind": _recorded(getattr(fix, "source_kind", None), "unavailable"),
        "source_locator": _recorded(
            getattr(fix, "source_locator", None), "unavailable"
        ),
        "verification_evidence": _recorded(
            getattr(fix, "verification_evidence", None), "unavailable"
        ),
        "created_at": _recorded(
            _isoformat(getattr(fix, "created_at", None)), "not recorded"
        ),
    }


def _reviewer_decision(fix: Any) -> dict[str, Any]:
    status = _review_status(getattr(fix, "review_status", None))
    if status == "auto_approved":
        decision_source = "automated"
    elif status in {"approved", "edited", "rejected"}:
        decision_source = "human"
    else:
        decision_source = "not recorded"
    return {
        "fix_id": fix.id,
        "review_status": status,
        "accepted": status in ACCEPTED_REVIEW_STATUSES,
        "decision_source": decision_source,
        "reviewer_id": _recorded(getattr(fix, "reviewed_by", None)),
        "reviewer_name": _recorded(getattr(fix, "_export_reviewer_name", None)),
        "reviewed_at": _recorded(
            _isoformat(getattr(fix, "reviewed_at", None)), "not recorded"
        ),
        "review_notes": _recorded(getattr(fix, "review_notes", None)),
        "review_digest": _recorded(getattr(fix, "review_digest", None)),
        "approved_review_digest": _recorded(
            getattr(fix, "approved_review_digest", None)
        ),
        "deferral": _deferral_evidence(fix),
    }


def _source_evidence(scan: Any) -> dict[str, Any]:
    return {
        "document_id": _recorded(getattr(scan, "document_id", None), "unavailable"),
        "document_source": _recorded(
            getattr(scan, "document_source", None), "unavailable"
        ),
        "sha256": _recorded(getattr(scan, "file_hash", None), "unavailable"),
    }


def _artifact_evidence(scan: Any) -> dict[str, Any]:
    artifact = getattr(scan, "current_remediation_artifact", None)
    if artifact is None:
        return {"availability": "unavailable"}
    return {
        "availability": _recorded(
            getattr(artifact, "lifecycle_status", None), "unavailable"
        ),
        "id": _recorded(getattr(artifact, "id", None)),
        "filename": _recorded(getattr(artifact, "filename", None)),
        "mime_type": _recorded(getattr(artifact, "mime_type", None)),
        "size_bytes": _recorded(getattr(artifact, "size_bytes", None)),
        "sha256": _recorded(getattr(artifact, "sha256", None)),
        "review_status": _recorded(getattr(artifact, "review_status", None)),
        "approval_review_digest": _recorded(
            getattr(artifact, "approval_review_digest", None)
        ),
        "written_back_at": _recorded(
            _isoformat(getattr(artifact, "written_back_at", None)), "not recorded"
        ),
    }


def _validator_result(total: int, passed: int, failed: int) -> str:
    """Summarize recorded checkpoints without making a conformance claim."""
    if total == 0:
        return "not_run"
    if failed > 0:
        return "recorded_checkpoint_failures"
    if passed == total:
        return "all_recorded_checkpoints_passed"
    return "recorded_checkpoint_results_available"


def _validator_result_label(result: str) -> str:
    """Human-readable label for the recorded Matterhorn result."""
    labels = {
        "all_recorded_checkpoints_passed": "All recorded Matterhorn checkpoints passed",
        "recorded_checkpoint_failures": "Recorded Matterhorn checkpoint failures remain",
        "recorded_checkpoint_results_available": "Recorded Matterhorn checkpoint results available",
        "not_run": "No recorded Matterhorn checkpoints",
    }
    return labels.get(result, result)


def bounded_audit_details(details: Any) -> Any:
    """Remove legacy conformance labels from public audit detail payloads."""
    if isinstance(details, list):
        return [bounded_audit_details(item) for item in details]
    if not isinstance(details, dict):
        return details

    legacy_keys = {"compliance_level", "matterhorn_compliance", "compliance"}
    legacy_values = [details.get(key) for key in legacy_keys if key in details]
    bounded = {
        key: bounded_audit_details(value)
        for key, value in details.items()
        if key not in legacy_keys
    }

    if legacy_values and "validator_result" not in bounded:
        total = details.get("total")
        passed = details.get("passed")
        failed = details.get("failed")
        if all(isinstance(value, int) for value in (total, passed, failed)):
            validator_result = _validator_result(total, passed, failed)
        else:
            legacy_value = str(legacy_values[0]).lower()
            if legacy_value in {"compliant", "fully_compliant"}:
                validator_result = "all_recorded_checkpoints_passed"
            elif legacy_value in {
                "partial",
                "partially_compliant",
                "non_compliant",
            }:
                validator_result = "recorded_checkpoint_failures"
            elif legacy_value in {"not_validated", "not_run"}:
                validator_result = "not_run"
            else:
                validator_result = "recorded_checkpoint_results_available"
        bounded = {"validator_result": validator_result, **bounded}

    return bounded


class AuditReportGenerator:
    """Generate audit trail export reports in JSON, CSV, and PDF formats."""

    @staticmethod
    def generate_json(
        scan: Any,
        fixes: list,
        audit_entries: list,
        matterhorn_results: list,
        department: Any,
    ) -> dict:
        """Generate a JSON-serializable audit report dictionary.

        All datetime values are converted to ISO 8601 strings to ensure
        the result is directly serializable with json.dumps().

        Args:
            scan: Scan database model instance.
            fixes: List of ScanFix model instances.
            audit_entries: List of audit entry objects (with id, action,
                user_name, details, created_at).
            matterhorn_results: List of MatterhornResult model instances.
            department: Department model instance.

        Returns:
            Dictionary with scan, department, fixes, audit_trail,
            matterhorn_results, and summary sections.
        """
        mh_total = len(matterhorn_results)
        mh_passed = sum(1 for m in matterhorn_results if m.status == "pass")
        mh_failed = sum(1 for m in matterhorn_results if m.status == "fail")

        status_counts = _review_status_counts(fixes)
        deferral_counts = _deferral_status_counts(fixes)
        applied_count = sum(
            count
            for status, count in status_counts.items()
            if status in ACCEPTED_REVIEW_STATUSES
        )

        validator_result = _validator_result(mh_total, mh_passed, mh_failed)

        applied_rate = round(applied_count / len(fixes) * 100, 1) if fixes else 0.0
        machine_observations = [_machine_observation(fix) for fix in fixes]
        reviewer_decisions = [_reviewer_decision(fix) for fix in fixes]
        validator_observations = [
            {
                "checkpoint_id": m.checkpoint_id,
                "checkpoint_name": m.checkpoint_name,
                "status": m.status,
                "severity": _recorded(m.severity),
                "details": _recorded(m.details),
                "page_number": _recorded(m.page_number),
            }
            for m in matterhorn_results
        ]

        return {
            "report_generated_at": _isoformat(datetime.now(timezone.utc)),
            "scan": {
                "id": scan.id,
                "file_name": scan.file_name,
                "scan_type": _safe_str(scan.scan_type),
                "status": _safe_str(scan.status),
                "created_at": _isoformat(scan.created_at),
                "completed_at": _isoformat(scan.completed_at),
            },
            "source": _source_evidence(scan),
            "artifact": _artifact_evidence(scan),
            "department": {
                "name": department.name,
                "institution": department.institution,
            },
            "summary": {
                "total_issues": len(fixes),
                "total_fixes": len(fixes),
                "applied_count": applied_count,
                "applied_rate": applied_rate,
                "approved_count": status_counts["approved"]
                + status_counts["auto_approved"],
                "rejected_count": status_counts["rejected"],
                "review_status_counts": status_counts,
                "deferral_status_counts": deferral_counts,
                "matterhorn_total": mh_total,
                "matterhorn_passed": mh_passed,
                "matterhorn_failed": mh_failed,
                "validator_result": validator_result,
                "is_conformance_determination": False,
            },
            "machine_observations": machine_observations,
            "reviewer_decisions": reviewer_decisions,
            "fixes": machine_observations,
            "audit_trail": [
                {
                    "id": e.id,
                    "action": e.action,
                    "user_name": e.user_name,
                    "details": bounded_audit_details(e.details),
                    "created_at": _isoformat(e.created_at),
                }
                for e in audit_entries
            ],
            "validator_observations": validator_observations,
            "matterhorn_results": validator_observations,
            "limitations": (
                "This package records review evidence. It does not establish WCAG, "
                "PDF/UA, or legal conformance."
            ),
        }

    @staticmethod
    def generate_csv(
        scan: Any,
        fixes: list,
        audit_entries: list,
        matterhorn_results: list,
        department: Any,
    ) -> str:
        """Generate a CSV string with multiple sections.

        Sections are separated by blank rows and section headers.
        Sections included: Metadata, Issues & Fixes, Audit Trail,
        Matterhorn Results.

        Args:
            scan: Scan database model instance.
            fixes: List of ScanFix model instances.
            audit_entries: List of audit entry objects.
            matterhorn_results: List of MatterhornResult model instances.
            department: Department model instance.

        Returns:
            CSV content as a string.
        """
        report = AuditReportGenerator.generate_json(
            scan=scan,
            fixes=fixes,
            audit_entries=audit_entries,
            matterhorn_results=matterhorn_results,
            department=department,
        )
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["Accessibility Review Evidence"])
        writer.writerow(["Evidence Boundary", report["limitations"]])
        writer.writerow(["Department", department.name])
        writer.writerow(["Institution", department.institution])
        writer.writerow(["Scan ID", scan.id])
        writer.writerow(["File Name", scan.file_name])
        writer.writerow(["Scan Type", _safe_str(scan.scan_type)])
        writer.writerow(["Generated At", report["report_generated_at"]])
        for key, value in report["source"].items():
            writer.writerow([f"Source {key.replace('_', ' ').title()}", value])
        for key, value in report["artifact"].items():
            writer.writerow([f"Artifact {key.replace('_', ' ').title()}", value])
        writer.writerow([])

        writer.writerow(["Review State Summary"])
        writer.writerow(["Applied Count", report["summary"]["applied_count"]])
        for status, count in report["summary"]["review_status_counts"].items():
            writer.writerow([status, count])
        writer.writerow([])

        writer.writerow(["Machine Observations"])
        writer.writerow(
            [
                "Fix ID",
                "Category",
                "Severity",
                "Description",
                "Fix Method",
                "Confidence",
                "WCAG Criteria",
                "Page",
                "Source Kind",
                "Source Locator",
                "Verification Evidence",
            ]
        )
        for observation in report["machine_observations"]:
            writer.writerow(
                [
                    observation["id"],
                    observation["category"],
                    observation["severity"],
                    observation["description"],
                    observation["fix_method"],
                    observation["confidence"],
                    observation["wcag_criteria"],
                    observation["page_number"],
                    observation["source_kind"],
                    observation["source_locator"],
                    observation["verification_evidence"],
                ]
            )
        writer.writerow([])

        writer.writerow(["Reviewer Decisions"])
        writer.writerow(
            [
                "Fix ID",
                "Review Status",
                "Accepted",
                "Decision Source",
                "Reviewer ID",
                "Reviewer Name",
                "Reviewed At",
                "Review Notes",
                "Review Digest",
                "Approved Review Digest",
                "Deferral Lifecycle",
                "Deferral Owner",
                "Deferral Reason",
                "Deferral Expires At",
            ]
        )
        for decision in report["reviewer_decisions"]:
            writer.writerow(
                [
                    decision["fix_id"],
                    decision["review_status"],
                    decision["accepted"],
                    decision["decision_source"],
                    decision["reviewer_id"],
                    decision["reviewer_name"],
                    decision["reviewed_at"],
                    decision["review_notes"],
                    decision["review_digest"],
                    decision["approved_review_digest"],
                    (decision["deferral"]["lifecycle"] if decision["deferral"] else ""),
                    decision["deferral"]["owner"] if decision["deferral"] else "",
                    decision["deferral"]["reason"] if decision["deferral"] else "",
                    (
                        decision["deferral"]["expires_at"]
                        if decision["deferral"]
                        else ""
                    ),
                ]
            )
        writer.writerow([])

        # -- Audit Trail section --
        writer.writerow(["Audit Trail"])
        writer.writerow(["Entry ID", "Action", "User", "Details", "Timestamp"])
        for e in audit_entries:
            details_str = ""
            if e.details:
                # Flatten details dict to a readable string
                public_details = bounded_audit_details(e.details)
                details_str = "; ".join(f"{k}={v}" for k, v in public_details.items())
            writer.writerow(
                [
                    e.id,
                    e.action,
                    _safe_str(e.user_name, "System"),
                    details_str,
                    _isoformat(e.created_at),
                ]
            )
        writer.writerow([])

        writer.writerow(["Validator Observations"])
        writer.writerow(
            [
                "Checkpoint ID",
                "Checkpoint Name",
                "Status",
                "Severity",
                "Details",
                "Page",
            ]
        )
        for observation in report["validator_observations"]:
            writer.writerow(
                [
                    observation["checkpoint_id"],
                    observation["checkpoint_name"],
                    observation["status"],
                    observation["severity"],
                    observation["details"],
                    observation["page_number"],
                ]
            )

        return output.getvalue()

    @staticmethod
    def generate_pdf(
        scan: Any,
        fixes: list,
        audit_entries: list,
        matterhorn_results: list,
        department: Any,
    ) -> bytes:
        """Generate branded accessibility review evidence.

        The report includes:
        1. Header with Aelira logo (gracefully skipped if missing)
        2. Executive Summary
        3. Issues Found table
        4. Fixes Applied table
        5. Review History
        6. Matterhorn Results (if applicable)
        7. Scope and limitations

        Args:
            scan: Scan database model instance.
            fixes: List of ScanFix model instances.
            audit_entries: List of audit entry objects.
            matterhorn_results: List of MatterhornResult model instances.
            department: Department model instance.

        Returns:
            PDF file content as bytes.
        """
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=36,
        )

        story: list = []
        styles = getSampleStyleSheet()

        # Extract scan type string (handles both enum and plain string)
        scan_type_raw = scan.scan_type
        scan_type_str = (
            (
                scan_type_raw.value
                if hasattr(scan_type_raw, "value")
                else str(scan_type_raw)
            ).lower()
            if scan_type_raw
            else ""
        )
        is_web_scan = scan_type_str in ("web", "website")

        # -- Custom styles --
        title_style = ParagraphStyle(
            "AuditTitle",
            parent=styles["Heading1"],
            fontSize=22,
            textColor=colors.HexColor(_DARK_GRAY),
            spaceAfter=20,
            alignment=TA_CENTER,
        )
        subtitle_style = ParagraphStyle(
            "AuditSubtitle",
            parent=styles["Normal"],
            fontSize=11,
            textColor=colors.HexColor(_MEDIUM_GRAY),
            spaceAfter=16,
            alignment=TA_CENTER,
        )
        heading_style = ParagraphStyle(
            "AuditHeading",
            parent=styles["Heading2"],
            fontSize=15,
            textColor=colors.HexColor(_BLUE_DARK),
            spaceAfter=10,
            spaceBefore=14,
        )
        body_style = styles["Normal"]

        # -- Header / Logo --
        # Try multiple possible logo paths
        logo_paths = [
            os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "assets",
                "aelira-main-logo-pdf.png",
            ),
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "assets",
                "aelira-main-logo-pdf.png",
            ),
        ]
        for logo_path in logo_paths:
            if os.path.exists(logo_path):
                try:
                    logo = Image(
                        logo_path,
                        width=2.5 * inch,
                        height=0.6 * inch,
                        kind="proportional",
                    )
                    logo.hAlign = "LEFT"
                    story.append(logo)
                    story.append(Spacer(1, 0.25 * inch))
                    break
                except Exception:
                    pass

        settings = get_settings()
        publisher_name = (settings.brand_name or "").strip() or "Accessibility Review"
        support_email = (settings.support_email or "").strip()
        if "example.com" in support_email.lower() or support_email.lower().endswith(
            ".invalid"
        ):
            support_email = ""

        # -- Title --
        story.append(Paragraph("Accessibility Review Evidence", title_style))
        story.append(
            Paragraph(
                f"{department.name} &mdash; {department.institution}",
                subtitle_style,
            )
        )

        report_date = datetime.now(timezone.utc).strftime("%B %d, %Y")
        source_evidence = _source_evidence(scan)
        artifact_evidence = _artifact_evidence(scan)

        # -- Metadata table --
        meta_data = [
            ["Report Date:", report_date],
            ["Document:", scan.file_name],
            ["Scan Type:", scan_type_str.upper() if scan_type_str else "N/A"],
            ["Department:", department.name],
            ["Institution:", department.institution],
            ["Source Document ID:", source_evidence["document_id"]],
            ["Source SHA-256:", source_evidence["sha256"]],
            ["Artifact:", artifact_evidence.get("id", "unavailable")],
            ["Artifact SHA-256:", artifact_evidence.get("sha256", "unavailable")],
            ["Generated By:", publisher_name],
        ]
        meta_table = Table(meta_data, colWidths=[1.8 * inch, 4.2 * inch])
        meta_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor(_LIGHT_GRAY)),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor(_DARK_GRAY)),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                ]
            )
        )
        story.append(meta_table)
        story.append(Spacer(1, 0.3 * inch))

        # -- Compute summary statistics --
        mh_total = len(matterhorn_results)
        mh_passed = sum(1 for m in matterhorn_results if m.status == "pass")
        mh_failed = sum(1 for m in matterhorn_results if m.status == "fail")
        status_counts = _review_status_counts(fixes)
        deferral_counts = _deferral_status_counts(fixes)
        applied_count = sum(
            count
            for status, count in status_counts.items()
            if status in ACCEPTED_REVIEW_STATUSES
        )
        total_fixes = len(fixes)
        applied_rate = (
            round(applied_count / total_fixes * 100, 1) if total_fixes else 0.0
        )
        validator_result = _validator_result(mh_total, mh_passed, mh_failed)

        # ==========================================================
        # Section 1: Executive Summary
        # ==========================================================
        story.append(Paragraph("Executive Summary", heading_style))
        summary_text = (
            f"This report records the accessibility review evidence stored for "
            f"<b>{scan.file_name}</b> scanned on "
            f"{_isoformat(scan.created_at)[:10] if scan.created_at else 'N/A'}."
            f"<br/><br/>"
            f"<b>Key Metrics:</b><br/>"
            f"&bull; Total Issues Found: <b>{total_fixes}</b><br/>"
            f"&bull; Durably Accepted: <b>{applied_count}</b> ({applied_rate}%)<br/>"
            f"&bull; Pending: <b>{status_counts['pending']}</b><br/>"
            f"&bull; Approved: <b>{status_counts['approved']}</b><br/>"
            f"&bull; Edited: <b>{status_counts['edited']}</b><br/>"
            f"&bull; Auto Approved: <b>{status_counts['auto_approved']}</b><br/>"
            f"&bull; Rejected: <b>{status_counts['rejected']}</b><br/>"
            f"&bull; Unresolved: <b>{status_counts['unresolved']}</b><br/>"
            f"&bull; Unavailable: <b>{status_counts['unavailable']}</b><br/>"
            f"&bull; Active Deferrals: <b>{deferral_counts['active']}</b><br/>"
            f"&bull; Expired Deferrals: <b>{deferral_counts['expired']}</b><br/>"
            f"&bull; Revoked Deferrals: <b>{deferral_counts['revoked']}</b><br/>"
            f"&bull; Resolved Deferrals: <b>{deferral_counts['resolved']}</b><br/>"
            f"&bull; Matterhorn Checkpoints: <b>{mh_passed}/{mh_total} passed</b><br/>"
            f"&bull; Recorded Validator Result: <b>{_validator_result_label(validator_result)}</b>"
        )
        story.append(Paragraph(summary_text, body_style))
        story.append(Spacer(1, 0.3 * inch))

        # ==========================================================
        # Section 2: Machine observations
        # ==========================================================
        story.append(Paragraph("Machine Observations", heading_style))

        if fixes:
            issue_header = ["#", "Category", "Severity", "Description", "Page", "WCAG"]
            issue_rows = [issue_header]
            for i, f in enumerate(fixes, 1):
                issue_rows.append(
                    [
                        str(i),
                        f.category,
                        f.severity,
                        Paragraph(f.description[:80], body_style),
                        _safe_str(f.page_number, "-"),
                        _safe_str(f.wcag_criteria, "-"),
                    ]
                )

            issue_table = Table(
                issue_rows,
                colWidths=[
                    0.4 * inch,
                    1.0 * inch,
                    0.8 * inch,
                    2.5 * inch,
                    0.5 * inch,
                    0.7 * inch,
                ],
            )
            table_style_data = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_BLUE_PRIMARY)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            # Apply severity-based row coloring
            for row_idx, f in enumerate(fixes, 1):
                severity_color = _SEVERITY_COLORS.get(f.severity)
                if severity_color:
                    table_style_data.append(
                        (
                            "BACKGROUND",
                            (0, row_idx),
                            (-1, row_idx),
                            colors.HexColor(severity_color),
                        )
                    )

            issue_table.setStyle(TableStyle(table_style_data))
            story.append(issue_table)
        else:
            story.append(
                Paragraph(
                    "<i>No recorded finding or fix entries were available for this export.</i>",
                    body_style,
                )
            )

        story.append(Spacer(1, 0.3 * inch))

        # ==========================================================
        # Section 3: Reviewer decisions
        # ==========================================================
        story.append(Paragraph("Reviewer Decisions", heading_style))

        if fixes:
            fix_header = [
                "#",
                "Status",
                "Source",
                "Reviewer",
                "Deferral",
                "Review Digest",
            ]
            fix_rows = [fix_header]
            for i, f in enumerate(fixes, 1):
                decision = _reviewer_decision(f)
                status_display = decision["review_status"].replace("_", " ").title()
                fix_rows.append(
                    [
                        str(i),
                        status_display,
                        decision["decision_source"].replace("_", " ").title(),
                        Paragraph(str(decision["reviewer_name"])[:60], body_style),
                        (
                            decision["deferral"]["lifecycle"].replace("_", " ").title()
                            if decision["deferral"]
                            else "-"
                        ),
                        Paragraph(str(decision["review_digest"])[:72], body_style),
                    ]
                )

            fix_table = Table(
                fix_rows,
                colWidths=[
                    0.3 * inch,
                    0.8 * inch,
                    0.8 * inch,
                    1.0 * inch,
                    0.8 * inch,
                    2.1 * inch,
                ],
            )
            fix_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_BLUE_DARK)),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#F9FAFB")],
                        ),
                    ]
                )
            )
            story.append(fix_table)
        else:
            story.append(
                Paragraph("<i>No reviewer decisions were available.</i>", body_style)
            )

        story.append(Spacer(1, 0.3 * inch))

        # ==========================================================
        # Section 3b: Web Remediation Guide (web scans only)
        # ==========================================================
        if is_web_scan:
            # Filter for approved/edited fixes only
            approved_fixes = [
                f
                for f in fixes
                if f.review_status in ("approved", "edited", "auto_approved")
            ]

            if approved_fixes:
                story.append(Paragraph("Web Remediation Guide", heading_style))
                story.append(
                    Paragraph(
                        "The following approved fixes are grouped by page URL. "
                        "Apply the <font color='#059669'><b>After</b></font> "
                        "code to replace the "
                        "<font color='#DC2626'><b>Before</b></font> code on each page.",
                        body_style,
                    )
                )
                story.append(Spacer(1, 0.15 * inch))

                # Group fixes by page URL from the location field
                from collections import OrderedDict

                url_groups: OrderedDict[str, list] = OrderedDict()
                for f in approved_fixes:
                    loc = _safe_str(getattr(f, "location", None), "")
                    if " | " in loc:
                        page_url = loc.split(" | ", 1)[0].strip()
                    elif loc:
                        page_url = loc.strip()
                    else:
                        page_url = "(unknown page)"
                    url_groups.setdefault(page_url, []).append(f)

                # Styles for before/after code snippets
                before_style = ParagraphStyle(
                    "WebFixBefore",
                    parent=body_style,
                    fontSize=7,
                    fontName="Courier",
                    textColor=colors.HexColor("#991B1B"),
                    backColor=colors.HexColor("#FEF2F2"),
                    leftIndent=12,
                    rightIndent=12,
                    spaceBefore=2,
                    spaceAfter=2,
                )
                after_style = ParagraphStyle(
                    "WebFixAfter",
                    parent=body_style,
                    fontSize=7,
                    fontName="Courier",
                    textColor=colors.HexColor("#065F46"),
                    backColor=colors.HexColor("#F0FDF4"),
                    leftIndent=12,
                    rightIndent=12,
                    spaceBefore=2,
                    spaceAfter=6,
                )
                url_heading_style = ParagraphStyle(
                    "WebUrlHeading",
                    parent=body_style,
                    fontSize=10,
                    fontName="Helvetica-Bold",
                    textColor=colors.HexColor(_BLUE_PRIMARY),
                    spaceBefore=10,
                    spaceAfter=4,
                )
                fix_label_style = ParagraphStyle(
                    "WebFixLabel",
                    parent=body_style,
                    fontSize=8,
                    textColor=colors.HexColor(_DARK_GRAY),
                    spaceBefore=4,
                    spaceAfter=2,
                )

                for page_url, page_fixes in url_groups.items():
                    # Truncate long URLs for display
                    display_url = (
                        page_url if len(page_url) <= 80 else page_url[:77] + "..."
                    )
                    story.append(Paragraph(display_url, url_heading_style))

                    for f in page_fixes:
                        wcag = _safe_str(getattr(f, "wcag_criteria", None), "N/A")
                        desc = _safe_str(f.description, "")[:80]
                        severity = _safe_str(getattr(f, "severity", None), "")
                        severity_label = f" [{severity.upper()}]" if severity else ""
                        story.append(
                            Paragraph(
                                f"<b>{wcag}</b>{severity_label} &mdash; {desc}",
                                fix_label_style,
                            )
                        )

                        # Show element selector if available from location
                        loc = _safe_str(getattr(f, "location", None), "")
                        if " | " in loc:
                            selector = loc.split(" | ", 1)[1].strip()
                            if selector:
                                selector_escaped = (
                                    selector[:80]
                                    .replace("&", "&amp;")
                                    .replace("<", "&lt;")
                                    .replace(">", "&gt;")
                                )
                                story.append(
                                    Paragraph(
                                        f"<i>Element:</i> <font face='Courier'>{selector_escaped}</font>",
                                        fix_label_style,
                                    )
                                )

                        # Before (original) code
                        original = _safe_str(
                            getattr(f, "original_content", None), "(not available)"
                        )
                        # Escape XML/HTML special chars and truncate
                        original_escaped = (
                            original[:120]
                            .replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                        )
                        if len(original) > 120:
                            original_escaped += "..."
                        story.append(
                            Paragraph(
                                f"<b>Before:</b> {original_escaped}",
                                before_style,
                            )
                        )

                        # After (fixed) code
                        fixed = _safe_str(
                            getattr(f, "fixed_content", None), "(not available)"
                        )
                        fixed_escaped = (
                            fixed[:120]
                            .replace("&", "&amp;")
                            .replace("<", "&lt;")
                            .replace(">", "&gt;")
                        )
                        if len(fixed) > 120:
                            fixed_escaped += "..."
                        story.append(
                            Paragraph(
                                f"<b>After:</b> {fixed_escaped}",
                                after_style,
                            )
                        )

                story.append(Spacer(1, 0.3 * inch))

        # ==========================================================
        # Section 4: Review History
        # ==========================================================
        story.append(Paragraph("Review History", heading_style))

        if audit_entries:
            audit_header = ["Timestamp", "Action", "User", "Details"]
            audit_rows = [audit_header]
            for e in audit_entries:
                details_str = ""
                if e.details:
                    public_details = bounded_audit_details(e.details)
                    details_str = "; ".join(
                        f"{k}={v}" for k, v in public_details.items()
                    )
                audit_rows.append(
                    [
                        _isoformat(e.created_at)[:19].replace("T", " "),
                        e.action.replace("_", " ").title(),
                        _safe_str(e.user_name, "System"),
                        Paragraph(
                            details_str[:100] if details_str else "-", body_style
                        ),
                    ]
                )

            audit_table = Table(
                audit_rows,
                colWidths=[1.4 * inch, 1.2 * inch, 1.0 * inch, 2.3 * inch],
            )
            audit_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_MEDIUM_GRAY)),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#F9FAFB")],
                        ),
                    ]
                )
            )
            story.append(audit_table)
        else:
            story.append(
                Paragraph("<i>No review actions have been recorded.</i>", body_style)
            )

        story.append(Spacer(1, 0.3 * inch))

        # ==========================================================
        # Section 5: Matterhorn Results
        # ==========================================================
        if matterhorn_results:
            story.append(Paragraph("Matterhorn Protocol Results", heading_style))

            # Summary line
            mh_summary = (
                f"<b>{mh_passed}</b> of <b>{mh_total}</b> checkpoints passed. "
                f"<b>{mh_failed}</b> failed."
            )
            story.append(Paragraph(mh_summary, body_style))
            story.append(Spacer(1, 0.15 * inch))

            mh_header = ["Checkpoint", "Name", "Status", "Severity", "Page"]
            mh_rows = [mh_header]
            for m in matterhorn_results:
                status_icon = (
                    "PASS"
                    if m.status == "pass"
                    else "FAIL" if m.status == "fail" else "WARN"
                )
                mh_rows.append(
                    [
                        m.checkpoint_id,
                        Paragraph(m.checkpoint_name[:60], body_style),
                        status_icon,
                        _safe_str(m.severity, "-"),
                        _safe_str(m.page_number, "-"),
                    ]
                )

            mh_table = Table(
                mh_rows,
                colWidths=[0.9 * inch, 2.5 * inch, 0.7 * inch, 0.8 * inch, 0.5 * inch],
            )
            mh_style_data = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#059669")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            # Color-code pass/fail rows
            for row_idx, m in enumerate(matterhorn_results, 1):
                if m.status == "pass":
                    mh_style_data.append(
                        (
                            "BACKGROUND",
                            (0, row_idx),
                            (-1, row_idx),
                            colors.HexColor("#D1FAE5"),
                        )
                    )
                elif m.status == "fail":
                    mh_style_data.append(
                        (
                            "BACKGROUND",
                            (0, row_idx),
                            (-1, row_idx),
                            colors.HexColor("#FEE2E2"),
                        )
                    )
                elif m.status == "warning":
                    mh_style_data.append(
                        (
                            "BACKGROUND",
                            (0, row_idx),
                            (-1, row_idx),
                            colors.HexColor("#FEF3C7"),
                        )
                    )

            mh_table.setStyle(TableStyle(mh_style_data))
            story.append(mh_table)
            story.append(Spacer(1, 0.3 * inch))

        # ==========================================================
        # Section 6: Scope and Limitations
        # ==========================================================
        story.append(Paragraph("Scope and Limitations", heading_style))

        limitations_text = (
            f"This export records the automated scan, Matterhorn checkpoint results, "
            f"and review actions stored for <b>{scan.file_name}</b>.<br/><br/>"
            f"<b>Recorded Validator Result:</b> {_validator_result_label(validator_result)} "
            f"({mh_failed} failed of {mh_total} recorded checkpoints).<br/><br/>"
            "Matterhorn results are format-validator evidence. Automated checks and "
            "recorded review actions do not determine WCAG conformance or legal "
            "compliance, and they do not replace manual testing with assistive "
            "technology or review of requirements outside the recorded checks."
        )

        story.append(Paragraph(limitations_text, body_style))

        # -- Footer --
        story.append(Spacer(1, 0.5 * inch))
        footer_style = ParagraphStyle(
            "AuditFooter",
            parent=body_style,
            fontSize=9,
            textColor=colors.HexColor(_MEDIUM_GRAY),
            alignment=TA_CENTER,
        )
        footer_text = f"<i>Generated by {publisher_name} on {report_date}."
        if support_email:
            footer_text += f" For questions, contact {support_email}."
        footer_text += "</i>"
        story.append(Paragraph(footer_text, footer_style))

        # Build and return
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
