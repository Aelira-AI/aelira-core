"""Recover immutable source locators for a reviewed PDF replay without generation."""

from collections import Counter

from .base import IssueCategory, classify_issue_category


def recover_reviewed_pdf_issues(selected, source_rows):
    """Bind each reviewed row to the same unique original scan finding."""
    source_rows = (
        source_rows.get("details", []) if isinstance(source_rows, dict) else source_rows
    )
    if not isinstance(source_rows, list) or not isinstance(selected, list):
        raise ValueError("reviewed_pdf_source_binding_unavailable")
    rows = [
        (index, {**row, "id": row.get("id") or f"source-{index}"})
        for index, row in enumerate(source_rows)
        if isinstance(row, dict)
    ]
    ids = [row["id"] for _, row in rows]
    if any(not isinstance(identifier, str) for identifier in ids):
        raise ValueError("reviewed_pdf_source_binding_unavailable")
    counts = Counter(ids)
    by_id = {row["id"]: (index, row) for index, row in rows}
    seen = set()
    recovered = []
    for issue in selected:
        identifier = issue.get("id")
        if (
            not isinstance(identifier, str)
            or counts[identifier] != 1
            or identifier in seen
        ):
            raise ValueError("reviewed_pdf_source_binding_unavailable")
        original_index, source = by_id[identifier]
        category = classify_issue_category(source, authoritative=True)
        source_category = category.category
        if (
            source_category == IssueCategory.OTHER
            and source.get("rule") == "WCAG 1.1.1"
        ):
            # Match BaseRemediator's legacy WCAG mapping for the saved image
            # findings; display-only AI labels do not establish a new identity.
            source_category = IssueCategory.ALT_TEXT
        if (
            category.manual_reason is not None
            or source_category.value != issue.get("category")
            or source.get("description", source.get("message"))
            != issue.get("description")
            or source.get("location") != issue.get("location")
            or not isinstance(issue.get("fixed_content"), str)
        ):
            raise ValueError("reviewed_pdf_source_binding_unavailable")
        metadata = source.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("reviewed_pdf_source_binding_unavailable")
        recovered.append(
            {
                **source,
                **issue,
                "original_source_index": original_index,
                "metadata": {
                    **source,
                    **metadata,
                    "reviewed_fixed_content": issue["fixed_content"],
                },
            }
        )
        seen.add(identifier)
    return recovered
