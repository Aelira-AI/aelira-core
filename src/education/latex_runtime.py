"""Owned TeX caches and functional checks for a small declared runtime profile.

Readiness means these synthetic conversions work, never accessibility conformance.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import tempfile


def version_command(tool: str) -> list[str]:
    args = [tool, "--VERSION" if tool in {"latexml", "latexmlpost"} else "--version"]
    if tool in {"lualatex", "pdflatex"}:
        args.append("-no-shell-escape")
    return args


def tex_environment(directory: Path) -> dict[str, str]:
    """Keep format/font creation inside this already-owned conversion attempt."""
    environment = dict(os.environ)
    for name, suffix in (
        ("TEXMFVAR", "var"),
        ("TEXMFCONFIG", "config"),
        ("TEXMFCACHE", "var"),
        ("XDG_CACHE_HOME", "cache"),
    ):
        path = directory.resolve() / ".tex-cache" / suffix
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        environment[name] = str(path)
    return environment


def run_tool(args: list[str], directory: Path, timeout: float = 90):
    """Bound the complete process group, including format/font helper children."""
    with tempfile.TemporaryFile() as output:
        try:
            with subprocess.Popen(
                args,
                cwd=directory,
                env=tex_environment(directory),
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            ) as process:
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    return None, "", "timeout"
        except OSError:
            return None, "", "tool_unavailable"
        output.seek(0)
        log = output.read(131073)
        if len(log) > 131072:
            return code, "", "diagnostics_truncated"
        return code, log.decode("utf-8", errors="replace"), None


def failure_reason(log: str) -> str | None:
    """Known prerequisite failures, including exit-zero font substitutions."""
    patterns = (
        (
            "format_unavailable",
            r"can't find the format|cannot find.*format|failed to.*format",
        ),
        (
            "font_unavailable",
            r"module ['\"]luaotfload[^'\"]*['\"] not found|luaotfload[^\n]*(?:error|failed)|fontspec error|font[^\n]*cannot be found|LaTeX Font Warning|missing character",
        ),
        (
            "language_unavailable",
            r"unknown option.*(?:german|english)|(?:german|english)\.ldf.*not found|babel.*(?:error|unknown language)",
        ),
        ("package_unavailable", r"file [`'].*\.(?:sty|cls)['`].*not found"),
    )
    for reason, pattern in patterns:
        if re.search(pattern, log, re.IGNORECASE):
            return reason
    return None


HINTS = {
    "format_unavailable": "Install texlive-luatex and permit format creation in owned scratch storage.",
    "font_unavailable": "Install texlive-luatex and Latin Modern fonts; permit the scratch font cache.",
    "language_unavailable": "Install texlive-lang-german and English Babel support for this profile.",
    "package_unavailable": "Install the documented TeX runtime packages; do not change the source to hide missing dependencies.",
    "tool_unavailable": "Install the named converter and expose it on the service PATH.",
    "timeout": "Check format/font initialization and the service resource limit; rerun the bounded probe.",
    "diagnostics_truncated": "The control exceeded its diagnostic limit; inspect the runtime before accepting readiness.",
    "conversion_failed": "Inspect the installed converter and packages using the documented synthetic control.",
    "output_invalid": "The converter did not produce the expected readable content and mathematics.",
    "scratch_unwritable": "Provide writable service-owned temporary storage; system and home directories need not be writable.",
}


def inspect_output(path: Path, kind: str, sentinel: str) -> bool:
    if not path.is_file() or not 0 < path.stat().st_size < 8 * 1024 * 1024:
        return False
    if kind == "pdf":
        import pymupdf

        try:
            with pymupdf.open(path) as document:
                text = " ".join(page.get_text() for page in document)
                return (
                    len(document) == 1
                    and sentinel in text
                    and "x2+1" in re.sub(r"\s+", "", text)
                )
        except Exception:
            return False
    try:
        # Both controlled HTML lanes are requested as XML-compatible HTML5.
        # HTMLParser handles Pandoc's HTML void elements without guessing at text.
        from html.parser import HTMLParser

        class Inspector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []
                self.math = False
                self.depth = 0
                self.math_text = []

            def handle_starttag(self, tag, attrs):
                if tag.split(":")[-1] == "math":
                    self.math = True
                    self.depth += 1

            def handle_endtag(self, tag):
                if tag.split(":")[-1] == "math":
                    self.depth = max(0, self.depth - 1)

            def handle_data(self, data):
                self.text.append(data)
                if self.depth:
                    self.math_text.append(data)

        reader = Inspector()
        reader.feed(path.read_text(encoding="utf-8"))
        return (
            reader.math
            and sentinel in "".join(reader.text)
            and "x2+1" in re.sub(r"\s+", "", "".join(reader.math_text))
        )
    except (OSError, UnicodeError):
        return False


def probe_runtime(scratch: Path | None = None, timeout: float = 90) -> dict:
    report = {
        "profile": "latex-runtime-v1",
        "scope": "English/German LuaLaTeX font control and LaTeXML/Pandoc MathML controls; no accessibility certification",
        "status": "unavailable",
        "uid": os.getuid(),
        "architecture": platform.machine(),
        "image_identity": os.environ.get("AELIRA_RUNTIME_IMAGE_ID"),
        "tools": {},
        "packages": {},
        "checks": [],
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="aelira-latex-probe-", dir=scratch
        ) as temporary:
            root = Path(temporary)
            package_names = [
                "texlive-luatex",
                "texlive-lang-german",
                "texlive-latex-recommended",
                "texlive-fonts-recommended",
                "latexml",
                "pandoc",
            ]
            code, package_log, error = run_tool(
                ["dpkg-query", "-W", "-f=${Package} ${Version}\n", *package_names],
                root,
                5,
            )
            if code == 0 and not error:
                report["packages"] = dict(
                    line.split(" ", 1)
                    for line in package_log.splitlines()
                    if re.fullmatch(r"[a-z0-9+.-]+ [a-zA-Z0-9+:~._-]+", line)
                )
            for tool in ("lualatex", "latexml", "latexmlpost", "pandoc"):
                version = None
                if shutil.which(tool):
                    args = version_command(tool)
                    code, log, error = run_tool(args, root, 5)
                    if code == 0 and not error:
                        match = re.search(r"\b\d+(?:\.\d+){1,3}\b", log)
                        version = match.group(0) if match else "unreported"
                report["tools"][tool] = {
                    "found": shutil.which(tool) is not None,
                    "version": version,
                }
            for lane in ("lualatex-english", "lualatex-german", "latexml", "pandoc"):
                directory = root / lane
                directory.mkdir()
                sentinel = "Aelira runtime control"
                if lane == "lualatex-german":
                    sentinel += " Grüße"
                language = "ngerman" if lane.endswith("german") else "english"
                font = (
                    "\\usepackage{fontspec}\n\\setmainfont{Latin Modern Roman}\n"
                    if lane.startswith("lualatex")
                    else ""
                )
                source = directory / "control.tex"
                source.write_text(
                    "\\documentclass{article}\n"
                    + font
                    + (
                        f"\\usepackage[{language}]{{babel}}\n"
                        if font
                        else "\\usepackage{amsmath}\n"
                    )
                    + "\\begin{document}\n"
                    + sentinel
                    + ". $x^2+1$.\n\\end{document}\n",
                    encoding="utf-8",
                )
                kind = "pdf" if font else "html"
                candidate = directory / f"control.{kind}"
                if font:
                    commands = [
                        [
                            "lualatex",
                            "-no-shell-escape",
                            "-interaction=nonstopmode",
                            "-halt-on-error",
                            str(source),
                        ]
                    ]
                elif lane == "latexml":
                    commands = [
                        ["latexml", "--dest=control.xml", str(source)],
                        [
                            "latexmlpost",
                            "--format=html5",
                            "--dest=control.html",
                            "control.xml",
                        ],
                    ]
                else:
                    commands = [
                        [
                            "pandoc",
                            str(source),
                            "--from=latex",
                            "--to=html5",
                            "--mathml",
                            "--standalone",
                            "-o",
                            str(candidate),
                        ]
                    ]
                reason = None
                for command in commands:
                    code, log, error = run_tool(command, directory, timeout)
                    reason = (
                        error
                        or failure_reason(log)
                        or ("conversion_failed" if code != 0 else None)
                    )
                    if reason:
                        break
                if not reason and not inspect_output(candidate, kind, sentinel):
                    reason = "output_invalid"
                report["checks"].append(
                    {
                        "name": lane,
                        "status": (
                            "ready"
                            if reason is None
                            else (
                                "unavailable"
                                if reason == "tool_unavailable"
                                else "degraded"
                            )
                        ),
                        "reason": reason,
                        "action": HINTS.get(reason),
                        "source_sha256": hashlib.sha256(
                            source.read_bytes()
                        ).hexdigest(),
                        "output_sha256": (
                            hashlib.sha256(candidate.read_bytes()).hexdigest()
                            if candidate.is_file()
                            and candidate.stat().st_size < 8 * 1024 * 1024
                            else None
                        ),
                    }
                )
            statuses = {check["status"] for check in report["checks"]}
            report["status"] = (
                "ready"
                if statuses == {"ready"}
                else (
                    "degraded"
                    if "ready" in statuses or "degraded" in statuses
                    else "unavailable"
                )
            )
    except OSError:
        report["status"] = "unavailable"
        report["error"] = "scratch_unwritable"
        report["action"] = HINTS["scratch_unwritable"]
    return report
