"""Audit trail export report generator.

Generates compliance audit reports in JSON, CSV, and PDF formats for
the review workflow. Used by the GET /reviews/{scan_id}/audit/export endpoint.

PDF reports include:
- Executive Summary with fix/approval rates
- Issues Found table
- Fixes Applied table
- Review History (chronological audit log)
- Matterhorn Protocol results (for PDF scans)
- WCAG Conformance Statement
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


def _compute_compliance_level(total: int, failed: int) -> str:
    """Determine compliance level from Matterhorn results.

    Delegates to the canonical implementation in review_routes to avoid
    duplication.  Falls back to inline logic if the import fails (e.g.
    during isolated unit tests).
    """
    try:
        from src.api.review_routes import compute_compliance_level

        return compute_compliance_level(total, failed)
    except ImportError:
        pass
    if total == 0:
        return "not_validated"
    if failed == 0:
        return "compliant"
    if failed <= total * 0.2:
        return "partial"
    return "non_compliant"


def _compliance_label(level: str) -> str:
    """Human-readable compliance level label."""
    labels = {
        "compliant": "WCAG 2.1 Level AA Compliant",
        "partial": "Partial Compliance — Minor Issues Remain",
        "non_compliant": "Non-Compliant — Significant Issues Found",
        "not_validated": "Not Validated — No Matterhorn Checkpoints Run",
    }
    return labels.get(level, level)


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

        approved_count = sum(
            1 for f in fixes if f.review_status in ("approved", "auto_approved")
        )
        rejected_count = sum(1 for f in fixes if f.review_status == "rejected")

        compliance_level = _compute_compliance_level(mh_total, mh_failed)

        applied_count = sum(1 for f in fixes if f.review_status != "rejected")
        fix_rate = round(applied_count / len(fixes) * 100, 1) if fixes else 0.0
        approval_rate = round(approved_count / len(fixes) * 100, 1) if fixes else 0.0

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
            "department": {
                "name": department.name,
                "institution": department.institution,
            },
            "summary": {
                "total_issues": len(fixes),
                "total_fixes": len(fixes),
                "approved_count": approved_count,
                "rejected_count": rejected_count,
                "fix_rate": fix_rate,
                "approval_rate": approval_rate,
                "matterhorn_total": mh_total,
                "matterhorn_passed": mh_passed,
                "matterhorn_failed": mh_failed,
                "compliance_level": compliance_level,
            },
            "fixes": [
                {
                    "id": f.id,
                    "category": f.category,
                    "severity": f.severity,
                    "description": f.description,
                    "fix_method": f.fix_method,
                    "confidence": f.confidence,
                    "review_status": f.review_status,
                    "review_notes": f.review_notes,
                    "wcag_criteria": f.wcag_criteria,
                    "page_number": f.page_number,
                    "created_at": _isoformat(getattr(f, "created_at", None)),
                }
                for f in fixes
            ],
            "audit_trail": [
                {
                    "id": e.id,
                    "action": e.action,
                    "user_name": e.user_name,
                    "details": e.details,
                    "created_at": _isoformat(e.created_at),
                }
                for e in audit_entries
            ],
            "matterhorn_results": [
                {
                    "checkpoint_id": m.checkpoint_id,
                    "checkpoint_name": m.checkpoint_name,
                    "status": m.status,
                    "severity": m.severity,
                    "details": m.details,
                    "page_number": m.page_number,
                }
                for m in matterhorn_results
            ],
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
        output = io.StringIO()
        writer = csv.writer(output)

        # -- Metadata section --
        writer.writerow(["Audit Trail Export"])
        writer.writerow(["Department", department.name])
        writer.writerow(["Institution", department.institution])
        writer.writerow(["Scan ID", scan.id])
        writer.writerow(["File Name", scan.file_name])
        writer.writerow(["Scan Type", _safe_str(scan.scan_type)])
        writer.writerow(["Generated At", _isoformat(datetime.now(timezone.utc))])
        writer.writerow([])

        # -- Issues & Fixes section --
        writer.writerow(["Issues & Fixes"])
        writer.writerow(
            [
                "Fix ID",
                "Category",
                "Severity",
                "Description",
                "Fix Method",
                "Confidence",
                "Review Status",
                "Review Notes",
                "WCAG Criteria",
                "Page",
            ]
        )
        for f in fixes:
            writer.writerow(
                [
                    f.id,
                    f.category,
                    f.severity,
                    f.description,
                    f.fix_method,
                    f.confidence,
                    f.review_status,
                    _safe_str(f.review_notes),
                    _safe_str(f.wcag_criteria),
                    _safe_str(f.page_number),
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
                details_str = "; ".join(f"{k}={v}" for k, v in e.details.items())
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

        # -- Matterhorn Results section --
        writer.writerow(["Matterhorn Results"])
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
        for m in matterhorn_results:
            writer.writerow(
                [
                    m.checkpoint_id,
                    m.checkpoint_name,
                    m.status,
                    _safe_str(m.severity),
                    _safe_str(m.details),
                    _safe_str(m.page_number),
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
        """Generate a branded PDF compliance report.

        The report includes:
        1. Header with Aelira logo (gracefully skipped if missing)
        2. Executive Summary
        3. Issues Found table
        4. Fixes Applied table
        5. Review History
        6. Matterhorn Results (if applicable)
        7. WCAG Conformance Statement

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

        # -- Title --
        story.append(Paragraph("Compliance Audit Report", title_style))
        story.append(
            Paragraph(
                f"{department.name} &mdash; {department.institution}",
                subtitle_style,
            )
        )

        report_date = datetime.now(timezone.utc).strftime("%B %d, %Y")

        # -- Metadata table --
        meta_data = [
            ["Report Date:", report_date],
            ["Document:", scan.file_name],
            ["Scan Type:", scan_type_str.upper() if scan_type_str else "N/A"],
            ["Department:", department.name],
            ["Institution:", department.institution],
            ["Generated By:", "Aelira Compliance Platform"],
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
        approved_count = sum(
            1 for f in fixes if f.review_status in ("approved", "auto_approved")
        )
        rejected_count = sum(1 for f in fixes if f.review_status == "rejected")
        total_fixes = len(fixes)
        approval_rate = (
            round(approved_count / total_fixes * 100, 1) if total_fixes else 0.0
        )
        compliance_level = _compute_compliance_level(mh_total, mh_failed)

        # ==========================================================
        # Section 1: Executive Summary
        # ==========================================================
        story.append(Paragraph("Executive Summary", heading_style))
        summary_text = (
            f"This report documents the accessibility compliance audit for "
            f"<b>{scan.file_name}</b> scanned on "
            f"{_isoformat(scan.created_at)[:10] if scan.created_at else 'N/A'}."
            f"<br/><br/>"
            f"<b>Key Metrics:</b><br/>"
            f"&bull; Total Issues Found: <b>{total_fixes}</b><br/>"
            f"&bull; Fixes Approved: <b>{approved_count}</b> ({approval_rate}%)<br/>"
            f"&bull; Fixes Rejected: <b>{rejected_count}</b><br/>"
            f"&bull; Matterhorn Checkpoints: <b>{mh_passed}/{mh_total} passed</b><br/>"
            f"&bull; Compliance Level: <b>{_compliance_label(compliance_level)}</b>"
        )
        story.append(Paragraph(summary_text, body_style))
        story.append(Spacer(1, 0.3 * inch))

        # ==========================================================
        # Section 2: Issues Found
        # ==========================================================
        story.append(Paragraph("Issues Found", heading_style))

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
                Paragraph("<i>No issues were found during scanning.</i>", body_style)
            )

        story.append(Spacer(1, 0.3 * inch))

        # ==========================================================
        # Section 3: Fixes Applied
        # ==========================================================
        story.append(Paragraph("Fixes Applied", heading_style))

        if fixes:
            fix_header = ["#", "Method", "Confidence", "Status", "Reviewer Notes"]
            fix_rows = [fix_header]
            for i, f in enumerate(fixes, 1):
                status_display = f.review_status.replace("_", " ").title()
                fix_rows.append(
                    [
                        str(i),
                        f.fix_method,
                        f"{f.confidence:.0%}",
                        status_display,
                        Paragraph(_safe_str(f.review_notes, "-")[:100], body_style),
                    ]
                )

            fix_table = Table(
                fix_rows,
                colWidths=[0.4 * inch, 1.0 * inch, 0.9 * inch, 1.2 * inch, 2.4 * inch],
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
            story.append(Paragraph("<i>No fixes were applied.</i>", body_style))

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
                    details_str = "; ".join(f"{k}={v}" for k, v in e.details.items())
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
        # Section 6: WCAG Conformance Statement
        # ==========================================================
        story.append(Paragraph("WCAG Conformance Statement", heading_style))

        conformance_text = (
            f"Based on the automated scan and Matterhorn Protocol validation, "
            f"the document <b>{scan.file_name}</b> has been evaluated against "
            f"WCAG 2.1 Level AA success criteria.<br/><br/>"
            f"<b>Assessment Result:</b> {_compliance_label(compliance_level)}<br/><br/>"
        )

        if compliance_level == "compliant":
            conformance_text += (
                "All evaluated checkpoints have passed. The document meets "
                "WCAG 2.1 Level AA conformance requirements based on the "
                "automated checks performed."
            )
        elif compliance_level == "partial":
            conformance_text += (
                "The document meets most WCAG 2.1 Level AA requirements but "
                f"has {mh_failed} checkpoint failure(s) that should be addressed "
                "to achieve full conformance."
            )
        elif compliance_level == "non_compliant":
            conformance_text += (
                f"The document has {mh_failed} Matterhorn checkpoint failure(s) "
                "and does not meet WCAG 2.1 Level AA conformance requirements. "
                "Remediation is required before the document can be considered compliant."
            )
        else:
            conformance_text += (
                "No Matterhorn Protocol checkpoints were evaluated for this "
                "document. A full PDF/UA validation is recommended."
            )

        story.append(Paragraph(conformance_text, body_style))

        # -- Footer --
        story.append(Spacer(1, 0.5 * inch))
        footer_style = ParagraphStyle(
            "AuditFooter",
            parent=body_style,
            fontSize=9,
            textColor=colors.HexColor(_MEDIUM_GRAY),
            alignment=TA_CENTER,
        )
        story.append(
            Paragraph(
                f"<i>Generated by Aelira Compliance Platform on {report_date}. "
                f"For questions, contact support@example.com</i>",
                footer_style,
            )
        )

        # Build and return
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
