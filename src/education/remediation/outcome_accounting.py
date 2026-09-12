"""Account only for source-bound outcomes and distinguish withheld changes."""

from collections import Counter
from typing import Any


def outcome_accounting(
    issues,
    result,
    *,
    published: bool,
    original_issues=None,
    source_index_scope="original_scan",
) -> dict[str, Any]:
    """Associate exact, unique source IDs; missing evidence stays unreported."""

    def field(item, name):
        return item.get(name) if isinstance(item, dict) else getattr(item, name, None)

    ids = [field(issue, "id") for issue in issues]
    source_counts = Counter(
        identifier for identifier in ids if isinstance(identifier, str)
    )
    dispositions = {}
    for attr, status in (
        ("fixed_issues", "fixed" if published else "withheld"),
        ("manual_issues", "manual"),
        ("failed_issues", "failed"),
    ):
        records = getattr(result, attr, None)
        if not isinstance(records, (list, tuple)):
            continue
        for record in records:
            identifier = field(record, "issue_id")
            if isinstance(identifier, str):
                dispositions.setdefault(identifier, []).append(status)
    outcomes = []
    for index, identifier in enumerate(ids):
        matches = (
            dispositions.get(identifier, []) if isinstance(identifier, str) else []
        )
        status = (
            matches[0]
            if source_counts.get(identifier, 0) == 1 and len(matches) == 1
            else "unreported"
        )
        outcome = {
            "source_index": index,
            "source_index_scope": source_index_scope,
            "status": status,
        }
        original_id = (
            field(original_issues[index], "id")
            if original_issues is not None
            else identifier
        )
        if isinstance(original_id, str):
            outcome["issue_id"] = original_id
        outcomes.append(outcome)
    counts = Counter(item["status"] for item in outcomes)
    return {
        "total_issues": len(issues),
        "fixed_count": counts["fixed"],
        "withheld_count": counts["withheld"],
        "manual_count": counts["manual"],
        "failed_count": counts["failed"],
        "skipped_count": 0,
        "outcome_unreported_count": counts["unreported"],
        "remaining_count": len(issues) - counts["fixed"],
        "issue_outcomes": outcomes,
    }
