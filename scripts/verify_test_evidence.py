"""Validate pytest evidence offline against exact-node skip and coverage policy."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

CLASSIFICATIONS = {
    "environment-gated",
    "intentionally-unsupported",
    "obsolete",
    "defect",
}


class InvalidEvidence(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidEvidence(message)


def read_json(path, label):
    try:
        data = Path(path).read_bytes()
        value = json.loads(data, object_pairs_hook=unique_keys)
    except (OSError, ValueError, UnicodeError):
        raise InvalidEvidence(f"{label}: missing or malformed JSON") from None
    require(isinstance(value, dict), f"{label}: expected JSON object")
    return value, hashlib.sha256(data).hexdigest()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidEvidence("duplicate JSON key")
        result[key] = value
    return result


def exact_node(value):
    # Parameter IDs can legitimately contain glob characters. They remain literal:
    # policy matching below uses set membership, never glob/regex matching.
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        return False
    base = value.split("[", 1)[0]
    return base.split("::", 1)[0].endswith(".py") and not any(c in base for c in "*?]")


def nodes(value, label):
    require(
        isinstance(value, list) and all(exact_node(n) for n in value),
        f"{label}: expected exact nodeid list",
    )
    require(len(value) == len(set(value)), f"{label}: duplicate nodeid")
    return set(value)


def policy_for(policy, profile):
    require(
        type(policy.get("schema_version")) is int and policy["schema_version"] == 1,
        "policy: unsupported schema_version",
    )
    floor = policy.get("minimum_coverage")
    require(
        type(floor) in (int, float) and math.isfinite(floor) and 0 <= floor <= 100,
        "policy: invalid minimum_coverage",
    )
    profiles = policy.get("profiles")
    require(isinstance(profiles, dict) and profiles, "policy: missing profiles")
    require(profile in profiles, "policy: requested profile missing")
    normalized = {}
    for name, config in profiles.items():
        require(
            isinstance(name, str) and name.strip() and isinstance(config, dict),
            "policy: invalid profile",
        )
        required = nodes(config.get("required_tests"), "policy required_tests")
        require(bool(required), "policy: required_tests must not be empty")
        groups = config.get("allowed_skips")
        require(isinstance(groups, list), "policy: invalid allowed_skips")
        allowed, ids = {}, set()
        for group in groups:
            require(isinstance(group, dict), "policy: invalid skip group")
            for field in ("id", "owner", "reason", "issue"):
                require(
                    isinstance(group.get(field), str) and group[field].strip(),
                    f"policy: skip group missing {field}",
                )
            require(group["id"] not in ids, "policy: duplicate skip group id")
            ids.add(group["id"])
            require(
                isinstance(group.get("classification"), str)
                and group["classification"] in CLASSIFICATIONS,
                "policy: invalid skip classification",
            )
            members = nodes(group.get("nodeids"), "policy skip nodeids")
            require(bool(members), "policy: empty skip group")
            require(
                not members.intersection(allowed),
                "policy: duplicate allowed skip nodeid",
            )
            require(
                not members.intersection(required),
                "policy: required test allowed to skip",
            )
            allowed.update((node, group["classification"]) for node in members)
        normalized[name] = (required, allowed)
    return floor, *normalized[profile]


def validate_report(report, profile, revision):
    require(
        type(report.get("schema_version")) is int and report["schema_version"] == 1,
        "report: unsupported schema_version",
    )
    require(
        isinstance(revision, str)
        and re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision),
        "revision: expected full commit hash",
    )
    require(report.get("revision") == revision, "report: revision mismatch")
    require(report.get("profile") == profile, "report: profile mismatch")
    session = report.get("session")
    require(
        isinstance(session, dict) and session.get("finished") is True,
        "report: incomplete session",
    )
    require(
        type(session.get("exitstatus")) is int and 0 <= session["exitstatus"] <= 5,
        "report: invalid exitstatus",
    )
    collected = nodes(report.get("collected"), "report collected")
    deselected = nodes(report.get("deselected"), "report deselected")
    require(deselected <= collected, "report: deselected node was not collected")
    tests, entries = {}, report.get("tests")
    require(isinstance(entries, list), "report: missing test outcomes")
    for entry in entries:
        require(
            isinstance(entry, dict) and exact_node(entry.get("nodeid")),
            "report: invalid test entry",
        )
        node = entry["nodeid"]
        require(node not in tests, "report: duplicate test entry")
        outcomes = entry.get("outcomes")
        require(isinstance(outcomes, list) and outcomes, "report: missing phases")
        phases = {}
        for outcome in outcomes:
            require(isinstance(outcome, dict), "report: invalid outcome")
            phase = outcome.get("phase")
            require(
                isinstance(phase, str)
                and phase in {"setup", "call", "teardown"}
                and phase not in phases,
                "report: invalid or duplicate phase",
            )
            require(
                isinstance(outcome.get("outcome"), str)
                and outcome["outcome"] in {"passed", "failed", "skipped"}
                and type(outcome.get("expected_failure")) is bool,
                "report: invalid phase outcome",
            )
            phases[phase] = outcome
        require(
            "setup" in phases and "teardown" in phases, "report: incomplete test phases"
        )
        require(
            ("call" in phases) == (phases["setup"]["outcome"] == "passed"),
            "report: call phase inconsistent with setup",
        )
        tests[node] = phases
    require(
        set(tests) == collected - deselected,
        "report: selected tests missing outcomes or unexpected outcomes",
    )
    collection = report.get("collection")
    require(isinstance(collection, list), "report: missing collection outcomes")
    seen = set()
    for entry in collection:
        require(
            isinstance(entry, dict)
            and isinstance(entry.get("nodeid"), str)
            and isinstance(entry.get("outcome"), str)
            and entry["outcome"] in {"skipped", "failed"},
            "report: invalid collection outcome",
        )
        require(entry["nodeid"] not in seen, "report: duplicate collection outcome")
        seen.add(entry["nodeid"])
    return collected, deselected, tests, collection


def coverage_totals(path):
    coverage, _ = read_json(path, "coverage")
    totals = coverage.get("totals")
    require(isinstance(totals, dict), "coverage: missing totals")
    covered, total, percent = (
        totals.get(k) for k in ("covered_lines", "num_statements", "percent_covered")
    )
    require(
        type(covered) is int
        and type(total) is int
        and total > 0
        and 0 <= covered <= total,
        "coverage: invalid line counts",
    )
    require(
        type(percent) in (int, float)
        and math.isfinite(percent)
        and abs(percent - covered * 100 / total) < 1e-7,
        "coverage: percent inconsistent with line counts",
    )
    return {
        "covered_lines": covered,
        "total_lines": total,
        "percent_covered": covered * 100 / total,
    }


def verify(report, policy, profile, revision, coverage_path):
    floor, required, allowed = policy_for(policy, profile)
    collected, deselected, tests, collection = validate_report(
        report, profile, revision
    )
    errors = []
    if report["session"]["exitstatus"] != 0:
        errors.append("pytest: nonzero exitstatus")
    skipped = {entry["nodeid"] for entry in collection if entry["outcome"] == "skipped"}
    collection_errors = sum(entry["outcome"] == "failed" for entry in collection)
    if collection_errors:
        errors.append("pytest: collection errors")
    passed, failed, xfailed, xpassed = set(), set(), set(), set()
    for node, phases in tests.items():
        outcomes = list(phases.values())
        if any(p["expected_failure"] and p["outcome"] == "skipped" for p in outcomes):
            xfailed.add(node)
        if any(
            p["expected_failure"] and p["outcome"] in {"passed", "failed"}
            for p in outcomes
        ):
            xpassed.add(node)
        if any(
            p["outcome"] == "skipped" and not p["expected_failure"] for p in outcomes
        ):
            skipped.add(node)
        if any(p["outcome"] == "failed" for p in outcomes):
            failed.add(node)
        if all(
            p["outcome"] == "passed" and not p["expected_failure"] for p in outcomes
        ):
            passed.add(node)
    for node in sorted(required - passed):
        errors.append(f"required test did not pass: {node}")
    for node in sorted(skipped - allowed.keys()):
        errors.append(f"unexpected skip: {node}")
    # An xfail can hide a regression just as a skip can. Keep it explicit in policy.
    for node in sorted(xfailed - allowed.keys()):
        errors.append(f"unexpected xfail: {node}")
    if failed:
        errors.append("pytest: failed tests or fixture errors")
    if xpassed:
        errors.append("pytest: unexpected passes (XPASS)")
    coverage = None
    if coverage_path:
        coverage = coverage_totals(coverage_path)
        if coverage["covered_lines"] * 100 < floor * coverage["total_lines"]:
            errors.append("coverage: below policy minimum")
    elif profile == "main":
        errors.append("coverage: required for main profile")
    classifications = {kind: 0 for kind in sorted(CLASSIFICATIONS)}
    for node in skipped | xfailed:
        if node in allowed:
            classifications[allowed[node]] += 1
    return {
        "errors": sorted(errors),
        "counts": {
            "collected": len(collected),
            "deselected": len(deselected),
            "passed": len(passed),
            "failed": len(failed),
            "skipped": len(skipped),
            "xfail": len(xfailed),
            "xpass": len(xpassed),
            "collection_errors": collection_errors,
            "required": len(required),
        },
        "skip_classifications": classifications,
        "baseline_now_passing": sorted(passed.intersection(allowed)),
        "coverage": coverage,
        "minimum_coverage": floor,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("report", "policy", "profile", "revision", "output"):
        parser.add_argument(f"--{option}", required=True)
    parser.add_argument("--coverage")
    args = parser.parse_args(argv)
    result = {
        "schema_version": 1,
        "revision": args.revision,
        "profile": args.profile,
        "report_sha256": None,
        "policy_sha256": None,
    }
    try:
        report, result["report_sha256"] = read_json(args.report, "report")
        policy, result["policy_sha256"] = read_json(args.policy, "policy")
        result.update(
            verify(report, policy, args.profile, args.revision, args.coverage)
        )
    except InvalidEvidence as error:
        result["errors"] = [str(error)]
    result["ok"] = not result["errors"]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("Test evidence: " + ("PASS" if result["ok"] else "FAIL"))
    for error in result["errors"]:
        print(error)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
