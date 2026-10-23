"""LaTeX tools run confined to their conversion and source directories.

The confinement tests run real processes under Landlock. The converter tests
run the real LaTeX tools on legitimate documents and on documents that name
files outside their directories. Where the tool itself allows the read, a
positive control uses the same syntax on a file inside the readable directory
and must succeed, so a refused read is the boundary and not a broken probe.
"""

import bz2
import gzip
import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pymupdf
import pytest

from src.education import latex_project_conversion as project_conversion
from src.education import latex_runtime as runtime
from src.education import latex_sandbox as sandbox
from src.education.remediation import latex_converter
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
    monkeypatch.setenv("MAGICK_CONFIGURE_PATH", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    with sandbox.tool_home(work) as home:
        environment = sandbox.tool_environment(work, home)
    assert MARKER not in "".join(environment.values())
    assert set(environment) == {
        "PATH",
        "HOME",
        "XDG_CONFIG_HOME",
        "LANG",
        "LC_ALL",
        "openout_any",
        "TEXMFVAR",
        "TEXMFCONFIG",
        "TEXMFCACHE",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "MAGICK_TEMPORARY_PATH",
        "MAGICK_TIME_LIMIT",
    }
    assert environment["openout_any"] == "p"
    assert environment["MAGICK_TIME_LIMIT"] == "50"
    assert Path(environment["HOME"]) == home
    assert Path(environment["XDG_CONFIG_HOME"]) == home / ".config"
    assert not home.is_relative_to(work.resolve())
    for name in ("TEXMFVAR", "TMPDIR", "XDG_CACHE_HOME"):
        assert Path(environment[name]).is_relative_to(work.resolve())
        assert Path(environment[name]).stat().st_mode & 0o777 == 0o700
    with pytest.raises(OSError):
        sandbox.tool_environment(work, work / "home")


def test_tool_home_is_new_empty_outside_and_removed(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    with sandbox.tool_home(work) as home, sandbox.tool_home(work) as other:
        assert home != other
        assert home.is_dir() and not any(home.iterdir())
        assert home.stat().st_mode & 0o777 == 0o700
        assert not home.is_relative_to(work.resolve())
        (home / ".config").mkdir()
    assert not home.exists() and not other.exists()
    with pytest.raises(RuntimeError):
        with sandbox.tool_home(work) as home:
            raise RuntimeError("tool failed")
    assert not home.exists()
    # A conversion directory that contains the temporary directory gets a
    # home somewhere else, or none at all.
    temporary = Path(sandbox.tempfile.gettempdir()).resolve()
    if any(Path(p).is_dir() for p in sandbox._home_parents()[1:]):
        with sandbox.tool_home(temporary) as home:
            assert not home.is_relative_to(temporary)
        assert not home.exists()
    monkeypatch.setattr(sandbox, "_home_parents", lambda: (str(work / "inside"),))
    (work / "inside").mkdir()
    with pytest.raises(OSError):
        with sandbox.tool_home(work):
            pytest.fail("a home inside the conversion directory was used")


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
        with sandbox.tool_home(work) as home:
            return subprocess.run(
                sandbox.confined(list(args), work, read),
                cwd=work,
                env=sandbox.tool_environment(work, home),
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
    # The tool can neither list nor write its own home directory.
    assert run("sh", "-c", 'ls -A "$HOME"').returncode != 0
    assert run("sh", "-c", 'mkdir "$HOME/.config"').returncode != 0
    assert run("sh", "-c", 'test "$HOME" != "$PWD"').returncode == 0


@requires_landlock
def test_run_tool_passes_no_application_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AELIRA_SECRET_FOR_TEST", MARKER)
    code, log, error = runtime.run_tool(["env"], tmp_path, 30)
    assert (code, error) == (0, None)
    assert "openout_any=p" in log and MARKER not in log
    home = Path(next(line[5:] for line in log.splitlines() if line.startswith("HOME=")))
    assert not home.is_relative_to(tmp_path.resolve())
    assert not home.exists()


LOOSENING_POLICY = (
    '<policymap><policy domain="coder" rights="read" pattern="PS"/></policymap>\n'
)


@requires_landlock
def test_configuration_planted_in_the_conversion_directory_is_not_read(dirs):
    """ImageMagick adds $HOME/.config/ImageMagick and $XDG_CONFIG_HOME/ImageMagick
    policy files after the system policy. A file planted in the conversion
    directory must not be reachable through either variable. The image check
    (scripts/imagemagick_policy_probe.sh) shows the same with ImageMagick itself.
    """
    work, _, _ = dirs
    planted = work / ".config" / "ImageMagick"
    planted.mkdir(parents=True)
    (planted / "policy.xml").write_text(LOOSENING_POLICY)
    read_both = (
        'cat "$HOME/.config/ImageMagick/policy.xml"'
        ' "$XDG_CONFIG_HOME/ImageMagick/policy.xml"'
    )

    def run(home_is_conversion_directory):
        with sandbox.tool_home(work) as home:
            environment = sandbox.tool_environment(work, home)
            if home_is_conversion_directory:
                # The control: the home the tools had before this change.
                environment["HOME"] = str(work)
                environment["XDG_CONFIG_HOME"] = str(work / ".config")
            return subprocess.run(
                sandbox.confined(["sh", "-c", read_both], work),
                cwd=work,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )

    refused = run(home_is_conversion_directory=False)
    assert refused.returncode != 0 and 'pattern="PS"' not in refused.stdout
    control = run(home_is_conversion_directory=True)
    assert control.returncode == 0
    assert control.stdout == LOOSENING_POLICY * 2


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


@requires_tool("pandoc")
def test_project_runner_converts_a_full_document_within_its_bounds(tmp_path):
    # pandoc reserves more address space on x86-64 than on arm64; the runner's
    # bound must leave room for it on both.
    rows = "\n".join(f"{n} & ${n}^2 = {n * n}$ \\\\" for n in range(1, 41))
    body = "\n".join(
        [
            r"\section{Results}",
            r"\begin{itemize}\item First point\item Second point\end{itemize}",
            r"\begin{tabular}{ll}n & square \\",
            rows,
            r"\end{tabular}",
            *(
                rf"\[ \int_0^{n} x^{n}\,dx = \frac{{{n}^{n + 1}}}{{{n + 1}}} \]"
                for n in range(1, 31)
            ),
        ]
    )
    (tmp_path / "full.tex").write_text(_document(body))
    code, error = project_conversion._run_pandoc(
        tmp_path / "full.tex", tmp_path / "full.html", tmp_path / "full.log"
    )
    html = (tmp_path / "full.html").read_text()
    assert (code, error) == (0, None), (tmp_path / "full.log").read_text()[-500:]
    assert html.count("<math") >= 70 and "<table" in html and "<li>" in html


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


def _png(width=4, height=3):
    """A small, valid RGB PNG."""

    def chunk(kind, data):
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + b"\x00\x80\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


@pytest.mark.parametrize(
    "head,expected",
    [
        (b"\x1f\x8b\x08\x00", "gzip"),
        (b"BZh91AY&SY", "bzip2"),
        (b"\xfd7zXZ\x00\x00", "xz"),
        (b"\x28\xb5\x2f\xfd\x00", "zstd"),
        (b"\x89PNG\r\n\x1a\n", None),
        (b"\xff\xd8\xff\xe0", None),
        (b"%PDF-1.7", None),
        (b"%!PS-Adobe-3.0 EPSF-3.0", None),
        (b"\\documentclass{article}", None),
        (b"@article{key,", None),
        (b"BZ", None),
        (b"\x1f", None),
        (b"", None),
    ],
)
def test_compression_wrapper_recognises_exactly_the_refused_magics(head, expected):
    assert sandbox.compression_wrapper(head) == expected


def test_find_compressed_file_checks_every_file_whatever_its_name(
    tmp_path, monkeypatch
):
    work = tmp_path / "work"
    (work / "nested" / "deeper").mkdir(parents=True)
    (work / "doc.tex").write_text("\\documentclass{article}")
    (work / "figure.png").write_bytes(_png())
    assert sandbox.find_compressed_file([work]) is None
    hidden = work / "nested" / "deeper" / "anything.dat"
    hidden.write_bytes(bz2.compress(_png()))
    assert sandbox.find_compressed_file([work, work]) == hidden.resolve()
    hidden.unlink()
    # A link to a wrapped file is checked through the link; a link to a
    # directory is not entered.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "wrapped.png").write_bytes(bz2.compress(_png()))
    (work / "linked-dir").symlink_to(outside, target_is_directory=True)
    assert sandbox.find_compressed_file([work]) is None
    (work / "linked.png").symlink_to(outside / "wrapped.png")
    assert sandbox.find_compressed_file([work]) == work.resolve() / "linked.png"
    (work / "linked.png").unlink()
    # Too many files to check is a finding, not a pass.
    monkeypatch.setattr(sandbox, "MAX_SCANNED_FILES", 1)
    assert sandbox.find_compressed_file([work]) is not None


def _latexml_codes(attempt):
    path = attempt / "latexml-parse-1.diagnostics.json"
    return [d["code"] for d in json.loads(path.read_text())["diagnostics"]]


@requires_tool("latexmlpost")
@pytest.mark.parametrize("wrapper", ["bzip2", "gzip"])
def test_latexml_refuses_a_compression_wrapped_graphic_before_it_runs(
    dirs, monkeypatch, wrapper
):
    work, source, _ = dirs
    converter = LaTeXConverter()
    (source / "doc.tex").write_text(
        _document("\\includegraphics{figure.png}").replace(
            "\\begin{document}", "\\usepackage{graphicx}\n\\begin{document}"
        )
    )
    wrap = bz2.compress if wrapper == "bzip2" else gzip.compress

    def attempt(name, figure):
        directory = work / name
        directory.mkdir()
        (directory / "figure.png").write_bytes(figure)
        html = converter._convert_with_latexml(str(source / "doc.tex"), directory)
        return directory, html

    refused, html = attempt("refused", wrap(_png()))
    assert html is None
    assert _latexml_codes(refused) == ["compressed_input"]
    assert not (refused / "doc.xml").exists()
    # Control: the same PNG, unwrapped, goes through both LaTeXML stages and
    # the graphic reaches the output. (The converter's later acceptance checks
    # refuse a figure without alternative text, which is not what is tested.)
    control, _ = attempt("control", _png())
    assert _latexml_codes(control) == []
    assert "figure.png" in (control / "doc.html").read_text()
    # Negative control: with the check removed, LaTeXML runs on the wrapped file.
    monkeypatch.setattr(latex_converter, "find_compressed_file", lambda dirs: None)
    unchecked, _ = attempt("unchecked", wrap(_png()))
    assert (unchecked / "doc.xml").exists()
    assert "compressed_input" not in _latexml_codes(unchecked)


@requires_tool("latexml")
def test_latexml_refuses_a_source_that_itself_starts_with_bzip2_magic(dirs):
    """A document can name its own processed copy as a graphic, so a source
    whose first bytes are a bzip2 magic is refused too. A real document never
    starts with these three letters; this is the only text-file case."""
    work, source, _ = dirs
    (source / "doc.tex").write_text("BZh9 " + _document("Text."))
    attempt = work / "attempt"
    attempt.mkdir()
    assert (
        LaTeXConverter()._convert_with_latexml(str(source / "doc.tex"), attempt) is None
    )
    assert _latexml_codes(attempt) == ["compressed_input"]
