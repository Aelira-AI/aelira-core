"""Run the shipped POSIX shell example against deterministic local commands."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "api_scan.sh"
SUBMITTED = {"scan_id": "example-scan"}
COMPLETED = {"status": "COMPLETED"}
DETAILS = {"success": True, "scan": {"scan_id": "example-scan"}}


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """These subprocess tests must never initialize or contact a database."""
    yield


@pytest.fixture
def run_example(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    curl = bin_dir / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "root = pathlib.Path(os.environ['EXAMPLE_TEST_ROOT'])\n"
        "log = root / 'requests.jsonl'\n"
        "index = len(log.read_text().splitlines()) if log.exists() else 0\n"
        "with log.open('a') as out: out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "scenario = json.loads((root / 'scenario.json').read_text())\n"
        "if index >= len(scenario):\n"
        "    if os.environ.get('EXAMPLE_REPEAT_PENDING'): item = {'body': {'status': 'PENDING'}}\n"
        "    else: sys.exit(77)\n"
        "else: item = scenario[index]\n"
        "time.sleep(item.get('delay', 0))\n"
        "body = item.get('body', {})\n"
        "print(body if isinstance(body, str) else json.dumps(body))\n"
        "sys.exit(item.get('exit', 0))\n"
    )
    curl.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "if os.environ.get('EXAMPLE_REAL_SLEEP'): time.sleep(float(sys.argv[1]))\n"
    )
    sleep.chmod(0o755)

    def run(responses, *, repeat_pending=False, **settings):
        (tmp_path / "scenario.json").write_text(json.dumps(responses))
        # Deliberately do not inherit API keys, application settings, or proxies.
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "AELIRA_URL": "http://example.invalid",
            "AELIRA_API_KEY": "test-key",
            "EXAMPLE_TEST_ROOT": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
            **settings,
        }
        if repeat_pending:
            env.update(EXAMPLE_REPEAT_PENDING="1", EXAMPLE_REAL_SLEEP="1")
        result = subprocess.run(
            ["/bin/sh", str(SCRIPT), str(tmp_path / "document with spaces.pdf")],
            env=env,
            capture_output=True,
            text=True,
            timeout=4,
        )
        log = tmp_path / "requests.jsonl"
        requests = (
            [json.loads(line) for line in log.read_text().splitlines()]
            if log.exists()
            else []
        )
        return result, requests

    return run


def response(body, exit_code=0):
    return {"body": body, "exit": exit_code}


@pytest.mark.parametrize(
    "states",
    [
        ["COMPLETED"],
        ["PENDING", "PROCESSING", "COMPLETED"],
        ["pending", "processing", "completed"],
    ],
)
def test_completion_fetches_results_once(run_example, states):
    result, requests = run_example(
        [
            response(SUBMITTED),
            *[response({"status": state}) for state in states],
            response(DETAILS),
        ]
    )
    assert result.returncode == 0, result.stderr
    assert '"success": true' in result.stdout
    assert len(requests) == len(states) + 2
    assert "http://example.invalid/education/scans/example-scan" in requests[-1]
    assert "file=@" in " ".join(requests[0])
    for request in requests:
        assert "--max-time" in request
        assert float(request[request.index("--max-time") + 1]) > 0
        assert "--connect-timeout" in request


@pytest.mark.parametrize("state", ["FAILED", "failed"])
def test_failure_is_nonzero_without_fetching_results(run_example, state):
    result, requests = run_example(
        [
            response(SUBMITTED),
            response({"status": state}),
            response(DETAILS),
        ]
    )
    assert result.returncode != 0
    assert "failed" in result.stderr.lower()
    assert len(requests) == 2


@pytest.mark.parametrize("phase", ["submit", "progress", "results"])
@pytest.mark.parametrize("exit_code", [22, 28])
def test_http_and_transport_errors_are_not_hidden_by_valid_json(
    run_example, phase, exit_code
):
    responses = [response(SUBMITTED), response(COMPLETED), response(DETAILS)]
    index = ["submit", "progress", "results"].index(phase)
    responses[index]["exit"] = exit_code
    result, requests = run_example(responses)
    assert result.returncode != 0
    assert len(requests) == index + 1


@pytest.mark.parametrize(
    "phase,body",
    [
        ("submit", "not JSON"),
        ("submit", {}),
        ("submit", {"scan_id": None}),
        ("submit", {"scan_id": ""}),
        ("submit", []),
        ("progress", "not JSON"),
        ("progress", {}),
        ("progress", {"status": None}),
        ("progress", {"status": "UNKNOWN"}),
        ("progress", []),
        ("results", "not JSON"),
        ("results", []),
    ],
)
def test_malformed_or_unknown_responses_stop_immediately(run_example, phase, body):
    responses = [response(SUBMITTED), response(COMPLETED), response(DETAILS)]
    index = ["submit", "progress", "results"].index(phase)
    responses[index] = response(body)
    result, requests = run_example(responses)
    assert result.returncode != 0
    assert len(requests) == index + 1


def test_pending_scan_hits_deadline(run_example):
    result, requests = run_example(
        [response(SUBMITTED)],
        repeat_pending=True,
        AELIRA_POLL_TIMEOUT="1",
    )
    assert result.returncode != 0
    assert "timed out" in result.stderr.lower()
    assert len(requests) >= 2
    for request in requests[1:]:
        assert float(request[request.index("--max-time") + 1]) <= 1
        assert any(url.endswith("/progress") for url in request)


@pytest.mark.parametrize("value", ["", "0", "-1", "abc", "1.5"])
@pytest.mark.parametrize("setting", ["AELIRA_POLL_TIMEOUT", "AELIRA_REQUEST_TIMEOUT"])
def test_invalid_timeout_configuration_fails_before_submission(
    run_example, setting, value
):
    result, requests = run_example([], **{setting: value})
    assert result.returncode != 0
    assert setting in result.stderr
    assert not requests


def test_request_time_counts_toward_poll_deadline(run_example):
    result, requests = run_example(
        [
            response(SUBMITTED),
            {"body": COMPLETED, "delay": 1.1},
            response(DETAILS),
        ],
        AELIRA_POLL_TIMEOUT="1",
    )
    assert result.returncode != 0
    assert "timed out" in result.stderr.lower()
    assert len(requests) == 2


def test_request_timeout_override_applies_to_every_call(run_example):
    result, requests = run_example(
        [response(SUBMITTED), response(COMPLETED), response(DETAILS)],
        AELIRA_REQUEST_TIMEOUT="7",
    )
    assert result.returncode == 0, result.stderr
    assert len(requests) == 3
    for request in requests:
        assert float(request[request.index("--max-time") + 1]) == 7
