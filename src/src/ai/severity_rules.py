"""Deterministic severity resolution for accessibility violations.

Severity is *computed*, never generated. A compliance tool has to return the
same severity for the same violation every time it is asked: audit reports must
be reproducible, remediation queues must be stable, and an institution comparing
two scan runs must be able to trust that a change in the report reflects a change
in their content rather than a change in sampling.

Language models are the wrong tool for that job. Sampling makes them
non-deterministic by construction, and setting ``temperature=0`` does not fix it
(measured 2026-08-14: identical input, five runs, ``color-contrast`` alternated
between Critical and High at both 0.3 and 0.0). Provider errors add a second
source of variance that no decoding parameter addresses.

So severity comes from this module, and the LLM is left to do what it is good at:
writing the human-readable explanation and business-impact prose, where variation
between runs is harmless.

Resolution order:

1. An explicit per-rule override in :data:`SEVERITY_BY_RULE`, for rules whose
   compliance consequence differs from the scanner's generic impact rating.
2. The scanner's own ``impact`` value, mapped through :data:`SEVERITY_BY_IMPACT`.
3. :data:`DEFAULT_SEVERITY`, so an unrecognised rule degrades to a safe middle
   rather than raising.
"""

from __future__ import annotations

from typing import Dict, Literal, NamedTuple, Optional

Severity = Literal["Critical", "High", "Medium", "Low"]

#: Returned when neither the rule nor the impact is recognised.
DEFAULT_SEVERITY: Severity = "Medium"

#: axe-core impact levels mapped to Aelira severities. This is the backbone:
#: axe already computes impact deterministically, so inheriting it keeps us
#: aligned with the scanner rather than second-guessing it.
SEVERITY_BY_IMPACT: Dict[str, Severity] = {
    "critical": "Critical",
    "serious": "High",
    "moderate": "Medium",
    "minor": "Low",
}

#: Per-rule overrides, used only where the compliance consequence genuinely
#: differs from the generic impact rating. Every entry here is a deliberate
#: decision, not a transcription of axe's defaults — if a rule is absent, the
#: impact mapping above is doing the right thing already.
#:
#: Rationale for the escalations: each of these blocks access outright for at
#: least one user group, or is a documented WCAG 2.1 AA failure that an auditor
#: will treat as disqualifying regardless of how the scanner rates it.
SEVERITY_BY_RULE: Dict[str, Severity] = {
    # Content that is simply unavailable without the missing information.
    "image-alt": "Critical",
    "input-image-alt": "Critical",
    "area-alt": "Critical",
    "object-alt": "Critical",
    "video-caption": "Critical",
    # Forms that cannot be completed with assistive technology.
    "label": "Critical",
    "form-field-multiple-labels": "High",
    "select-name": "Critical",
    # Structural failures that break screen-reader navigation entirely.
    "html-has-lang": "Critical",
    "html-lang-valid": "Critical",
    "valid-lang": "High",
    "document-title": "High",
    # Keyboard and focus: no workaround exists for a keyboard-only user.
    "keyboard": "Critical",
    "focus-order-semantics": "High",
    "tabindex": "High",
    "accesskeys": "Low",
    # Tables: a data table without headers is unreadable non-visually.
    "td-headers-attr": "High",
    "th-has-data-cells": "High",
    "table-duplicate-name": "Low",
    # NOTE: no pdf-* entries. Verified 2026-08-14 that PDFProcessor does not
    # emit axe-style rule ids at all - its issues carry a "rule" string like
    # "WCAG 3.1.1" and a severity already fixed in code, and they never reach
    # this module. Speculative pdf-* keys were removed rather than left here
    # implying coverage that does not exist. Routing the PDF path through this
    # table would be a real improvement, but it is a change to that path, not
    # a lookup table entry.
    # Contrast is a serious barrier but content remains reachable, so it stays
    # at High rather than being escalated. Listed explicitly because this is
    # the rule that exposed the non-determinism and its rating should not drift.
    "color-contrast": "High",
    "color-contrast-enhanced": "Medium",
    # Landmark and heading structure: navigable, but materially harder.
    "heading-order": "Medium",
    "empty-heading": "Medium",
    "page-has-heading-one": "Medium",
    "landmark-one-main": "Medium",
    "region": "Low",
    # Link purpose.
    "link-name": "Critical",
    "link-in-text-block": "High",
    "identical-links-same-purpose": "Low",
}


class SeverityResolution(NamedTuple):
    """The resolved severity and where it came from.

    ``source`` exists so the decision is auditable: an institution asking why a
    violation was rated the way it was gets "rule table" or "scanner impact"
    rather than "the model said so".
    """

    severity: Severity
    source: Literal["rule", "impact", "default"]


def resolve_severity(
    rule_id: Optional[str], impact: Optional[str]
) -> SeverityResolution:
    """Resolve a violation's severity deterministically.

    Args:
        rule_id: Scanner rule identifier (axe-core rule ID or an Aelira PDF
            check ID). Matched case-insensitively.
        impact: Scanner impact level (``critical``/``serious``/``moderate``/
            ``minor``). Used when the rule has no explicit override.

    Returns:
        The resolved severity and the resolution path that produced it. The same
        arguments always produce the same result; this function performs no I/O
        and holds no state.
    """
    if rule_id:
        override = SEVERITY_BY_RULE.get(rule_id.strip().lower())
        if override is not None:
            return SeverityResolution(override, "rule")

    if impact:
        mapped = SEVERITY_BY_IMPACT.get(impact.strip().lower())
        if mapped is not None:
            return SeverityResolution(mapped, "impact")

    return SeverityResolution(DEFAULT_SEVERITY, "default")


def severity_for(rule_id: Optional[str], impact: Optional[str]) -> Severity:
    """Convenience wrapper returning only the severity string."""
    return resolve_severity(rule_id, impact).severity
