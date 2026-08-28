"""
Email Template Rendering Functions

Standalone functions for rendering email templates.
These provide simple template rendering for alerts and notifications.

All emails use a unified brand template with:
- Aelira branded header (purple gradient)
- White card container on gray background
- Context-aware accent colors (green=success, red=error, amber=warning, blue=info)
- Consistent footer with privacy/unsubscribe links
"""

import html
from typing import List, Dict, Any, Optional

from src.education.deadline_config import DeadlineService

# Brand colors
BRAND_PRIMARY = "#8B5CF6"  # Purple
BRAND_SECONDARY = "#6366F1"  # Indigo
BRAND_ACCENT = "#3B82F6"  # Blue

# Context colors (Tailwind-inspired)
COLOR_SUCCESS = "#22c55e"
COLOR_SUCCESS_BG = "#f0fdf4"
COLOR_SUCCESS_DARK = "#16a34a"

COLOR_ERROR = "#ef4444"
COLOR_ERROR_BG = "#fee2e2"
COLOR_ERROR_DARK = "#dc2626"

COLOR_WARNING = "#f59e0b"
COLOR_WARNING_BG = "#fffbeb"
COLOR_WARNING_DARK = "#92400e"

COLOR_INFO = "#3b82f6"
COLOR_INFO_BG = "#eff6ff"
COLOR_INFO_DARK = "#1e40af"

COLOR_NEUTRAL = "#6b7280"
COLOR_NEUTRAL_BG = "#f3f4f6"


def _deadline_guidance_html(deadline: Any) -> str:
    """Render bounded, escaped guidance from a canonical deadline object."""
    if deadline is None:
        return ""

    applicability = str(getattr(deadline, "applicability", "") or "").lower()
    if applicability in {"none", "not_applicable", "not-applicable"}:
        return ""

    framework = html.escape(str(getattr(deadline, "framework_name", "") or ""))
    standard = html.escape(str(getattr(deadline, "standard", "") or ""))
    label = html.escape(str(getattr(deadline, "deadline_label", "") or ""))
    message = html.escape(str(getattr(deadline, "message", "") or ""))

    metadata = " &bull; ".join(value for value in (framework, standard) if value)
    if getattr(deadline, "has_deadline", False):
        heading = "Configured accessibility target"
        details = " &bull; ".join(value for value in (label, metadata) if value)
    else:
        heading = "Accessibility guidance"
        details = metadata

    paragraphs = [
        f'<p style="margin: 0; font-size: 14px;"><strong>{heading}:</strong> {details}</p>'
    ]
    if message:
        paragraphs.append(
            f'<p style="margin: 8px 0 0 0; font-size: 13px;">{message}</p>'
        )
    return (
        '<div style="background-color: #eff6ff; border-left: 4px solid #3b82f6; '
        "border-radius: 0 8px 8px 0; padding: 12px 16px; margin: 0 0 24px 0; "
        'color: #1e3a8a;">' + "".join(paragraphs) + "</div>"
    )


def _deadline_for_email(department: Any = None, deadline: Any = None) -> Any:
    if deadline is not None:
        return deadline
    if department is not None:
        return DeadlineService.for_department(department)
    return None


def get_email_wrapper(
    content: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Wrap email content in branded Aelira template.

    Args:
        content: HTML content to wrap (goes inside the white card)
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        Complete HTML email with branded header/footer
    """
    unsubscribe_link = ""
    if unsubscribe_url:
        unsubscribe_link = f'<a href="{unsubscribe_url}" style="color: #9ca3af; text-decoration: underline;">Unsubscribe</a> | '

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Aelira</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color: #f3f4f6;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <!-- Main container -->
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width: 600px; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">

                    <!-- Header -->
                    <tr>
                        <td style="background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 50%, #3B82F6 100%); padding: 32px; text-align: center;">
                            <a href="https://example.com" style="text-decoration: none;">
                                <img src="https://api.example.com/static/logo.png" alt="Aelira" width="180" style="display: inline-block; max-width: 180px; height: auto; margin-bottom: 12px;" />
                            </a>
                            <p style="margin: 0; font-size: 12px; color: rgba(255, 255, 255, 0.85); text-transform: uppercase; letter-spacing: 1.5px;">Higher Education Accessibility</p>
                        </td>
                    </tr>

                    <!-- Content -->
                    <tr>
                        <td style="padding: 32px 40px; line-height: 1.6; color: #333;">
                            {content}
                        </td>
                    </tr>

                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #1f2937; padding: 24px 40px; text-align: center;">
                            <p style="margin: 0 0 12px 0; font-size: 12px; color: #9ca3af;">
                                {unsubscribe_link}<a href="https://example.com/privacy" style="color: #9ca3af; text-decoration: underline;">Privacy Policy</a> |
                                <a href="mailto:support@example.com" style="color: #9ca3af; text-decoration: underline;">Support</a>
                            </p>
                            <p style="margin: 0; font-size: 11px; color: #6b7280;">
                                © 2026 Aelira. All rights reserved.
                            </p>
                        </td>
                    </tr>

                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""


def get_email_footer(unsubscribe_url: Optional[str] = None) -> str:
    """
    Generate standard email footer with unsubscribe link.

    DEPRECATED: Use get_email_wrapper() instead for new templates.
    Kept for backwards compatibility with existing templates.

    Args:
        unsubscribe_url: Full URL for unsubscribe link. If None, shows generic footer.

    Returns:
        HTML string for email footer
    """
    unsubscribe_section = ""
    if unsubscribe_url:
        unsubscribe_section = f"""
            <a href="{unsubscribe_url}" style="color: #9ca3af; text-decoration: underline;">Unsubscribe</a> |
        """

    return f"""
        <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 32px 0 16px 0;">
        <p style="color: #9ca3af; font-size: 12px; text-align: center; margin: 0;">
            You're receiving this because you use Aelira.<br>
            {unsubscribe_section}
            <a href="https://example.com/privacy" style="color: #9ca3af; text-decoration: underline;">Privacy Policy</a>
        </p>
        <p style="color: #9ca3af; font-size: 12px; text-align: center; margin: 8px 0 0 0;">
            © 2026 Aelira. All rights reserved.
        </p>
    """


def render_scan_complete_email(
    file_name: str,
    issues_found: int,
    compliance_score: float,
    scan_url: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render scan complete email template.

    Args:
        file_name: Name of the scanned file
        issues_found: Number of issues found
        compliance_score: Compliance score (0-1 or 0-100)
        scan_url: URL to view scan results
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    # Normalize score to 0-100 range
    if compliance_score <= 1.0:
        compliance_score = compliance_score * 100

    # Determine status colors and text
    if compliance_score >= 90:
        status_color = COLOR_SUCCESS
        status_bg = COLOR_SUCCESS_BG
        status_text = "Excellent"
        badge_text = "✓ Compliant"
    elif compliance_score >= 70:
        status_color = COLOR_WARNING
        status_bg = COLOR_WARNING_BG
        status_text = "Needs Improvement"
        badge_text = "⚠ Review Needed"
    else:
        status_color = COLOR_ERROR
        status_bg = COLOR_ERROR_BG
        status_text = "Critical Issues Found"
        badge_text = "⚠ Action Required"

    content = f"""
            <!-- Status Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {status_bg}; border: 2px solid {status_color}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {status_color};">
                    {badge_text}
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Scan Complete</h2>

            <p style="margin: 0 0 24px 0;">The accessibility scan for <strong>{file_name}</strong> has completed.</p>

            <!-- Score Box -->
            <div style="text-align: center; margin: 0 0 24px 0; padding: 24px; background: {status_bg}; border-radius: 8px;">
                <div style="font-size: 48px; font-weight: bold; color: {status_color};">
                    {compliance_score:.0f}%
                </div>
                <div style="color: {status_color}; font-weight: 600; margin-top: 8px;">{status_text}</div>
                <div style="color: #666; margin-top: 8px;">{issues_found} issue{"s" if issues_found != 1 else ""} found</div>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{scan_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    View Detailed Results
                </a>
            </div>

            <p style="color: #666; font-size: 14px; margin: 0; text-align: center;">
                Review the results and use Aelira's AI-powered remediation to fix issues automatically.
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_critical_issue_email(
    file_name: str,
    critical_issues: List[Dict[str, Any]],
    scan_url: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render critical issue alert email template.

    Args:
        file_name: Name of the file with critical issues
        critical_issues: List of critical issues with 'type' and 'count' or 'description'
        scan_url: URL to view scan results
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    # Build issues list HTML
    issues_html = ""
    for issue in critical_issues[:5]:  # Show top 5
        issue_type = issue.get("type", "Unknown")
        count = issue.get("count", "")
        description = issue.get("description", "")

        issues_html += f"""
                <tr>
                    <td style="padding: 12px 0; border-bottom: 1px solid #fecaca;">
                        <strong style="color: {COLOR_ERROR_DARK};">{issue_type}</strong>
                        {f'<span style="color: #666;"> - {count} instance{"s" if count != 1 else ""}</span>' if count else ''}
                        {f'<br><span style="color: #666; font-size: 14px;">{description}</span>' if description else ''}
                    </td>
                </tr>
        """

    more_issues_html = ""
    if len(critical_issues) > 5:
        more_issues_html = f'<p style="color: #666; font-size: 14px; margin: 16px 0 0 0;">... and {len(critical_issues) - 5} more critical issues</p>'

    content = f"""
            <!-- Critical Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_ERROR_BG}; border: 2px solid {COLOR_ERROR}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_ERROR_DARK};">
                    ⚠️ Critical Issues Detected
                </span>
            </div>

            <h2 style="color: {COLOR_ERROR}; text-align: center; margin: 0 0 24px 0;">Action Required</h2>

            <div style="background-color: {COLOR_ERROR_BG}; border-left: 4px solid {COLOR_ERROR}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <p style="margin: 0; color: {COLOR_ERROR_DARK};">
                    <strong>{len(critical_issues)} critical accessibility issue{"s" if len(critical_issues) != 1 else ""}</strong> found in <strong>{file_name}</strong>.
                </p>
            </div>

            <p style="margin: 0 0 16px 0;">These issues may prevent users with disabilities from accessing the content:</p>

            <!-- Issues Table -->
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin: 0 0 16px 0;">
                {issues_html}
            </table>
            {more_issues_html}

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{scan_url}" style="display: inline-block; background: {COLOR_ERROR}; color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    View & Fix Issues
                </a>
            </div>

            <p style="color: #666; font-size: 14px; margin: 0; text-align: center;">
                Use Aelira's AI-powered auto-remediation to fix most issues automatically.
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_weekly_summary_email(
    start_date: str,
    end_date: str,
    total_scans: int,
    total_issues: int,
    avg_compliance_score: float,
    issues_fixed: int = 0,
    critical_count: int = 0,
    serious_count: int = 0,
    moderate_count: int = 0,
    minor_count: int = 0,
    dashboard_url: str = "https://dashboard.example.com",
    unsubscribe_url: Optional[str] = None,
    department: Any = None,
    deadline: Any = None,
) -> str:
    """
    Render weekly summary email template.

    Args:
        start_date: Start date of the summary period
        end_date: End date of the summary period
        total_scans: Total number of scans performed
        total_issues: Total number of issues found
        avg_compliance_score: Average compliance score (0-1 or 0-100)
        issues_fixed: Number of issues fixed this week
        critical_count: Number of critical issues
        serious_count: Number of serious issues
        moderate_count: Number of moderate issues
        minor_count: Number of minor issues
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    # Normalize score to 0-100 range
    if avg_compliance_score <= 1.0:
        avg_compliance_score = avg_compliance_score * 100

    # Determine score color
    if avg_compliance_score >= 90:
        score_color = COLOR_SUCCESS
        score_bg = COLOR_SUCCESS_BG
    elif avg_compliance_score >= 70:
        score_color = COLOR_WARNING
        score_bg = COLOR_WARNING_BG
    else:
        score_color = COLOR_ERROR
        score_bg = COLOR_ERROR_BG

    # Calculate progress bar width
    progress_width = min(100, max(0, avg_compliance_score))

    deadline_guidance = _deadline_guidance_html(
        _deadline_for_email(department, deadline)
    )
    content = f"""
            <h2 style="color: #1f2937; text-align: center; margin: 0 0 8px 0;">Weekly Accessibility Summary</h2>
            <p style="color: #666; text-align: center; margin: 0 0 24px 0; font-size: 14px;">{start_date} - {end_date}</p>

            <!-- Stats Grid -->
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin: 0 0 24px 0;">
                <tr>
                    <td width="33%" style="text-align: center; padding: 16px; background: {COLOR_INFO_BG}; border-radius: 8px 0 0 8px;">
                        <div style="font-size: 28px; font-weight: bold; color: {COLOR_INFO};">{total_scans}</div>
                        <div style="font-size: 12px; color: #666; margin-top: 4px;">Scans</div>
                    </td>
                    <td width="34%" style="text-align: center; padding: 16px; background: {score_bg};">
                        <div style="font-size: 28px; font-weight: bold; color: {score_color};">{avg_compliance_score:.0f}%</div>
                        <div style="font-size: 12px; color: #666; margin-top: 4px;">Avg Score</div>
                    </td>
                    <td width="33%" style="text-align: center; padding: 16px; background: {COLOR_SUCCESS_BG}; border-radius: 0 8px 8px 0;">
                        <div style="font-size: 28px; font-weight: bold; color: {COLOR_SUCCESS};">{issues_fixed}</div>
                        <div style="font-size: 12px; color: #666; margin-top: 4px;">Fixed</div>
                    </td>
                </tr>
            </table>

            <!-- Issues Breakdown -->
            <div style="background: #f9fafb; border-radius: 8px; padding: 20px; margin: 0 0 24px 0;">
                <p style="margin: 0 0 16px 0; font-weight: 600; color: #1f2937;">Issues by Severity</p>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                        <td style="padding: 8px 0; color: {COLOR_ERROR}; font-weight: 600;">Critical</td>
                        <td style="padding: 8px 0; text-align: right;">{critical_count}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: {COLOR_WARNING}; font-weight: 600;">Serious</td>
                        <td style="padding: 8px 0; text-align: right;">{serious_count}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: {COLOR_INFO}; font-weight: 600;">Moderate</td>
                        <td style="padding: 8px 0; text-align: right;">{moderate_count}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: {COLOR_NEUTRAL}; font-weight: 600;">Minor</td>
                        <td style="padding: 8px 0; text-align: right;">{minor_count}</td>
                    </tr>
                    <tr style="border-top: 1px solid #e5e7eb;">
                        <td style="padding: 12px 0 0 0; font-weight: 700; color: #1f2937;">Total Issues</td>
                        <td style="padding: 12px 0 0 0; text-align: right; font-weight: 700;">{total_issues}</td>
                    </tr>
                </table>
            </div>

            <!-- Progress Bar -->
            <div style="margin: 0 0 24px 0;">
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #666;">Automated Scan Score</p>
                <div style="background: #e5e7eb; border-radius: 4px; height: 8px; overflow: hidden;">
                    <div style="background: {score_color}; width: {progress_width}%; height: 100%;"></div>
                </div>
            </div>

            <p style="color: #666; font-size: 13px; margin: 0 0 24px 0;">
                Automated scan results are bounded evidence and do not determine
                accessibility-standard conformance or legal compliance.
            </p>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    View Full Report
                </a>
            </div>

            {deadline_guidance}
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_remediation_success_email(
    file_name: str,
    fixed_count: int,
    manual_count: int,
    compliance_improvement: float,
    output_file_url: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render remediation success email template.

    Args:
        file_name: Name of the remediated file
        fixed_count: Number of issues fixed automatically
        manual_count: Number of issues needing manual review
        compliance_improvement: Percentage improvement in compliance score
        output_file_url: URL to view the remediated file
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Success Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_SUCCESS_BG}; border: 2px solid {COLOR_SUCCESS}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_SUCCESS_DARK};">
                    ✓ Remediation Complete
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">File Successfully Remediated</h2>

            <p style="margin: 0 0 24px 0;">Great news! Accessibility remediation for <strong>{file_name}</strong> has completed successfully.</p>

            <!-- Results Box -->
            <div style="background-color: {COLOR_SUCCESS_BG}; border-left: 4px solid {COLOR_SUCCESS}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                        <td style="padding: 6px 0; color: #333;">Issues Fixed Automatically</td>
                        <td style="padding: 6px 0; text-align: right; font-weight: 700; color: {COLOR_SUCCESS};">{fixed_count}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; color: #333;">Needs Manual Review</td>
                        <td style="padding: 6px 0; text-align: right; font-weight: 600;">{manual_count}</td>
                    </tr>
                    <tr style="border-top: 1px solid {COLOR_SUCCESS};">
                        <td style="padding: 10px 0 0 0; color: #333;">Compliance Improvement</td>
                        <td style="padding: 10px 0 0 0; text-align: right; font-weight: 700; font-size: 18px; color: {COLOR_SUCCESS};">+{compliance_improvement:.1f}%</td>
                    </tr>
                </table>
            </div>

            <p style="margin: 0 0 24px 0; color: #666; font-size: 14px;">
                A new remediated version has been created with the suffix "_remediated".
            </p>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{output_file_url}" style="display: inline-block; background: {COLOR_SUCCESS}; color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    View Remediated File
                </a>
            </div>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_remediation_partial_email(
    file_name: str,
    fixed_count: int,
    failed_count: int,
    manual_count: int,
    error_summary: str,
    output_file_url: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render partial remediation email template.

    Args:
        file_name: Name of the remediated file
        fixed_count: Number of issues fixed successfully
        failed_count: Number of fixes that failed
        manual_count: Number of issues needing manual review
        error_summary: Brief summary of errors encountered
        output_file_url: URL to view the partially remediated file
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Warning Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_WARNING_BG}; border: 2px solid {COLOR_WARNING}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_WARNING_DARK};">
                    ⚠️ Partial Success
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Remediation Completed with Issues</h2>

            <p style="margin: 0 0 24px 0;">Remediation for <strong>{file_name}</strong> completed, but some fixes could not be applied.</p>

            <!-- Results Box -->
            <div style="background-color: {COLOR_WARNING_BG}; border-left: 4px solid {COLOR_WARNING}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                        <td style="padding: 6px 0; color: #333;">Issues Fixed Successfully</td>
                        <td style="padding: 6px 0; text-align: right; font-weight: 700; color: {COLOR_SUCCESS};">{fixed_count}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; color: #333;">Fixes Failed</td>
                        <td style="padding: 6px 0; text-align: right; font-weight: 700; color: {COLOR_ERROR};">{failed_count}</td>
                    </tr>
                    <tr>
                        <td style="padding: 6px 0; color: #333;">Needs Manual Review</td>
                        <td style="padding: 6px 0; text-align: right; font-weight: 600;">{manual_count}</td>
                    </tr>
                </table>
            </div>

            <!-- Error Summary -->
            <div style="background-color: {COLOR_ERROR_BG}; border-radius: 8px; padding: 16px; margin: 0 0 24px 0;">
                <p style="margin: 0 0 8px 0; font-weight: 600; color: {COLOR_ERROR_DARK};">Error Summary</p>
                <p style="margin: 0; color: #666; font-size: 14px;">{error_summary}</p>
            </div>

            <p style="margin: 0 0 24px 0; color: #666; font-size: 14px;">
                The file has been saved with the successful fixes applied. You can retry the failed fixes or address them manually.
            </p>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{output_file_url}" style="display: inline-block; background: {COLOR_WARNING}; color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    View Partially Remediated File
                </a>
            </div>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_remediation_failure_email(
    file_name: str,
    error_message: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render remediation failure email template.

    Args:
        file_name: Name of the file that failed remediation
        error_message: Error message describing what went wrong
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Error Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_ERROR_BG}; border: 2px solid {COLOR_ERROR}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_ERROR_DARK};">
                    ✗ Remediation Failed
                </span>
            </div>

            <h2 style="color: {COLOR_ERROR}; text-align: center; margin: 0 0 24px 0;">Unable to Process File</h2>

            <p style="margin: 0 0 24px 0;">Unfortunately, remediation for <strong>{file_name}</strong> could not be completed.</p>

            <!-- Error Box -->
            <div style="background-color: {COLOR_ERROR_BG}; border-left: 4px solid {COLOR_ERROR}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <p style="margin: 0; color: {COLOR_ERROR_DARK};">
                    <strong>Error:</strong> {error_message}
                </p>
            </div>

            <p style="margin: 0 0 12px 0;"><strong>What to try:</strong></p>
            <ul style="color: #666; padding-left: 20px; margin: 0 0 24px 0;">
                <li>Verify the file format is supported (PDF, DOCX, PPTX, XLSX)</li>
                <li>Check that the file isn't password-protected</li>
                <li>Try uploading the file again</li>
                <li>Contact support if the issue persists</li>
            </ul>

            <p style="color: #666; font-size: 14px; margin: 0;">
                Need help? Contact us at <a href="mailto:support@example.com" style="color: {BRAND_PRIMARY};">support@example.com</a>
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_department_welcome_email(
    name: str,
    department_name: str,
    institution: str,
    dashboard_url: str,
    unsubscribe_url: Optional[str] = None,
    department: Any = None,
    deadline: Any = None,
) -> str:
    """
    Render department welcome email.

    Args:
        name: Contact name
        department_name: Department name
        institution: Institution name
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    deadline_guidance = _deadline_guidance_html(
        _deadline_for_email(department, deadline)
    )
    content = f"""
            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Welcome to Aelira!</h2>

            <p style="margin: 0 0 16px 0;">Hi {name or "there"},</p>

            <p style="margin: 0 0 24px 0;">The department workspace <strong>{department_name}</strong> at <strong>{institution}</strong> is now active.</p>

            <p style="margin: 0 0 12px 0;"><strong>What this workspace can do:</strong></p>
            <ul style="color: #333; padding-left: 20px; margin: 0 0 24px 0;">
                <li>Scan documents for accessibility issues (PDF, DOCX, PPTX, Excel)</li>
                <li>Auto-remediate accessibility issues with AI</li>
                <li>Convert LaTeX equations to accessible MathML</li>
                <li>Generate transcriptions for video content</li>
                <li>Connect to Canvas, Blackboard, Google, and Microsoft</li>
                <li>Invite your team members</li>
            </ul>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    Start Scanning Documents
                </a>
            </div>

            {deadline_guidance}
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_faculty_invitation_email(
    inviter_name: str,
    department_name: str,
    institution: str,
    accept_url: str,
    expires_in_days: int = 7,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render faculty team invitation email.

    Args:
        inviter_name: Name of the person who sent the invitation
        department_name: Department name
        institution: Institution name
        accept_url: URL to accept the invitation
        expires_in_days: Days until invitation expires
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Invitation Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_INFO_BG}; border: 2px solid {COLOR_INFO}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_INFO_DARK};">
                    ✉️ Team Invitation
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">You're Invited to Join Aelira</h2>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6;">
                <strong>{inviter_name}</strong> has invited you to join the <strong>{department_name}</strong> team at <strong>{institution}</strong> on Aelira.
            </p>

            <!-- What You'll Get -->
            <div style="background-color: #f9fafb; border-radius: 8px; padding: 20px; margin: 0 0 24px 0;">
                <p style="margin: 0 0 12px 0; font-weight: 600; color: #1f2937;">What you'll be able to do:</p>
                <ul style="color: #4b5563; margin: 0; padding-left: 20px;">
                    <li style="margin-bottom: 8px;">Scan documents for accessibility issues</li>
                    <li style="margin-bottom: 8px;">Auto-remediate PDFs, PowerPoints, Word docs, and Excel files</li>
                    <li style="margin-bottom: 8px;">Convert LaTeX equations to accessible MathML</li>
                    <li style="margin-bottom: 8px;">View your department's compliance dashboard</li>
                </ul>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{accept_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 16px 40px; border-radius: 8px;">
                    Accept Invitation
                </a>
            </div>

            <!-- Expiration Warning -->
            <div style="background-color: {COLOR_WARNING_BG}; border-left: 4px solid {COLOR_WARNING}; border-radius: 0 8px 8px 0; padding: 12px 16px; margin: 0 0 24px 0;">
                <p style="margin: 0; font-size: 14px; color: {COLOR_WARNING_DARK};">
                    ⏰ This invitation expires in <strong>{expires_in_days} days</strong>.
                </p>
            </div>

            <p style="color: #666; font-size: 14px; margin: 0;">
                If you didn't expect this invitation, you can safely ignore this email.
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


# =============================================================================
# Account Deletion Templates
# =============================================================================


def render_deletion_code_email(
    name: str,
    code: str,
) -> tuple:
    """
    Render account deletion confirmation code email.

    Transactional email with a 6-digit code for account deletion verification.

    Args:
        name: Recipient's name
        code: 6-digit confirmation code

    Returns:
        Tuple of (html_body, text_body)
    """
    content = f"""
            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Account Deletion Request</h2>

            <p style="margin: 0 0 16px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Hi {name},
            </p>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                You requested to permanently delete your Aelira account. Use the confirmation code below to proceed:
            </p>

            <!-- Code Box -->
            <div style="text-align: center; margin: 32px 0;">
                <div style="display: inline-block; background: {COLOR_ERROR_BG}; border: 2px solid {COLOR_ERROR}; border-radius: 12px; padding: 20px 40px;">
                    <span style="font-size: 32px; font-weight: 700; letter-spacing: 8px; color: {COLOR_ERROR_DARK}; font-family: monospace;">
                        {code}
                    </span>
                </div>
            </div>

            <p style="margin: 0 0 16px 0; font-size: 14px; color: #6b7280; text-align: center;">
                This code expires in <strong>15 minutes</strong>.
            </p>

            <!-- Warning Box -->
            <div style="background-color: {COLOR_ERROR_BG}; border-left: 4px solid {COLOR_ERROR}; border-radius: 0 8px 8px 0; padding: 16px; margin: 24px 0;">
                <p style="margin: 0 0 8px 0; font-size: 14px; font-weight: 600; color: {COLOR_ERROR_DARK};">
                    This action is irreversible
                </p>
                <p style="margin: 0; font-size: 13px; color: {COLOR_ERROR_DARK};">
                    After confirmation, your account will be deactivated immediately.
                    All data will be permanently deleted after a 30-day grace period.
                </p>
            </div>

            <p style="margin: 24px 0 0 0; font-size: 13px; color: #9ca3af; text-align: center;">
                Didn't request this? You can safely ignore this email. Your account will not be affected.
            </p>
    """
    html_body = get_email_wrapper(content)

    text_body = f"""Account Deletion Request

Hi {name},

You requested to permanently delete your Aelira account.
Use this confirmation code: {code}

This code expires in 15 minutes.

WARNING: This action is irreversible. After confirmation, your account will be
deactivated immediately. All data will be permanently deleted after 30 days.

Didn't request this? You can safely ignore this email.

--
Aelira
https://example.com"""

    return html_body, text_body


def render_deletion_scheduled_email(
    name: str,
    scheduled_date: str,
) -> tuple:
    """
    Render account deletion scheduled confirmation email.

    Transactional email confirming deletion is scheduled.

    Args:
        name: Recipient's name
        scheduled_date: Formatted date when deletion will occur

    Returns:
        Tuple of (html_body, text_body)
    """
    content = f"""
            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Account Deletion Scheduled</h2>

            <p style="margin: 0 0 16px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Hi {name},
            </p>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Your Aelira account has been deactivated and is scheduled for permanent deletion on:
            </p>

            <!-- Date Box -->
            <div style="text-align: center; margin: 24px 0;">
                <div style="display: inline-block; background: {COLOR_NEUTRAL_BG}; border-radius: 12px; padding: 16px 32px;">
                    <span style="font-size: 20px; font-weight: 600; color: #1f2937;">
                        {scheduled_date}
                    </span>
                </div>
            </div>

            <!-- Info Box -->
            <div style="background-color: {COLOR_INFO_BG}; border-left: 4px solid {COLOR_INFO}; border-radius: 0 8px 8px 0; padding: 16px; margin: 24px 0;">
                <p style="margin: 0 0 8px 0; font-size: 14px; font-weight: 600; color: {COLOR_INFO_DARK};">
                    Changed your mind?
                </p>
                <p style="margin: 0; font-size: 13px; color: {COLOR_INFO_DARK};">
                    You can cancel the deletion within 30 days by contacting us at
                    <a href="mailto:hello@example.com" style="color: {BRAND_SECONDARY};">hello@example.com</a>.
                </p>
            </div>

            <p style="margin: 24px 0 0 0; font-size: 14px; color: #4b5563;">
                After this date, all your data including scan history, reports, and account information will be permanently removed.
            </p>

            <p style="margin: 16px 0 0 0; font-size: 13px; color: #9ca3af; text-align: center;">
                Thank you for using Aelira. We're sorry to see you go.
            </p>
    """
    html_body = get_email_wrapper(content)

    text_body = f"""Account Deletion Scheduled

Hi {name},

Your Aelira account has been deactivated and is scheduled for permanent deletion on {scheduled_date}.

Changed your mind? You can cancel the deletion within 30 days by contacting us at hello@example.com.

After this date, all your data including scan history, reports, and account information will be permanently removed.

Thank you for using Aelira. We're sorry to see you go.

--
Aelira
https://example.com"""

    return html_body, text_body


__all__ = [
    # Base template
    "get_email_wrapper",
    "get_email_footer",  # Deprecated, use get_email_wrapper
    # Color constants
    "COLOR_SUCCESS",
    "COLOR_SUCCESS_BG",
    "COLOR_SUCCESS_DARK",
    "COLOR_ERROR",
    "COLOR_ERROR_BG",
    "COLOR_ERROR_DARK",
    "COLOR_WARNING",
    "COLOR_WARNING_BG",
    "COLOR_WARNING_DARK",
    "COLOR_INFO",
    "COLOR_INFO_BG",
    "COLOR_INFO_DARK",
    "COLOR_NEUTRAL",
    "COLOR_NEUTRAL_BG",
    "BRAND_PRIMARY",
    "BRAND_SECONDARY",
    "BRAND_ACCENT",
    # Scan/Remediation emails
    "render_scan_complete_email",
    "render_critical_issue_email",
    "render_weekly_summary_email",
    "render_remediation_success_email",
    "render_remediation_partial_email",
    "render_remediation_failure_email",
    # Onboarding/Welcome emails
    "render_department_welcome_email",
    "render_faculty_invitation_email",
    # Account deletion emails
    "render_deletion_code_email",
    "render_deletion_scheduled_email",
]
