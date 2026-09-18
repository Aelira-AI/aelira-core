"""Real isolated pytest runs prove the evidence gate's failure sensitivity."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
REQUIRED = "test_sample.py::test_required"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    return tmp_path


def run_tests(
    project, source, *options, extra_files=None, evidence=True, coverage=False
):
    (project / "test_sample.py").write_text(source)
    for name, contents in (extra_files or {}).items():
        (project / name).write_text(contents)
    env = os.environ.copy()
    env.pop("GITHUB_SHA", None)
    env.pop("PYTEST_ADDOPTS", None)
    env.update(
        PYTHONPATH=str(ROOT),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    command = [sys.executable, "-m", "pytest", "-p", "scripts.pytest_ci_evidence", "-q"]
    if evidence:
        command += [
            "--test-evidence=report.json",
            f"--test-evidence-revision={REVISION}",
        ]
    if coverage:
        command += ["-p", "pytest_cov.plugin"]
    result = subprocess.run(
        command + list(options),
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    report_path = project / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return result, report


def policy(allowed=()):
    return {
        "schema_version": 1,
        "minimum_coverage": 68,
        "profiles": {
            "main": {
                "required_tests": [REQUIRED],
                "allowed_skips": (
                    [
                        {
                            "id": "optional",
                            "classification": "environment-gated",
                            "owner": "platform",
                            "reason": "Requires optional runtime",
                            "issue": "https://example.test/issues/80",
                            "nodeids": list(allowed),
                        }
                    ]
                    if allowed
                    else []
                ),
            },
            "worker-postgres": {"required_tests": [REQUIRED], "allowed_skips": []},
        },
    }


def check(project, report=None, selected_policy=None, coverage="valid", *options):
    if report is not None:
        (project / "report.json").write_text(json.dumps(report))
    (project / "policy.json").write_text(json.dumps(selected_policy or policy()))
    command = [
        sys.executable,
        str(ROOT / "scripts/verify_test_evidence.py"),
        "--report",
        "report.json",
        "--policy",
        "policy.json",
        "--profile",
        "main",
        "--revision",
        REVISION,
        "--output",
        "summary.json",
    ]
    if coverage is not None:
        coverage_path = project / "coverage.json"
        if coverage == "valid":
            coverage_path.write_text(
                json.dumps(
                    {
                        "totals": {
                            "covered_lines": 68,
                            "num_statements": 100,
                            "percent_covered": 68.0,
                        }
                    }
                )
            )
        elif isinstance(coverage, dict):
            coverage_path.write_text(json.dumps(coverage))
        elif coverage == "malformed":
            coverage_path.write_text("{broken")
        command += ["--coverage", "coverage.json"]
    result = subprocess.run(
        command + list(options), cwd=project, capture_output=True, text=True, timeout=30
    )
    summary = json.loads((project / "summary.json").read_text())
    assert result.returncode == (0 if summary["ok"] else 1), result.stderr
    return summary


PASS = "def test_required():\n    assert True\n"


def test_real_positive_skip_accounting_and_progress(project):
    source = PASS + """
import pytest
@pytest.mark.skip(reason='credential-like-string-must-not-be-exported')
def test_optional(): pass
def test_recovered(): pass
"""
    result, report = run_tests(project, source)
    assert result.returncode == 0, result.stdout
    assert "credential-like" not in json.dumps(report)
    allowed = ["test_sample.py::test_optional", "test_sample.py::test_recovered"]
    summary = check(project, selected_policy=policy(allowed))
    assert summary["ok"]
    assert summary["counts"]["collected"] == 3
    assert summary["counts"]["skipped"] == 1
    assert summary["skip_classifications"]["environment-gated"] == 1
    assert summary["baseline_now_passing"] == [allowed[1]]
    assert summary["coverage"] == {
        "covered_lines": 68,
        "total_lines": 100,
        "percent_covered": 68.0,
    }
    assert summary["revision"] == REVISION
    assert (
        summary["report_sha256"]
        == hashlib.sha256((project / "report.json").read_bytes()).hexdigest()
    )


def test_opt_in_and_deterministic_report(project):
    result, report = run_tests(project, PASS, evidence=False)
    assert result.returncode == 0 and report is None
    result, report = run_tests(project, PASS)
    first = (project / "report.json").read_bytes()
    result, report = run_tests(project, PASS)
    assert result.returncode == 0
    assert (project / "report.json").read_bytes() == first


@pytest.mark.parametrize(
    "source,options,error",
    [
        ("def test_other(): pass\n", (), "required test did not pass"),
        (
            "import pytest\n@pytest.mark.skip(reason='optional')\ndef test_required(): pass\n",
            (),
            "required test did not pass",
        ),
        (
            PASS + "import pytest\ndef test_new_skip(): pytest.skip('optional')\n",
            (),
            "unexpected skip",
        ),
        (
            PASS + "def test_other(): pass\n",
            ("-k", "other"),
            "required test did not pass",
        ),
        (
            "import pytest\n@pytest.mark.xfail(reason='defect')\ndef test_required(): assert False\n",
            (),
            "required test did not pass",
        ),
        (
            PASS
            + "import pytest\n@pytest.mark.xfail(reason='defect')\ndef test_other(): assert False\n",
            (),
            "unexpected xfail",
        ),
        (
            PASS
            + "import pytest\n@pytest.mark.xfail(reason='fixed')\ndef test_other(): pass\n",
            (),
            "XPASS",
        ),
        (
            PASS
            + "import pytest\n@pytest.mark.xfail(reason='fixed', strict=True)\ndef test_other(): pass\n",
            (),
            "XPASS",
        ),
        (
            PASS
            + "import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError('secret')\ndef test_other(broken): pass\n",
            (),
            "fixture errors",
        ),
    ],
)
def test_real_regressions_fail(project, source, options, error):
    _, report = run_tests(project, source, *options)
    summary = check(project)
    assert not summary["ok"]
    assert any(error in value for value in summary["errors"]), summary
    if options:
        assert report["deselected"] == [REQUIRED]
        assert summary["counts"]["deselected"] == 1


@pytest.mark.parametrize(
    "module,expected",
    [
        (
            "import pytest\npytest.skip('secret reason', allow_module_level=True)\n",
            "skipped",
        ),
        ("raise RuntimeError('private traceback')\n", "failed"),
    ],
)
def test_collection_skip_and_error_fail(project, module, expected):
    result, report = run_tests(
        project, PASS, extra_files={"test_collection.py": module}
    )
    assert {"nodeid": "test_collection.py", "outcome": expected} in report["collection"]
    assert "secret reason" not in json.dumps(report)
    assert "private traceback" not in json.dumps(report)
    summary = check(project)
    assert not summary["ok"]
    if expected == "skipped":
        assert "unexpected skip: test_collection.py" in summary["errors"]
        allowed = check(project, selected_policy=policy(["test_collection.py"]))
        assert allowed["ok"]
    else:
        assert result.returncode != 0


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda r: r.update(schema_version=2), "schema_version"),
        (lambda r: r.update(revision="b" * 40), "revision mismatch"),
        (lambda r: r.update(profile="worker-postgres"), "profile mismatch"),
        (lambda r: r["session"].update(finished=False), "incomplete session"),
        (lambda r: r.update(tests=[]), "missing outcomes"),
        (lambda r: r["tests"][0]["outcomes"].pop(), "incomplete test phases"),
        (
            lambda r: r["tests"][0]["outcomes"][0].update(outcome=[]),
            "invalid phase outcome",
        ),
        (lambda r: r["collected"].append(REQUIRED), "duplicate nodeid"),
    ],
)
def test_malformed_or_unbound_evidence_fails(project, mutation, expected):
    _, report = run_tests(project, PASS)
    mutation(report)
    summary = check(project, report=report)
    assert not summary["ok"]
    assert expected in " ".join(summary["errors"])


@pytest.mark.parametrize(
    "coverage,expected",
    [
        (None, "required for main"),
        ("missing", "missing or malformed"),
        ("malformed", "missing or malformed"),
        ({}, "missing totals"),
        (
            {
                "totals": {
                    "covered_lines": 67,
                    "num_statements": 100,
                    "percent_covered": 67,
                }
            },
            "below policy",
        ),
        (
            {
                "totals": {
                    "covered_lines": 67,
                    "num_statements": 100,
                    "percent_covered": 68,
                }
            },
            "inconsistent",
        ),
        (
            {
                "totals": {
                    "covered_lines": True,
                    "num_statements": 100,
                    "percent_covered": 1,
                }
            },
            "invalid line counts",
        ),
        (
            {
                "totals": {
                    "covered_lines": 0,
                    "num_statements": 0,
                    "percent_covered": 100,
                }
            },
            "invalid line counts",
        ),
    ],
)
def test_coverage_evidence_failures(project, coverage, expected):
    run_tests(project, PASS)
    summary = check(project, coverage=coverage)
    assert not summary["ok"]
    assert expected in " ".join(summary["errors"])


def test_real_pytest_cov_below_floor_fails_both_gates(project):
    result, report = run_tests(
        project,
        "import subject\n" + PASS,
        "--cov=subject",
        "--cov-report=json:coverage.json",
        "--cov-fail-under=68",
        extra_files={
            "subject.py": "def unused():\n    a = 1\n    b = 2\n    return a + b\n"
        },
        coverage=True,
    )
    assert result.returncode == 1, result.stdout
    assert "FAIL Required test coverage" in result.stdout
    assert report["session"]["exitstatus"] == 1
    summary = check(project, coverage="existing")
    assert not summary["ok"]
    assert "coverage: below policy minimum" in summary["errors"]
    assert summary["coverage"]["percent_covered"] == 25


def test_worker_profile_can_omit_coverage(project):
    run_tests(project, PASS, "--test-evidence-profile=worker-postgres")
    summary = check(project, None, None, None, "--profile", "worker-postgres")
    assert summary["ok"] and summary["coverage"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p["profiles"]["main"].update(required_tests=[]),
        lambda p: p["profiles"]["main"]["allowed_skips"][0].update(owner=""),
        lambda p: p["profiles"]["main"]["allowed_skips"][0].update(
            classification="unclassified"
        ),
        lambda p: p["profiles"]["main"]["allowed_skips"][0].update(
            nodeids=["tests/*.py"]
        ),
        lambda p: p["profiles"]["main"]["allowed_skips"][0].update(nodeids=[REQUIRED]),
        lambda p: p["profiles"]["main"]["allowed_skips"].append(
            {
                **copy.deepcopy(p["profiles"]["main"]["allowed_skips"][0]),
                "id": "duplicate-node",
            }
        ),
    ],
)
def test_malformed_policy_fails(project, mutation):
    run_tests(project, PASS)
    selected = policy(["test_sample.py::test_optional"])
    mutation(selected)
    summary = check(project, selected_policy=selected)
    assert not summary["ok"] and summary["errors"][0].startswith("policy")


def test_missing_and_malformed_report_fail(project):
    summary = check(project)
    assert not summary["ok"] and "report:" in summary["errors"][0]
    (project / "report.json").write_text('{"schema_version":1,"schema_version":1}')
    summary = check(project)
    assert not summary["ok"] and "malformed JSON" in summary["errors"][0]
