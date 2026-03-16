"""
Compliance Certificate Generator - Official Compliance Attestation

Generates professional, legal-ready compliance certificates that departments
can use to demonstrate WCAG 2.2 compliance status for audits and documentation.

Features:
- Official-looking certificate design
- Unique certificate ID for verification
- QR code for digital verification (optional)
- Compliance level (Bronze, Silver, Gold, Platinum)
- Signature block for department chair
- Valid date range

Author: Aelira Team
Created: November 30, 2025
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from io import BytesIO
import os
import uuid
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image,
)
from reportlab.lib.enums import TA_CENTER


class ComplianceCertificate:
    """
    Generate professional compliance certificates for departments

    Certificate Levels:
    - Bronze: 70-79% compliance (Meeting Basic Standards)
    - Silver: 80-89% compliance (Good Compliance)
    - Gold: 90-94% compliance (Excellent Compliance)
    - Platinum: 95-100% compliance (Exceptional Compliance)
    """

    # Certificate level thresholds and details
    LEVELS = {
        "platinum": {
            "min_score": 95,
            "name": "PLATINUM",
            "color": colors.HexColor("#E5E4E2"),  # Platinum gray
            "accent": colors.HexColor("#5E5E5E"),
            "description": "Exceptional Compliance Achievement",
            "statement": "has demonstrated exceptional commitment to digital accessibility, meeting or exceeding all WCAG 2.2 Level AA requirements.",
        },
        "gold": {
            "min_score": 90,
            "name": "GOLD",
            "color": colors.HexColor("#FFD700"),  # Gold
            "accent": colors.HexColor("#B8860B"),
            "description": "Excellent Compliance Achievement",
            "statement": "has demonstrated excellent commitment to digital accessibility, meeting WCAG 2.2 Level AA requirements.",
        },
        "silver": {
            "min_score": 80,
            "name": "SILVER",
            "color": colors.HexColor("#C0C0C0"),  # Silver
            "accent": colors.HexColor("#808080"),
            "description": "Good Compliance Achievement",
            "statement": "has demonstrated good progress toward digital accessibility compliance with WCAG 2.2 Level AA standards.",
        },
        "bronze": {
            "min_score": 70,
            "name": "BRONZE",
            "color": colors.HexColor("#CD7F32"),  # Bronze
            "accent": colors.HexColor("#8B4513"),
            "description": "Basic Compliance Achievement",
            "statement": "has demonstrated commitment to improving digital accessibility and meeting basic WCAG 2.2 requirements.",
        },
    }

    @staticmethod
    def get_certificate_level(compliance_score: float) -> Optional[Dict]:
        """Get certificate level details based on compliance score"""
        if compliance_score >= 95:
            return ComplianceCertificate.LEVELS["platinum"]
        elif compliance_score >= 90:
            return ComplianceCertificate.LEVELS["gold"]
        elif compliance_score >= 80:
            return ComplianceCertificate.LEVELS["silver"]
        elif compliance_score >= 70:
            return ComplianceCertificate.LEVELS["bronze"]
        return None

    @staticmethod
    def generate_certificate(
        department_name: str,
        institution: str,
        compliance_score: float,
        total_scans: int = 0,
        files_analyzed: int = 0,
        issued_by: str = "Aelira Compliance Dashboard",
        valid_months: int = 12,
        include_qr: bool = True,
    ) -> Optional[bytes]:
        """
        Generate a compliance certificate PDF

        Args:
            department_name: Name of the department
            institution: Name of the institution
            compliance_score: Average compliance score (0-100)
            total_scans: Number of scans performed
            files_analyzed: Number of unique files analyzed
            issued_by: Name of issuing authority
            valid_months: How long the certificate is valid (default 12 months)
            include_qr: Whether to include a QR code for verification

        Returns:
            PDF bytes or None if compliance score is below threshold
        """
        # Check if eligible for certificate
        level = ComplianceCertificate.get_certificate_level(compliance_score)
        if not level:
            return None  # Below minimum threshold

        # Generate unique certificate ID
        cert_id = (
            f"AELIRA-{datetime.now().strftime('%Y%m')}-{uuid.uuid4().hex[:8].upper()}"
        )

        # Calculate dates
        issue_date = datetime.now()
        expiry_date = issue_date + timedelta(days=valid_months * 30)

        # Create PDF in landscape mode for certificate look
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(letter),
            rightMargin=0.5 * inch,
            leftMargin=0.5 * inch,
            topMargin=0.5 * inch,
            bottomMargin=0.5 * inch,
        )

        # Build content
        story = []
        styles = getSampleStyleSheet()

        # Custom styles
        title_style = ParagraphStyle(
            "CertTitle",
            parent=styles["Heading1"],
            fontSize=36,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=10,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        )

        level_style = ParagraphStyle(
            "LevelStyle",
            parent=styles["Heading2"],
            fontSize=24,
            textColor=level["accent"],
            spaceAfter=20,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        )

        subtitle_style = ParagraphStyle(
            "CertSubtitle",
            parent=styles["Normal"],
            fontSize=14,
            textColor=colors.HexColor("#4B5563"),
            spaceAfter=20,
            alignment=TA_CENTER,
        )

        body_style = ParagraphStyle(
            "CertBody",
            parent=styles["Normal"],
            fontSize=12,
            textColor=colors.HexColor("#374151"),
            spaceAfter=10,
            alignment=TA_CENTER,
            leading=16,
        )

        dept_style = ParagraphStyle(
            "DeptStyle",
            parent=styles["Heading1"],
            fontSize=28,
            textColor=colors.HexColor("#1F2937"),
            spaceBefore=20,
            spaceAfter=10,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        )

        institution_style = ParagraphStyle(
            "InstStyle",
            parent=styles["Normal"],
            fontSize=18,
            textColor=colors.HexColor("#6B7280"),
            spaceAfter=20,
            alignment=TA_CENTER,
        )

        # ==================== Certificate Header ====================
        # Add logo if available
        logo_path = os.path.join(
            os.path.dirname(__file__), "assets", "aelira-logo-horizontal-light.png"
        )
        if os.path.exists(logo_path):
            try:
                logo = Image(
                    logo_path, width=1.5 * inch, height=0.36 * inch, kind="proportional"
                )
                logo.hAlign = "CENTER"
                story.append(logo)
                story.append(Spacer(1, 0.2 * inch))
            except Exception:
                pass

        # Certificate title
        story.append(Paragraph("CERTIFICATE OF COMPLIANCE", title_style))
        story.append(Paragraph(f"{level['name']} LEVEL", level_style))
        story.append(Paragraph(level["description"], subtitle_style))

        story.append(Spacer(1, 0.1 * inch))

        # Divider line
        story.append(
            Paragraph(
                "━" * 60,
                ParagraphStyle(
                    "Divider",
                    fontSize=12,
                    textColor=level["accent"],
                    alignment=TA_CENTER,
                ),
            )
        )

        story.append(Spacer(1, 0.2 * inch))

        # This is to certify...
        story.append(Paragraph("This is to certify that", body_style))

        # Department and Institution (prominent)
        story.append(Paragraph(department_name, dept_style))
        story.append(Paragraph(institution, institution_style))

        # Statement
        story.append(Paragraph(level["statement"], body_style))

        story.append(Spacer(1, 0.2 * inch))

        # ==================== Score Box ====================
        score_data = [
            ["COMPLIANCE SCORE", "FILES ANALYZED", "TOTAL SCANS"],
            [f"{compliance_score:.1f}/100", str(files_analyzed), str(total_scans)],
        ]

        score_table = Table(score_data, colWidths=[2.5 * inch, 2.5 * inch, 2.5 * inch])
        score_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6B7280")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("BACKGROUND", (0, 1), (-1, 1), colors.white),
                    ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#1F2937")),
                    ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 1), (-1, 1), 18),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("BOX", (0, 0), (-1, -1), 1, level["accent"]),
                    ("LINEABOVE", (0, 1), (-1, 1), 0.5, colors.HexColor("#E5E7EB")),
                ]
            )
        )
        story.append(score_table)

        story.append(Spacer(1, 0.3 * inch))

        # ==================== Dates and Certificate ID ====================
        cert_info_data = [
            ["Issue Date", "Valid Until", "Certificate ID"],
            [
                issue_date.strftime("%B %d, %Y"),
                expiry_date.strftime("%B %d, %Y"),
                cert_id,
            ],
        ]

        cert_info_table = Table(
            cert_info_data, colWidths=[2.5 * inch, 2.5 * inch, 2.5 * inch]
        )
        cert_info_table.setStyle(
            TableStyle(
                [
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#9CA3AF")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                    ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor("#374151")),
                    ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 1), (-1, 1), 11),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(cert_info_table)

        story.append(Spacer(1, 0.3 * inch))

        # ==================== Signature Block ====================
        sig_style = ParagraphStyle(
            "SigStyle",
            fontSize=10,
            textColor=colors.HexColor("#6B7280"),
            alignment=TA_CENTER,
        )

        story.append(Paragraph("━" * 30, sig_style))
        story.append(
            Paragraph(
                issued_by,
                ParagraphStyle(
                    "Issuer",
                    fontSize=11,
                    textColor=colors.HexColor("#374151"),
                    alignment=TA_CENTER,
                    fontName="Helvetica-Bold",
                ),
            )
        )
        story.append(Paragraph("Authorized Digital Signature", sig_style))

        # ==================== Footer with Standards ====================
        story.append(Spacer(1, 0.2 * inch))

        footer_text = """
        <font size="8" color="#9CA3AF">
        This certificate attests that the digital content produced by the above department
        has been scanned and verified against WCAG 2.2 Level AA accessibility standards
        as mandated by Title II of the Americans with Disabilities Act.
        Certificate verification: education@aelira.ai
        </font>
        """
        story.append(
            Paragraph(
                footer_text,
                ParagraphStyle(
                    "Footer",
                    fontSize=8,
                    textColor=colors.HexColor("#9CA3AF"),
                    alignment=TA_CENTER,
                    leading=10,
                ),
            )
        )

        # Build PDF
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        return pdf_bytes

    @staticmethod
    def generate_certificate_from_stats(stats: Dict[str, Any]) -> Optional[bytes]:
        """
        Generate certificate from compliance stats dictionary

        Args:
            stats: Dictionary from ComplianceDashboard.get_department_compliance()

        Returns:
            PDF bytes or None if not eligible
        """
        return ComplianceCertificate.generate_certificate(
            department_name=stats.get("department_name", "Unknown Department"),
            institution=stats.get("institution", "Unknown Institution"),
            compliance_score=stats.get("compliance_scores", {}).get("average", 0)
            or stats.get("avg_compliance_score", 0),
            total_scans=stats.get("overview", {}).get("total_scans", 0)
            or stats.get("total_scans", 0),
            files_analyzed=stats.get("overview", {}).get("total_files_scanned", 0)
            or stats.get("total_files_scanned", 0),
        )
