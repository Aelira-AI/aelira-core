"""Map WCAG criteria and scanner rules to IssueCategory values.

Shared utility used by web scanner and code scanner ScanFix persistence
to avoid duplicating category resolution logic.
"""

# ---------------------------------------------------------------------------
# WCAG 2.1 Success Criterion -> IssueCategory value
# ---------------------------------------------------------------------------

_WCAG_CRITERION_MAP: dict[str, str] = {
    # 1.1 Text Alternatives
    "1.1.1": "alt_text",
    # 1.2 Time-based Media
    "1.2.1": "alt_text",
    "1.2.2": "alt_text",
    "1.2.3": "alt_text",
    # 1.3 Adaptable
    "1.3.1": "structure",
    "1.3.2": "reading_order",
    # 1.4 Distinguishable
    "1.4.1": "color",
    "1.4.3": "contrast",
    "1.4.6": "contrast",
    # 2.1 Keyboard Accessible
    "2.1.1": "navigation",
    "2.1.2": "navigation",
    # 2.4 Navigable
    "2.4.1": "navigation",  # Skip navigation
    "2.4.2": "title",
    "2.4.3": "navigation",  # Focus order
    "2.4.4": "link",
    "2.4.6": "heading",
    "2.4.7": "navigation",  # Focus visible
    # 3.1 Readable
    "3.1.1": "language",
    "3.1.2": "language",
    # 3.3 Input Assistance
    "3.3.1": "form",
    "3.3.2": "form",
    # 4.1 Compatible
    "4.1.1": "structure",
    "4.1.2": "aria",
    "4.1.3": "aria",
}

# ---------------------------------------------------------------------------
# Code scanner (category, rule) -> IssueCategory value
# ---------------------------------------------------------------------------

_CODE_RULE_MAP: dict[tuple[str, str], str] = {
    # HTML rules
    ("html", "image-alt"): "alt_text",
    ("html", "heading-hierarchy"): "heading",
    ("html", "form-label"): "form",
    ("html", "lang-attribute"): "language",
    ("html", "page-title"): "title",
    ("html", "landmark-main"): "aria",
    ("html", "button-keyboard"): "navigation",
    # CSS rules
    ("css", "focus-indicator"): "navigation",
    ("css", "color-contrast"): "contrast",
    ("css", "font-size"): "structure",
}

# Categories where any rule maps to a single IssueCategory
_CATEGORY_WILDCARD_MAP: dict[str, str] = {
    "aria": "aria",
}

# ---------------------------------------------------------------------------
# axe-core impact -> severity / confidence
# ---------------------------------------------------------------------------

_IMPACT_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "serious": "high",
    "moderate": "medium",
    "minor": "low",
}

_IMPACT_CONFIDENCE_MAP: dict[str, float] = {
    "critical": 0.9,
    "serious": 0.8,
    "moderate": 0.7,
    "minor": 0.6,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def wcag_criterion_to_category(criterion: str) -> str:
    """Map a WCAG 2.1 success criterion to an IssueCategory value.

    Args:
        criterion: WCAG criterion string (e.g. "1.1.1").

    Returns:
        IssueCategory value string. Defaults to "structure" for unknown
        criteria.
    """
    return _WCAG_CRITERION_MAP.get(criterion.strip(), "structure")


def code_rule_to_category(scanner_category: str, rule: str) -> str:
    """Map a code scanner (category, rule) pair to an IssueCategory value.

    Args:
        scanner_category: Scanner category (e.g. "html", "css", "aria").
        rule: Scanner rule identifier (e.g. "image-alt").

    Returns:
        IssueCategory value string. Defaults to "structure" for unknown
        combinations.
    """
    cat = scanner_category.lower()
    r = rule.lower()

    # Check wildcard categories first (e.g. aria -> aria for any rule)
    if cat in _CATEGORY_WILDCARD_MAP:
        return _CATEGORY_WILDCARD_MAP[cat]

    return _CODE_RULE_MAP.get((cat, r), "structure")


def impact_to_severity(impact: str) -> str:
    """Map an axe-core impact level to an issue severity.

    Args:
        impact: axe-core impact string (critical, serious, moderate, minor).

    Returns:
        Severity string. Defaults to "medium" for unknown impacts.
    """
    return _IMPACT_SEVERITY_MAP.get(impact.lower(), "medium")


def impact_to_confidence(impact: str) -> float:
    """Map an axe-core impact level to a confidence score.

    Args:
        impact: axe-core impact string (critical, serious, moderate, minor).

    Returns:
        Confidence float between 0 and 1. Defaults to 0.7 for unknown
        impacts.
    """
    return _IMPACT_CONFIDENCE_MAP.get(impact.lower(), 0.7)
