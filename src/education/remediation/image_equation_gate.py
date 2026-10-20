"""Fail-closed publication gates for verified image-equation fixes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

IMAGE_EQUATION_SOURCE_KIND = "image_equation"
CHEMICAL_STRUCTURE_SOURCE_KIND = "chemical_structure"
CHEMICAL_FORMULA_SOURCE_KIND = "chemical_formula"
COMMUTATIVE_DIAGRAM_SOURCE_KIND = "commutative_diagram"
REVIEW_GATED_VISUAL_SOURCE_KINDS = frozenset(
    {
        IMAGE_EQUATION_SOURCE_KIND,
        CHEMICAL_STRUCTURE_SOURCE_KIND,
        CHEMICAL_FORMULA_SOURCE_KIND,
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


def recorded_source_matterhorn_failures(remediation_result: Any) -> Any:
    """Return the source failures the remediator recorded for the claimed output.

    The remediator compares the source against the exact claimed output bytes
    and records the checkpoints (id, page) the source already failed. Every
    publisher reads that record through this one accessor so the gate sees the
    same evidence whichever path produced the artifact.
    """
    return getattr(remediation_result, "source_matterhorn_failures", None)


def _failure_key(identifier: Any, page: Any) -> tuple[str, int | None]:
    if (
        not isinstance(identifier, str)
        or not identifier
        or (page is not None and (type(page) is not int or page < 0))
    ):
        raise ImageEquationMatterhornError("matterhorn_result_integrity_invalid")
    return identifier, page


def _source_failure_counts(source_failures: Any) -> Counter[tuple[str, int | None]]:
    """Validate the recorded source baseline; absence proves nothing pre-existed."""
    if source_failures is None:
        return Counter()
    if not isinstance(source_failures, (list, tuple)):
        raise ImageEquationMatterhornError("matterhorn_result_integrity_invalid")
    counts: Counter[tuple[str, int | None]] = Counter()
    for entry in source_failures:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ImageEquationMatterhornError("matterhorn_result_integrity_invalid")
        counts[_failure_key(entry[0], entry[1])] += 1
    return counts


def require_image_equation_matterhorn_result(result: Any, source_failures: Any) -> None:
    """Gate publication on a usable Matterhorn result for the exact output.

    The result must be present, non-empty, well formed and internally
    consistent. A failed checkpoint blocks publication only when the output
    fails it more often than the source did: pre-existing failures, such as a
    missing PDF/UA identifier that remediation never sets, are not introduced
    by remediation. ``source_failures`` is the remediator's record of the
    (checkpoint id, page) pairs the source failed; when it is absent nothing
    can be shown to pre-exist, so any failed checkpoint blocks.
    """
    checkpoints = getattr(result, "checkpoints", None)
    if not isinstance(checkpoints, (list, tuple)) or not checkpoints:
        raise ImageEquationMatterhornError("matterhorn_result_unavailable")

    statuses: list[str] = []
    output_failures: Counter[tuple[str, int | None]] = Counter()
    for checkpoint in checkpoints:
        status = getattr(checkpoint, "status", None)
        status_value = getattr(status, "value", status)
        if status_value not in {"pass", "fail", "warning"}:
            raise ImageEquationMatterhornError("matterhorn_result_integrity_invalid")
        statuses.append(status_value)
        if status_value == "fail":
            output_failures[
                _failure_key(
                    getattr(checkpoint, "id", None),
                    getattr(checkpoint, "page_number", None),
                )
            ] += 1

    expected = {
        "total": len(statuses),
        "passed": statuses.count("pass"),
        "failed": statuses.count("fail"),
        "warnings": statuses.count("warning"),
    }
    for field, value in expected.items():
        if getattr(result, field, None) != value:
            raise ImageEquationMatterhornError("matterhorn_result_integrity_invalid")

    if output_failures - _source_failure_counts(source_failures):
        raise ImageEquationMatterhornError("matterhorn_result_disqualifying")
