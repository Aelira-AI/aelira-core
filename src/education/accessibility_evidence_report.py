"""Bounded accessibility evidence reports for department scan history.

The report deliberately separates recorded evidence from any legal or standards
determination. Missing provenance and review data are reported as limitations.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..db.models import (
    Department,
    IssueTracking,
    MatterhornResult,
    Scan,
    ScanFix,
    ScanResult,
    ScanStatus,
)
from .deadline_config import DeadlineService


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _public_setting(value: str | None) -> str | None:
    """Keep configured public metadata while rejecting placeholder domains."""

    if not value:
        return None
    cleaned = value.strip()
    lowered = cleaned.lower()
    if not cleaned or "example.com" in lowered or lowered.endswith(".invalid"):
        return None
    return cleaned


class AccessibilityEvidenceReport:
    """Collect and render one truthful department evidence snapshot."""

    REPORT_KIND = "accessibility_evidence_report"
    SCHEMA_VERSION = 1

    @classmethod
    def collect(cls, db: Session, department_id: str) -> dict[str, Any]:
        department = db.query(Department).filter(Department.id == department_id).first()
        if not department:
            raise ValueError("Department not found")

        scans = db.query(Scan).filter(Scan.department_id == department_id).all()
        scan_ids = [scan.id for scan in scans]
        results = (
            db.query(ScanResult).filter(ScanResult.scan_id.in_(scan_ids)).all()
            if scan_ids
            else []
        )
        tracked = (
            db.query(IssueTracking)
            .filter(
                IssueTracking.department_id == department_id,
                IssueTracking.scan_id.in_(scan_ids),
            )
            .all()
            if scan_ids
            else []
        )
        fixes = (
            db.query(ScanFix).filter(ScanFix.scan_id.in_(scan_ids)).all()
            if scan_ids
            else []
        )
        validator_results = (
            db.query(MatterhornResult)
            .filter(MatterhornResult.scan_id.in_(scan_ids))
            .all()
            if scan_ids
            else []
        )

        completed_scans = [
            scan
            for scan in scans
            if _enum_value(scan.status) == ScanStatus.COMPLETED.value
        ]
        hashes = {scan.file_hash for scan in scans if scan.file_hash}
        content_types = Counter(_enum_value(scan.scan_type) for scan in scans)
        pages_or_slides = sum(scan.pages or 0 for scan in scans)
        scan_dates = [scan.created_at for scan in scans if scan.created_at]

        scores = [
            float(result.compliance_score)
            for result in results
            if result.compliance_score is not None
        ]
        engines = sorted(
            {
                str(engine)
                for result in results
                for engine in (result.engines_used or [])
                if engine
            }
        )
        scan_modes = sorted(
            {str(result.scan_mode) for result in results if result.scan_mode}
        )
        wcag_levels = sorted(
            {str(result.wcag_level) for result in results if result.wcag_level}
        )

        severity_counts = Counter(
            {
                "critical": sum(result.critical_issues or 0 for result in results),
                "high": sum(result.high_issues or 0 for result in results),
                "medium": sum(result.medium_issues or 0 for result in results),
                "low": sum(result.low_issues or 0 for result in results),
            }
        )
        raw_findings: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result.issues, list):
                raw_findings.extend(
                    item for item in result.issues if isinstance(item, dict)
                )
        raw_total = max(sum(severity_counts.values()), len(raw_findings))
        tracked_statuses = Counter(_enum_value(issue.status) for issue in tracked)

        if not results:
            tracker_coverage = "unavailable"
        elif raw_total == 0 or len(tracked) >= raw_total:
            tracker_coverage = "complete"
        elif not tracked:
            tracker_coverage = "unavailable"
        else:
            tracker_coverage = "partial"

        pending_manual_reviews = sum(
            1
            for fix in fixes
            if fix.needs_review and _enum_value(fix.review_status).lower() == "pending"
        )
        reviewed_fixes = sum(
            1
            for fix in fixes
            if _enum_value(fix.review_status).lower() in {"approved", "rejected"}
        )
        validator_counts = Counter(
            _enum_value(result.status).lower() for result in validator_results
        )

        closed_statuses = {"RESOLVED", "WONT_FIX", "FALSE_POSITIVE"}
        tracked_closed = sum(
            count
            for status, count in tracked_statuses.items()
            if status.upper() in closed_statuses
        )
        tracked_open = sum(tracked_statuses.values()) - tracked_closed
        unresolved_total = (
            tracked_open
            if tracker_coverage == "complete"
            else max(raw_total - tracked_closed, tracked_open)
        )

        if not results:
            verification_status = "not_assessed"
            verification_statement = "No recorded automated scan result is available."
        elif pending_manual_reviews:
            verification_status = "human_review_incomplete"
            verification_statement = (
                "Automated scan evidence is present and some fixes still require "
                "human review."
            )
        elif reviewed_fixes:
            verification_status = "human_review_recorded"
            verification_statement = (
                "Automated scan evidence and recorded human review actions are present. "
                "The evidence remains bounded to the recorded checks."
            )
        else:
            verification_status = "automated_evidence_only"
            verification_statement = "Automated scan evidence is present. Human review has not been recorded."

        deadline = DeadlineService.for_department(department)

        limitations = [
            "Coverage includes only scans recorded in this deployment and is not a measure of the institution's full content corpus.",
            "Automated scan scores summarize configured checks and do not determine whether content meets an accessibility standard or legal requirement.",
            "Repeated scans may be counted separately; this report does not identify each document's latest verified state.",
            "Historical engine versions and ruleset versions were not recorded and are unavailable for this report.",
        ]
        if tracker_coverage != "complete":
            limitations.append(
                "Issue-tracker coverage is not complete; raw scan findings remain the source for total finding counts."
            )
        if not validator_results:
            limitations.append(
                "No stored format-validator checkpoint evidence is available."
            )
        if not reviewed_fixes:
            limitations.append("No completed human review actions are recorded.")

        settings = get_settings()
        support = {
            key: value
            for key, value in {
                "brand_name": _public_setting(settings.brand_name),
                "public_website_url": _public_setting(settings.public_website_url),
                "support_email": _public_setting(settings.support_email),
            }.items()
            if value is not None
        }

        representative_findings = []
        for item in raw_findings[:10]:
            representative_findings.append(
                {
                    "type": item.get("type") or item.get("code") or "unclassified",
                    "severity": item.get("severity") or "unknown",
                    "criterion": item.get("wcag") or item.get("criterion"),
                }
            )

        return {
            "schema_version": cls.SCHEMA_VERSION,
            "report_kind": cls.REPORT_KIND,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "subject": {
                "department_name": department.name,
                "institution": department.institution,
            },
            "coverage": {
                "scope": "scanned_content_only",
                "total_scans": len(scans),
                "completed_scans": len(completed_scans),
                "unique_hashed_assets": len(hashes),
                "unhashed_scan_count": sum(1 for scan in scans if not scan.file_hash),
                "pages_or_slides": pages_or_slides,
                "content_types": dict(sorted(content_types.items())),
                "earliest_scan_at": _iso(min(scan_dates)) if scan_dates else None,
                "latest_scan_at": _iso(max(scan_dates)) if scan_dates else None,
                "corpus_denominator": None,
            },
            "methodology": {
                "scan_modes": scan_modes,
                "engines_used": engines,
                "engine_versions": [],
                "ruleset_version": None,
                "recorded_wcag_levels": wcag_levels,
                "automated_checks_present": bool(results),
                "validator_evidence_present": bool(validator_results),
                "human_review_present": bool(reviewed_fixes),
            },
            "score": {
                "label": "automated_scan_score",
                "average": round(sum(scores) / len(scores), 2) if scores else None,
                "minimum": min(scores) if scores else None,
                "maximum": max(scores) if scores else None,
                "is_conformance_determination": False,
            },
            "unresolved_findings": {
                "total": unresolved_total,
                "by_severity": dict(severity_counts),
                "by_status": dict(sorted(tracked_statuses.items())),
                "tracker_coverage": tracker_coverage,
                "representative_findings": representative_findings,
            },
            "verification": {
                "status": verification_status,
                "automated_scan_count": len(results),
                "validator_checkpoint_counts": dict(sorted(validator_counts.items())),
                "pending_manual_reviews": pending_manual_reviews,
                "reviewed_fixes": reviewed_fixes,
                "statement": verification_statement,
            },
            "standard": {
                "applicability": deadline.applicability,
                "framework_code": deadline.framework_code,
                "framework_name": deadline.framework_name,
                "target_standard": deadline.standard,
                "deadline_date": (
                    deadline.deadline_date.isoformat()
                    if deadline.deadline_date is not None
                    else None
                ),
                "deadline_label": deadline.deadline_label,
                "has_deadline": deadline.has_deadline,
                "message": deadline.message,
                "applicability_source": "department_configuration",
            },
            "limitations": limitations,
            "support": support,
        }

    @classmethod
    def generate(cls, db: Session, department_id: str) -> tuple[dict[str, Any], bytes]:
        report = cls.collect(db, department_id)
        return report, cls.render(report)

    @staticmethod
    def render(report: dict[str, Any]) -> bytes:
        """Render a deterministic PDF from the canonical evidence dictionary."""

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.65 * inch,
            leftMargin=0.65 * inch,
            topMargin=0.6 * inch,
            bottomMargin=0.6 * inch,
            title="Accessibility Evidence Report",
        )
        styles = getSampleStyleSheet()
        title = ParagraphStyle(
            "EvidenceTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            fontSize=23,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=8,
        )
        subtitle = ParagraphStyle(
            "EvidenceSubtitle",
            parent=styles["Normal"],
            alignment=TA_CENTER,
            textColor=colors.HexColor("#4B5563"),
            leading=14,
            spaceAfter=16,
        )
        heading = ParagraphStyle(
            "EvidenceHeading",
            parent=styles["Heading2"],
            fontSize=14,
            textColor=colors.HexColor("#374151"),
            spaceBefore=12,
            spaceAfter=7,
        )
        body = ParagraphStyle(
            "EvidenceBody",
            parent=styles["BodyText"],
            fontSize=9.5,
            leading=13,
            spaceAfter=5,
        )

        story = [
            Paragraph("Accessibility Evidence Report", title),
            Paragraph(
                "Bounded evidence from recorded scans. This report does not determine whether the subject meets an accessibility standard or legal requirement.",
                subtitle,
            ),
        ]

        subject = report["subject"]
        story.extend(
            [
                Paragraph(escape(str(subject["department_name"])), styles["Heading1"]),
                Paragraph(escape(str(subject["institution"])), body),
                Paragraph(f"Generated: {escape(str(report['generated_at']))}", body),
            ]
        )

        def add_table(section: str, rows: list[tuple[str, Any]]) -> None:
            story.append(Paragraph(section, heading))
            data = [[Paragraph("Field", body), Paragraph("Recorded evidence", body)]]
            for label, value in rows:
                if isinstance(value, (dict, list)):
                    value = (
                        ", ".join(f"{key}: {item}" for key, item in value.items())
                        if isinstance(value, dict)
                        else ", ".join(map(str, value))
                    )
                if value in (None, "", []):
                    value = "Unavailable"
                data.append(
                    [
                        Paragraph(escape(str(label)), body),
                        Paragraph(escape(str(value)), body),
                    ]
                )
            table = Table(data, colWidths=[2.0 * inch, 4.7 * inch], repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E5E7EB")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D1D5DB")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ]
                )
            )
            story.append(table)

        coverage = report["coverage"]
        add_table(
            "Coverage",
            [
                ("Scope", coverage["scope"]),
                ("Total scans", coverage["total_scans"]),
                ("Completed scans", coverage["completed_scans"]),
                ("Unique hashed assets", coverage["unique_hashed_assets"]),
                ("Unhashed scans", coverage["unhashed_scan_count"]),
                ("Pages or slides", coverage["pages_or_slides"]),
                ("Content types", coverage["content_types"]),
                ("Earliest scan", coverage["earliest_scan_at"]),
                ("Latest scan", coverage["latest_scan_at"]),
                ("Institution corpus denominator", coverage["corpus_denominator"]),
            ],
        )

        methodology = report["methodology"]
        add_table(
            "Methodology",
            [
                ("Scan modes", methodology["scan_modes"]),
                ("Engines used", methodology["engines_used"]),
                ("Engine versions", methodology["engine_versions"]),
                ("Ruleset version", methodology["ruleset_version"]),
                ("Recorded target levels", methodology["recorded_wcag_levels"]),
                ("Automated checks present", methodology["automated_checks_present"]),
                (
                    "Validator evidence present",
                    methodology["validator_evidence_present"],
                ),
                ("Human review present", methodology["human_review_present"]),
            ],
        )

        score = report["score"]
        add_table(
            "Automated Scan Score",
            [
                ("Average", score["average"]),
                ("Minimum", score["minimum"]),
                ("Maximum", score["maximum"]),
                (
                    "Conformance determination",
                    score["is_conformance_determination"],
                ),
            ],
        )

        findings = report["unresolved_findings"]
        add_table(
            "Unresolved Findings",
            [
                ("Raw finding total", findings["total"]),
                ("By severity", findings["by_severity"]),
                ("Tracked by status", findings["by_status"]),
                ("Tracker coverage", findings["tracker_coverage"]),
            ],
        )

        verification = report["verification"]
        add_table(
            "Verification",
            [
                ("Status", verification["status"]),
                ("Automated scan count", verification["automated_scan_count"]),
                ("Validator checkpoints", verification["validator_checkpoint_counts"]),
                ("Pending manual reviews", verification["pending_manual_reviews"]),
                ("Reviewed fixes", verification["reviewed_fixes"]),
                ("Statement", verification["statement"]),
            ],
        )

        story.append(PageBreak())
        standard = report["standard"]
        standard_rows = [
            ("Applicability", standard.get("applicability")),
            ("Framework code", standard["framework_code"]),
            ("Framework name", standard["framework_name"]),
            ("Target standard", standard["target_standard"]),
        ]
        if standard.get("has_deadline") and standard.get("deadline_date"):
            standard_rows.extend(
                [
                    ("Deadline label", standard.get("deadline_label")),
                    ("Deadline date", standard["deadline_date"]),
                ]
            )
        standard_rows.extend(
            [
                ("Guidance", standard.get("message")),
                ("Applicability source", standard["applicability_source"]),
            ]
        )
        add_table(
            "Applicable Standard Metadata",
            standard_rows,
        )

        story.append(Paragraph("Limitations", heading))
        for limitation in report["limitations"]:
            story.append(Paragraph(f"• {escape(str(limitation))}", body))

        support = report.get("support") or {}
        if support:
            story.append(Spacer(1, 10))
            add_table(
                "Configured Publisher Metadata",
                [
                    (key.replace("_", " ").title(), value)
                    for key, value in support.items()
                ],
            )

        doc.build(story)
        pdf = buffer.getvalue()
        buffer.close()
        return pdf
