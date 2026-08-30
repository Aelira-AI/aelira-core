"""Fail-closed publication gates for verified image-equation fixes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

IMAGE_EQUATION_SOURCE_KIND = "image_equation"
CHEMICAL_STRUCTURE_SOURCE_KIND = "chemical_structure"
COMMUTATIVE_DIAGRAM_SOURCE_KIND = "commutative_diagram"
REVIEW_GATED_VISUAL_SOURCE_KINDS = frozenset(
    {
        IMAGE_EQUATION_SOURCE_KIND,
        CHEMICAL_STRUCTURE_SOURCE_KIND,
        COMMUTATIVE_DIAGRAM_SOURCE_KIND,
    }
)


class ImageEquationMatterhornError(RuntimeError):
    """The exact claimed artifact did not earn a usable Matterhorn result."""


def contains_image_equation_fixes(fixes: Iterable[Any]) -> bool:
    """Return whether an artifact carries review-gated visual semantics."""
    return any(
        getattr(fix, "source_kind", None) in REVIEW_GATED_VISUAL_SOURCE_KINDS
        for fix in fixes
    )


contains_review_gated_visual_fixes = contains_image_equation_fixes


def require_image_equation_matterhorn_result(result: Any) -> None:
    """Reject unavailable, malformed, empty, or failing Matterhorn results."""
    checkpoints = getattr(result, "checkpoints", None)
    if not isinstance(checkpoints, (list, tuple)) or not checkpoints:
        raise ImageEquationMatterhornError("matterhorn_result_unavailable")

    statuses: list[str] = []
    for checkpoint in checkpoints:
        status = getattr(checkpoint, "status", None)
        status_value = getattr(status, "value", status)
        if status_value not in {"pass", "fail", "warning"}:
            raise ImageEquationMatterhornError("matterhorn_result_integrity_invalid")
        statuses.append(status_value)

    expected = {
        "total": len(statuses),
        "passed": statuses.count("pass"),
        "failed": statuses.count("fail"),
        "warnings": statuses.count("warning"),
    }
    for field, value in expected.items():
        if getattr(result, field, None) != value:
            raise ImageEquationMatterhornError("matterhorn_result_integrity_invalid")
    if expected["failed"]:
        raise ImageEquationMatterhornError("matterhorn_result_disqualifying")
