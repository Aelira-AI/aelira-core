"""
PDF Report Generator for Accessibility Scans
Generates professional PDF reports with scan results
"""

from datetime import datetime
from typing import List, Dict, Any
from io import BytesIO
import html
import os
import base64
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
    Image,
)
from reportlab.platypus import KeepTogether
from reportlab.lib.enums import TA_CENTER

# Import matplotlib for chart generation
try:
    import matplotlib

    matplotlib.use("Agg")  # Use non-interactive backend
    import matplotlib.pyplot as plt
    from collections import Counter
    import re

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


# Human-readable display names for issue types (Tasks 1-14)
ISSUE_TYPE_DISPLAY_NAMES = {
    # PDF - Reading Order & Tables (Task 4, 14)
    "reading_order": "Reading Order Issue",
    "table_header": "Table Header Detection",
    "table_accessibility": "Table Accessibility",
    # Web - Shadow DOM (Task 13)
    "shadow_dom": "Shadow DOM Accessibility",
    "image-alt": "Missing Image Alt Text (Shadow DOM)",
    "button-name": "Missing Button Name (Shadow DOM)",
    "link-name": "Missing Link Name (Shadow DOM)",
    "form-label": "Missing Form Label (Shadow DOM)",
    # XLSX - Conditional Formatting & Pivots (Task 10, 11)
    "conditional_format": "Conditional Formatting (Color-Only)",
    "pivot_table": "Pivot Table Structure",
    "color_only": "Color-Only Information",
    # Multimedia - Flashing & Diarization (Task 2, 5)
    "red_flash": "Red Flash Seizure Risk",
    "flashing_content": "Flashing Content Warning",
    "speaker_diarization": "Speaker Identification Needed",
    # PPTX - Animations & Media (Task 8, 9)
    "animation": "Animation Accessibility",
    "animation_flash": "Animation Flash Risk",
    "animation_auto": "Auto-Start Animation",
    "embedded_media": "Embedded Media Issue",
    "missing_captions": "Missing Captions",
    "missing_transcript": "Missing Transcript",
    # DOCX - SmartArt & Embedded (Task 6, 7)
    "smartart": "SmartArt Diagram",
    "embedded_object": "Embedded Object",
    "ole_object": "OLE Object",
    # LaTeX - Accessibility Issues
    "missing_title": "Missing Document Title",
    "missing_author": "Missing Document Author",
    "missing_alt_text": "Missing Image Alt Text",
    "missing_figure_caption": "Missing Figure Caption",
    "missing_table_caption": "Missing Table Caption",
    "complex_table_no_header": "Table Without Header Structure",
    "equation_no_label": "Equation Without Label",
    "color_only_emphasis": "Color-Only Emphasis",
    "missing_lang": "Missing Language Declaration",
    "low_contrast_potential": "Potential Low Contrast",
    "unlabeled_hyperlink": "Bare URL Without Description",
    "missing_list_structure": "Manual List Formatting",
    "conversion_failed": "LaTeX Conversion Failed",
    "wcag_noncompliant": "WCAG Non-Compliant Equation",
    # LaTeX PDF scanner - Math/Equation detection
    "latex_equations_inaccessible": "LaTeX Equations Inaccessible",
    "math_content_accessibility": "Math Content Accessibility",
    "raw_latex_code": "Raw LaTeX Code Detected",
    "mathml_recommendation": "MathML Conversion Recommended",
    # Common issue types
    "alt_text": "Missing Alt Text",
    "contrast": "Color Contrast",
    "heading": "Heading Structure",
    "link": "Link Accessibility",
    "form": "Form Accessibility",
    "language": "Language Declaration",
}

# Issue types that pose seizure risk (WCAG 2.3.1)
SEIZURE_RISK_ISSUE_TYPES = {"red_flash", "flashing_content", "animation_flash"}


class AccessibilityPDFReportGenerator:
    """Generate PDF reports for accessibility scan results"""

    @staticmethod
    def _generate_severity_chart(issues: List[Dict[str, Any]]) -> BytesIO:
        """Generate a bar chart showing issues by severity"""
        if not MATPLOTLIB_AVAILABLE or not issues:
            return None

        try:
            # Count issues by severity/impact
            severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

            for issue in issues:
                impact = (issue.get("impact") or issue.get("severity") or "").lower()
                if impact == "critical":
                    severity_counts["critical"] += 1
                elif impact in ["high", "serious"]:
                    severity_counts["high"] += 1
                elif impact in ["medium", "moderate"]:
                    severity_counts["medium"] += 1
                elif impact in ["low", "minor"]:
                    severity_counts["low"] += 1

            # Create bar chart
            _fig, ax = plt.figure(figsize=(6, 3)), plt.gca()
            severities = ["Critical", "High", "Medium", "Low"]
            counts = [
                severity_counts["critical"],
                severity_counts["high"],
                severity_counts["medium"],
                severity_counts["low"],
            ]
            colors_list = ["#DC2626", "#EA580C", "#CA8A04", "#6B7280"]

            ax.bar(
                severities, counts, color=colors_list, edgecolor="white", linewidth=1.5
            )
            ax.set_ylabel("Number of Issues", fontsize=10)
            ax.set_title("Issues by Severity", fontsize=12, fontweight="bold")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            plt.tight_layout()

            # Save to BytesIO
            img_buffer = BytesIO()
            plt.savefig(img_buffer, format="png", dpi=150, bbox_inches="tight")
            img_buffer.seek(0)
            plt.close()

            return img_buffer
        except Exception as e:
            print(f"Error generating severity chart: {e}")
            return None

    @staticmethod
    def _generate_wcag_criteria_chart(issues: List[Dict[str, Any]]) -> BytesIO:
        """Generate a bar chart showing top WCAG criteria violations"""
        if not MATPLOTLIB_AVAILABLE or not issues:
            return None

        try:
            # Extract WCAG criterion numbers from issues
            criterion_counts = Counter()

            for issue in issues:
                criterion = (
                    issue.get("criterion") or issue.get("wcag_criterion") or "Unknown"
                )
                # Extract just the number part (e.g., "1.1.1" from "WCAG 2.1 Level AA: 1.1.1 Non-text Content")
                match = re.search(r"(\d+\.\d+(?:\.\d+)?)", criterion)
                if match:
                    criterion_num = match.group(1)
                    criterion_counts[criterion_num] += 1

            if not criterion_counts:
                return None

            # Get top 5 criteria
            top_criteria = criterion_counts.most_common(5)
            if not top_criteria:
                return None

            # Create horizontal bar chart
            fig, ax = plt.subplots(figsize=(6, 3))
            criteria = [item[0] for item in top_criteria]
            counts = [item[1] for item in top_criteria]

            ax.barh(criteria, counts, color="#8b5cf6", edgecolor="white", linewidth=1.5)
            ax.set_xlabel("Number of Violations", fontsize=10)
            ax.set_title("Top WCAG Criteria Violations", fontsize=12, fontweight="bold")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.invert_yaxis()  # Highest count at top
            plt.tight_layout()

            # Save to BytesIO
            img_buffer = BytesIO()
            plt.savefig(img_buffer, format="png", dpi=150, bbox_inches="tight")
            img_buffer.seek(0)
            plt.close()

            return img_buffer
        except Exception as e:
            print(f"Error generating WCAG criteria chart: {e}")
            return None

    @staticmethod
    def generate_website_report(scan_data: Dict[str, Any]) -> bytes:
        """
        Generate a comprehensive PDF report for a website accessibility scan

        Args:
            scan_data: Dictionary containing scan information

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

        # Build the PDF content
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

        heading_style = ParagraphStyle(
            "CustomHeading",
            parent=styles["Heading2"],
            fontSize=16,
            textColor=colors.HexColor("#333333"),
            spaceAfter=12,
            spaceBefore=12,
        )

        # Extract data
        url = scan_data.get("url", "Unknown")
        created_at = scan_data.get("created_at", datetime.now().isoformat())
        score = scan_data.get("compliance_score", 0)
        issues = scan_data.get("issues", [])
        pages_scanned = scan_data.get("pages_scanned", 1)

        # Count issues by severity (support both "impact" from website scans
        # and "severity" from document scanners like DOCX/PPTX/XLSX)
        def _get_severity(i: dict) -> str:
            return (i.get("impact") or i.get("severity") or "minor").lower()

        critical = len([i for i in issues if _get_severity(i) == "critical"])
        serious = len([i for i in issues if _get_severity(i) in ["serious", "high"]])
        moderate = len(
            [i for i in issues if _get_severity(i) in ["moderate", "medium"]]
        )
        minor = len([i for i in issues if _get_severity(i) in ["minor", "low"]])

        # Format date
        try:
            date_obj = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            formatted_date = date_obj.strftime("%B %d, %Y at %I:%M %p")
        except Exception:
            formatted_date = created_at

        # Add Aelira logo at the top (before title)
        # Use PNG logo from Looka.ai account
        logo_path = os.path.join(
            os.path.dirname(__file__), "assets", "aelira-main-logo-pdf.png"
        )
        if os.path.exists(logo_path):
            try:
                from reportlab.platypus import Image as RLImage

                # Load PNG logo with proper aspect ratio
                # PNG is 1505x608 pixels, aspect ratio = 1505/608 = 2.475:1
                # Set width and calculate height to maintain aspect ratio
                logo_width = 2.0 * inch
                logo_height = logo_width / 2.475  # Maintain 2.475:1 aspect ratio
                logo_img = RLImage(logo_path, width=logo_width, height=logo_height)
                logo_img.hAlign = "LEFT"  # Left-aligned for professional look

                story.append(logo_img)
                story.append(Spacer(1, 0.3 * inch))
            except Exception:
                # If logo fails to load, just skip it
                pass

        # Title
        story.append(Paragraph("Accessibility Scan Report", title_style))
        story.append(Spacer(1, 0.2 * inch))

        # Determine label based on content type (Website vs File)
        # If URL starts with http/https, it's a website scan, otherwise it's a file
        url_label = (
            "Website:"
            if (url.startswith("http://") or url.startswith("https://"))
            else "File:"
        )

        # Metadata table
        metadata = [
            [url_label, url],
            ["Scan Date:", formatted_date],
            ["Pages Scanned:", str(pages_scanned)],
            ["Generated by:", "Aelira.ai"],
        ]

        metadata_table = Table(metadata, colWidths=[2 * inch, 4.5 * inch])
        metadata_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#666666")),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )

        story.append(metadata_table)
        story.append(Spacer(1, 0.3 * inch))

        # Summary Section
        story.append(Paragraph("Summary", heading_style))

        # Determine score color and label
        if score >= 90:
            score_color = colors.HexColor("#10b981")
            score_label = "Excellent"
        elif score >= 70:
            score_color = colors.HexColor("#f59e0b")
            score_label = "Good"
        elif score >= 50:
            score_color = colors.HexColor("#ef4444")
            score_label = "Needs Work"
        else:
            score_color = colors.HexColor("#dc2626")
            score_label = "Poor"

        summary_data = [
            ["Compliance Score", "Critical", "Serious", "Moderate", "Minor"],
            [
                f"{score}%\n({score_label})",
                str(critical),
                str(serious),
                str(moderate),
                str(minor),
            ],
        ]

        summary_table = Table(
            summary_data,
            colWidths=[1.3 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch],
        )
        summary_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111827")),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 11),
                    ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 1), (-1, 1), 14),
                    ("TEXTCOLOR", (0, 1), (0, 1), score_color),
                    ("TEXTCOLOR", (1, 1), (1, 1), colors.HexColor("#dc2626")),
                    ("TEXTCOLOR", (2, 1), (2, 1), colors.HexColor("#f59e0b")),
                    ("TEXTCOLOR", (3, 1), (3, 1), colors.HexColor("#f59e0b")),
                    ("TEXTCOLOR", (4, 1), (4, 1), colors.HexColor("#6b7280")),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("GRID", (0, 0), (-1, -1), 1, colors.HexColor("#e5e7eb")),
                ]
            )
        )

        story.append(summary_table)
        story.append(Spacer(1, 0.3 * inch))

        # Data Visualizations Section
        if issues and MATPLOTLIB_AVAILABLE:
            story.append(Paragraph("Data Visualizations", heading_style))
            story.append(Spacer(1, 0.1 * inch))

            # Generate and add severity chart
            severity_chart = AccessibilityPDFReportGenerator._generate_severity_chart(
                issues
            )
            if severity_chart:
                try:
                    chart_img = Image(
                        severity_chart, width=5.5 * inch, height=2.75 * inch
                    )
                    chart_img.hAlign = "CENTER"
                    story.append(chart_img)
                    story.append(Spacer(1, 0.2 * inch))
                except Exception as e:
                    print(f"Error adding severity chart to PDF: {e}")

            # Generate and add WCAG criteria chart
            wcag_chart = AccessibilityPDFReportGenerator._generate_wcag_criteria_chart(
                issues
            )
            if wcag_chart:
                try:
                    chart_img = Image(wcag_chart, width=5.5 * inch, height=2.75 * inch)
                    chart_img.hAlign = "CENTER"
                    story.append(chart_img)
                    story.append(Spacer(1, 0.3 * inch))
                except Exception as e:
                    print(f"Error adding WCAG chart to PDF: {e}")

        # Issues Section
        story.append(Paragraph(f"Accessibility Issues ({len(issues)})", heading_style))
        story.append(Spacer(1, 0.1 * inch))

        if not issues:
            no_issues_text = Paragraph(
                "✅ <b>No accessibility issues found!</b><br/>This document meets WCAG 2.1 Level AA standards.",
                ParagraphStyle(
                    "NoIssues",
                    parent=styles["Normal"],
                    fontSize=12,
                    textColor=colors.HexColor("#10b981"),
                    alignment=TA_CENTER,
                    spaceAfter=12,
                ),
            )
            story.append(no_issues_text)
        else:
            for idx, issue in enumerate(
                issues[:50], 1
            ):  # Limit to 50 issues for PDF size
                # Support both website scan fields (impact/description/element/fix)
                # and document scanner fields (severity/title/location/suggested_fix)
                impact = (
                    issue.get("impact") or issue.get("severity") or "minor"
                ).lower()
                description = (
                    issue.get("description")
                    or issue.get("title")
                    or issue.get("message")
                    or "No description"
                )
                element = issue.get("element") or issue.get("location") or "N/A"
                fix = (
                    issue.get("fix")
                    or issue.get("suggested_fix")
                    or issue.get("how_to_fix")
                    or issue.get("recommendation")
                    or "No fix available"
                )
                generated_fix = (
                    issue.get("generated_code_fix", "")
                    or issue.get("generated_alt_text", "")
                    or issue.get("alt_text", "")
                )  # Support code fixes, generated alt text
                screenshot = issue.get("screenshot", None)
                page_url = issue.get("page_url", None)
                selector = issue.get("selector", None)
                xpath = issue.get("xpath", None)

                # Get issue type and display name
                rule = issue.get("rule", "")
                issue_type = (
                    issue.get("type")
                    or issue.get("issue_type")
                    or (rule.get("id", "") if isinstance(rule, dict) else rule)
                )
                issue_type_display = ISSUE_TYPE_DISPLAY_NAMES.get(issue_type, "")

                # Check for seizure risk issues (WCAG 2.3.1)
                is_seizure_risk = issue_type in SEIZURE_RISK_ISSUE_TYPES

                # Check for Shadow DOM metadata
                metadata = issue.get("metadata", {})
                is_shadow_dom = metadata.get("shadow_dom", False)

                # Impact color - seizure risk always uses critical red
                impact_colors = {
                    "critical": colors.HexColor("#dc2626"),
                    "serious": colors.HexColor("#f59e0b"),
                    "high": colors.HexColor("#f59e0b"),
                    "moderate": colors.HexColor("#f59e0b"),
                    "medium": colors.HexColor("#f59e0b"),
                    "minor": colors.HexColor("#6b7280"),
                    "low": colors.HexColor("#6b7280"),
                }
                impact_color = impact_colors.get(impact, colors.HexColor("#6b7280"))
                if is_seizure_risk:
                    impact_color = colors.HexColor("#dc2626")  # Always critical red

                # Build issue header with type display name
                header_text = f"<b>#{idx} - {impact.upper()}"
                if issue_type_display:
                    header_text += f" [{issue_type_display}]"
                header_text += f": {description}</b>"

                issue_header = Paragraph(
                    header_text,
                    ParagraphStyle(
                        "IssueHeader",
                        parent=styles["Normal"],
                        fontSize=11,
                        textColor=impact_color,
                        spaceBefore=6,
                        spaceAfter=4,
                    ),
                )

                # Issue details
                issue_details = []

                # Add seizure risk warning banner for photosensitive epilepsy hazards
                if is_seizure_risk:
                    seizure_warning = Paragraph(
                        "⚠️ <b>SEIZURE RISK WARNING:</b> This content contains flashing that may trigger "
                        "seizures in people with photosensitive epilepsy. This is a WCAG 2.3.1 violation "
                        "and poses serious health risks. Immediate remediation recommended.",
                        ParagraphStyle(
                            "SeizureWarning",
                            parent=styles["Normal"],
                            fontSize=9,
                            textColor=colors.HexColor("#991b1b"),
                            backColor=colors.HexColor("#fef2f2"),
                            borderColor=colors.HexColor("#dc2626"),
                            borderWidth=1,
                            borderPadding=6,
                            spaceBefore=4,
                            spaceAfter=6,
                        ),
                    )
                    issue_details.append(seizure_warning)

                # Add Shadow DOM indicator for web component issues
                if is_shadow_dom:
                    shadow_info = Paragraph(
                        "🔲 <b>Shadow DOM Component:</b> This issue is inside a web component's Shadow DOM. "
                        "Standard DOM queries may not reach this element. Use pierce selectors (>>>) or "
                        "component-specific APIs for remediation.",
                        ParagraphStyle(
                            "ShadowDOMInfo",
                            parent=styles["Normal"],
                            fontSize=9,
                            textColor=colors.HexColor("#5b21b6"),
                            backColor=colors.HexColor("#f5f3ff"),
                            borderPadding=4,
                            spaceBefore=4,
                            spaceAfter=6,
                        ),
                    )
                    issue_details.append(shadow_info)

                # Add location information if available
                if page_url:
                    issue_details.append(
                        Paragraph(f"<b>Page:</b> {page_url}", styles["Normal"])
                    )
                    issue_details.append(Spacer(1, 0.05 * inch))

                if selector:
                    escaped_selector = html.escape(selector[:200])
                    issue_details.append(
                        Paragraph(
                            f"<b>CSS Selector:</b> <font face='Courier' size='8'>{escaped_selector}</font>",
                            styles["Normal"],
                        )
                    )
                    issue_details.append(Spacer(1, 0.05 * inch))

                if xpath:
                    escaped_xpath = html.escape(xpath[:200])
                    issue_details.append(
                        Paragraph(
                            f"<b>XPath:</b> <font face='Courier' size='8'>{escaped_xpath}</font>",
                            styles["Normal"],
                        )
                    )
                    issue_details.append(Spacer(1, 0.05 * inch))

                # Add screenshot if available
                if screenshot:
                    try:
                        # Decode base64 screenshot
                        screenshot_bytes = base64.b64decode(screenshot)
                        screenshot_buffer = BytesIO(screenshot_bytes)

                        # Match dashboard: max-w-full, max-h-[300px] (≈4.17 inches at 72 DPI)
                        # Get image dimensions to calculate aspect ratio
                        from PIL import Image as PILImage

                        pil_img = PILImage.open(screenshot_buffer)
                        img_width, img_height = pil_img.size

                        # Verify image is valid (not blank/corrupt)
                        if img_width == 0 or img_height == 0:
                            # Skip invalid images
                            screenshot_buffer.close()
                            raise ValueError("Invalid image dimensions")

                        screenshot_buffer.seek(0)  # Reset buffer after reading

                        # Calculate dimensions maintaining aspect ratio
                        # ReportLab uses points (72 points = 1 inch)
                        # Assume screenshots are at standard screen DPI (72 or 96)
                        # Use pixels directly as points for 1:1 rendering

                        max_width_pts = 4 * inch  # Max 4 inches = 288 points
                        max_height_pts = 4 * inch  # Max 4 inches = 288 points

                        # Calculate scale factor - NEVER scale UP, only scale DOWN
                        if img_width > max_width_pts or img_height > max_height_pts:
                            # Image is too large, scale it down
                            width_scale = max_width_pts / img_width
                            height_scale = max_height_pts / img_height
                            scale = min(width_scale, height_scale)
                            final_width = img_width * scale
                            final_height = img_height * scale
                        else:
                            # Image is small enough, use actual size (1px = 1pt)
                            final_width = img_width
                            final_height = img_height

                        img = Image(
                            screenshot_buffer, width=final_width, height=final_height
                        )
                        img.hAlign = "LEFT"
                        issue_details.append(img)
                        issue_details.append(Spacer(1, 0.1 * inch))
                    except Exception as e:
                        # If screenshot fails to load, log and skip it
                        import logging

                        logger = logging.getLogger(__name__)
                        logger.warning(
                            f"Failed to add screenshot to PDF: {type(e).__name__}: {str(e)}"
                        )

                # Escape HTML to prevent parsing errors in PDF
                escaped_element = html.escape(element[:200])
                # Use "Location" label for document scans, "Element" for website scans
                element_label = "Location" if issue.get("location") else "Element"
                issue_details.append(
                    Paragraph(
                        f"<b>{element_label}:</b> <font face='Courier'>{escaped_element}</font>",
                        styles["Normal"],
                    )
                )
                issue_details.append(Spacer(1, 0.05 * inch))
                issue_details.append(
                    Paragraph(f"<b>How to fix:</b> {fix}", styles["Normal"])
                )

                if generated_fix:
                    issue_details.append(Spacer(1, 0.05 * inch))
                    # Escape HTML in generated fix as well
                    escaped_generated_fix = html.escape(generated_fix[:500])
                    # Use appropriate label based on whether this is alt text or code
                    fix_label = (
                        "🤖 AI-Generated Alt Text:"
                        if issue.get("alt_text")
                        else "🤖 AI-Generated Fix:"
                    )
                    issue_details.append(
                        Paragraph(
                            f"<b>{fix_label}</b><br/><font face='Courier' size='8'>{escaped_generated_fix}</font>",
                            styles["Normal"],
                        )
                    )

                # Keep issue header and details together on same page
                issue_content = [issue_header] + issue_details + [Spacer(1, 0.1 * inch)]
                story.append(KeepTogether(issue_content))

        # Footer
        story.append(Spacer(1, 0.5 * inch))
        footer_text = Paragraph(
            "<b>Report generated by Aelira.ai</b><br/>Automated Accessibility Testing based on WCAG 2.1 Level AA Standards",
            ParagraphStyle(
                "Footer",
                parent=styles["Normal"],
                fontSize=9,
                textColor=colors.HexColor("#666666"),
                alignment=TA_CENTER,
            ),
        )
        story.append(footer_text)

        # Build PDF
        doc.build(story)

        # Get PDF bytes
        pdf_bytes = buffer.getvalue()
        buffer.close()

        return pdf_bytes
