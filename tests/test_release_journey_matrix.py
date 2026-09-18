"""Inventory validity must never be mistaken for executed user journeys."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.verify_release_journeys import build_report, InvalidMatrix, load_matrix

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests/fixtures/release_journeys.json"
REVISION = "a" * 40


def matrix():
    return load_matrix(MANIFEST)


def test_real_inventory_reports_boundaries_without_execution():
    report = build_report(ROOT, matrix(), REVISION)
    assert report["inventory_valid"] is True
    assert report["journey_verification"] == "not_evaluated"
    assert report["revision"] == REVISION
    assert {row["id"] for row in report["journeys"]} == {
        "document",
        "canvas",
        "web",
        "media",
    }
    for journey in report["journeys"]:
        assert journey["missing"]
        for evidence in journey["evidence"]:
            assert evidence["execution"] == "not_run_by_inventory"
            assert evidence["source_sha256"]
            assert all(len(v) == 64 for v in evidence["source_sha256"].values())
            for path, digest in evidence["source_sha256"].items():
                assert digest == hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    assert report["workflow_sha256"]
    assert report["manifest_sha256"]


@pytest.mark.parametrize(
    "damage",
    [
        "missing-pillar",
        "duplicate-pillar",
        "unknown-pillar",
        "missing-gap",
        "duplicate-evidence",
        "unsupported-level",
        "empty-limitations",
        "missing-source",
        "absolute-source",
        "traversal-source",
        "missing-job",
        "missing-step",
        "wrong-command",
        "manual-ci",
        "simulated-without-mocks",
        "promoted-status",
        "bad-revision",
    ],
)
def test_invalid_inventory_fails_closed(damage):
    value = deepcopy(matrix())
    evidence = value["journeys"][0]["evidence"][0]
    revision = REVISION
    if damage == "missing-pillar":
        value["journeys"].pop()
    elif damage == "duplicate-pillar":
        value["journeys"].append(deepcopy(value["journeys"][0]))
    elif damage == "unknown-pillar":
        value["journeys"][0]["id"] = "other"
    elif damage == "missing-gap":
        value["journeys"][0]["missing"] = []
    elif damage == "duplicate-evidence":
        value["journeys"][0]["evidence"].append(deepcopy(evidence))
    elif damage == "unsupported-level":
        evidence["level"] = "full_user_journey"
    elif damage == "empty-limitations":
        evidence["limitations"] = ""
    elif damage == "missing-source":
        evidence["sources"] = ["tests/does-not-exist.py"]
    elif damage == "absolute-source":
        evidence["sources"] = [str(ROOT / "scripts/verify_document_stack.ts")]
    elif damage == "traversal-source":
        evidence["sources"] = ["tests/../scripts/verify_document_stack.ts"]
    elif damage == "missing-job":
        evidence["ci"]["job"] = "not-a-job"
    elif damage == "missing-step":
        evidence["ci"]["step"] = "not-a-step"
    elif damage == "wrong-command":
        evidence["ci"]["run_contains"] = "not-the-command"
    elif damage == "manual-ci":
        evidence["level"] = "manual"
    elif damage == "simulated-without-mocks":
        evidence["level"] = "browser_simulated"
    elif damage == "promoted-status":
        evidence["status"] = "passed"
    else:
        revision = "main"
    with pytest.raises(InvalidMatrix):
        build_report(ROOT, value, revision)


def test_duplicate_json_keys_are_rejected(tmp_path):
    path = tmp_path / "matrix.json"
    path.write_text('{"schema_version":1,"schema_version":2}')
    with pytest.raises(InvalidMatrix):
        load_matrix(path)


def test_ci_requires_and_retains_inventory():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    step = next(
        s for s in steps if s.get("name") == "Validate release journey inventory"
    )
    assert not step.get("continue-on-error")
    assert "scripts/verify_release_journeys.py" in step["run"]
    assert '--revision "${{ github.sha }}"' in step["run"]
    assert "--output test-results/journey-matrix.json" in step["run"]
    upload = next(
        s for s in steps if s.get("name") == "Retain revision-bound test evidence"
    )
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "test-results/*.json"


def test_cli_emits_inventory_and_refuses_report_overwrite(tmp_path):
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        str(ROOT / "scripts/verify_release_journeys.py"),
        "--revision",
        REVISION,
        "--output",
        str(output),
    ]
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report["journey_verification"] == "not_evaluated"
    assert "not_evaluated" in result.stdout
    original = output.read_bytes()
    result = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=20
    )
    assert result.returncode != 0
    assert output.read_bytes() == original


@pytest.mark.parametrize("damage", ["ignored-job", "ignored-step", "ambiguous-step"])
def test_ci_binding_rejects_ignored_or_ambiguous_steps(monkeypatch, damage):
    from scripts import verify_release_journeys as validator

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["document-stack"]
    step = next(
        s
        for s in job["steps"]
        if s.get("name") == "Verify real API, durable worker and downloaded files"
    )
    if damage == "ignored-job":
        job["continue-on-error"] = True
    elif damage == "ignored-step":
        step["continue-on-error"] = True
    else:
        job["steps"].append(deepcopy(step))
    monkeypatch.setattr(validator.yaml, "safe_load", lambda _: workflow)
    with pytest.raises(InvalidMatrix):
        build_report(ROOT, matrix(), REVISION)


def test_source_symlink_cannot_leave_repository(tmp_path):
    from scripts.verify_release_journeys import _source

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "external.txt"
    outside.write_text("External content")
    (root / "source.txt").symlink_to(outside)
    with pytest.raises(InvalidMatrix):
        _source(root, "source.txt")
