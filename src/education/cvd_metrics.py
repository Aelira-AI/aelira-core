"""Persist and aggregate color-vision-deficiency scan evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

_MISSING = object()


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, _MISSING)
    return getattr(value, name, _MISSING)


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        serialized = model_dump(mode="json")
        return dict(serialized) if isinstance(serialized, Mapping) else None
    return None


def _serialize_sequence(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, (list, tuple)):
        return None
    serialized: list[dict[str, Any]] = []
    for analysis in value:
        item = _json_object(analysis)
        if item is None:
            return None
        serialized.append(item)
    return serialized


def serialize_cvd_analysis(result: Any) -> list[dict[str, Any]] | None:
    """Return JSON-safe CVD evidence without turning missing analysis into a pass."""

    direct = _field(result, "cvd_analysis")
    if direct is not _MISSING:
        return _serialize_sequence(direct)

    pages = _field(result, "pages")
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
        return None

    serialized: list[dict[str, Any]] = []
    for page in pages:
        page_analysis = _field(page, "cvd_analysis")
        if page_analysis is _MISSING or page_analysis is None:
            return None
        page_serialized = _serialize_sequence(page_analysis)
        if page_serialized is None:
            return None
        serialized.extend(page_serialized)
    return serialized if pages else None


def _count_cvd_issues(value: Any) -> int | None:
    if not isinstance(value, list):
        return None
    issue_count = 0
    for analysis in value:
        if not isinstance(analysis, Mapping):
            return None
        issues = analysis.get("issues")
        if not isinstance(issues, list) or not all(
            isinstance(issue, Mapping) for issue in issues
        ):
            return None
        issue_count += len(issues)
    return issue_count


@dataclass(frozen=True)
class CVDMetrics:
    files_analyzed: int
    affected_files: int
    issues_total: int
    accessibility_rate: float | None


def aggregate_cvd_metrics(documents: Iterable[Any]) -> CVDMetrics:
    """Aggregate only documents carrying complete, structurally valid evidence."""

    files_analyzed = affected_files = issues_total = 0
    for document in documents:
        result = _field(document, "result")
        if result is _MISSING or result is None:
            continue
        issue_count = _count_cvd_issues(_field(result, "cvd_analysis"))
        if issue_count is None:
            continue
        files_analyzed += 1
        issues_total += issue_count
        if issue_count:
            affected_files += 1

    accessibility_rate = (
        round((files_analyzed - affected_files) / files_analyzed * 100, 2)
        if files_analyzed
        else None
    )
    return CVDMetrics(
        files_analyzed=files_analyzed,
        affected_files=affected_files,
        issues_total=issues_total,
        accessibility_rate=accessibility_rate,
    )
