"""
Compliance Report PDF Generator - Legal-Ready Department Reports

Generates professional, legal-ready PDF compliance reports for department administrators
to demonstrate WCAG 2.1 compliance for DOJ audits and April 2026 deadline.

Features:
- Executive summary with compliance rate
- Department-wide statistics
- Issue breakdown by severity
- Faculty participation metrics
- AI-powered recommendations via Gemini
- Historical trend analysis (Phase 4)
- Issue tracking status (Phase 4)
- April 2026 deadline tracking with projections
- Professional formatting for legal review

Author: Aelira Team
Created: November 1, 2025
Updated: November 30, 2025 - Added Gemini AI recommendations + Phase 4 analytics
"""

from datetime import datetime
from typing import Dict, Any, Optional, List
from io import BytesIO
import os
import logging
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    PageBreak,
    Image,
)
from reportlab.lib.enums import TA_CENTER

logger = logging.getLogger(__name__)


class ComplianceReportGenerator:
    """
    Generate legal-ready PDF compliance reports for departments

    Perfect for:
    - DOJ audit documentation
    - Section 504 compliance reviews
    - Internal department tracking
    - University administration reporting
    - April 2026 deadline documentation

    Enhanced with:
    - Gemini AI-powered recommendations
    - Historical trend analysis (Phase 4)
    - Issue tracking integration (Phase 4)
    - Deadline projection status
    """

    @staticmethod
    async def generate_ai_recommendations(
        stats: Dict[str, Any],
        trend_analysis: Optional[Dict] = None,
        issue_stats: Optional[Dict] = None,
    ) -> List[Dict[str, str]]:
        """
        Generate personalized AI recommendations using Gemini.

        Args:
            stats: Department compliance statistics
            trend_analysis: Optional historical trend data (week-over-week)
            issue_stats: Optional issue tracking statistics

        Returns:
            List of recommendation dicts with title and description
        """
        try:
            from src.ai.providers import get_provider_manager

            client = get_provider_manager()

            # Build comprehensive context for AI
            context_parts = [
                f"Department: {stats['department_name']} at {stats['institution']}",
                f"Current Compliance Rate: {stats['overview']['compliance_rate']}%",
                f"Average Score: {stats['compliance_scores']['average']}/100",
                f"Total Scans: {stats['overview']['total_scans']}",
                f"Total Issues: {stats['issues']['total']} (Critical: {stats['issues']['critical']}, High: {stats['issues']['high']}, Medium: {stats['issues']['medium']}, Low: {stats['issues']['low']})",
                f"Faculty Participation: {stats['faculty']['participation_rate']}%",
                f"Days Until April 2026 Deadline: {stats['april_2026_deadline']['days_remaining']}",
                f"On Track for Deadline: {'Yes' if stats['april_2026_deadline']['on_track'] else 'No'}",
                f"Estimated Hours Remaining: {stats['april_2026_deadline']['estimated_hours_remaining']}",
            ]

            # Add trend analysis if available
            if trend_analysis:
                context_parts.extend(
                    [
                        "\nWeek-over-Week Trend:",
                        f"- Current Week Score: {trend_analysis.get('current_avg_score', 'N/A')}",
                        f"- Previous Week Score: {trend_analysis.get('previous_avg_score', 'N/A')}",
                        f"- Score Change: {trend_analysis.get('score_change', 'N/A')} ({trend_analysis.get('score_change_pct', 0)}%)",
                        f"- Trend Direction: {trend_analysis.get('trend_direction', 'unknown')}",
                    ]
                )

            # Add issue stats if available
            if issue_stats:
                context_parts.extend(
                    [
                        "\nIssue Tracking Status:",
                        f"- Open Issues: {issue_stats.get('open_issues', 0)}",
                        f"- In Progress: {issue_stats.get('in_progress_issues', 0)}",
                        f"- Resolved: {issue_stats.get('resolved_issues', 0)}",
                        f"- Resolution Rate: {issue_stats.get('resolution_rate', 0)}%",
                        f"- Auto-Fixable: {issue_stats.get('auto_fixable_issues', 0)}",
                    ]
                )

            # Add scan type breakdown
            scan_types = stats["scan_types"]
            context_parts.extend(
                [
                    "\nContent Types Scanned:",
                    f"- PDFs: {scan_types['pdf']}",
                    f"- PowerPoints: {scan_types['powerpoint']}",
                    f"- LaTeX: {scan_types['latex']}",
                    f"- Images: {scan_types['image']}",
                    f"- Videos: {scan_types['video']}",
                ]
            )

            context = "\n".join(context_parts)

            prompt = f"""You are an accessibility compliance expert for higher education. Based on the following department data, provide 3-5 specific, actionable recommendations to improve WCAG 2.1 compliance before the April 2026 deadline.

{context}

Generate recommendations in this exact JSON format:
[
  {{"title": "Brief Action Title", "description": "2-3 sentence actionable recommendation with specific steps", "priority": "Critical|High|Medium"}},
  ...
]

Focus on:
1. Most impactful actions given current compliance rate
2. Specific next steps based on issue distribution
3. Faculty engagement strategies if participation is low
4. Timeline urgency given days remaining
5. Auto-fix opportunities if available

Return ONLY valid JSON array, no other text."""

            result = await client.generate_text(
                prompt=prompt,
                max_tokens=800,
                temperature=0.4,
                system_prompt="You are an expert in WCAG 2.1 compliance for higher education. Provide specific, actionable recommendations.",
            )

            if result.success and result.content:
                import json

                content = result.content
                # Extract JSON from response
                if "[" in content and "]" in content:
                    start = content.index("[")
                    end = content.rindex("]") + 1
                    recommendations = json.loads(content[start:end])
                    logger.info(
                        f"Generated {len(recommendations)} AI recommendations via {result.provider}"
                    )
                    return recommendations

        except Exception as e:
            logger.warning(f"AI recommendations failed, using rule-based fallback: {e}")

        # Fallback to rule-based recommendations
        return _generate_recommendations(stats)

    @staticmethod
    def generate_department_report(
        stats: Dict[str, Any],
        issues: list = None,
        leaderboard: list = None,
        trend_analysis: Optional[Dict] = None,
        issue_stats: Optional[Dict] = None,
        ai_recommendations: Optional[List[Dict]] = None,
    ) -> bytes:
        """
        Generate a comprehensive department compliance report

        Args:
            stats: Department compliance statistics (from ComplianceDashboard.get_department_compliance)
            issues: Optional priority issues list
            leaderboard: Optional faculty leaderboard
            trend_analysis: Optional historical trend data (from SnapshotService.analyze_trend)
            issue_stats: Optional issue tracking statistics (from IssueTrackingService.get_issue_stats)
            ai_recommendations: Optional pre-generated AI recommendations (from generate_ai_recommendations)
                              If not provided, uses rule-based recommendations

        Returns:
            PDF file as bytes
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

        # Build content
        story = []
        styles = getSampleStyleSheet()

        # Custom styles
        title_style = ParagraphStyle(
            "CustomTitle",
            parent=styles["Heading1"],
            fontSize=24,
            textColor=colors.HexColor(
                "#1F2937"
            ),  # Professional dark gray instead of purple
            spaceAfter=30,
            alignment=TA_CENTER,
        )

        subtitle_style = ParagraphStyle(
            "Subtitle",
            parent=styles["Normal"],
            fontSize=12,
            textColor=colors.HexColor("#666666"),
            spaceAfter=20,
            alignment=TA_CENTER,
        )

        heading_style = ParagraphStyle(
            "CustomHeading",
            parent=styles["Heading2"],
            fontSize=16,
            textColor=colors.HexColor("#333333"),
            spaceAfter=12,
            spaceBefore=16,
        )

        ParagraphStyle(
            "CustomSubheading",
            parent=styles["Heading3"],
            fontSize=14,
            textColor=colors.HexColor("#444444"),
            spaceAfter=8,
            spaceBefore=12,
        )

        # ==================== Add Logo at Top ====================
        logo_path = os.path.join(
            os.path.dirname(__file__), "assets", "aelira-main-logo-pdf.png"
        )
        if os.path.exists(logo_path):
            try:
                logo = Image(
                    logo_path, width=2.8 * inch, height=0.67 * inch, kind="proportional"
                )
                logo.hAlign = "LEFT"  # Left-aligned for professional look
                story.append(logo)
                story.append(Spacer(1, 0.3 * inch))
            except Exception:
                # If logo fails to load, just skip it
                pass

        # ==================== Title Page ====================
        story.append(Paragraph("WCAG 2.1 Compliance Report", title_style))
        story.append(
            Paragraph(
                f"{stats['department_name']}<br/>{stats['institution']}", subtitle_style
            )
        )
        story.append(Spacer(1, 0.3 * inch))

        # Report metadata
        report_date = datetime.now().strftime("%B %d, %Y")
        metadata = [
            ["Report Date:", report_date],
            ["Department:", stats["department_name"]],
            ["Institution:", stats["institution"]],
            ["Total Scans:", str(stats["overview"]["total_scans"])],
            ["Files Analyzed:", str(stats["overview"]["total_files_scanned"])],
            ["Generated By:", "Aelira Compliance Dashboard"],
        ]

        metadata_table = Table(metadata, colWidths=[2 * inch, 4 * inch])
        metadata_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#333333")),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                ]
            )
        )
        story.append(metadata_table)
        story.append(Spacer(1, 0.4 * inch))

        # ==================== Executive Summary ====================
        story.append(Paragraph("Executive Summary", heading_style))

        overview = stats["overview"]
        compliance_scores = stats["compliance_scores"]
        deadline = stats["april_2026_deadline"]

        summary_text = f"""
        This report provides a comprehensive overview of WCAG 2.1 Level AA compliance
        for {stats['department_name']} at {stats['institution']}.
        <br/><br/>
        <b>Key Findings:</b><br/>
        • Average Compliance Score: <b>{compliance_scores['average']}/100</b><br/>
        • Compliance Rate: <b>{overview['compliance_rate']}%</b> of files meet WCAG 2.1 AA standards<br/>
        • Total Issues Identified: <b>{stats['issues']['total']}</b><br/>
        • Days Until April 2026 Deadline: <b>{deadline['days_remaining']} days</b><br/>
        • Estimated Hours of Work Remaining: <b>{deadline['estimated_hours_remaining']} hours</b><br/>
        • On Track for Deadline: <b>{'Yes' if deadline['on_track'] else 'No - Immediate Action Required'}</b>
        """
        story.append(Paragraph(summary_text, styles["Normal"]))
        story.append(Spacer(1, 0.3 * inch))

        # ==================== Compliance Scorecard ====================
        story.append(Paragraph("Compliance Scorecard", heading_style))

        scorecard_data = [
            ["Metric", "Value", "Status"],
            [
                "Average Compliance Score",
                f"{compliance_scores['average']}/100",
                _get_status_color(compliance_scores["average"]),
            ],
            [
                "Compliant Files (≥90)",
                str(stats["compliance_breakdown"]["compliant"]),
                "✓ Good",
            ],
            [
                "Needs Work (70-89)",
                str(stats["compliance_breakdown"]["needs_work"]),
                "⚠ Review",
            ],
            [
                "Critical (<70)",
                str(stats["compliance_breakdown"]["critical"]),
                "✗ Urgent",
            ],
            [
                "Faculty Participation",
                f"{stats['faculty']['participation_rate']}%",
                _get_participation_status(stats["faculty"]["participation_rate"]),
            ],
        ]

        scorecard_table = Table(
            scorecard_data, colWidths=[2.5 * inch, 1.5 * inch, 1.5 * inch]
        )
        scorecard_table.setStyle(
            TableStyle(
                [
                    (
                        "BACKGROUND",
                        (0, 0),
                        (-1, 0),
                        colors.HexColor("#1F2937"),
                    ),  # Professional dark gray header
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 11),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
                    ("TOPPADDING", (0, 0), (-1, 0), 10),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                    ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#999999")),
                    ("FONTSIZE", (0, 1), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                    ("TOPPADDING", (0, 1), (-1, -1), 6),
                ]
            )
        )
        story.append(scorecard_table)
        story.append(Spacer(1, 0.3 * inch))

        # ==================== Issue Breakdown ====================
        story.append(Paragraph("Issue Breakdown by Severity", heading_style))

        issues_data = stats["issues"]
        issue_table_data = [
            ["Severity", "Count", "Priority"],
            ["Critical", str(issues_data["critical"]), "Immediate Action Required"],
            ["High", str(issues_data["high"]), "Address Within 1 Week"],
            ["Medium", str(issues_data["medium"]), "Address Within 1 Month"],
            ["Low", str(issues_data["low"]), "Address Before Deadline"],
            ["<b>Total</b>", f"<b>{issues_data['total']}</b>", ""],
        ]

        issue_table = Table(
            issue_table_data, colWidths=[1.5 * inch, 1.5 * inch, 3 * inch]
        )
        issue_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#444444")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#cccccc")),
                    (
                        "BACKGROUND",
                        (0, 1),
                        (-1, 1),
                        colors.HexColor("#ffcccc"),
                    ),  # Critical - red
                    (
                        "BACKGROUND",
                        (0, 2),
                        (-1, 2),
                        colors.HexColor("#ffe6cc"),
                    ),  # High - orange
                    (
                        "BACKGROUND",
                        (0, 3),
                        (-1, 3),
                        colors.HexColor("#ffffcc"),
                    ),  # Medium - yellow
                    (
                        "BACKGROUND",
                        (0, 4),
                        (-1, 4),
                        colors.HexColor("#e6ffe6"),
                    ),  # Low - green
                    (
                        "BACKGROUND",
                        (0, 5),
                        (-1, 5),
                        colors.HexColor("#f0f0f0"),
                    ),  # Total - gray
                    ("FONTNAME", (0, 5), (-1, 5), "Helvetica-Bold"),
                ]
            )
        )
        story.append(issue_table)
        story.append(Spacer(1, 0.3 * inch))

        # ==================== Scan Type Breakdown ====================
        story.append(Paragraph("Scans by Content Type", heading_style))

        scan_types = stats["scan_types"]
        scan_type_data = [
            ["Content Type", "Scans", "Percentage"],
            [
                "PDF Documents",
                str(scan_types["pdf"]),
                f"{_percent(scan_types['pdf'], overview['total_scans'])}%",
            ],
            [
                "PowerPoint Presentations",
                str(scan_types["powerpoint"]),
                f"{_percent(scan_types['powerpoint'], overview['total_scans'])}%",
            ],
            [
                "LaTeX Equations",
                str(scan_types["latex"]),
                f"{_percent(scan_types['latex'], overview['total_scans'])}%",
            ],
            [
                "Images",
                str(scan_types["image"]),
                f"{_percent(scan_types['image'], overview['total_scans'])}%",
            ],
            [
                "Videos/Audio",
                str(scan_types["video"]),
                f"{_percent(scan_types['video'], overview['total_scans'])}%",
            ],
            [
                "Websites",
                str(scan_types["website"]),
                f"{_percent(scan_types['website'], overview['total_scans'])}%",
            ],
            [
                "Code",
                str(scan_types["code"]),
                f"{_percent(scan_types['code'], overview['total_scans'])}%",
            ],
        ]

        scan_type_table = Table(
            scan_type_data, colWidths=[2.5 * inch, 1.5 * inch, 1.5 * inch]
        )
        scan_type_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#666666")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("ALIGN", (1, 1), (2, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#cccccc")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f9f9f9")],
                    ),
                ]
            )
        )
        story.append(scan_type_table)
        story.append(Spacer(1, 0.3 * inch))

        # ==================== Historical Trend Analysis (Phase 4) ====================
        if trend_analysis:
            story.append(Paragraph("Week-over-Week Trend Analysis", heading_style))

            trend_direction = trend_analysis.get("trend_direction", "stable")
            trend_icon = (
                "↑"
                if trend_direction == "improving"
                else "↓" if trend_direction == "declining" else "→"
            )

            trend_data = [
                ["Metric", "This Week", "Last Week", "Change"],
                [
                    "Avg Compliance Score",
                    f"{trend_analysis.get('current_avg_score', 'N/A')}/100",
                    f"{trend_analysis.get('previous_avg_score', 'N/A')}/100",
                    f"{trend_icon} {trend_analysis.get('score_change', 0):+.1f} ({trend_analysis.get('score_change_pct', 0):+.1f}%)",
                ],
                [
                    "Total Issues",
                    str(trend_analysis.get("current_total_issues", "N/A")),
                    str(trend_analysis.get("previous_total_issues", "N/A")),
                    f"{trend_analysis.get('issues_change', 0):+d}",
                ],
                [
                    "Trend Direction",
                    trend_direction.capitalize(),
                    "",
                    f"{'On Track' if trend_analysis.get('on_track_for_deadline') else 'Needs Attention'}",
                ],
            ]

            trend_table = Table(
                trend_data, colWidths=[2 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch]
            )
            trend_table.setStyle(
                TableStyle(
                    [
                        (
                            "BACKGROUND",
                            (0, 0),
                            (-1, 0),
                            colors.HexColor("#2563EB"),
                        ),  # Blue header
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#cccccc")),
                        (
                            "ROWBACKGROUNDS",
                            (0, 1),
                            (-1, -1),
                            [colors.white, colors.HexColor("#EBF5FF")],
                        ),
                    ]
                )
            )
            story.append(trend_table)
            story.append(Spacer(1, 0.3 * inch))

        # ==================== Issue Tracking Status (Phase 4) ====================
        if issue_stats:
            story.append(Paragraph("Issue Tracking Status", heading_style))

            resolution_rate = issue_stats.get("resolution_rate", 0)

            issue_tracking_data = [
                ["Status", "Count", "Notes"],
                [
                    "Open Issues",
                    str(issue_stats.get("open_issues", 0)),
                    "Require immediate attention",
                ],
                [
                    "In Progress",
                    str(issue_stats.get("in_progress_issues", 0)),
                    "Currently being worked on",
                ],
                [
                    "Resolved",
                    str(issue_stats.get("resolved_issues", 0)),
                    "Successfully fixed",
                ],
                [
                    "Won't Fix",
                    str(issue_stats.get("wont_fix_issues", 0)),
                    "Accepted risk / not applicable",
                ],
                [
                    "False Positive",
                    str(issue_stats.get("false_positive_issues", 0)),
                    "Incorrectly flagged",
                ],
                [
                    "<b>Resolution Rate</b>",
                    f"<b>{resolution_rate}%</b>",
                    "Target: 80%+",
                ],
            ]

            issue_tracking_table = Table(
                issue_tracking_data, colWidths=[2 * inch, 1.5 * inch, 3 * inch]
            )
            issue_tracking_table.setStyle(
                TableStyle(
                    [
                        (
                            "BACKGROUND",
                            (0, 0),
                            (-1, 0),
                            colors.HexColor("#059669"),
                        ),  # Green header
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 10),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#cccccc")),
                        (
                            "BACKGROUND",
                            (0, 1),
                            (-1, 1),
                            colors.HexColor("#FEE2E2"),
                        ),  # Open - red tint
                        (
                            "BACKGROUND",
                            (0, 2),
                            (-1, 2),
                            colors.HexColor("#FEF3C7"),
                        ),  # In Progress - yellow tint
                        (
                            "BACKGROUND",
                            (0, 3),
                            (-1, 3),
                            colors.HexColor("#D1FAE5"),
                        ),  # Resolved - green tint
                        (
                            "BACKGROUND",
                            (0, 6),
                            (-1, 6),
                            colors.HexColor("#f0f0f0"),
                        ),  # Total row
                    ]
                )
            )
            story.append(issue_tracking_table)

            # Auto-fix section
            auto_fixable = issue_stats.get("auto_fixable_issues", 0)
            auto_fixed = issue_stats.get("auto_fixed_issues", 0)
            if auto_fixable > 0:
                story.append(Spacer(1, 0.1 * inch))
                auto_fix_text = f"""
                <b>Auto-Fix Opportunity:</b> {auto_fixable} issues can be automatically fixed by Aelira.
                {auto_fixed} have already been auto-fixed. <i>Use the "Apply Auto-Fix" feature in the dashboard
                to quickly resolve remaining auto-fixable issues.</i>
                """
                story.append(Paragraph(auto_fix_text, styles["Normal"]))

            story.append(Spacer(1, 0.3 * inch))

        # ==================== Recommendations ====================
        story.append(PageBreak())

        # Use AI recommendations if provided, otherwise fall back to rule-based
        recommendations = (
            ai_recommendations
            if ai_recommendations
            else _generate_recommendations(stats)
        )
        rec_title = (
            "AI-Powered Recommendations"
            if ai_recommendations
            else "Recommendations for Compliance"
        )
        story.append(Paragraph(rec_title, heading_style))

        if ai_recommendations:
            story.append(
                Paragraph(
                    "<i>The following recommendations were generated by Aelira's AI based on your department's specific compliance data.</i>",
                    styles["Normal"],
                )
            )
            story.append(Spacer(1, 0.1 * inch))

        for i, rec in enumerate(recommendations, 1):
            priority = rec.get("priority", "")
            priority_badge = (
                f" <font color='#EF4444'>[{priority}]</font>"
                if priority == "Critical"
                else (
                    f" <font color='#F59E0B'>[{priority}]</font>"
                    if priority == "High"
                    else (
                        f" <font color='#3B82F6'>[{priority}]</font>"
                        if priority
                        else ""
                    )
                )
            )
            rec_text = (
                f"<b>{i}. {rec['title']}</b>{priority_badge}<br/>{rec['description']}"
            )
            story.append(Paragraph(rec_text, styles["Normal"]))
            story.append(Spacer(1, 0.15 * inch))

        # ==================== Deadline Tracking ====================
        story.append(Spacer(1, 0.3 * inch))
        story.append(Paragraph("April 2026 Deadline Tracking", heading_style))

        deadline_text = f"""
        The Department of Justice has set <b>April 24, 2026</b> as the WCAG 2.1 Level AA
        compliance deadline for universities receiving federal funding.
        <br/><br/>
        <b>Current Status:</b><br/>
        • Days Remaining: <b>{deadline['days_remaining']} days</b><br/>
        • Estimated Work Remaining: <b>{deadline['estimated_hours_remaining']} hours</b><br/>
        • On Track: <b>{'Yes - Continue current pace' if deadline['on_track'] else 'No - Increase remediation efforts'}</b>
        <br/><br/>
        <b>Next Steps:</b><br/>
        1. Address all Critical issues within 1 week<br/>
        2. Address all High issues within 1 month<br/>
        3. Establish weekly faculty training sessions<br/>
        4. Schedule monthly compliance reviews
        """
        story.append(Paragraph(deadline_text, styles["Normal"]))

        # ==================== Footer ====================
        story.append(Spacer(1, 0.5 * inch))
        footer_text = f"""
        <br/><br/>
        <i>This report was generated by <b>Aelira Compliance Dashboard</b> on {report_date}.
        <br/>For questions or support, contact education@aelira.ai</i>
        """
        story.append(Paragraph(footer_text, subtitle_style))

        # Build PDF
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()

        return pdf_bytes


def _get_status_color(score: float) -> str:
    """Get status string based on compliance score"""
    if score >= 90:
        return "✓ Excellent"
    elif score >= 80:
        return "✓ Good"
    elif score >= 70:
        return "⚠ Needs Work"
    else:
        return "✗ Critical"


def _get_participation_status(rate: float) -> str:
    """Get status string based on faculty participation rate"""
    if rate >= 80:
        return "✓ Excellent"
    elif rate >= 60:
        return "✓ Good"
    elif rate >= 40:
        return "⚠ Moderate"
    else:
        return "✗ Low"


def _percent(value: int, total: int) -> str:
    """Calculate percentage, handle division by zero"""
    if total == 0:
        return "0"
    return f"{(value / total * 100):.1f}"


def _generate_recommendations(stats: Dict[str, Any]) -> list:
    """Generate tailored recommendations based on department stats"""
    recommendations = []

    # Critical issues
    if stats["issues"]["critical"] > 0:
        recommendations.append(
            {
                "title": "Address Critical Issues Immediately",
                "description": f"You have {stats['issues']['critical']} critical accessibility issues that require immediate attention. "
                f"Critical issues prevent users from accessing content and pose significant legal risk. "
                f"Assign these to faculty members within the next 7 days.",
            }
        )

    # Low compliance rate
    if stats["overview"]["compliance_rate"] < 70:
        recommendations.append(
            {
                "title": "Increase Overall Compliance Rate",
                "description": f"Your current compliance rate is {stats['overview']['compliance_rate']}%, below the "
                f"recommended 80% threshold. Focus on bulk remediation of PDF and PowerPoint files using "
                f"Aelira's automated tools to quickly improve this metric.",
            }
        )

    # Low faculty participation
    if stats["faculty"]["participation_rate"] < 60:
        recommendations.append(
            {
                "title": "Improve Faculty Participation",
                "description": f"Only {stats['faculty']['participation_rate']}% of faculty have used accessibility scanning. "
                f"Consider mandatory training sessions and provide clear instructions for using Aelira tools. "
                f"Highlight faculty champions on the leaderboard to encourage participation.",
            }
        )

    # Deadline pressure
    if (
        stats["april_2026_deadline"]["days_remaining"] < 180
        and not stats["april_2026_deadline"]["on_track"]
    ):
        recommendations.append(
            {
                "title": "Urgent: Accelerate Remediation Efforts",
                "description": f"With only {stats['april_2026_deadline']['days_remaining']} days until the April 2026 deadline "
                f"and {stats['april_2026_deadline']['estimated_hours_remaining']} hours of work remaining, "
                f"you are not on track. Consider hiring temporary accessibility specialists or increasing "
                f"faculty release time for remediation work.",
            }
        )

    # LaTeX support
    if stats["scan_types"]["latex"] > 0:
        recommendations.append(
            {
                "title": "Continue LaTeX Remediation",
                "description": f"You've scanned {stats['scan_types']['latex']} LaTeX files. Aelira is the only tool that "
                f"supports automated LaTeX to MathML conversion. Continue using this feature for STEM content "
                f"to ensure math equations are accessible to screen readers.",
            }
        )

    # Default recommendations
    if not recommendations:
        recommendations.append(
            {
                "title": "Maintain Current Progress",
                "description": f"Your department is on track with a {stats['overview']['compliance_rate']}% compliance rate. "
                f"Continue scanning new content as it's created and address remaining issues by priority level.",
            }
        )

    return recommendations
