"""
Merge and deduplicate results from multiple accessibility engines.

Combines results from axe-core and Pa11y, removing duplicates while
preserving the most detailed information.
"""

import logging
from typing import Dict, List, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MergedIssue:
    """A merged accessibility issue from multiple engines"""

    selector: str
    code: str
    message: str
    severity: str  # error, warning, notice
    detected_by: List[str]  # Which engines found this issue
    context: str
    wcag_criteria: List[str]  # WCAG success criteria violated
    fix_suggestion: str = ""


class ResultMerger:
    """
    Merge and deduplicate accessibility scan results from multiple engines.

    Deduplication strategy:
    - Same selector + same WCAG code = duplicate
    - If found by multiple engines, keep most detailed message
    - Track which engines found each issue (attribution)
    """

    @staticmethod
    def create_issue_key(selector: str, code: str) -> str:
        """
        Create a unique key for an issue based on selector and code.

        Args:
            selector: CSS selector where issue was found
            code: Issue code (WCAG criterion or engine-specific code)

        Returns:
            Unique key string for deduplication
        """
        # Normalize selector (remove extra spaces, lowercase)
        normalized_selector = " ".join(selector.lower().split())
        # Normalize code (uppercase, remove prefixes)
        normalized_code = code.upper().replace("WCAG2AA.", "").replace("AXE_", "")

        return f"{normalized_selector}::{normalized_code}"

    @staticmethod
    def extract_wcag_criteria(code: str) -> List[str]:
        """
        Extract WCAG success criteria from issue code.

        Examples:
            "WCAG2AA.Principle1.Guideline1_4.1_4_3.G18" -> ["1.4.3"]
            "color-contrast" -> ["1.4.3"]

        Args:
            code: Issue code from accessibility engine

        Returns:
            List of WCAG criteria codes
        """
        criteria = []

        # Handle WCAG2AA format (HTML_CodeSniffer)
        if "WCAG2" in code.upper():
            parts = code.split(".")
            for part in parts:
                if (
                    part.startswith("1_")
                    or part.startswith("2_")
                    or part.startswith("3_")
                    or part.startswith("4_")
                ):
                    # Convert 1_4_3 to 1.4.3
                    criteria.append(part.replace("_", "."))

        # Map common axe-core rules to WCAG criteria
        axe_to_wcag = {
            "color-contrast": ["1.4.3"],
            "image-alt": ["1.1.1"],
            "label": ["1.3.1", "4.1.2"],
            "link-name": ["2.4.4", "4.1.2"],
            "button-name": ["4.1.2"],
            "document-title": ["2.4.2"],
            "html-has-lang": ["3.1.1"],
            "valid-lang": ["3.1.2"],
            "landmark-one-main": ["1.3.1"],
            "page-has-heading-one": ["1.3.1"],
            "region": ["1.3.1"],
        }

        if code in axe_to_wcag:
            criteria.extend(axe_to_wcag[code])

        return list(set(criteria))  # Remove duplicates

    @staticmethod
    def merge_axe_and_pa11y_results(
        axe_results: Dict[str, Any], pa11y_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge results from axe-core and Pa11y, removing duplicates.

        Args:
            axe_results: Results from axe-core scanner
            pa11y_results: Results from Pa11y scanner

        Returns:
            Merged results with deduplication and attribution
        """
        seen: Dict[str, MergedIssue] = {}

        # Process axe-core results
        if axe_results and "violations" in axe_results:
            for violation in axe_results["violations"]:
                for node in violation.get("nodes", []):
                    selector = (
                        node.get("target", ["unknown"])[0]
                        if isinstance(node.get("target"), list)
                        else str(node.get("target", "unknown"))
                    )
                    code = violation.get("id", "unknown")

                    key = ResultMerger.create_issue_key(selector, code)

                    issue = MergedIssue(
                        selector=selector,
                        code=code,
                        message=violation.get("help", node.get("failureSummary", "")),
                        severity="error",  # axe-core violations are errors
                        detected_by=["axe-core"],
                        context=node.get("html", ""),
                        wcag_criteria=ResultMerger.extract_wcag_criteria(code),
                        fix_suggestion=violation.get("helpUrl", ""),
                    )

                    seen[key] = issue

        # Process Pa11y results
        if pa11y_results and "issues" in pa11y_results:
            for issue_data in pa11y_results["issues"]:
                selector = issue_data.get("selector", "unknown")
                code = issue_data.get("code", "unknown")

                key = ResultMerger.create_issue_key(selector, code)

                if key in seen:
                    # Duplicate found - add Pa11y to detected_by
                    seen[key].detected_by.append("pa11y")

                    # Use longer message if Pa11y's is more detailed
                    pa11y_msg = issue_data.get("message", "")
                    if len(pa11y_msg) > len(seen[key].message):
                        seen[key].message = pa11y_msg

                    # Merge context if Pa11y has more
                    pa11y_context = issue_data.get("context", "")
                    if len(pa11y_context) > len(seen[key].context):
                        seen[key].context = pa11y_context
                else:
                    # New issue only found by Pa11y
                    issue = MergedIssue(
                        selector=selector,
                        code=code,
                        message=issue_data.get("message", ""),
                        severity=issue_data.get("type", "error"),
                        detected_by=["pa11y"],
                        context=issue_data.get("context", ""),
                        wcag_criteria=ResultMerger.extract_wcag_criteria(code),
                    )
                    seen[key] = issue

        # Convert to output format
        unique_issues = list(seen.values())

        # Count by severity and engine
        severity_counts = {"error": 0, "warning": 0, "notice": 0}
        engine_counts = {"axe-core": 0, "pa11y": 0, "both": 0}

        for issue in unique_issues:
            severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1

            if len(issue.detected_by) > 1:
                engine_counts["both"] += 1
            elif "axe-core" in issue.detected_by:
                engine_counts["axe-core"] += 1
            elif "pa11y" in issue.detected_by:
                engine_counts["pa11y"] += 1

        logger.info(
            f"Merged results: {len(unique_issues)} unique issues "
            f"(errors={severity_counts['error']}, warnings={severity_counts['warning']}, "
            f"notices={severity_counts['notice']}) | "
            f"Found by: axe-core={engine_counts['axe-core']}, "
            f"pa11y={engine_counts['pa11y']}, both={engine_counts['both']}"
        )

        return {
            "total_issues": len(unique_issues),
            "unique_issues": len(unique_issues),
            "severity_counts": severity_counts,
            "engine_counts": engine_counts,
            "issues": [
                {
                    "selector": issue.selector,
                    "code": issue.code,
                    "message": issue.message,
                    "severity": issue.severity,
                    "detected_by": issue.detected_by,
                    "context": issue.context,
                    "wcag_criteria": issue.wcag_criteria,
                    "fix_suggestion": issue.fix_suggestion,
                }
                for issue in unique_issues
            ],
            "engines_used": list(
                set(engine for issue in unique_issues for engine in issue.detected_by)
            ),
        }

    @staticmethod
    def calculate_coverage_improvement(
        axe_only_count: int, pa11y_only_count: int, both_count: int
    ) -> Dict[str, float]:
        """
        Calculate how much Pa11y improves coverage over axe-core alone.

        Args:
            axe_only_count: Issues found only by axe-core
            pa11y_only_count: Issues found only by Pa11y
            both_count: Issues found by both engines

        Returns:
            Coverage statistics and improvement percentage
        """
        total_issues = axe_only_count + pa11y_only_count + both_count
        axe_total = axe_only_count + both_count
        pa11y_total = pa11y_only_count + both_count

        if axe_total == 0:
            improvement = 0.0
        else:
            improvement = (pa11y_only_count / axe_total) * 100

        return {
            "total_unique_issues": total_issues,
            "axe_core_found": axe_total,
            "pa11y_found": pa11y_total,
            "both_found": both_count,
            "pa11y_only_found": pa11y_only_count,
            "improvement_percentage": round(improvement, 2),
            "overlap_percentage": round(
                (both_count / total_issues * 100) if total_issues > 0 else 0, 2
            ),
        }
