"""Account only for source-bound outcomes and distinguish withheld changes."""

from collections import Counter
from typing import Any
from .outcome_explanations import manual_explanation, public_explanation
from .outcome_evidence import fixed_record_evidence


def outcome_accounting(
    issues,
    result,
    *,
    published: bool,
    original_issues=None,
    source_index_scope="original_scan",
    withheld_reason=None,
    source_sha256=None,
    output_sha256=None,
    source_original_indices=None,
) -> dict[str, Any]:
    """Associate exact, unique source IDs; missing evidence stays unreported."""

    def field(item, name):
        return item.get(name) if isinstance(item, dict) else getattr(item, name, None)

    ids = [field(issue, "id") for issue in issues]
    original_indices = None
    if (
        source_index_scope == "approved_subset"
        and isinstance(source_original_indices, list)
        and len(source_original_indices) == len(issues)
        and all(
            type(value) is int and 0 <= value < 10_000
            for value in source_original_indices
        )
        and len(set(source_original_indices)) == len(source_original_indices)
    ):
        original_indices = source_original_indices
    source_counts = Counter(
        identifier for identifier in ids if isinstance(identifier, str)
    )
    dispositions: dict[str, list[tuple[str, dict]]] = {}
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
                explanation = (
                    manual_explanation(record)
                    if status == "manual"
                    else (
                        public_explanation(withheld_reason)
                        if status == "withheld"
                        else {}
                    )
                )
                explanation.update(
                    fixed_record_evidence(
                        {
                            key: field(record, key)
                            for key in (
                                "issue_id",
                                "verification_passed",
                                "needs_review",
                                "saved_file_verification",
                            )
                        },
                        status=status,
                        source_sha256=source_sha256,
                        output_sha256=output_sha256,
                    )
                )
                dispositions.setdefault(identifier, []).append((status, explanation))
    outcomes = []
    for index, identifier in enumerate(ids):
        matches = (
            dispositions.get(identifier, []) if isinstance(identifier, str) else []
        )
        status = (
            matches[0][0]
            if isinstance(identifier, str)
            and source_counts.get(identifier, 0) == 1
            and len(matches) == 1
            else "unreported"
        )
        outcome = {
            "source_index": index,
            "source_index_scope": source_index_scope,
            "status": status,
        }
        if status != "unreported":
            outcome.update(matches[0][1])
        original_id = (
            field(original_issues[index], "id")
            if original_issues is not None
            else identifier
        )
        if isinstance(original_id, str):
            outcome["issue_id"] = original_id
        if "saved_file_verification" in outcome:
            receipt = dict(outcome["saved_file_verification"])
            receipt.pop("issue_id")
            outcome["saved_file_verification"] = {
                **receipt,
                "source_index": index,
                "source_index_scope": source_index_scope,
            }
            if original_indices is not None:
                original_index = original_indices[index]
                outcome["original_source_index"] = original_index
                outcome["saved_file_verification"][
                    "original_source_index"
                ] = original_index
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
