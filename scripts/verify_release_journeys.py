"""Validate journey inventory references, without running or certifying journeys."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
LEVELS = {
    "unit",
    "component_integration",
    "api_integration",
    "browser_simulated",
    "manual",
}
PILLARS = {"document", "canvas", "web", "media"}


class InvalidMatrix(ValueError):
    """The inventory has drifted or attempts an unsupported evidence claim."""


def require(condition, message):
    if not condition:
        raise InvalidMatrix(message)


def _keys(value, expected, label):
    require(
        isinstance(value, dict) and set(value) == set(expected),
        f"{label}: invalid fields",
    )


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _strings(value, label, *, empty=False):
    require(
        isinstance(value, list) and (empty or bool(value)), f"{label}: expected list"
    )
    require(all(_text(item) for item in value), f"{label}: expected nonempty strings")
    require(len(value) == len(set(value)), f"{label}: duplicate entries")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def load_matrix(path):
    try:
        return json.loads(Path(path).read_text(), object_pairs_hook=_unique)
    except (OSError, ValueError) as exc:
        raise InvalidMatrix("Cannot read valid inventory JSON") from exc


def _source(root, value):
    require(_text(value), "source path missing")
    path = PurePosixPath(value)
    require(
        not path.is_absolute() and ".." not in path.parts and "\\" not in value,
        "source path must be repository relative",
    )
    resolved = (root / path).resolve()
    require(
        resolved.is_relative_to(root) and resolved.is_file(),
        "source missing or outside repository",
    )
    return hashlib.sha256(resolved.read_bytes()).hexdigest()


def _binding(workflow, binding):
    if binding is None:
        return None
    _keys(binding, {"job", "step", "run_contains"}, "CI binding")
    require(all(_text(v) for v in binding.values()), "CI binding must be nonempty")
    job = workflow.get("jobs", {}).get(binding["job"])
    require(isinstance(job, dict), "CI job missing")
    steps = [
        step for step in job.get("steps", []) if step.get("name") == binding["step"]
    ]
    require(len(steps) == 1, "CI step missing or ambiguous")
    step = steps[0]
    require(
        binding["run_contains"] in step.get("run", ""), "CI command reference missing"
    )
    require(
        not job.get("continue-on-error") and not step.get("continue-on-error"),
        "CI binding cannot ignore failures",
    )
    return dict(binding)


def build_report(root, matrix, revision):
    root = Path(root).resolve()
    require(
        isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{40}", revision),
        "revision must be a full lowercase commit SHA",
    )
    _keys(matrix, {"schema_version", "tracking_issue", "journeys"}, "inventory")
    require(
        type(matrix["schema_version"]) is int and matrix["schema_version"] == 1,
        "unsupported schema version",
    )
    require(matrix["tracking_issue"] == 375, "unexpected tracking issue")
    journeys = matrix["journeys"]
    require(
        isinstance(journeys, list) and len(journeys) == len(PILLARS),
        "four pillars required",
    )
    workflow_path = root / ".github/workflows/ci.yml"
    workflow_bytes = workflow_path.read_bytes()
    workflow = yaml.safe_load(workflow_bytes)
    ids, evidence_ids, result = set(), set(), []
    for journey in journeys:
        _keys(journey, {"id", "evidence", "missing"}, "journey")
        identifier = journey["id"]
        require(
            isinstance(identifier, str)
            and identifier in PILLARS
            and identifier not in ids,
            "unknown or duplicate pillar",
        )
        ids.add(identifier)
        _strings(journey["missing"], "missing journey evidence")
        require(
            isinstance(journey["evidence"], list) and journey["evidence"],
            "evidence inventory empty",
        )
        entries = []
        for evidence in journey["evidence"]:
            _keys(
                evidence,
                {"id", "level", "sources", "mocked", "ci", "limitations"},
                "evidence",
            )
            name = evidence["id"]
            require(
                _text(name) and name not in evidence_ids,
                "invalid or duplicate evidence ID",
            )
            evidence_ids.add(name)
            require(
                isinstance(evidence["level"], str) and evidence["level"] in LEVELS,
                "unsupported evidence level; inventory cannot certify full journeys",
            )
            require(_text(evidence["limitations"]), "evidence limitations missing")
            _strings(evidence["sources"], "sources")
            _strings(evidence["mocked"], "mocked boundaries", empty=True)
            require(
                evidence["level"] != "browser_simulated" or evidence["mocked"],
                "simulated browser evidence must name its mocks",
            )
            require(
                evidence["level"] != "manual" or evidence["ci"] is None,
                "manual evidence cannot inherit a CI binding",
            )
            binding = _binding(workflow, evidence["ci"])
            entries.append(
                {
                    **evidence,
                    "ci": binding,
                    "execution": "not_run_by_inventory",
                    "source_sha256": {
                        path: _source(root, path) for path in evidence["sources"]
                    },
                }
            )
        result.append(
            {"id": identifier, "evidence": entries, "missing": journey["missing"]}
        )
    return {
        "schema_version": 1,
        "revision": revision,
        "inventory_valid": True,
        "journey_verification": "not_evaluated",
        "tracking_issue": 375,
        "manifest_sha256": hashlib.sha256(
            json.dumps(matrix, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "workflow_sha256": hashlib.sha256(workflow_bytes).hexdigest(),
        "journeys": result,
        "claim_boundary": "Reference validation only. No tests, browsers, providers or user journeys were executed by this tool. CI bindings describe configuration, not run results or skip status.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "tests/fixtures/release_journeys.json"
    )
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build_report(ROOT, load_matrix(args.manifest), args.revision)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as handle:
            handle.write(json.dumps(report, indent=2) + "\n")
    except (InvalidMatrix, OSError, yaml.YAMLError) as exc:
        parser.exit(1, f"Journey inventory rejected: {exc}\n")
    print(
        json.dumps(
            {
                "inventory_valid": True,
                "journey_verification": "not_evaluated",
                "pillars": len(report["journeys"]),
            }
        )
    )


if __name__ == "__main__":
    main()
