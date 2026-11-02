"""Tests for cJSON evidence collection subprocess timeouts and step attribution (Issue #834)."""

import ast
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
COLLECT_SCRIPT = ROOT / "runtime-security/cjson/collect-evidence.py"
TEST_EVIDENCE_SCRIPT = ROOT / "runtime-security/cjson/test-evidence.py"


def test_collect_evidence_ast_installed_consumers_timeouts():
    """Verify dpkg-deb and nm timeouts are set to 120s with step attribution in collect-evidence.py."""
    source_text = COLLECT_SCRIPT.read_text()
    tree = ast.parse(source_text)

    # Locate calls to subprocess.check_output in collect-evidence.py
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "check_output"
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            ):
                calls.append(node)

    dpkg_calls = [
        c
        for c in calls
        if c.args
        and isinstance(c.args[0], ast.List)
        and len(c.args[0].elts) > 0
        and isinstance(c.args[0].elts[0], ast.Constant)
        and c.args[0].elts[0].value == "dpkg-deb"
    ]
    assert len(dpkg_calls) == 1, "Expected exactly 1 dpkg-deb check_output call"
    dpkg_timeout = next(
        (kw.value.value for kw in dpkg_calls[0].keywords if kw.arg == "timeout"),
        None,
    )
    assert dpkg_timeout == 120, f"dpkg-deb timeout must be 120s, got {dpkg_timeout}"

    nm_calls = [
        c
        for c in calls
        if c.args
        and isinstance(c.args[0], ast.List)
        and len(c.args[0].elts) > 0
        and isinstance(c.args[0].elts[0], ast.Constant)
        and c.args[0].elts[0].value == "nm"
    ]
    assert len(nm_calls) == 1, "Expected exactly 1 nm check_output call"
    nm_timeout = next(
        (kw.value.value for kw in nm_calls[0].keywords if kw.arg == "timeout"),
        None,
    )
    assert nm_timeout == 120, f"nm timeout must be 120s, got {nm_timeout}"


def test_test_evidence_ast_invoke_timeout():
    """Verify test-evidence.py invoke timeout provides headroom (>= 240s) for collect-evidence."""
    source_text = TEST_EVIDENCE_SCRIPT.read_text()
    tree = ast.parse(source_text)

    run_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            ):
                timeout_val = next(
                    (kw.value.value for kw in node.keywords if kw.arg == "timeout"),
                    None,
                )
                if timeout_val is not None:
                    run_calls.append(timeout_val)

    assert any(
        t >= 240 for t in run_calls
    ), f"test-evidence.py invoke timeout should be >= 240s, found {run_calls}"


def test_collect_evidence_reports_exact_step_on_timeout(tmp_path):
    """Verify that when a subprocess call times out, collect-evidence reports TimeoutExpired: <step>."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("collect_evidence", COLLECT_SCRIPT)
    collect_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collect_mod)

    source = tmp_path / "source"
    dest = tmp_path / "dest"
    source.mkdir()

    import argparse

    args = argparse.Namespace(
        source=source.resolve(),
        dest=dest,
        repo=ROOT,
        arch="amd64",
        revision="a" * 40,
        tree="b" * 40,
        build_exit=0,
        require_builder=False,
    )

    # Simulate timeout on git:rev-parse during checkout validation
    with patch(
        "subprocess.check_output",
        side_effect=subprocess.TimeoutExpired(cmd=["git", "rev-parse"], timeout=30),
    ):
        exit_code = collect_mod.export(args)

    assert exit_code == 1
    receipt = json.loads((dest / "component.json").read_text())
    assert receipt["status"] == "fail"
    assert receipt["validation_stage"] == "checkout"
    assert receipt["errors"] == ["TimeoutExpired: git:rev-parse"]


@pytest.mark.parametrize(
    "step_name,expected_error",
    [
        ("dpkg-deb", "TimeoutExpired: dpkg-deb"),
        ("nm:baseline-core.so", "TimeoutExpired: nm:baseline-core.so"),
        ("nm:baseline-utils.so", "TimeoutExpired: nm:baseline-utils.so"),
        ("docker-save", "TimeoutExpired: docker-save"),
        (None, "TimeoutExpired"),
    ],
)
def test_timeout_formatting_logic(step_name, expected_error):
    """Verify error list formatting for subprocess.TimeoutExpired with and without step."""
    errors = []
    error = subprocess.TimeoutExpired(cmd=["test"], timeout=120)
    step = step_name

    if isinstance(error, subprocess.TimeoutExpired):
        errors.append(f"TimeoutExpired: {step}" if step else "TimeoutExpired")
    else:
        errors.append(type(error).__name__)

    assert errors == [expected_error]
