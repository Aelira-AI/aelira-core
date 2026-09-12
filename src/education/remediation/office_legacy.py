"""Recover legacy Office records against the retained, real source file.

Recovery is a one-to-one enrichment of the requested findings, never a new job
containing every result from the rescan. Stored evidence is not modified.
"""

from copy import deepcopy

from .office_verification import scan_office
from .base import classify_issue_category

_ANCHORS = (
    "paragraph_index",
    "image_index",
    "table_index",
    "sheet_name",
    "sheet_index",
    "table_range",
    "chart_index",
    "merge_range",
    "cell_range",
    "cell_reference",
    "shape_id",
    "shape_name",
    "slide_index",
    "link_url",
    "link_text",
    "text",
    "current_level",
    "expected_level",
    "font_size_pt",
    "has_alt_text",
    "existing_alt_text",
    "existing_title",
)


def recover_office_findings(
    document_type, source, issues, *, approved_fixes_only=False
):
    """Only hydrate recognizable legacy rows with a unique source match."""
    recovered = deepcopy(issues)
    legacy = [
        index
        for index, issue in enumerate(issues)
        if (not issue.get("category") and not issue.get("location"))
        or approved_fixes_only
    ]
    if not legacy:
        return recovered
    candidates = scan_office(document_type, str(source)).issues
    matches = {}
    for index in legacy:
        raw = issues[index]
        if raw.get("metadata") is not None and not isinstance(raw["metadata"], dict):
            continue
        fields = {**(raw.get("metadata") or {}), **raw}
        if approved_fixes_only and fields.get("category"):
            classification = classify_issue_category(raw, authoritative=True)
            if classification.manual_reason or not fields.get("location"):
                continue
            possible = [
                candidate_index
                for candidate_index, candidate in enumerate(candidates)
                if candidate.get("location") == fields["location"]
                and classify_issue_category(candidate, authoritative=True).category
                == classification.category
            ]
            if len(possible) == 1:
                matches[index] = possible[0]
            continue
        if fields.get("slide") is not None:
            if type(fields["slide"]) is not int or fields["slide"] < 1:
                continue
            fields["slide_index"] = fields["slide"] - 1
        anchors = {key: fields[key] for key in _ANCHORS if fields.get(key) is not None}
        subtype = fields.get("issue_type")
        # Global DOCX metadata is uniquely located by its concrete finding type.
        if not anchors and not (
            document_type == "word"
            and fields.get("type") in {"title", "language"}
            and subtype
        ):
            continue
        possible = []
        for candidate_index, candidate in enumerate(candidates):
            metadata = candidate.get("metadata", {})
            if metadata.get("scanner_type") != fields.get("type"):
                continue
            if subtype and metadata.get("scanner_issue_type") != subtype:
                continue
            if all(metadata.get(key) == value for key, value in anchors.items()):
                possible.append(candidate_index)
        if len(possible) == 1:
            matches[index] = possible[0]
    for index in legacy:
        target = matches.get(index)
        if target is None or list(matches.values()).count(target) != 1:
            metadata = recovered[index].get("metadata")
            recovered[index]["metadata"] = (
                metadata if isinstance(metadata, dict) else {}
            )
            recovered[index]["metadata"][
                "classification_manual_reason"
            ] = "office_source_finding_not_uniquely_matched"
            continue
        raw = issues[index]
        candidate = deepcopy(candidates[target])
        # Only the subtype actually reproduced above is scanner context. Other
        # category-bearing input must retain its ordinary fail-closed meaning.
        guard = deepcopy(raw)
        guard.update(category=candidate["category"], type=candidate["category"])
        guard.pop("issue_type", None)
        guard_metadata = guard.get("metadata") or {}
        if guard_metadata.get("issue_type") == candidate["metadata"].get(
            "scanner_issue_type"
        ):
            guard_metadata.pop("issue_type", None)
        if guard.get("rule") == "Best Practice" and raw.get("type") in {
            "sheet_name",
            "merge",
            "navigation",
        }:
            guard.pop("rule")
        classification = classify_issue_category(guard, authoritative=True)
        expected = classify_issue_category(candidate, authoritative=True)
        if classification.manual_reason or classification.category != expected.category:
            recovered[index].setdefault("metadata", {})[
                "classification_manual_reason"
            ] = "conflicting_office_source_evidence"
            continue
        candidate["id"] = raw.get("id") or f"issue-{index}"
        # Keep review-selected fixes and semantic evidence; do not re-author them.
        candidate["metadata"] = {
            **deepcopy(raw.get("metadata") or {}),
            **candidate["metadata"],
        }
        candidate["metadata"].pop("issue_type", None)
        for key in (
            "alt_text_validated",
            "alt_text_accurate",
            "has_alt_text",
            "existing_alt_text",
        ):
            if key in (raw.get("metadata") or {}):
                candidate["metadata"][key] = deepcopy(raw["metadata"][key])
        for key in (
            "fixed_content",
            "fix_suggestion",
            "original_content",
            "visual_semantic_contract",
            "alt_text_validated",
            "alt_text_accurate",
            "has_alt_text",
            "existing_alt_text",
        ):
            if key in raw:
                candidate[key] = deepcopy(raw[key])
        if raw.get("description"):
            candidate["description"] = raw["description"]
        for key, restrictive_value in (
            ("alt_text_validated", True),
            ("has_alt_text", True),
            ("alt_text_accurate", False),
        ):
            if any(
                record.get(key) is restrictive_value
                for record in (raw, raw.get("metadata") or {})
            ):
                candidate[key] = restrictive_value
                candidate["metadata"][key] = restrictive_value
        recovered[index] = candidate
    return recovered
