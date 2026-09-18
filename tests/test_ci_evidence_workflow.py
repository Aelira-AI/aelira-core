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
        ("queue-races-postgres", "race-tests"),
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


def test_queue_races_use_a_separate_disposable_required_database():
    from conftest import require_disposable_postgres_url

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    suite = next(step for step in steps if step.get("id") == "race-tests")
    env = suite["env"]
    assert env["REQUIRE_QUEUE_POSTGRES_TESTS"] == "1"
    assert env["ALLOW_DESTRUCTIVE_MIGRATION_TESTS"] == "1"
    assert (
        env["DATABASE_URL"]
        == env["TEST_DATABASE_URL"]
        == env["TEST_MIGRATION_DATABASE_URL"]
    )
    url = require_disposable_postgres_url(
        env["TEST_DATABASE_URL"], destructive=True, environment=env
    )
    assert url.endswith("/queue_races_test")
    for step_id in ("main-tests", "worker-tests"):
        other = next(step for step in steps if step.get("id") == step_id)
        assert other["env"]["DATABASE_URL"] != url
    create = next(
        step for step in steps if step.get("run", "").endswith(" queue_races_test")
    )
    assert steps.index(create) < steps.index(suite)
    policy = json.loads((ROOT / "tests/ci_skip_policy.json").read_text())
    _, required, allowed = policy_for(policy, "queue-races-postgres")
    assert required == {
        "tests/test_task17b_postgres.py::test_concurrent_enqueue_unique_race_returns_the_single_winner",
        "tests/test_session_refresh_rotation.py::test_concurrent_refreshes_serialize_and_return_identical_pair",
    }
    assert not allowed
    assert "tests/test_task17b_postgres.py" in shlex.split(suite["run"])
    assert (
        "tests/test_session_refresh_rotation.py::test_concurrent_refreshes_serialize_and_return_identical_pair"
        in shlex.split(suite["run"])
    )
    _, worker_required, worker_allowed = policy_for(policy, "worker-postgres")
    assert len(worker_required) == 41
    assert not worker_allowed
