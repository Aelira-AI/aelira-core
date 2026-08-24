"""Canonical issue-type contract for PDF math accessibility."""

from typing import Any, Mapping, Optional

IMAGE_EQUATION_ISSUE_TYPE = "image_equation_inaccessible"

DOCUMENT_WIDE_MATH_ISSUE_TYPES = frozenset(
    {
        "latex_equations_inaccessible",
        "math_content_accessibility",
        "raw_latex_code",
        "mathml_recommendation",
    }
)
CONCRETE_MATH_ISSUE_TYPES = frozenset({IMAGE_EQUATION_ISSUE_TYPE})
MATH_ISSUE_TYPES = DOCUMENT_WIDE_MATH_ISSUE_TYPES | CONCRETE_MATH_ISSUE_TYPES


def math_issue_type_from(issue: Mapping[str, Any]) -> Optional[str]:
    """Return a canonical math issue type from top-level or metadata fields."""
    issue_type = issue.get("issue_type")
    if not isinstance(issue_type, str):
        metadata = issue.get("metadata")
        issue_type = metadata.get("issue_type") if isinstance(metadata, Mapping) else None
    return issue_type if issue_type in MATH_ISSUE_TYPES else None


def is_concrete_math_issue_type(issue_type: object) -> bool:
    """Return whether an issue identifies one addressable math occurrence."""
    return isinstance(issue_type, str) and issue_type in CONCRETE_MATH_ISSUE_TYPES
