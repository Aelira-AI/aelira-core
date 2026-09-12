"""Measured Canvas candidate outcomes bound to the source and saved body."""

from collections import Counter
import hashlib
import json
import math

VERIFICATION_KEY = "content_score_verification"
METHOD_VERSION = "canvas-axe-v1"


def paired_canvas_outcome(source_results, output_results, original_issues):
    """Require the current source rules to agree before comparing saved HTML."""
    source = measured_canvas_outcome(source_results, original_issues)
    if source["fixed"] or source["introduced"]:
        raise ValueError("Source scan findings changed; rescan required")
    return {
        **measured_canvas_outcome(output_results, original_issues),
        "source_score": source["score"],
    }


def canvas_score_measurement(source_body, output_body, outcome):
    return {
        "method_version": METHOD_VERSION,
        "source_sha256": hashlib.sha256(source_body.encode("utf-8")).hexdigest(),
        "output_sha256": hashlib.sha256(output_body.encode("utf-8")).hexdigest(),
        "source_score": outcome["source_score"],
        "output_score": outcome["score"],
    }


def unresolved_source_count(issues):
    """Keep every source finding unresolved when a comparable rescan is absent."""
    return sum(
        (
            len(issue["nodes"])
            if isinstance(issue, dict) and isinstance(issue.get("nodes"), list)
            else 1
        )
        for issue in issues
    )


def count_nodes_by_rule(violations):
    counts = Counter()
    for issue in violations:
        if not isinstance(issue, dict) or not isinstance(issue.get("id"), str):
            raise ValueError("Malformed accessibility finding")
        nodes = issue.get("nodes")
        if not isinstance(nodes, list):
            raise ValueError("Missing accessibility finding nodes")
        counts[issue["id"]] += len(nodes)
    return counts


def measured_canvas_outcome(results, original_issues):
    """Compare measured axe node counts; an empty check is not a perfect score."""
    violations, passes = results["violations"], results["passes"]
    if not isinstance(violations, list) or not isinstance(passes, list):
        raise ValueError("Invalid axe result")
    total_rules = len(passes) + len(violations)
    if total_rules == 0:
        raise ValueError("No accessibility rules executed")
    before, after = count_nodes_by_rule(original_issues), count_nodes_by_rule(
        violations
    )
    return {
        "score": round(100 * len(passes) / total_rules, 1),
        "fixed": sum((before - after).values()),
        "remaining": sum((before & after).values()),
        "introduced": sum((after - before).values()),
    }


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _record(cloud_file, issues, outcome):
    return {
        "version": 2,
        "scanner": "deterministic_axe",
        "method_version": METHOD_VERSION,
        "scan_id": cloud_file.last_scan_id,
        "source_sha256": _digest(cloud_file.content_body),
        "candidate_sha256": _digest(cloud_file.remediated_body),
        "issues_sha256": _digest(issues),
        **outcome,
    }


def store_canvas_verification(cloud_file, issues, outcome):
    metadata = getattr(cloud_file, "provider_metadata", None)
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    metadata.pop(VERIFICATION_KEY, None)
    if outcome is not None:
        metadata[VERIFICATION_KEY] = _record(cloud_file, issues, outcome)
    cloud_file.provider_metadata = metadata


def current_canvas_verification(cloud_file, issues):
    """Only use persisted counts for the exact source scan and saved candidate."""
    if getattr(cloud_file, "needs_rescan", False) is True:
        return None
    metadata = getattr(cloud_file, "provider_metadata", None)
    record = metadata.get(VERIFICATION_KEY) if isinstance(metadata, dict) else None
    if not isinstance(record, dict) or cloud_file.remediated_body is None:
        return None
    outcome = {
        key: record.get(key)
        for key in ("score", "source_score", "fixed", "remaining", "introduced")
    }
    if any(
        type(outcome[key]) is not int or outcome[key] < 0
        for key in ("fixed", "remaining", "introduced")
    ):
        return None
    score = outcome["score"]
    for value in (score, outcome["source_score"]):
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not 0 <= value <= 100
        ):
            return None
    try:
        if record != _record(cloud_file, issues, outcome):
            return None
        if (
            cloud_file.remediated_compliance_score != score
            or cloud_file.remediated_issues_fixed != outcome["fixed"]
            or cloud_file.remediated_issues_remaining
            != outcome["remaining"] + outcome["introduced"]
        ):
            return None
    except (AttributeError, TypeError, ValueError):
        return None
    return outcome
