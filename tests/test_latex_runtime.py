"""Functional readiness cannot be inferred from executable presence."""

import os
from pathlib import Path
import sys

import pymupdf
import pytest
import yaml

from src.education import latex_runtime as runtime


@pytest.mark.parametrize(
    "log,reason",
    [
        ("I can't find the format file `lualatex.fmt'!", "format_unavailable"),
        ("module 'luaotfload-main' not found", "font_unavailable"),
        ("Package fontspec Error: The font cannot be found", "font_unavailable"),
        (
            "LaTeX Font Warning: Font shape undefined, defaults substituted",
            "font_unavailable",
        ),
        ("Missing character: There is no x in font nullfont!", "font_unavailable"),
        ("Package babel Error: Unknown option 'ngerman'", "language_unavailable"),
        ("! LaTeX Error: File `fontspec.sty' not found.", "package_unavailable"),
        ("luaotfload | db : Font names database not found, generating new one", None),
        ("Output written on control.pdf", None),
    ],
)
def test_known_prerequisites_and_legitimate_cold_cache_creation(log, reason):
    assert runtime.failure_reason(log) == reason


def test_cache_environment_is_owned_per_attempt_without_mutating_process(tmp_path):
    before = dict(os.environ)
    one = runtime.tex_environment(tmp_path / "one")
    two = runtime.tex_environment(tmp_path / "two")
    for name in ("TEXMFVAR", "TEXMFCONFIG", "TEXMFCACHE", "XDG_CACHE_HOME"):
        assert Path(one[name]).is_dir()
        assert Path(one[name]).is_relative_to(tmp_path / "one")
        assert one[name] != two[name]
        assert Path(one[name]).stat().st_mode & 0o777 == 0o700
    assert dict(os.environ) == before


def test_real_command_success_failure_and_timeout(tmp_path):
    code, log, error = runtime.run_tool(
        [sys.executable, "-c", "print('control')"], tmp_path
    )
    assert (code, log.strip(), error) == (0, "control", None)
    assert runtime.run_tool(["/does-not-exist"], tmp_path)[2] == "tool_unavailable"
    assert (
        runtime.run_tool([sys.executable, "-c", "print('x' * 131072)"], tmp_path)[2]
        == "diagnostics_truncated"
    )
    assert (
        runtime.run_tool(
            [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, 0.05
        )[2]
        == "timeout"
    )


def test_inspect_actual_pdf_and_html_contents(tmp_path):
    pdf = tmp_path / "control.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), "Aelira runtime control x2+1")
        document.save(pdf)
    assert runtime.inspect_output(pdf, "pdf", "Aelira runtime control")
    assert not runtime.inspect_output(pdf, "pdf", "Absent sentence")
    pdf.write_bytes(b"%PDF fake")
    assert not runtime.inspect_output(pdf, "pdf", "Aelira runtime control")
    html = tmp_path / "control.html"
    html.write_text(
        "<p>Aelira runtime control</p><math><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo><mn>1</mn></math>"
    )
    assert runtime.inspect_output(html, "html", "Aelira runtime control")
    html.write_text("<p>Aelira runtime control x2+1</p><math></math>")
    assert not runtime.inspect_output(html, "html", "Aelira runtime control")


@pytest.mark.parametrize(
    "log,code,reason",
    [
        ("", 0, "output_invalid"),
        ("! unexpected converter failure", 1, "conversion_failed"),
        ("I can't find the format file", 1, "format_unavailable"),
        ("LaTeX Font Warning: defaults substituted", 0, "font_unavailable"),
        ("Package babel Error: Unknown option 'ngerman'", 1, "language_unavailable"),
    ],
)
def test_installed_binaries_and_zero_exit_cannot_fake_ready(
    tmp_path, monkeypatch, log, code, reason
):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: "/installed/tool")
    monkeypatch.setattr(runtime, "run_tool", lambda *a, **kw: (code, log, None))
    report = runtime.probe_runtime(tmp_path)
    assert report["status"] == "degraded"
    assert {check["reason"] for check in report["checks"]} == {reason}
    assert all(check["action"] for check in report["checks"])


def test_absent_tools_and_unwritable_scratch_are_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        runtime, "run_tool", lambda *a, **kw: (None, "", "tool_unavailable")
    )
    assert runtime.probe_runtime(tmp_path)["status"] == "unavailable"
    report = runtime.probe_runtime(tmp_path / "missing" / "scratch")
    assert report["status"] == "unavailable"
    assert report["error"] == "scratch_unwritable"


def test_both_production_architectures_require_the_non_root_profile():
    root = Path(__file__).parents[1]
    job = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"][
        "docker"
    ]
    assert {row["platform"] for row in job["strategy"]["matrix"]["include"]} == {
        "linux/amd64",
        "linux/arm64",
    }
    step = next(
        row
        for row in job["steps"]
        if row.get("name")
        == "Verify non-root LaTeX runtime with cold scratch and read-only root"
    )
    assert "if" not in step and not step.get("continue-on-error")
    assert "--network none --read-only --tmpfs /tmp" in step["run"]
    assert "--user" not in step["run"]  # exercise the actual image USER
    assert 'r["uid"] == 1000' in step["run"]
    assert "AELIRA_RUNTIME_IMAGE_ID" in step["run"]
    for name in ("Dockerfile", "Dockerfile.dev"):
        content = (root / name).read_text()
        assert "texlive-luatex" in content and "texlive-lang-german" in content
