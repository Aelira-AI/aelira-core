"""LaTeX tools run confined to their conversion and source directories.

The confinement tests run real processes under Landlock. The converter tests
run the real LaTeX tools on legitimate documents and on documents that name
files outside their directories. Where the tool itself allows the read, a
positive control uses the same syntax on a file inside the readable directory
and must succeed, so a refused read is the boundary and not a broken probe.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from src.education import latex_project_conversion as project_conversion
from src.education import latex_runtime as runtime
from src.education import latex_sandbox as sandbox
from src.education.remediation.latex_converter import LaTeXConverter

MARKER = "OUTSIDEMARKER4242"
SIBLING = "SIBLINGMARKER4242"


def _landlock_available():
    if sys.platform != "linux":
        return False
    probe = subprocess.run(
        sandbox.confined(["true"], Path("/tmp")), capture_output=True, timeout=30
    )
    return probe.returncode == 0


LANDLOCK = _landlock_available()
requires_landlock = pytest.mark.skipif(
    not LANDLOCK, reason="Landlock confinement requires a Linux kernel with Landlock"
)


def requires_tool(tool):
    return pytest.mark.skipif(
        shutil.which(tool) is None or not LANDLOCK,
        reason=f"{tool} and Landlock are required",
    )


@pytest.fixture
def dirs(tmp_path):
    work, source, outside = (tmp_path / name for name in ("work", "src", "outside"))
    for path in (work, source, outside):
        path.mkdir()
    return work, source, outside


def _content(path):
    if path.suffix == ".pdf":
        try:
            with pymupdf.open(path) as document:
                return "".join(page.get_text() for page in document)
        except Exception:
            return ""
    return path.read_bytes().decode("utf-8", errors="replace")


def _found(directory, marker):
    return sorted(
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file() and marker in _content(path)
    )


def _document(body):
    return "\\documentclass{article}\n\\begin{document}\n%s\n\\end{document}\n" % body


def test_tool_environment_carries_no_application_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("AELIRA_SECRET_FOR_TEST", MARKER)
    environment = sandbox.tool_environment(tmp_path)
    assert MARKER not in "".join(environment.values())
    assert set(environment) == {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "openout_any",
        "TEXMFVAR",
        "TEXMFCONFIG",
        "TEXMFCACHE",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "MAGICK_TEMPORARY_PATH",
    }
    assert environment["openout_any"] == "p"
    assert Path(environment["HOME"]) == tmp_path.resolve()
    for name in ("TEXMFVAR", "TMPDIR", "XDG_CACHE_HOME"):
        assert Path(environment[name]).is_relative_to(tmp_path.resolve())
        assert Path(environment[name]).stat().st_mode & 0o777 == 0o700


def test_confined_command_names_only_declared_directories(tmp_path):
    source = tmp_path / "src"
    command = sandbox.confined(["lualatex", "doc.tex"], tmp_path, (source,))
    assert command[:3] == [
        sys.executable,
        "-I",
        str(Path(sandbox.__file__).resolve()),
    ]
    assert command[3:] == [
        "--write",
        str(tmp_path.resolve()),
        "--read",
        str(source.resolve()),
        "--",
        "lualatex",
        "doc.tex",
    ]


def test_launcher_fails_closed_without_running_the_tool(monkeypatch, capsys):
    def unavailable(writable, readable):
        raise OSError("Landlock is unavailable")

    monkeypatch.setattr(sandbox, "restrict", unavailable)
    monkeypatch.setattr(
        sandbox.os, "execvp", lambda *args: pytest.fail("tool ran unconfined")
    )
    assert sandbox.main(["--write", "/tmp", "--", "lualatex"]) == sandbox.UNAVAILABLE
    assert capsys.readouterr().err.startswith("latex_sandbox:")
    assert sandbox.main(["--other", "/tmp", "--", "x"]) == sandbox.UNAVAILABLE
    assert sandbox.main(["--write", "/tmp", "--"]) == sandbox.UNAVAILABLE


def test_path_rule_matches_the_kernel_layout():
    rule = sandbox._path_beneath(sandbox.READ_ONLY, 7)
    assert len(rule) == 12
    assert int.from_bytes(rule.raw[:8], sys.byteorder) == sandbox.READ_ONLY
    assert int.from_bytes(rule.raw[8:], sys.byteorder, signed=True) == 7


@requires_landlock
def test_confined_process_reads_and_writes_only_declared_directories(dirs):
    work, source, outside = dirs
    (source / "inside.txt").write_text(SIBLING)
    (outside / "secret.txt").write_text(MARKER)

    def run(*args, read=(source,)):
        return subprocess.run(
            sandbox.confined(list(args), work, read),
            cwd=work,
            env=sandbox.tool_environment(work),
            capture_output=True,
            text=True,
            timeout=30,
        )

    allowed = run("cat", str(source / "inside.txt"))
    assert (allowed.returncode, allowed.stdout) == (0, SIBLING)
    refused = run("cat", str(outside / "secret.txt"))
    assert refused.returncode != 0 and MARKER not in refused.stdout
    assert run("cat", str(source / "inside.txt"), read=()).returncode != 0
    assert run("cat", "/proc/self/environ").returncode != 0
    assert run("sh", "-c", f"echo x > {work / 'made.txt'}").returncode == 0
    assert (work / "made.txt").read_text() == "x\n"
    assert run("sh", "-c", f"echo x > {source / 'made.txt'}").returncode != 0
    assert run("sh", "-c", f"echo x > {outside / 'made.txt'}").returncode != 0
    assert not (source / "made.txt").exists()
    assert not (outside / "made.txt").exists()


@requires_landlock
def test_run_tool_passes_no_application_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AELIRA_SECRET_FOR_TEST", MARKER)
    code, log, error = runtime.run_tool(["env"], tmp_path, 30)
    assert (code, error) == (0, None)
    assert "openout_any=p" in log and MARKER not in log


@requires_tool("pdflatex")
@requires_tool("lualatex")
@pytest.mark.parametrize("method", ["_convert_with_lualatex", "_convert_with_pdflatex"])
def test_pdf_engines_keep_relative_inputs_and_refuse_outside_files(dirs, method):
    work, source, outside = dirs
    convert = getattr(LaTeXConverter(), method)
    (source / "section.tex").write_text(SIBLING + "\n")
    (source / "inside.tex").write_text(MARKER + " inside\n")
    (outside / "secret.tex").write_text(MARKER + "\n")
    cases = {
        "relative": "\\input{section}",
        "control": "\\input %s " % (source / "inside.tex"),
        "outside": "\\input %s " % (outside / "secret.tex"),
        "environ": "\\input /proc/self/environ ",
    }
    for name, body in cases.items():
        (source / f"{name}.tex").write_text(_document(body))
        attempt = work / name
        attempt.mkdir()
        convert(str(source / f"{name}.tex"), attempt)
        if name == "relative":
            assert _found(attempt, SIBLING) == ["relative.pdf"]
        elif name == "control":
            assert _found(attempt, MARKER) == ["control.pdf"]
        elif name == "environ":
            # Every tool environment carries this entry.
            assert _found(attempt, "openout_any=p") == []
        else:
            assert _found(attempt, MARKER) == []
    assert _found(outside, MARKER) == ["secret.tex"]


@requires_tool("latexml")
def test_latexml_keeps_math_and_refuses_outside_files(dirs):
    work, source, outside = dirs
    converter = LaTeXConverter()
    (outside / "secret.tex").write_text(MARKER + "\n")
    (source / "math.tex").write_text(
        _document(r"The area is $A = \pi r^2$ and \[ \int_0^1 x^2\,dx. \]")
    )
    (work / "math").mkdir()
    converter._convert_with_latexml(str(source / "math.tex"), work / "math")
    assert "<math" in (work / "math" / "math.html").read_text()
    # LaTeXML reads its preprocessed copy inside the attempt directory, so
    # that directory is the readable one for the positive control.
    for name, target in (("control", work / "control"), ("outside", outside)):
        (work / name).mkdir(exist_ok=True)
        if name == "control":
            (target / "inside.tex").write_text(MARKER + " inside\n")
        path = target / ("inside.tex" if name == "control" else "secret.tex")
        (source / f"{name}.tex").write_text(_document("\\input %s " % path))
        converter._convert_with_latexml(str(source / f"{name}.tex"), work / name)
    assert "control.xml" in _found(work / "control", MARKER)
    assert _found(work / "outside", MARKER) == []


@requires_tool("latexml")
def test_latexml_postprocessor_cannot_read_outside_bibliography(dirs):
    work, source, outside = dirs
    converter = LaTeXConverter()
    entity = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY m "%s">]><x>&m;<unclosed>'
    for name, target in (("control", work / "control"), ("outside", outside)):
        target.mkdir(exist_ok=True)
        (target / "refs.bib.xml").write_text(entity % MARKER)
        (source / f"{name}.tex").write_text(
            _document(
                "\\cite{k}\\csname bibliography\\endcsname{%s}" % (target / "refs")
            )
        )
        (work / name).mkdir(exist_ok=True)
        converter._convert_with_latexml(str(source / f"{name}.tex"), work / name)
    assert "postprocess.log" in _found(work / "control", MARKER)
    assert _found(work / "outside", MARKER) == []


@requires_tool("pandoc")
def test_pandoc_keeps_math_and_refuses_outside_files(dirs):
    work, source, outside = dirs
    converter = LaTeXConverter()
    (source / "inside.tex").write_text(MARKER + " inside\n")
    (outside / "secret.tex").write_text(MARKER + "\n")
    cases = {
        "math": r"Area $A = \pi r^2$.",
        "control": "\\input{%s}" % (source / "inside.tex"),
        "outside": "\\input{%s}" % (outside / "secret.tex"),
    }
    for name, body in cases.items():
        (source / f"{name}.tex").write_text(_document(body))
        (work / name).mkdir()
        converter._convert_with_pandoc(str(source / f"{name}.tex"), work / name)
    assert "<math" in (work / "math" / "math.html").read_text()
    assert _found(work / "control", MARKER) == ["control.html"]
    assert _found(work / "outside", MARKER) == []


@requires_tool("pandoc")
def test_project_runner_converts_under_confinement(dirs):
    work, source, outside = dirs
    (outside / "secret.tex").write_text(MARKER + "\n")
    (source / "math.tex").write_text(_document(r"Area $A = \pi r^2$."))
    (source / "outside.tex").write_text(
        _document("\\input{%s}" % (outside / "secret.tex"))
    )
    for name in ("math", "outside"):
        code, error = project_conversion._run_pandoc(
            source / f"{name}.tex", work / f"{name}.html", work / f"{name}.log"
        )
        assert error is None, name
    assert "<math" in (work / "math.html").read_text()
    assert _found(work, MARKER) == []


def test_unconfined_run_is_only_available_by_request(tmp_path, monkeypatch):
    seen = []
    real = subprocess.Popen

    def capture(args, **kwargs):
        seen.append(args)
        return real(["true"], **kwargs)

    monkeypatch.setattr(runtime.subprocess, "Popen", capture)
    runtime.run_tool(["lualatex", "--version"], tmp_path, 5)
    runtime.run_tool(["dpkg-query", "-W"], tmp_path, 5, confine=False)
    assert seen[0][:3] == [sys.executable, "-I", str(Path(sandbox.__file__).resolve())]
    assert seen[1] == ["dpkg-query", "-W"]
