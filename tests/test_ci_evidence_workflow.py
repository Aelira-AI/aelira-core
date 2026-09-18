"""Keep the real CI invocation connected to the tested evidence boundary."""

import configparser
import json
from pathlib import Path
import shlex

import yaml

from scripts.verify_test_evidence import policy_for

ROOT = Path(__file__).resolve().parents[1]


def argument(command, name):
    tokens = shlex.split(command)
    for index, token in enumerate(tokens):
        if token.startswith(name + "="):
            return token.split("=", 1)[1]
        if token == name:
            return tokens[index + 1]
    raise AssertionError(f"Missing {name}")


def test_full_suite_coverage_floor_matches_policy_without_new_omissions():
    policy = json.loads((ROOT / "tests/ci_skip_policy.json").read_text())
    floor, required, allowed = policy_for(policy, "main")
    config = configparser.ConfigParser()
    config.read(ROOT / "pytest.ini")
    options = config["pytest"]["addopts"]
    assert float(argument(options, "--cov-fail-under")) == floor == 68
    assert argument(options, "--cov") == "src"
    assert "--cov-config" not in options
    assert required.isdisjoint(allowed)
    assert {
        "tests/test_canvas_journey.py::test_scan_remediate_approve_write_back[True]",
        "tests/test_canvas_journey.py::test_scan_remediate_approve_write_back[False]",
    } <= required
    _, worker_required, worker_allowed = policy_for(policy, "worker-postgres")
    assert worker_required
    assert not worker_allowed


def test_ci_retains_and_validates_each_profile_at_the_tested_revision():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["test"]
    steps = job["steps"]
    for profile, step_id in (
        ("main", "main-tests"),
        ("worker-postgres", "worker-tests"),
    ):
        suite = next(step for step in steps if step.get("id") == step_id)
        assert argument(suite["run"], "--test-evidence-profile") == profile
        report = argument(suite["run"], "--test-evidence")
        gate = next(
            step
            for step in steps
            if "scripts/verify_test_evidence.py" in step.get("run", "")
            and argument(step["run"], "--profile") == profile
        )
        assert steps.index(gate) > steps.index(suite)
        assert not suite.get("continue-on-error")
        assert not gate.get("continue-on-error")
        assert gate["if"] == f"always() && steps.{step_id}.outcome != 'skipped'"
        assert argument(gate["run"], "--report") == report
        assert argument(gate["run"], "--policy") == "tests/ci_skip_policy.json"
        assert argument(gate["run"], "--revision") == "${{ github.sha }}"
        if profile == "main":
            assert (
                argument(suite["run"], "--cov-report")
                == "json:test-results/coverage.json"
            )
            assert argument(gate["run"], "--coverage") == "test-results/coverage.json"
        else:
            assert "--no-cov" in shlex.split(suite["run"])
    upload = next(step for step in steps if "upload-artifact@" in step.get("uses", ""))
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "test-results/*.json"
    assert job["permissions"] == {"contents": "read", "artifact-metadata": "write"}
