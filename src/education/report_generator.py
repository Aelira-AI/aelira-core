"""
HTML Report Generator for Accessibility Scans
Generates downloadable HTML reports with scan results
"""

from datetime import datetime
from typing import Any, Dict, List


class AccessibilityReportGenerator:
    """Generate HTML reports for accessibility scan results"""

    @staticmethod
    def generate_website_report(scan_data: Dict[str, Any]) -> str:
        """
        Generate a comprehensive HTML report for a website accessibility scan

        Args:
            scan_data: Dictionary containing scan information
                - scan_id: Scan ID
                - url: Scanned URL
                - created_at: Scan timestamp
                - compliance_score: Overall score (0-100)
                - issues: List of accessibility issues
                - pages_scanned: Number of pages scanned
                - total_issues: Total issue count

        Returns:
            HTML string for the report
        """
        url = scan_data.get("url", "Unknown")
        created_at = scan_data.get("created_at", datetime.now().isoformat())
        score = scan_data.get("compliance_score", 0)
        issues = scan_data.get("issues", [])
        pages_scanned = scan_data.get("pages_scanned", 1)
        cvd_analysis = scan_data.get("cvd_analysis", [])

        # Count issues by severity
        critical = len([i for i in issues if i.get("impact") == "critical"])
        serious = len([i for i in issues if i.get("impact") in ["serious", "high"]])
        moderate = len([i for i in issues if i.get("impact") in ["moderate", "medium"]])
        minor = len([i for i in issues if i.get("impact") in ["minor", "low"]])

        # Determine score color
        if score >= 90:
            score_class = "score-excellent"
            score_label = "Excellent"
        elif score >= 70:
            score_class = "score-good"
            score_label = "Good"
        elif score >= 50:
            score_class = "score-fair"
            score_label = "Needs Work"
        else:
            score_class = "score-poor"
            score_label = "Poor"

        # Format date
        try:
            date_obj = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            formatted_date = date_obj.strftime("%B %d, %Y at %I:%M %p")
        except Exception:
            formatted_date = created_at

        # Generate issues HTML
        issues_html = ""
        if issues:
            # Issue type to badge mapping
            ISSUE_TYPE_BADGES = {
                "red_flash": ("⚠️ SEIZURE RISK", "badge-seizure"),
                "flashing_content": ("⚠️ SEIZURE RISK", "badge-seizure"),
                "animation_flash": ("⚠️ SEIZURE RISK", "badge-seizure"),
                "shadow_dom": ("🔲 Shadow DOM", "badge-shadow-dom"),
                "image-alt": ("🔲 Shadow DOM", "badge-shadow-dom"),
                "button-name": ("🔲 Shadow DOM", "badge-shadow-dom"),
                "link-name": ("🔲 Shadow DOM", "badge-shadow-dom"),
                "form-label": ("🔲 Shadow DOM", "badge-shadow-dom"),
                "animation": ("▶️ Animation", "badge-animation"),
                "animation_auto": ("▶️ Animation", "badge-animation"),
                "pivot_table": ("📊 Pivot Table", "badge-pivot"),
                "smartart": ("📐 SmartArt", "badge-smartart"),
                "embedded_object": ("📎 Embedded", "badge-embedded"),
                "ole_object": ("📎 Embedded", "badge-embedded"),
            }
            SEIZURE_RISK_TYPES = {"red_flash", "flashing_content", "animation_flash"}
            SHADOW_DOM_TYPES = {
                "shadow_dom",
                "image-alt",
                "button-name",
                "link-name",
                "form-label",
            }
            ANIMATION_TYPES = {"animation", "animation_auto", "animation_flash"}

            for idx, issue in enumerate(issues, 1):
                impact = issue.get("impact", "minor")
                description = issue.get("description", "No description")
                element = issue.get("element", "N/A")
                fix = issue.get("fix", "No fix available")
                help_url = issue.get("help_url", "")
                generated_fix = issue.get("generated_code_fix", "")
                issue_type = issue.get("type") or issue.get("issue_type", "")
                metadata = issue.get("metadata", {})

                # Determine impact class
                impact_class = {
                    "critical": "impact-critical",
                    "serious": "impact-serious",
                    "high": "impact-serious",
                    "moderate": "impact-moderate",
                    "medium": "impact-moderate",
                    "minor": "impact-minor",
                    "low": "impact-minor",
                }.get(impact, "impact-minor")

                # Add special classes for new issue types
                extra_classes = []
                if issue_type in SEIZURE_RISK_TYPES:
                    extra_classes.append("issue-seizure-risk")
                elif metadata.get("shadow_dom") or issue_type in SHADOW_DOM_TYPES:
                    extra_classes.append("issue-shadow-dom")
                elif issue_type in ANIMATION_TYPES:
                    extra_classes.append("issue-animation")

                all_classes = f"issue {impact_class} {' '.join(extra_classes)}".strip()

                # Generate badge HTML
                badge_html = ""
                if issue_type in ISSUE_TYPE_BADGES:
                    badge_text, badge_class = ISSUE_TYPE_BADGES[issue_type]
                    badge_html = f'<span class="issue-type-badge {badge_class}">{badge_text}</span>'

                # Generate seizure warning if applicable
                seizure_warning_html = ""
                if issue_type in SEIZURE_RISK_TYPES:
                    seizure_warning_html = """
                        <div class="seizure-warning">
                            ⚠️ <strong>SEIZURE RISK WARNING:</strong> This content may trigger seizures in people with
                            photosensitive epilepsy. This is a WCAG 2.3.1 violation. Immediate remediation recommended.
                        </div>"""

                # Generate shadow DOM info if applicable
                shadow_dom_html = ""
                if metadata.get("shadow_dom") or issue_type in SHADOW_DOM_TYPES:
                    shadow_dom_html = """
                        <div class="shadow-dom-info">
                            🔲 <strong>Shadow DOM Component:</strong> This issue is inside a web component's Shadow DOM.
                            Use pierce selectors (>>>) for remediation.
                        </div>"""

                issues_html += f"""
                <div class="{all_classes}">
                    <div class="issue-header">
                        <span class="issue-number">#{idx}</span>
                        <span class="issue-impact">{impact.upper()}</span>
                        <h3>{description}{badge_html}</h3>
                    </div>
                    <div class="issue-body">
                        {seizure_warning_html}
                        {shadow_dom_html}
                        <div class="issue-section">
                            <strong>Element:</strong>
                            <pre><code>{html_escape(element)}</code></pre>
                        </div>
                        <div class="issue-section">
                            <strong>How to fix:</strong>
                            <p>{html_escape(fix)}</p>
                        </div>
                        {f'''
                        <div class="issue-section ai-fix">
                            <strong>🤖 AI-Generated Code Fix:</strong>
                            <pre><code>{html_escape(generated_fix)}</code></pre>
                        </div>
                        ''' if generated_fix else ''}
                        {f'<div class="issue-section"><a href="{help_url}" target="_blank" class="help-link">Learn more →</a></div>' if help_url else ''}
                    </div>
                </div>
                """
        else:
            issues_html = '<div class="no-issues">✅ No accessibility issues found! This website meets WCAG 2.2 standards.</div>'

        # Generate CVD analysis HTML
        cvd_html = AccessibilityReportGenerator._generate_cvd_section(cvd_analysis)

        # Generate full HTML report
        html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Accessibility Report - {html_escape(url)}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        header {{
            border-bottom: 3px solid #8b5cf6;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }}
        
        h1 {{
            color: #8b5cf6;
            font-size: 2em;
            margin-bottom: 10px;
        }}
        
        .meta {{
            color: #666;
            font-size: 0.9em;
        }}
        
        .summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 30px 0;
        }}
        
        .summary-card {{
            padding: 20px;
            border-radius: 8px;
            border: 2px solid #e0e0e0;
        }}
        
        .summary-card h2 {{
            font-size: 0.9em;
            color: #666;
            margin-bottom: 10px;
            text-transform: uppercase;
        }}
        
        .summary-card .value {{
            font-size: 2.5em;
            font-weight: bold;
        }}
        
        .score-excellent .value {{ color: #10b981; }}
        .score-good .value {{ color: #f59e0b; }}
        .score-fair .value {{ color: #ef4444; }}
        .score-poor .value {{ color: #dc2626; }}
        
        .impact-critical {{ border-left: 4px solid #dc2626; }}
        .impact-serious {{ border-left: 4px solid #f59e0b; }}
        .impact-moderate {{ border-left: 4px solid #f59e0b; }}
        .impact-minor {{ border-left: 4px solid #6b7280; }}
        
        .issues {{
            margin-top: 40px;
        }}
        
        .issues h2 {{
            font-size: 1.5em;
            margin-bottom: 20px;
            color: #333;
        }}
        
        .issue {{
            background: #f9fafb;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        
        .issue-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 15px;
        }}
        
        .issue-number {{
            background: #8b5cf6;
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.9em;
            font-weight: bold;
        }}
        
        .issue-impact {{
            background: #ef4444;
            color: white;
            padding: 4px 12px;
            border-radius: 4px;
            font-size: 0.8em;
            font-weight: bold;
        }}
        
        .issue-header h3 {{
            flex: 1;
            font-size: 1.1em;
            color: #111;
        }}
        
        .issue-section {{
            margin-bottom: 15px;
        }}
        
        .issue-section strong {{
            display: block;
            margin-bottom: 8px;
            color: #555;
        }}
        
        pre {{
            background: #1f2937;
            color: #f3f4f6;
            padding: 15px;
            border-radius: 6px;
            overflow-x: auto;
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 0.9em;
        }}
        
        code {{
            font-family: 'Monaco', 'Courier New', monospace;
        }}
        
        .ai-fix {{
            background: #ede9fe;
            padding: 15px;
            border-radius: 6px;
            border-left: 4px solid #8b5cf6;
        }}
        
        .ai-fix strong {{
            color: #6d28d9;
        }}
        
        .help-link {{
            color: #8b5cf6;
            text-decoration: none;
            font-weight: 500;
        }}
        
        .help-link:hover {{
            text-decoration: underline;
        }}
        
        .no-issues {{
            text-align: center;
            padding: 60px 20px;
            font-size: 1.2em;
            color: #10b981;
            background: #d1fae5;
            border-radius: 8px;
        }}

        /* New issue type styles (Tasks 1-14) */
        .issue-seizure-risk {{
            background: #fef2f2;
            border-left: 4px solid #dc2626;
        }}

        .issue-shadow-dom {{
            background: #f5f3ff;
            border-left: 4px solid #7c3aed;
        }}

        .issue-animation {{
            background: #fefce8;
            border-left: 4px solid #ca8a04;
        }}

        .seizure-warning {{
            background: #fef2f2;
            border: 1px solid #dc2626;
            border-radius: 6px;
            padding: 12px;
            margin-bottom: 15px;
            color: #991b1b;
            font-weight: 500;
        }}

        .shadow-dom-info {{
            background: #f5f3ff;
            border-radius: 6px;
            padding: 10px;
            margin-bottom: 15px;
            color: #5b21b6;
            font-size: 0.9em;
        }}

        .issue-type-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: 600;
            margin-left: 8px;
        }}

        .badge-seizure {{ background: #fecaca; color: #991b1b; }}
        .badge-shadow-dom {{ background: #ddd6fe; color: #5b21b6; }}
        .badge-animation {{ background: #fef08a; color: #854d0e; }}
        .badge-pivot {{ background: #dbeafe; color: #1e40af; }}
        .badge-smartart {{ background: #dcfce7; color: #166534; }}
        .badge-embedded {{ background: #ffedd5; color: #9a3412; }}

        footer {{
            margin-top: 50px;
            padding-top: 20px;
            border-top: 1px solid #e0e0e0;
            text-align: center;
            color: #666;
            font-size: 0.9em;
        }}
        
        /* CVD Section Styles */
        .cvd-section {{
            margin: 40px 0;
            padding: 30px;
            background: #faf5ff;
            border-radius: 8px;
            border-left: 4px solid #8b5cf6;
        }}

        .cvd-section h2 {{
            color: #6d28d9;
            margin-bottom: 15px;
        }}

        .cvd-section h3 {{
            color: #7c3aed;
            margin: 20px 0 15px;
            font-size: 1.1em;
        }}

        .cvd-intro {{
            color: #6b7280;
            margin-bottom: 20px;
        }}

        .cvd-success {{
            text-align: center;
            padding: 30px;
            color: #10b981;
            background: #d1fae5;
            border-radius: 6px;
            font-weight: 500;
        }}

        .cvd-summary {{
            display: flex;
            gap: 20px;
            margin-bottom: 25px;
        }}

        .cvd-summary-card {{
            padding: 20px;
            background: white;
            border-radius: 8px;
            text-align: center;
            flex: 1;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}

        .cvd-summary-card.cvd-warning {{
            border-top: 3px solid #f59e0b;
        }}

        .cvd-summary-value {{
            font-size: 2em;
            font-weight: bold;
            color: #6d28d9;
        }}

        .cvd-summary-label {{
            color: #6b7280;
            font-size: 0.9em;
        }}

        .cvd-type-breakdown {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}

        .cvd-type-card {{
            padding: 15px;
            background: white;
            border-radius: 6px;
            border-left: 3px solid #8b5cf6;
        }}

        .cvd-type-name {{
            font-weight: 600;
            color: #374151;
        }}

        .cvd-type-count {{
            color: #dc2626;
            font-weight: 500;
        }}

        .cvd-type-population {{
            color: #9ca3af;
            font-size: 0.85em;
        }}

        .cvd-pairs-list {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}

        .cvd-pair {{
            display: flex;
            align-items: center;
            gap: 20px;
            padding: 15px;
            background: white;
            border-radius: 6px;
        }}

        .cvd-pair-preview {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .cvd-color-sample {{
            width: 50px;
            height: 35px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: bold;
            border-radius: 4px;
            border: 1px solid #e0e0e0;
        }}

        .cvd-color-codes {{
            font-family: monospace;
            font-size: 0.85em;
            color: #6b7280;
        }}

        .cvd-pair-info {{
            flex: 1;
        }}

        .cvd-contrast {{
            font-weight: 500;
            color: #374151;
        }}

        .cvd-affected {{
            color: #dc2626;
            font-size: 0.9em;
        }}

        .cvd-recommendations {{
            margin-top: 25px;
            padding: 20px;
            background: white;
            border-radius: 6px;
        }}

        .cvd-recommendations ul {{
            margin: 10px 0 0 20px;
            color: #4b5563;
        }}

        .cvd-recommendations li {{
            margin-bottom: 8px;
        }}

        @media print {{
            body {{
                background: white;
            }}
            .container {{
                box-shadow: none;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Accessibility Scan Report</h1>
            <div class="meta">
                <strong>Website:</strong> {html_escape(url)}<br>
                <strong>Scan Date:</strong> {formatted_date}<br>
                <strong>Pages Scanned:</strong> {pages_scanned}<br>
                <strong>Generated by:</strong> Aelira.ai
            </div>
        </header>
        
        <div class="summary">
            <div class="summary-card {score_class}">
                <h2>Compliance Score</h2>
                <div class="value">{score}%</div>
                <div>{score_label}</div>
            </div>
            <div class="summary-card">
                <h2>Critical</h2>
                <div class="value" style="color: #dc2626;">{critical}</div>
                <div>Issues</div>
            </div>
            <div class="summary-card">
                <h2>Serious</h2>
                <div class="value" style="color: #f59e0b;">{serious}</div>
                <div>Issues</div>
            </div>
            <div class="summary-card">
                <h2>Moderate</h2>
                <div class="value" style="color: #f59e0b;">{moderate}</div>
                <div>Issues</div>
            </div>
            <div class="summary-card">
                <h2>Minor</h2>
                <div class="value" style="color: #6b7280;">{minor}</div>
                <div>Issues</div>
            </div>
        </div>
        
        {cvd_html}

        <div class="issues">
            <h2>Accessibility Issues ({len(issues)})</h2>
            {issues_html}
        </div>

        <footer>
            <p>Report generated by <strong>Aelira.ai</strong> - Automated Accessibility Testing</p>
            <p>Based on WCAG 2.2 Level AA Standards</p>
        </footer>
    </div>
</body>
</html>
"""
        return html

    @staticmethod
    def _generate_cvd_section(cvd_analysis: List[Any]) -> str:
        """
        Generate HTML section for Color Vision Deficiency analysis

        Args:
            cvd_analysis: List of ColorBlindnessAnalysisResult objects or dicts

        Returns:
            HTML string for CVD section
        """
        if not cvd_analysis:
            return ""

        # Count issues by CVD type
        cvd_type_counts: Dict[str, int] = {}
        total_affected_pairs = 0

        for analysis in cvd_analysis:
            if isinstance(analysis, dict):
                issues = analysis.get("issues", [])
                fg = analysis.get("foreground_color", "?")
                bg = analysis.get("background_color", "?")
            else:
                issues = analysis.issues if hasattr(analysis, "issues") else []
                fg = (
                    analysis.foreground_color
                    if hasattr(analysis, "foreground_color")
                    else "?"
                )
                bg = (
                    analysis.background_color
                    if hasattr(analysis, "background_color")
                    else "?"
                )

            if issues:
                total_affected_pairs += 1
                for issue in issues:
                    if isinstance(issue, dict):
                        cvd_type = issue.get("color_blindness_type", "unknown")
                    else:
                        cvd_type = (
                            issue.color_blindness_type
                            if hasattr(issue, "color_blindness_type")
                            else "unknown"
                        )

                    cvd_type_counts[cvd_type] = cvd_type_counts.get(cvd_type, 0) + 1

        if total_affected_pairs == 0:
            return """
        <div class="cvd-section">
            <h2>🎨 Color Vision Deficiency Analysis</h2>
            <div class="cvd-success">
                ✅ All color combinations are accessible for color-blind users!
            </div>
        </div>
        """

        # CVD type display names and population percentages
        cvd_info = {
            "protanopia": ("Protanopia (Red-blind)", "1% of males"),
            "deuteranopia": ("Deuteranopia (Green-blind)", "1% of males"),
            "tritanopia": ("Tritanopia (Blue-blind)", "0.01%"),
            "protanomaly": ("Protanomaly (Red-weak)", "1% of males"),
            "deuteranomaly": ("Deuteranomaly (Green-weak)", "5% of males"),
            "tritanomaly": ("Tritanomaly (Blue-weak)", "0.01%"),
            "achromatopsia": ("Achromatopsia (Monochrome)", "0.003%"),
        }

        # Generate CVD type breakdown HTML
        cvd_breakdown_html = ""
        for cvd_type, count in sorted(cvd_type_counts.items(), key=lambda x: -x[1]):
            display_name, population = cvd_info.get(
                cvd_type, (cvd_type.title(), "unknown")
            )
            cvd_breakdown_html += f"""
            <div class="cvd-type-card">
                <div class="cvd-type-name">{display_name}</div>
                <div class="cvd-type-count">{count} issue(s)</div>
                <div class="cvd-type-population">Affects {population}</div>
            </div>
            """

        # Generate color pair issues HTML
        color_pairs_html = ""
        for i, analysis in enumerate(cvd_analysis[:10], 1):  # Limit to 10
            if isinstance(analysis, dict):
                fg = analysis.get("foreground_color", "#000000")
                bg = analysis.get("background_color", "#ffffff")
                original_contrast = analysis.get("original_contrast", 0)
                issues = analysis.get("issues", [])
            else:
                fg = (
                    analysis.foreground_color
                    if hasattr(analysis, "foreground_color")
                    else "#000000"
                )
                bg = (
                    analysis.background_color
                    if hasattr(analysis, "background_color")
                    else "#ffffff"
                )
                original_contrast = (
                    analysis.original_contrast
                    if hasattr(analysis, "original_contrast")
                    else 0
                )
                issues = analysis.issues if hasattr(analysis, "issues") else []

            if not issues:
                continue

            affected_types = []
            for issue in issues:
                if isinstance(issue, dict):
                    affected_types.append(issue.get("color_blindness_type", "unknown"))
                else:
                    affected_types.append(
                        issue.color_blindness_type
                        if hasattr(issue, "color_blindness_type")
                        else "unknown"
                    )

            color_pairs_html += f"""
            <div class="cvd-pair">
                <div class="cvd-pair-preview">
                    <span class="cvd-color-sample" style="background: {bg}; color: {fg};">Aa</span>
                    <span class="cvd-color-codes">{fg} on {bg}</span>
                </div>
                <div class="cvd-pair-info">
                    <div class="cvd-contrast">Original contrast: {original_contrast:.1f}:1</div>
                    <div class="cvd-affected">Fails for: {", ".join(affected_types[:3])}{" ..." if len(affected_types) > 3 else ""}</div>
                </div>
            </div>
            """

        return f"""
        <div class="cvd-section">
            <h2>🎨 Color Vision Deficiency Analysis</h2>
            <p class="cvd-intro">
                Approximately 8% of males and 0.5% of females have some form of color vision deficiency.
                The following color combinations may be difficult for color-blind users to distinguish.
            </p>

            <div class="cvd-summary">
                <div class="cvd-summary-card cvd-warning">
                    <div class="cvd-summary-value">{total_affected_pairs}</div>
                    <div class="cvd-summary-label">Color pairs with issues</div>
                </div>
                <div class="cvd-summary-card">
                    <div class="cvd-summary-value">{len(cvd_type_counts)}</div>
                    <div class="cvd-summary-label">CVD types affected</div>
                </div>
            </div>

            <h3>Affected CVD Types</h3>
            <div class="cvd-type-breakdown">
                {cvd_breakdown_html}
            </div>

            <h3>Problematic Color Pairs</h3>
            <div class="cvd-pairs-list">
                {color_pairs_html}
            </div>

            <div class="cvd-recommendations">
                <h3>Recommendations</h3>
                <ul>
                    <li>Ensure contrast ratio of at least 4.5:1 for normal text (WCAG AA)</li>
                    <li>Avoid using red/green color combinations for important information</li>
                    <li>Don't rely on color alone to convey meaning - use patterns, labels, or icons</li>
                    <li>Test with color blindness simulation tools before publishing</li>
                </ul>
            </div>
        </div>
        """


def html_escape(text: str) -> str:
    """Escape HTML special characters"""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
