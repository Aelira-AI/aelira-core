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

from typing import List, Dict, Any, Optional

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


def wrap_campaign_content(
    html_content: str,
    plain_content: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    Wrap campaign content in the branded Aelira email template.

    If html_content is already a full HTML document (contains <html or <!DOCTYPE),
    it is returned as-is. Otherwise, the content is treated as body-only and
    wrapped in the branded template with header, logo, and footer.

    Args:
        html_content: Campaign HTML (body fragment or full document)
        plain_content: Plain text fallback (optional)
        unsubscribe_url: Unsubscribe URL (uses {{unsubscribe_url}} placeholder if None)

    Returns:
        Tuple of (wrapped_html, plain_content_with_footer)
    """
    unsub = unsubscribe_url or "{{unsubscribe_url}}"

    # If the content is already a full HTML document, just ensure the footer exists
    content_lower = html_content.strip().lower()
    if content_lower.startswith("<!doctype") or content_lower.startswith("<html"):
        # Full HTML — inject unsubscribe footer before </body> if not present
        if "unsubscribe" not in html_content.lower():
            import re

            footer = (
                '<div style="text-align: center; padding: 16px; font-size: 12px; color: #6b7280;">'
                f'<a href="{unsub}" style="color: #6b7280; text-decoration: underline;">Unsubscribe</a> | '
                '<a href="https://example.com/privacy" style="color: #6b7280; text-decoration: underline;">Privacy Policy</a>'
                "</div>"
            )
            html_content = re.sub(
                r"(</body>)",
                f"{footer}\\1",
                html_content,
                flags=re.IGNORECASE,
            )
        wrapped_html = html_content
    else:
        # Body-only content — wrap in branded template
        wrapped_html = get_email_wrapper(html_content, unsubscribe_url=unsub)

    # Add unsubscribe footer to plain text
    wrapped_plain = plain_content
    if wrapped_plain and "unsubscribe" not in wrapped_plain.lower():
        wrapped_plain += (
            f"\n\n---\nUnsubscribe: {unsub}\nPrivacy: https://example.com/privacy\n"
        )

    return wrapped_html, wrapped_plain


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
            You're receiving this because you use Aelira or signed up for our waitlist.<br>
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
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #666;">Compliance Progress</p>
                <div style="background: #e5e7eb; border-radius: 4px; height: 8px; overflow: hidden;">
                    <div style="background: {score_color}; width: {progress_width}%; height: 100%;"></div>
                </div>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    View Full Report
                </a>
            </div>

            <!-- Deadline Reminder -->
            <div style="background-color: {COLOR_ERROR_BG}; border-left: 4px solid {COLOR_ERROR}; border-radius: 0 8px 8px 0; padding: 12px 16px; margin: 0;">
                <p style="margin: 0; font-size: 14px; color: {COLOR_ERROR_DARK};">
                    <strong>Reminder:</strong> WCAG 2.1 compliance deadline is April 26, 2027.
                </p>
            </div>
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


def render_upgrade_success_email(
    name: str,
    tier: str,
    amount: float,
    billing_period: str,
    dashboard_url: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render upgrade success confirmation email.

    Args:
        name: User's name
        tier: New tier (e.g., "individual_plus", "individual_pro")
        amount: Amount charged
        billing_period: "monthly" or "yearly"
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    tier_display = tier.replace("individual_", "").title()
    period_display = "month" if billing_period == "monthly" else "year"

    # Dynamic features based on tier
    if tier == "individual_pro":
        features = "<li>Unlimited scans</li><li>10,000 pages/month</li><li>Priority support</li>"
    else:
        features = (
            "<li>500 scans/month</li><li>2,000 pages/month</li><li>Email support</li>"
        )

    content = f"""
            <!-- Success Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_SUCCESS_BG}; border: 2px solid {COLOR_SUCCESS}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_SUCCESS_DARK};">
                    ✓ Upgrade Complete
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Welcome to Aelira {tier_display}!</h2>

            <p style="margin: 0 0 16px 0;">Hi {name or "there"},</p>

            <p style="margin: 0 0 24px 0;">Thank you for upgrading! Your <strong>Aelira {tier_display}</strong> subscription is now active.</p>

            <!-- Plan Details Box -->
            <div style="background-color: {COLOR_SUCCESS_BG}; border-left: 4px solid {COLOR_SUCCESS}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <p style="margin: 0 0 8px 0;"><strong>Plan:</strong> {tier_display}</p>
                <p style="margin: 0 0 8px 0;"><strong>Amount:</strong> ${amount:.2f}/{period_display}</p>
                <p style="margin: 0;"><strong>Status:</strong> <span style="color: {COLOR_SUCCESS};">Active</span></p>
            </div>

            <p style="margin: 0 0 12px 0;"><strong>Your plan includes:</strong></p>
            <ul style="color: #333; padding-left: 20px; margin: 0 0 24px 0;">
                {features}
                <li>AI-powered remediation</li>
                <li>All document types (PDF, DOCX, PPTX, LaTeX)</li>
            </ul>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    Go to Dashboard
                </a>
            </div>

            <p style="color: #666; font-size: 14px; margin: 0;">
                You'll receive a receipt from Stripe shortly. Manage your subscription anytime in Settings.
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_subscription_cancelled_email(
    name: str,
    tier: str,
    end_date: str,
    resubscribe_url: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render subscription cancelled email.

    Args:
        name: User's name
        tier: Previous tier
        end_date: When access ends
        resubscribe_url: URL to resubscribe
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    tier_display = tier.replace("individual_", "").title()

    content = f"""
            <h2 style="color: {COLOR_NEUTRAL}; text-align: center; margin: 0 0 24px 0;">We're sorry to see you go</h2>

            <p style="margin: 0 0 16px 0;">Hi {name or "there"},</p>

            <p style="margin: 0 0 24px 0;">Your <strong>Aelira {tier_display}</strong> subscription has been cancelled.</p>

            <div style="background-color: {COLOR_NEUTRAL_BG}; border-left: 4px solid {COLOR_NEUTRAL}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <p style="margin: 0;">Your account has been downgraded to the free tier. You can continue using Aelira with limited features.</p>
            </div>

            <p style="margin: 0 0 12px 0;"><strong>What happens now:</strong></p>
            <ul style="color: #666; padding-left: 20px; margin: 0 0 24px 0;">
                <li>Your scan history is preserved</li>
                <li>Free tier limits now apply (10 scans/month)</li>
                <li>You can resubscribe anytime to restore full access</li>
            </ul>

            <p style="margin: 0 0 16px 0;">If you cancelled by mistake or change your mind, you can resubscribe at any time:</p>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{resubscribe_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    Resubscribe
                </a>
            </div>

            <p style="color: #666; font-size: 14px; margin: 0;">
                We'd love to hear why you left. Reply to this email with any feedback - it helps us improve.
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_department_trial_welcome_email(
    name: str,
    department_name: str,
    institution: str,
    trial_days: int,
    dashboard_url: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render department trial welcome email.

    Args:
        name: Contact name
        department_name: Department name
        institution: Institution name
        trial_days: Number of trial days
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Trial Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_INFO_BG}; border: 2px solid {COLOR_INFO}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_INFO_DARK};">
                    {trial_days}-Day Free Trial
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Welcome to Aelira!</h2>

            <p style="margin: 0 0 16px 0;">Hi {name or "there"},</p>

            <p style="margin: 0 0 24px 0;">Great news! Your department trial for <strong>{department_name}</strong> at <strong>{institution}</strong> is now active.</p>

            <div style="background-color: {COLOR_INFO_BG}; border-left: 4px solid {COLOR_INFO}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <p style="margin: 0; font-size: 16px; font-weight: 600; color: {COLOR_INFO_DARK};">Full access to all features</p>
                <p style="margin: 8px 0 0 0; color: {COLOR_INFO};">No credit card required during trial.</p>
            </div>

            <p style="margin: 0 0 12px 0;"><strong>What you can do during your trial:</strong></p>
            <ul style="color: #333; padding-left: 20px; margin: 0 0 24px 0;">
                <li>Scan unlimited documents (PDF, DOCX, PPTX, Excel)</li>
                <li>Auto-remediate accessibility issues with AI</li>
                <li>Convert LaTeX equations to accessible MathML</li>
                <li>Generate transcriptions for video content</li>
                <li>Connect to Canvas, Blackboard, Google, and Microsoft</li>
                <li>Add up to 5 team members</li>
            </ul>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    Start Scanning Documents
                </a>
            </div>

            <!-- Deadline Reminder -->
            <div style="background-color: {COLOR_ERROR_BG}; border-left: 4px solid {COLOR_ERROR}; border-radius: 0 8px 8px 0; padding: 12px 16px; margin: 0 0 16px 0;">
                <p style="margin: 0; font-size: 14px; color: {COLOR_ERROR_DARK};">
                    <strong>Reminder:</strong> WCAG 2.1 compliance deadline is April 26, 2027.
                </p>
            </div>

            <p style="color: #666; font-size: 14px; margin: 0;">
                Questions? Reply to this email or contact us at support@example.com.
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_payment_failed_email(
    name: str,
    tier: str,
    amount: float,
    update_payment_url: str,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render payment failed warning email.

    Args:
        name: User's name
        tier: Current tier
        amount: Amount that failed
        update_payment_url: URL to update payment method
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    tier_display = tier.replace("individual_", "").title()

    content = f"""
            <!-- Warning Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_WARNING_BG}; border: 2px solid {COLOR_WARNING}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_WARNING_DARK};">
                    ⚠️ Action Required
                </span>
            </div>

            <h2 style="color: {COLOR_WARNING}; text-align: center; margin: 0 0 24px 0;">Payment Failed</h2>

            <p style="margin: 0 0 16px 0;">Hi {name or "there"},</p>

            <p style="margin: 0 0 24px 0;">We were unable to process your payment of <strong>${amount:.2f}</strong> for your <strong>Aelira {tier_display}</strong> subscription.</p>

            <div style="background-color: {COLOR_WARNING_BG}; border-left: 4px solid {COLOR_WARNING}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <p style="margin: 0; color: {COLOR_WARNING_DARK};">
                    <strong>Please update your payment method</strong> to avoid interruption to your service.
                </p>
            </div>

            <p style="margin: 0 0 12px 0;">Common reasons for payment failure:</p>
            <ul style="color: #666; padding-left: 20px; margin: 0 0 24px 0;">
                <li>Expired credit card</li>
                <li>Insufficient funds</li>
                <li>Card declined by bank</li>
            </ul>

            <!-- CTA Button (amber for urgency) -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{update_payment_url}" style="display: inline-block; background: {COLOR_WARNING}; color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    Update Payment Method
                </a>
            </div>

            <p style="color: #666; font-size: 14px; margin: 0 0 12px 0;">
                We'll automatically retry the payment in a few days. If it fails again, your subscription will be cancelled and your account downgraded to the free tier.
            </p>

            <p style="color: #666; font-size: 14px; margin: 0;">
                Need help? Contact us at support@example.com.
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_welcome_individual_email(
    name: str,
    api_key: str,
    scans_limit: int = 10,
    pages_limit: int = 50,
    dashboard_url: str = "https://dashboard.example.com",
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render welcome email for individual faculty signup.

    Args:
        name: User's name
        api_key: The API key (shown only once)
        scans_limit: Monthly scan limit for free tier
        pages_limit: Page limit per document
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Welcome Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_SUCCESS_BG}; border: 2px solid {COLOR_SUCCESS}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_SUCCESS_DARK};">
                    ✓ Account Created
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Welcome, {name}!</h2>

            <p style="margin: 0 0 24px 0; text-align: center;">
                Your free Aelira account is ready. You can now scan PDFs, PowerPoint, Word, and Excel files for accessibility issues.
            </p>

            <!-- API Key Section -->
            <div style="background-color: {COLOR_WARNING_BG}; border: 1px solid {COLOR_WARNING}; border-radius: 8px; padding: 16px; margin: 0 0 24px 0;">
                <p style="color: {COLOR_WARNING_DARK}; font-weight: 600; margin: 0 0 8px 0;">
                    ⚠️ Important: Save Your API Key
                </p>
                <p style="color: #78350f; font-size: 14px; margin: 0 0 12px 0;">
                    This key will only be shown once. Store it safely!
                </p>
                <div style="background-color: #1f2937; border-radius: 4px; padding: 12px; font-family: monospace; font-size: 13px; color: {COLOR_SUCCESS}; word-break: break-all;">
                    {api_key}
                </div>
            </div>

            <!-- Quick Start Steps -->
            <p style="margin: 0 0 16px 0; font-weight: 600; color: #1f2937; font-size: 18px;">Quick Start</p>
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin: 0 0 24px 0;">
                <tr>
                    <td style="padding: 12px 0; vertical-align: top;">
                        <div style="width: 24px; height: 24px; background-color: {BRAND_PRIMARY}; border-radius: 50%; color: white; font-size: 12px; font-weight: bold; display: inline-block; text-align: center; line-height: 24px; margin-right: 12px;">1</div>
                        <strong>Log in to Dashboard:</strong> Go to dashboard.example.com and paste your API key
                    </td>
                </tr>
                <tr>
                    <td style="padding: 12px 0; vertical-align: top;">
                        <div style="width: 24px; height: 24px; background-color: {BRAND_PRIMARY}; border-radius: 50%; color: white; font-size: 12px; font-weight: bold; display: inline-block; text-align: center; line-height: 24px; margin-right: 12px;">2</div>
                        <strong>Upload a File:</strong> Drop in your PDF, PowerPoint, Word, or Excel file
                    </td>
                </tr>
                <tr>
                    <td style="padding: 12px 0; vertical-align: top;">
                        <div style="width: 24px; height: 24px; background-color: {BRAND_PRIMARY}; border-radius: 50%; color: white; font-size: 12px; font-weight: bold; display: inline-block; text-align: center; line-height: 24px; margin-right: 12px;">3</div>
                        <strong>Review Results:</strong> See issues found and AI-generated fixes
                    </td>
                </tr>
                <tr>
                    <td style="padding: 12px 0; vertical-align: top;">
                        <div style="width: 24px; height: 24px; background-color: {BRAND_PRIMARY}; border-radius: 50%; color: white; font-size: 12px; font-weight: bold; display: inline-block; text-align: center; line-height: 24px; margin-right: 12px;">4</div>
                        <strong>Download Fixed Version:</strong> Get your accessible file ready for your LMS
                    </td>
                </tr>
            </table>

            <!-- Quota Info -->
            <div style="background-color: {COLOR_INFO_BG}; border: 1px solid {COLOR_INFO}; border-radius: 8px; padding: 16px; margin: 0 0 24px 0;">
                <p style="color: {COLOR_INFO_DARK}; font-weight: 600; margin: 0 0 8px 0;">
                    Your Free Tier Includes:
                </p>
                <ul style="color: #0c4a6e; font-size: 14px; margin: 0; padding-left: 20px;">
                    <li>{scans_limit} documents per month</li>
                    <li>{pages_limit} pages per document</li>
                    <li>PDF, Word, Excel, PowerPoint scanning</li>
                    <li>AI-generated alt text</li>
                    <li>Auto-remediation</li>
                </ul>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 14px 32px; border-radius: 8px;">
                    Go to Dashboard
                </a>
            </div>

            <p style="color: #666; font-size: 14px; text-align: center; margin: 0;">
                Need more documents or advanced features like LaTeX support?<br>
                <a href="https://example.com/for-faculty" style="color: {BRAND_PRIMARY};">View upgrade options →</a>
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_pilot_welcome_email(
    recipient_name: str,
    institution_name: str,
    pilot_count: int,
    dashboard_url: str = "https://dashboard.example.com",
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render pilot program welcome email.

    Args:
        recipient_name: Admin's name
        institution_name: Institution name
        pilot_count: Which pilot number they are
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Confirmed Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_SUCCESS_BG}; border: 2px solid {COLOR_SUCCESS}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_SUCCESS_DARK};">
                    ✓ Pilot Account Confirmed
                </span>
            </div>

            <p style="margin: 0 0 24px 0; font-size: 18px; line-height: 1.6; color: #1f2937;">Dear {recipient_name},</p>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Your pilot program account for <strong>{institution_name}</strong> is now active. You are participant #{pilot_count} in the pilot program.
            </p>

            <!-- Access Details Box -->
            <div style="background-color: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 24px; margin: 0 0 32px 0;">
                <p style="margin: 0 0 16px 0; font-size: 16px; font-weight: 600; color: #1f2937;">Your Access Details</p>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280; font-size: 14px;">Account Status:</td>
                        <td style="padding: 8px 0; color: {COLOR_SUCCESS}; font-size: 14px; font-weight: 600;">Active</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280; font-size: 14px;">Institution:</td>
                        <td style="padding: 8px 0; color: #1f2937; font-size: 14px; font-weight: 600;">{institution_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280; font-size: 14px;">Program Start:</td>
                        <td style="padding: 8px 0; color: #1f2937; font-size: 14px; font-weight: 600;">February 2026</td>
                    </tr>
                </table>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 0 0 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 16px 40px; border-radius: 8px;">
                    Access Your Dashboard
                </a>
            </div>

            <!-- Next Steps -->
            <p style="margin: 0 0 16px 0; font-size: 18px; font-weight: 700; color: #1f2937;">Next Steps</p>
            <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="margin: 0 0 32px 0;">
                <tr>
                    <td style="padding: 12px 0; border-bottom: 1px solid #e5e7eb;">
                        <span style="display: inline-block; width: 24px; height: 24px; background-color: {BRAND_PRIMARY}; border-radius: 50%; text-align: center; line-height: 24px; color: #ffffff; font-size: 12px; font-weight: 600; margin-right: 12px;">1</span>
                        <strong>Log in to your dashboard</strong> using this email address
                    </td>
                </tr>
                <tr>
                    <td style="padding: 12px 0; border-bottom: 1px solid #e5e7eb;">
                        <span style="display: inline-block; width: 24px; height: 24px; background-color: {BRAND_PRIMARY}; border-radius: 50%; text-align: center; line-height: 24px; color: #ffffff; font-size: 12px; font-weight: 600; margin-right: 12px;">2</span>
                        <strong>Configure your AI provider</strong> in Settings
                    </td>
                </tr>
                <tr>
                    <td style="padding: 12px 0;">
                        <span style="display: inline-block; width: 24px; height: 24px; background-color: {BRAND_PRIMARY}; border-radius: 50%; text-align: center; line-height: 24px; color: #ffffff; font-size: 12px; font-weight: 600; margin-right: 12px;">3</span>
                        <strong>Upload your first document</strong> to begin scanning
                    </td>
                </tr>
            </table>

            <!-- Compliance Reminder -->
            <div style="background-color: {COLOR_ERROR_BG}; border-left: 4px solid {COLOR_ERROR}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 32px 0;">
                <p style="margin: 0 0 4px 0; font-size: 14px; font-weight: 600; color: {COLOR_ERROR};">WCAG 2.1 AA Deadline</p>
                <p style="margin: 0; font-size: 14px; color: #7f1d1d;">
                    April 26, 2027 - Title II compliance deadline for universities
                </p>
            </div>

            <!-- Support -->
            <div style="background-color: #f9fafb; border-radius: 8px; padding: 20px;">
                <p style="margin: 0 0 12px 0; font-size: 16px; font-weight: 600; color: #1f2937;">Need Help?</p>
                <p style="margin: 0 0 8px 0; font-size: 14px; color: #4b5563;">
                    <strong>Support:</strong> <a href="mailto:pilot@example.com" style="color: {BRAND_SECONDARY}; text-decoration: none;">pilot@example.com</a>
                </p>
                <p style="margin: 0; font-size: 14px; color: #4b5563;">
                    <strong>Documentation:</strong> <a href="https://example.com/docs" style="color: {BRAND_SECONDARY}; text-decoration: none;">example.com/docs</a>
                </p>
            </div>
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


def render_waitlist_confirmation_email(
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render waitlist signup confirmation email.

    This is a TRANSACTIONAL email (confirms user action).
    No promotional content - just confirms they're on the list.

    Args:
        unsubscribe_url: URL for unsubscribe link (optional)

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Confirmed Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_SUCCESS_BG}; border: 2px solid {COLOR_SUCCESS}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_SUCCESS_DARK};">
                    ✓ You're on the list
                </span>
            </div>

            <p style="margin: 0 0 16px 0; font-size: 16px; line-height: 1.6; color: #4b5563; text-align: center;">
                Thanks for joining the Aelira waitlist. We'll notify you when your account is ready.
            </p>

            <p style="margin: 0; font-size: 14px; color: #9ca3af; text-align: center;">
                Questions? Reply to this email or contact <a href="mailto:hello@example.com" style="color: {BRAND_SECONDARY};">hello@example.com</a>
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


# =============================================================================
# Marketing Campaign Templates
# =============================================================================


def render_deadline_reminder_email(
    name: str,
    days_remaining: int,
    dashboard_url: str = "https://dashboard.example.com",
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render WCAG deadline reminder email.

    Marketing email reminding about the April 2027 compliance deadline.

    Args:
        name: Recipient's name
        days_remaining: Days until April 26, 2027 deadline
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link

    Returns:
        HTML string for email body
    """
    urgency_color = (
        COLOR_ERROR
        if days_remaining <= 30
        else COLOR_WARNING if days_remaining <= 90 else COLOR_INFO
    )
    urgency_bg = (
        COLOR_ERROR_BG
        if days_remaining <= 30
        else COLOR_WARNING_BG if days_remaining <= 90 else COLOR_INFO_BG
    )

    content = f"""
            <!-- Countdown Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {urgency_bg}; border: 2px solid {urgency_color}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {urgency_color};">
                    ⏰ {days_remaining} Days Remaining
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">WCAG 2.1 AA Deadline Approaching</h2>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6;">
                Hi {name},
            </p>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                The DOJ's Title II deadline for WCAG 2.1 AA compliance is <strong style="color: {urgency_color};">April 26, 2027</strong> — just <strong>{days_remaining} days away</strong>.
            </p>

            <!-- Stats Box -->
            <div style="background-color: #f9fafb; border-radius: 8px; padding: 24px; margin: 0 0 24px 0;">
                <p style="margin: 0 0 16px 0; font-weight: 600; color: #1f2937; font-size: 18px;">What this means for your institution:</p>
                <ul style="color: #4b5563; margin: 0; padding-left: 20px; line-height: 1.8;">
                    <li>All course materials must be accessible</li>
                    <li>PDFs, PowerPoints, and videos need remediation</li>
                    <li>LaTeX equations require MathML alternatives</li>
                    <li>Non-compliance risks legal action and federal funding</li>
                </ul>
            </div>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                <strong>Aelira can help.</strong> Our AI-powered platform scans and remediates documents automatically — saving faculty 40+ hours per course.
            </p>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 16px 40px; border-radius: 8px;">
                    Start Free Trial
                </a>
            </div>

            <p style="color: #666; font-size: 14px; text-align: center; margin: 0;">
                Questions? Reply to this email or contact <a href="mailto:support@example.com" style="color: {BRAND_PRIMARY};">support@example.com</a>
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_trial_nurture_day1_email(
    name: str,
    dashboard_url: str = "https://dashboard.example.com",
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render trial welcome email (Day 1).

    Sent immediately when a user starts a trial.

    Args:
        name: Recipient's name
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Welcome Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_SUCCESS_BG}; border: 2px solid {COLOR_SUCCESS}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_SUCCESS_DARK};">
                    ✓ Trial Started
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Welcome to Aelira, {name}!</h2>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Your 14-day trial is now active. Here's how to get started:
            </p>

            <!-- Quick Start Steps -->
            <div style="background-color: #f9fafb; border-radius: 8px; padding: 24px; margin: 0 0 24px 0;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                        <td style="padding: 12px 0; vertical-align: top;">
                            <div style="width: 28px; height: 28px; background-color: {BRAND_PRIMARY}; border-radius: 50%; color: white; font-size: 14px; font-weight: bold; display: inline-block; text-align: center; line-height: 28px; margin-right: 16px;">1</div>
                            <strong style="color: #1f2937;">Upload your first document</strong>
                            <p style="margin: 8px 0 0 44px; color: #6b7280; font-size: 14px;">PDF, PowerPoint, Word, or Excel — we support them all</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 12px 0; vertical-align: top;">
                            <div style="width: 28px; height: 28px; background-color: {BRAND_PRIMARY}; border-radius: 50%; color: white; font-size: 14px; font-weight: bold; display: inline-block; text-align: center; line-height: 28px; margin-right: 16px;">2</div>
                            <strong style="color: #1f2937;">Review accessibility issues</strong>
                            <p style="margin: 8px 0 0 44px; color: #6b7280; font-size: 14px;">Our AI identifies missing alt text, contrast issues, and more</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 12px 0; vertical-align: top;">
                            <div style="width: 28px; height: 28px; background-color: {BRAND_PRIMARY}; border-radius: 50%; color: white; font-size: 14px; font-weight: bold; display: inline-block; text-align: center; line-height: 28px; margin-right: 16px;">3</div>
                            <strong style="color: #1f2937;">Auto-remediate with one click</strong>
                            <p style="margin: 8px 0 0 44px; color: #6b7280; font-size: 14px;">Download your WCAG-compliant document instantly</p>
                        </td>
                    </tr>
                </table>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 16px 40px; border-radius: 8px;">
                    Go to Dashboard
                </a>
            </div>

            <p style="color: #666; font-size: 14px; text-align: center; margin: 0;">
                Need help? Reply to this email — we're here for you.
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_trial_nurture_day3_email(
    name: str,
    dashboard_url: str = "https://dashboard.example.com",
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render trial feature highlight email (Day 3).

    Highlights key features to drive engagement.

    Args:
        name: Recipient's name
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link

    Returns:
        HTML string for email body
    """
    content = f"""
            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Did you know Aelira can do this?</h2>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Hi {name}, here are 3 powerful features you might not have tried yet:
            </p>

            <!-- Feature Cards -->
            <div style="margin: 0 0 24px 0;">
                <!-- Feature 1 -->
                <div style="background-color: {COLOR_INFO_BG}; border-radius: 8px; padding: 20px; margin: 0 0 16px 0;">
                    <p style="margin: 0 0 8px 0; font-weight: 600; color: {COLOR_INFO_DARK}; font-size: 16px;">
                        🧮 LaTeX → MathML Conversion
                    </p>
                    <p style="margin: 0; color: #4b5563; font-size: 14px;">
                        Convert complex equations to screen-reader-friendly MathML automatically. Supports ChemFig, physics notation, and more.
                    </p>
                </div>

                <!-- Feature 2 -->
                <div style="background-color: {COLOR_SUCCESS_BG}; border-radius: 8px; padding: 20px; margin: 0 0 16px 0;">
                    <p style="margin: 0 0 8px 0; font-weight: 600; color: {COLOR_SUCCESS_DARK}; font-size: 16px;">
                        🖼️ AI-Generated Alt Text
                    </p>
                    <p style="margin: 0; color: #4b5563; font-size: 14px;">
                        Our AI writes accurate, contextual alt text for images in seconds — no manual work required.
                    </p>
                </div>

                <!-- Feature 3 -->
                <div style="background-color: {COLOR_WARNING_BG}; border-radius: 8px; padding: 20px; margin: 0 0 16px 0;">
                    <p style="margin: 0 0 8px 0; font-weight: 600; color: {COLOR_WARNING_DARK}; font-size: 16px;">
                        📊 Bulk Processing
                    </p>
                    <p style="margin: 0; color: #4b5563; font-size: 14px;">
                        Upload entire folders of documents. Our CLI can process thousands of files overnight.
                    </p>
                </div>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 16px 40px; border-radius: 8px;">
                    Try These Features
                </a>
            </div>

            <p style="color: #666; font-size: 14px; text-align: center; margin: 0;">
                11 days left in your trial — make the most of it!
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_trial_nurture_day7_email(
    name: str,
    documents_scanned: int = 0,
    issues_fixed: int = 0,
    pricing_url: str = "https://example.com/pricing",
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render trial conversion email (Day 7).

    Mid-trial push with personalized stats and upgrade CTA.

    Args:
        name: Recipient's name
        documents_scanned: Number of docs they've scanned
        issues_fixed: Number of issues fixed
        pricing_url: URL to pricing page
        unsubscribe_url: URL for unsubscribe link

    Returns:
        HTML string for email body
    """
    has_activity = documents_scanned > 0

    if has_activity:
        stats_section = f"""
            <!-- Stats Box -->
            <div style="background-color: {COLOR_SUCCESS_BG}; border-radius: 8px; padding: 24px; margin: 0 0 24px 0; text-align: center;">
                <p style="margin: 0 0 16px 0; font-weight: 600; color: {COLOR_SUCCESS_DARK}; font-size: 16px;">
                    Your trial progress so far:
                </p>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                        <td width="50%" style="text-align: center; padding: 12px;">
                            <div style="font-size: 36px; font-weight: bold; color: {COLOR_SUCCESS};">{documents_scanned}</div>
                            <div style="color: #6b7280; font-size: 14px;">Documents Scanned</div>
                        </td>
                        <td width="50%" style="text-align: center; padding: 12px;">
                            <div style="font-size: 36px; font-weight: bold; color: {COLOR_SUCCESS};">{issues_fixed}</div>
                            <div style="color: #6b7280; font-size: 14px;">Issues Fixed</div>
                        </td>
                    </tr>
                </table>
            </div>
        """
        message = "You've already made great progress! With a paid plan, you can scale this across your entire department."
    else:
        stats_section = ""
        message = "You haven't uploaded any documents yet. Your trial ends in 7 days — don't miss out!"

    content = f"""
            <!-- Midpoint Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_WARNING_BG}; border: 2px solid {COLOR_WARNING}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_WARNING_DARK};">
                    ⏰ 7 Days Left in Trial
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Halfway There, {name}!</h2>

            {stats_section}

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                {message}
            </p>

            <!-- Pricing Comparison -->
            <div style="background-color: #f9fafb; border-radius: 8px; padding: 24px; margin: 0 0 24px 0;">
                <p style="margin: 0 0 16px 0; font-weight: 600; color: #1f2937; font-size: 16px;">Compare the cost:</p>
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280;">Manual remediation:</td>
                        <td style="padding: 8px 0; text-align: right; color: {COLOR_ERROR}; font-weight: 600;">$3,000-$6,000/course</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #6b7280;">Aelira Education:</td>
                        <td style="padding: 8px 0; text-align: right; color: {COLOR_SUCCESS}; font-weight: 600;">$999/month</td>
                    </tr>
                </table>
                <p style="margin: 16px 0 0 0; color: #6b7280; font-size: 14px;">
                    That's <strong>90% savings</strong> and unlimited documents per department.
                </p>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{pricing_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 16px 40px; border-radius: 8px;">
                    View Pricing Plans
                </a>
            </div>

            <p style="color: #666; font-size: 14px; text-align: center; margin: 0;">
                Questions about pricing? Reply to this email — we offer volume discounts!
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_waitlist_launch_email(
    name: str,
    signup_url: str = "https://example.com/signup",
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render waitlist launch email ("Your spot is ready").

    Sent when moving waitlist users to active signups.

    Args:
        name: Recipient's name
        signup_url: URL to create account
        unsubscribe_url: URL for unsubscribe link

    Returns:
        HTML string for email body
    """
    content = f"""
            <!-- Launch Badge -->
            <div style="text-align: center; margin-bottom: 24px;">
                <span style="background-color: {COLOR_SUCCESS_BG}; border: 2px solid {COLOR_SUCCESS}; border-radius: 24px; padding: 8px 20px; font-size: 14px; font-weight: 600; color: {COLOR_SUCCESS_DARK};">
                    🎉 You're In!
                </span>
            </div>

            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">Your Aelira Account is Ready</h2>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Hi {name},
            </p>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                Great news — you're off the waitlist! Your Aelira account is ready to go.
            </p>

            <!-- What You Get -->
            <div style="background-color: {COLOR_INFO_BG}; border-radius: 8px; padding: 24px; margin: 0 0 24px 0;">
                <p style="margin: 0 0 16px 0; font-weight: 600; color: {COLOR_INFO_DARK}; font-size: 16px;">
                    As a waitlist member, you get:
                </p>
                <ul style="color: #4b5563; margin: 0; padding-left: 20px; line-height: 1.8;">
                    <li><strong>Extended 30-day trial</strong> (normally 14 days)</li>
                    <li><strong>Priority support</strong> from our team</li>
                    <li><strong>10% lifetime discount</strong> on any paid plan</li>
                </ul>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{signup_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 16px 40px; border-radius: 8px;">
                    Activate Your Account
                </a>
            </div>

            <!-- Urgency -->
            <div style="background-color: {COLOR_WARNING_BG}; border-left: 4px solid {COLOR_WARNING}; border-radius: 0 8px 8px 0; padding: 12px 16px; margin: 0 0 24px 0;">
                <p style="margin: 0; font-size: 14px; color: {COLOR_WARNING_DARK};">
                    ⏰ This invitation expires in <strong>7 days</strong>. Activate now to keep your spot!
                </p>
            </div>

            <p style="color: #666; font-size: 14px; text-align: center; margin: 0;">
                Thanks for your patience — we can't wait to help you reach compliance!
            </p>
    """
    return get_email_wrapper(content, unsubscribe_url)


def render_reengagement_email(
    name: str,
    days_inactive: int,
    dashboard_url: str = "https://dashboard.example.com",
    unsubscribe_url: Optional[str] = None,
) -> str:
    """
    Render re-engagement email ("We miss you").

    Sent to users who haven't logged in recently.

    Args:
        name: Recipient's name
        days_inactive: Days since last activity
        dashboard_url: URL to the dashboard
        unsubscribe_url: URL for unsubscribe link

    Returns:
        HTML string for email body
    """
    content = f"""
            <h2 style="color: #1f2937; text-align: center; margin: 0 0 24px 0;">We Miss You, {name}!</h2>

            <p style="margin: 0 0 24px 0; font-size: 16px; line-height: 1.6; color: #4b5563;">
                It's been {days_inactive} days since you last used Aelira. A lot has happened since then!
            </p>

            <!-- What's New -->
            <div style="background-color: #f9fafb; border-radius: 8px; padding: 24px; margin: 0 0 24px 0;">
                <p style="margin: 0 0 16px 0; font-weight: 600; color: #1f2937; font-size: 16px;">
                    What's new in Aelira:
                </p>
                <ul style="color: #4b5563; margin: 0; padding-left: 20px; line-height: 1.8;">
                    <li>Faster document processing (2x speed improvement)</li>
                    <li>Enhanced LaTeX support for chemistry and physics</li>
                    <li>Canvas and Blackboard LTI integration</li>
                    <li>Bulk upload for entire course folders</li>
                </ul>
            </div>

            <!-- Deadline Reminder -->
            <div style="background-color: {COLOR_ERROR_BG}; border-left: 4px solid {COLOR_ERROR}; border-radius: 0 8px 8px 0; padding: 16px; margin: 0 0 24px 0;">
                <p style="margin: 0; font-size: 14px; color: {COLOR_ERROR_DARK};">
                    <strong>Reminder:</strong> The WCAG 2.1 compliance deadline is April 26, 2027. Don't fall behind!
                </p>
            </div>

            <!-- CTA Button -->
            <div style="text-align: center; margin: 32px 0;">
                <a href="{dashboard_url}" style="display: inline-block; background-color: #7C3AED; background: linear-gradient(135deg, #8B5CF6 0%, #6366F1 100%); color: #ffffff; font-size: 16px; font-weight: 600; text-decoration: none; padding: 16px 40px; border-radius: 8px;">
                    Resume Where You Left Off
                </a>
            </div>

            <p style="color: #666; font-size: 14px; text-align: center; margin: 0;">
                Need help? Reply to this email — we're here for you.
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
    # Billing emails
    "render_upgrade_success_email",
    "render_subscription_cancelled_email",
    "render_payment_failed_email",
    # Onboarding/Welcome emails
    "render_department_trial_welcome_email",
    "render_welcome_individual_email",
    "render_pilot_welcome_email",
    "render_faculty_invitation_email",
    "render_waitlist_confirmation_email",
    # Marketing campaign emails
    "render_deadline_reminder_email",
    "render_trial_nurture_day1_email",
    "render_trial_nurture_day3_email",
    "render_trial_nurture_day7_email",
    "render_waitlist_launch_email",
    "render_reengagement_email",
    # Account deletion emails
    "render_deletion_code_email",
    "render_deletion_scheduled_email",
]
