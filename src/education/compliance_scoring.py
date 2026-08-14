"""
Unified Compliance Scoring System

This module provides a consistent scoring methodology across all Aelira scanners
(PDF, PowerPoint, Web, Code, Multimedia, LaTeX).

WCAG Compliance Philosophy:
- WCAG 2.1 is binary: you either pass ALL criteria at a level, or you don't
- However, a percentage score helps users understand progress and prioritize fixes
- Our scoring reflects: "How close are you to full compliance?"

Scoring Methodology:
1. Critical issues = Automatic fail (max 49/100)
2. Without critical issues, score = (passed_elements / total_elements) * 100
3. Severity weighting adjusts the "cost" of each issue type

Severity Definitions (aligned with axe-core):
- Critical: Blocks access entirely (missing alt text on key images, no keyboard access)
- High/Serious: Major barrier but workaround exists
- Medium/Moderate: Degraded experience
- Low/Minor: Best practice violation
"""

import math
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

# Ratio scoring divides weighted issues by total_elements, so small documents
# produce degenerate scores: weighted_issues >= total_elements pins the score
# to 0.0 (a 3-element slide with one high + one medium issue can never score
# above zero), while tiny denominators swing the score wildly. Below this
# threshold the size-independent penalty path is used instead (issue #90).
MIN_ELEMENTS_FOR_RATIO = 20


class Severity(Enum):
    """Issue severity levels aligned with WCAG impact"""

    CRITICAL = "critical"
    HIGH = "high"
    SERIOUS = "serious"  # Alias for HIGH (axe-core uses this)
    MEDIUM = "medium"
    MODERATE = "moderate"  # Alias for MEDIUM (axe-core uses this)
    LOW = "low"
    MINOR = "minor"  # Alias for LOW (axe-core uses this)


@dataclass
class ScoringResult:
    """Result of compliance score calculation"""

    score: float  # 0-100
    grade: str  # A, B, C, D, F
    status: str  # Compliant, Needs Work, Failing
    has_critical_issues: bool
    total_issues: int
    issues_by_severity: Dict[str, int]
    max_possible_score: float  # With critical issues, this is 49


def normalize_severity(severity: str) -> str:
    """Normalize severity string to standard values"""
    severity = severity.lower().strip()

    # Map aliases to standard values
    mapping = {
        "serious": "high",
        "moderate": "medium",
        "minor": "low",
    }

    return mapping.get(severity, severity)


def calculate_compliance_score(
    issues: List[Dict[str, Any]],
    total_elements: Optional[int] = None,
    severity_field: str = "severity",
) -> ScoringResult:
    """
    Calculate unified compliance score.

    This function provides consistent scoring across all scanner types.

    Args:
        issues: List of issue dictionaries, each with a severity field
        total_elements: Optional total elements scanned (for ratio-based scoring)
                       If not provided, uses penalty-based scoring
        severity_field: Name of the field containing severity (default: 'severity')

    Returns:
        ScoringResult with score, grade, status, and breakdown

    Scoring Logic:
    1. If total_elements is provided: Ratio-based scoring
       - Score = (passed / total) * 100, adjusted by severity weights
    2. If no total_elements: Penalty-based scoring with diminishing returns
       - Prevents scores from hitting 0 too quickly
       - Still penalizes multiple issues appropriately
    """
    if not issues:
        return ScoringResult(
            score=100.0,
            grade="A",
            status="Compliant",
            has_critical_issues=False,
            total_issues=0,
            issues_by_severity={"critical": 0, "high": 0, "medium": 0, "low": 0},
            max_possible_score=100.0,
        )

    # Count issues by normalized severity
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    for issue in issues:
        sev = normalize_severity(str(issue.get(severity_field, "medium")))
        if sev in counts:
            counts[sev] += 1
        else:
            counts["medium"] += 1  # Default unknown severities to medium

    has_critical = counts["critical"] > 0
    total_issues = sum(counts.values())

    # Calculate score
    if total_elements and total_elements >= MIN_ELEMENTS_FOR_RATIO:
        # Ratio-based scoring (preferred when we know total elements and the
        # denominator is large enough not to saturate)
        score = _calculate_ratio_score(counts, total_elements)
    else:
        # Penalty-based scoring with diminishing returns — size-independent,
        # so identical defects score identically for any small document
        score = _calculate_penalty_score(counts)

    # Cap at 49 if critical issues exist
    max_possible = 49.0 if has_critical else 100.0
    score = min(score, max_possible)

    # Determine grade and status
    grade = _get_grade(score, has_critical)
    status = _get_status(score, has_critical)

    return ScoringResult(
        score=round(score, 1),
        grade=grade,
        status=status,
        has_critical_issues=has_critical,
        total_issues=total_issues,
        issues_by_severity=counts,
        max_possible_score=max_possible,
    )


def _calculate_ratio_score(counts: Dict[str, int], total_elements: int) -> float:
    """
    Calculate score based on ratio of issues to total elements.

    Weights:
    - Critical: Each issue counts as 4 failed elements
    - High: Each issue counts as 2 failed elements
    - Medium: Each issue counts as 1 failed element
    - Low: Each issue counts as 0.5 failed elements
    """
    weighted_issues = (
        counts["critical"] * 4.0
        + counts["high"] * 2.0
        + counts["medium"] * 1.0
        + counts["low"] * 0.5
    )

    # Calculate percentage of "failed" elements
    fail_ratio = weighted_issues / total_elements

    # Score is inverse of fail ratio, scaled to 0-100
    # Cap fail_ratio at 1.0 to avoid negative scores
    score = (1 - min(fail_ratio, 1.0)) * 100

    return max(0.0, score)


def _calculate_penalty_score(counts: Dict[str, int]) -> float:
    """
    Calculate score using penalty system with diminishing returns.

    This prevents scores from hitting 0 too quickly while still
    penalizing documents with many issues.

    Uses logarithmic scaling: first few issues hurt more,
    subsequent issues have diminishing impact.
    """

    def diminishing_penalty(count: int, base: float, max_pen: float) -> float:
        if count == 0:
            return 0.0
        # Log base 2 scaling: 1 issue = base, 2 = base*2, 4 = base*3, etc.
        return min(max_pen, base * (1 + math.log2(count)))

    # Penalty structure:
    # - Critical: base 20 points, max 50 points (very punitive)
    # - High: base 8 points, max 30 points
    # - Medium: base 3 points, max 15 points
    # - Low: base 1 point, max 5 points

    critical_penalty = diminishing_penalty(counts["critical"], 20, 50)
    high_penalty = diminishing_penalty(counts["high"], 8, 30)
    medium_penalty = diminishing_penalty(counts["medium"], 3, 15)
    low_penalty = diminishing_penalty(counts["low"], 1, 5)

    total_penalty = critical_penalty + high_penalty + medium_penalty + low_penalty

    return max(0.0, 100.0 - total_penalty)


def _get_grade(score: float, has_critical: bool) -> str:
    """Get letter grade from score"""
    if has_critical:
        return "F"  # Critical issues = automatic fail
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _get_status(score: float, has_critical: bool) -> str:
    """Get compliance status from score"""
    if has_critical:
        return "Critical Issues"
    if score >= 90:
        return "Compliant"
    if score >= 70:
        return "Needs Work"
    return "Failing"


# Convenience functions for common patterns


def score_from_severity_counts(
    critical: int = 0,
    high: int = 0,
    medium: int = 0,
    low: int = 0,
    total_elements: Optional[int] = None,
) -> ScoringResult:
    """
    Calculate score directly from severity counts.

    Convenience function when you already have counts.
    """
    # Create fake issues list with correct counts
    issues = []
    for sev, count in [
        ("critical", critical),
        ("high", high),
        ("medium", medium),
        ("low", low),
    ]:
        issues.extend([{"severity": sev}] * count)

    return calculate_compliance_score(issues, total_elements)


def get_score_only(
    issues: List[Dict[str, Any]],
    total_elements: Optional[int] = None,
    severity_field: str = "severity",
) -> float:
    """
    Get just the numeric score (0-100).

    Convenience function when you only need the score value.
    """
    result = calculate_compliance_score(issues, total_elements, severity_field)
    return result.score
