"""Fail-closed publication gates for verified image-equation fixes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

IMAGE_EQUATION_SOURCE_KIND = "image_equation"


class ImageEquationMatterhornError(RuntimeError):
    """The exact claimed artifact did not earn a usable Matterhorn result."""


def contains_image_equation_fixes(fixes: Iterable[Any]) -> bool:
    """Return whether an artifact carries any image-equation remediation."""
    return any(
        getattr(fix, "source_kind", None) == IMAGE_EQUATION_SOURCE_KIND for fix in fixes
    )


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
