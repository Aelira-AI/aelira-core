"""Bounded conversion-loss observations. Absence of loss is not fidelity proof."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import base64
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_LOG = 65536
MAX_ARTIFACT = 16 * 1024 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: Literal[
        "process_failed",
        "tool_unavailable",
        "timeout",
        "diagnostics_truncated",
        "missing_dependency",
        "unsupported_command",
        "malformed_expression",
        "converter_error",
        "unresolved_reference",
        "raw_tex",
        "error_node",
        "missing_asset",
        "candidate_missing",
        "candidate_unreadable",
        "unclassified_warning",
        "layout_warning",
        "metadata_warning",
        "font_warning",
        "rerun_required",
        "deprecation_warning",
    ]
    severity: Literal["info", "warning", "error"]
    source_line: int | None = Field(default=None, ge=1, le=10000000)


class ReferenceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_exists: bool
    target_identity: Literal["not_assessed"] = "not_assessed"
    reader_activation: Literal["not_assessed"] = "not_assessed"


class ConversionStage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tool: Literal[
        "latexml",
        "latexmlpost",
        "pandoc",
        "lualatex",
        "pdflatex",
        "inspection",
        "html-renderer",
    ]
    version: str = Field(
        default="unknown", pattern=r"^(unknown|[0-9]{1,4}(?:\.[0-9]{1,4}){1,3})$"
    )
    phase: Literal["parse", "postprocess", "compile", "inspect", "render"]
    pass_number: int = Field(default=1, ge=1, le=2)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    exit_code: int | None = None
    diagnostics: list[Diagnostic] = Field(default_factory=list, max_length=32)
    references: list[ReferenceObservation] = Field(default_factory=list, max_length=128)

    @property
    def blocked(self):
        return any(d.severity == "error" for d in self.diagnostics)


class ConversionDiagnostics(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )
    schema_version: Literal[1] = 1
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    status: Literal["accepted", "refused"]
    stages: list[ConversionStage] = Field(default_factory=list, max_length=24)
    coverage: Literal["known-loss-checks-only"] = "known-loss-checks-only"
    fidelity: Literal["not_assessed"] = "not_assessed"

    @model_validator(mode="after")
    def accepted_requires_observations(self):
        if self.status == "accepted" and (
            not self.candidate_sha256
            or not self.stages
            or any(s.blocked for s in self.stages)
        ):
            raise ValueError(
                "Accepted conversion requires loss checks on candidate bytes"
            )
        return self


# Request-local state: the converter itself is a shared singleton.
_active: ContextVar[list[ConversionStage] | None] = ContextVar(
    "latex_stages", default=None
)


@contextmanager
def conversion_session():
    stages: list[ConversionStage] = []
    token = _active.set(stages)
    try:
        yield stages
    finally:
        _active.reset(token)


def record(stage):
    stages = _active.get()
    if stages is not None:
        stages.append(stage)
    return stage


def has_loss():
    return any(s.blocked for s in (_active.get() or []))


def diagnostic(code, severity="error", line=None):
    return Diagnostic(code=code, severity=severity, source_line=line)


def classify(stdout, stderr, *, exit_code, final_pass=True):
    """Allowlisted codes only: no converter text, paths or authored text is public."""
    text = (stdout or "") + "\n" + (stderr or "")
    found = []
    if len(text) > MAX_LOG:
        found.append(diagnostic("diagnostics_truncated"))
        text = text[: MAX_LOG // 2] + "\n" + text[-MAX_LOG // 2 :]
    if exit_code:
        found.append(diagnostic("process_failed"))
    for line in text.splitlines():
        lower = line.lower()
        location = re.search(r"(?:line\s+|l\.)(\d+)", line)
        number = int(location[1]) if location else None
        number = number if number and number <= 10000000 else None
        code, severity = None, "error"
        if re.search(
            r"(?:no graphic source|could not find.*(?:image|graphic)|missing.*(?:image|graphic))",
            lower,
        ):
            code = "missing_asset"
        elif re.search(
            r"(?:cannot|could not|can't|failed to) (?:find|load|read)|not found|no such file|missing.*(?:file|include|input)|(?:error|warning):missing:",
            lower,
        ):
            code = "missing_dependency"
        elif re.search(
            r"undefined control sequence|unknown macro|unrecognized.*(?:macro|command)|unexpected control sequence|could not convert tex math|error:undefined:",
            lower,
        ):
            code = "unsupported_command"
        elif re.search(
            r"unbalanced|runaway argument|missing.*(?:brace|delimiter)|error:expected:|error:unexpected:",
            lower,
        ):
            code = "malformed_expression"
        elif re.search(
            r"undefined (?:references|citations)|(?:reference|citation).*undefined|unresolved|warning:expected:(?:label|id|ref)",
            lower,
        ):
            code = "unresolved_reference"
            severity = "error" if final_pass else "warning"
        elif "rerun" in lower and ("cross-reference" in lower or "label" in lower):
            code, severity = "rerun_required", "error" if final_pass else "info"
        elif re.search(r"(?:^|\s)error[:!]|^!|fatal|\b[1-9]\d* errors?\b", lower):
            code = "converter_error"
        elif "overfull" in lower or "underfull" in lower:
            code, severity = "layout_warning", "warning"
        elif "font" in lower and ("substitut" in lower or "warning" in lower):
            code, severity = "font_warning", "warning"
        elif (
            "pagetitle" in lower
            or "title> element" in lower
            or "title element" in lower
        ):
            code, severity = "metadata_warning", "warning"
        elif "deprecated" in lower:
            code, severity = "deprecation_warning", "warning"
        elif "warning" in lower and not re.search(
            r"(?:complete|processing).*\d+ warnings?", lower
        ):
            code = "unclassified_warning"
        if code:
            item = diagnostic(code, severity, number)
            if item not in found:
                found.append(item)
    if len(found) > 32:
        found = found[:31] + [diagnostic("diagnostics_truncated")]
    return found


class _HTMLObservations(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.links = []
        self.images = []
        self.errors = False
        self.raw = False
        self.has_mathml = False
        self.math_spans = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("href", "").startswith("#"):
            self.links.append(unquote(attrs["href"][1:]))
        if tag == "img":
            self.images.append(attrs.get("src", ""))
        if tag.split(":")[-1] == "math":
            self.has_mathml = True
        if "math" in classes and tag == "span":
            self.math_spans += 1
        if tag.split(":")[-1] in {"error", "merror"} or classes & {
            "ltx_ERROR",
            "ltx_missing",
            "ltx_ref_missing",
        }:
            self.errors = True
        if "tex" in classes or "raw" in classes:
            self.raw = True


def inspect_candidate(source: Path, candidate: Path, *, xml=False):
    if xml:
        return inspect_semantic_xml(source, candidate)
    findings = []
    references = []
    try:
        data = candidate.read_bytes()
        if not data or len(data) > MAX_ARTIFACT:
            raise ValueError("bounded candidate required")
        text = data.decode("utf-8")
        parser = _HTMLObservations()
        parser.feed(text)
        if parser.errors:
            findings.append(diagnostic("error_node"))
        from bs4 import BeautifulSoup

        markup = BeautifulSoup(text, "html.parser")
        raw_math = any(not span.find("math") for span in markup.select("span.math"))
        if parser.raw or raw_math:
            findings.append(diagnostic("raw_tex"))
        for citation in markup.select(".citation, .ltx_cite"):
            if not citation.get_text(strip=True) or not citation.find("a", href=True):
                findings.append(diagnostic("unresolved_reference"))
                break
        for equation in markup.select(".ltx_equation"):
            if not equation.find(["math", "img", "svg"]):
                findings.append(diagnostic("malformed_expression"))
                break
        source_text = source.read_text(encoding="utf-8")
        if "\\includegraphics" in source_text or "\\begin{tikzpicture}" in source_text:
            for figure in markup.find_all("figure"):
                if not figure.find(["img", "svg", "math", "table"]):
                    findings.append(diagnostic("missing_asset"))
                    break
        for target in parser.links[:128]:
            references.append(
                ReferenceObservation(
                    target_sha256=sha(target.encode()),
                    target_exists=bool(target and target in parser.ids),
                )
            )
        if len(parser.links) > 128:
            findings.append(diagnostic("diagnostics_truncated"))
        if any(not r.target_exists for r in references):
            findings.append(diagnostic("unresolved_reference"))
        for asset in parser.images:
            # External and embedded assets have not been fetched/verified here.
            if asset.startswith("data:image/"):
                try:
                    from PIL import Image

                    header, encoded = asset.split(",", 1)
                    if not header.endswith(";base64"):
                        raise ValueError("unsupported embedded image")
                    with Image.open(
                        BytesIO(base64.b64decode(encoded, validate=True))
                    ) as image:
                        image.verify()
                    continue
                except (ValueError, OSError):
                    findings.append(diagnostic("missing_asset"))
                    break
            if not asset or ":" in asset or asset.startswith("//"):
                findings.append(diagnostic("missing_asset"))
                break
            path = (candidate.parent / unquote(asset.split("#", 1)[0])).resolve()
            if (
                not path.is_relative_to(candidate.parent.resolve())
                or not path.is_file()
            ):
                findings.append(diagnostic("missing_asset"))
                break
        candidate_hash = sha(data)
    except (OSError, UnicodeError, ValueError):
        findings.append(diagnostic("candidate_unreadable"))
        candidate_hash = None
    return record(
        ConversionStage(
            tool="inspection",
            phase="inspect",
            input_sha256=sha(source.read_bytes()),
            candidate_sha256=candidate_hash,
            diagnostics=findings,
            references=references,
        )
    )


def inspect_source(source: Path):
    """Check explicit dependencies; do not claim to parse the complete TeX language."""
    data = source.read_bytes()
    text = re.sub(r"(?<!\\)%[^\n]*", "", data.decode("utf-8"))
    findings = []
    pattern = r"\\(input|include|includegraphics|bibliography)\*?(?:\[[^\]]*\])?\s*\{([^{}]+)\}"
    for match in re.finditer(pattern, text):
        command, argument = match.groups()
        suffixes = [""]
        if command in {"input", "include"}:
            suffixes += [".tex"]
        elif command == "bibliography":
            suffixes += [".bib"]
        else:
            suffixes += [".pdf", ".png", ".jpg", ".jpeg", ".svg", ".eps"]
        for name in argument.split(",") if command == "bibliography" else [argument]:
            paths = [
                (source.parent / (name.strip() + suffix)).resolve()
                for suffix in suffixes
            ]
            if not any(
                p.is_relative_to(source.parent.resolve()) and p.is_file() for p in paths
            ):
                findings.append(
                    diagnostic(
                        (
                            "missing_asset"
                            if command == "includegraphics"
                            else "missing_dependency"
                        ),
                        line=text[: match.start()].count("\n") + 1,
                    )
                )
    if len(findings) > 32:
        findings = findings[:31] + [diagnostic("diagnostics_truncated")]
    return record(
        ConversionStage(
            tool="inspection",
            phase="inspect",
            input_sha256=sha(data),
            diagnostics=findings,
        )
    )


def inspect_semantic_xml(source: Path, candidate: Path):
    """Parse semantic XML as XML; keep its bytes separately for fidelity review."""
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    findings = []
    candidate_hash = None
    try:
        data = candidate.read_bytes()
        candidate_hash = sha(data)
        if not data or len(data) > MAX_ARTIFACT:
            raise ValueError("bounded candidate required")
        root = ElementTree.fromstring(data)
        if any(
            node.tag.rsplit("}", 1)[-1].lower() in {"error", "merror"}
            for node in root.iter()
        ):
            findings.append(diagnostic("error_node"))
    except (OSError, ValueError, ElementTree.ParseError, DefusedXmlException):
        findings.append(diagnostic("candidate_unreadable"))
    return record(
        ConversionStage(
            tool="inspection",
            phase="inspect",
            input_sha256=sha(source.read_bytes()),
            candidate_sha256=candidate_hash,
            diagnostics=findings,
        )
    )
