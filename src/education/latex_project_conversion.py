"""Full-context project HTML, with originals kept separate from analysis copies."""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Literal, get_args

from bs4 import BeautifulSoup
from PIL import Image
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .latex_diagnostics import (
    Diagnostic,
    classify,
    conversion_session,
    inspect_candidate,
)
from .latex_metadata import extract_metadata, save_html_metadata
from .latex_semantics import extract_semantics, save_html_semantics
from .latex_semantics import _group
from .latex_compatibility import ConversionDecision, decide_html
from .latex_equation_provenance import (
    EquationRepresentationTrace,
    SourceEquationProvenance,
    observe_representation,
    source_provenance,
)

MAX_OUTPUT = 16 * 1024 * 1024
MAX_LOG = 65536
REASONS = set(get_args(Diagnostic.model_fields["code"].annotation)) | {
    "dependencies_unresolved",
    "bibliography_export_unsupported",
    "active_content",
    "project_export_limit",
}


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


class Transformation(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    kind: Literal[
        "literal_dependency_expansion",
        "html_conversion",
        "authored_metadata_and_relationships",
    ]
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProjectSourceEquations(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )
    path_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    equations: SourceEquationProvenance


class ProjectProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")
    schema_version: Literal[1] = 1
    profile: Literal["pandoc-project-html-v1"] = "pandoc-project-html-v1"
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    analysis_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    output_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    tool_version: str = Field(
        default="unknown", pattern=r"^(unknown|[0-9]{1,4}(?:\.[0-9]{1,4}){1,3})$"
    )
    status: Literal["accepted", "refused"] = "refused"
    transformations: list[Transformation] = Field(default_factory=list, max_length=3)
    reasons: list[str] = Field(default_factory=list, max_length=32)
    accessibility_status: Literal["not_verified"] = "not_verified"
    human_review_required: Literal[True] = True
    decision: ConversionDecision | None = None
    source_equations: SourceEquationProvenance | None = None
    equations: EquationRepresentationTrace | None = None
    original_equations: list[ProjectSourceEquations] = Field(
        default_factory=list, max_length=64
    )
    original_equations_complete: bool = False

    @model_validator(mode="after")
    def original_inventory_consistency(self):
        paths = [item.path_sha256 for item in self.original_equations]
        if len(paths) != len(set(paths)):
            raise ValueError("Duplicate original equation source")
        if self.original_equations_complete and (
            not paths or any(item.equations.issues for item in self.original_equations)
        ):
            raise ValueError("Complete original inventory requires parsed sources")
        if (
            sum(item.equations.source_bytes for item in self.original_equations)
            > 2 * 1024 * 1024
        ):
            raise ValueError("Original equation source budget exceeded")
        if (
            sum(
                len(item.equations.expressions)
                + len(item.equations.relationships)
                + sum(len(e.rows) for e in item.equations.expressions)
                for item in self.original_equations
            )
            > 128
        ):
            raise ValueError("Original equation record budget exceeded")
        return self

    @field_validator("reasons")
    @classmethod
    def bounded_reasons(cls, value):
        if any(reason not in REASONS for reason in value):
            raise ValueError("unknown reason")
        return value


def public_project_provenance(value):
    try:
        record = ProjectProvenance.model_validate(value)
        if (
            record.source_equations is not None
            and record.source_equations.source_sha256 != record.analysis_sha256
        ):
            return None
        if record.equations is not None and (
            record.equations.source_sha256 != record.analysis_sha256
            or record.equations.candidate_sha256 != record.output_sha256
        ):
            return None
        if (
            record.decision is not None
            and record.decision.source_sha256 != record.analysis_sha256
        ):
            return None
        if record.status == "accepted" and (
            not record.output_sha256 or not record.analysis_sha256 or record.reasons
        ):
            return None
        return record.model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ProjectConversion:
    path: str | None
    provenance: dict
    issues: tuple[dict, ...]


def _run_pandoc(source: Path, output: Path, log: Path):
    executable = shutil.which("pandoc")
    if not executable:
        return None, "tool_unavailable"
    launcher = Path(__file__).with_name("latex_project_runner.py")
    try:
        with output.open("xb") as destination, log.open("xb") as diagnostic_log:
            with subprocess.Popen(
                [
                    sys.executable,
                    str(launcher),
                    str(Path(executable).resolve()),
                    str(source.resolve()),
                    str(os.getpid()),
                ],
                cwd=source.parent,
                stdin=subprocess.DEVNULL,
                stdout=destination,
                stderr=diagnostic_log,
                start_new_session=True,
            ) as process:
                try:
                    return process.wait(timeout=65), None
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    return None, "timeout"
    except OSError:
        return None, "tool_unavailable"


def _version():
    try:
        result = subprocess.run(
            ["pandoc", "--version"], capture_output=True, text=True, timeout=5
        )
        match = re.match(
            r"pandoc ([0-9]{1,4}(?:\.[0-9]{1,4}){1,3})\b", result.stdout[:100]
        )
        return match[1] if match else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _inactive_html(text):
    soup = BeautifulSoup(text, "html.parser")
    if soup.find(
        ["script", "style", "iframe", "object", "embed", "form", "base", "link"]
    ):
        return False
    for tag in soup.find_all(True):
        if any(
            str(key).lower().startswith("on") or key == "srcdoc" for key in tag.attrs
        ):
            return False
        for key in ("href", "src", "action", "xlink:href"):
            value = str(tag.get(key, ""))
            if (
                re.sub(r"[\x00-\x20]", "", value)
                .lower()
                .startswith(("javascript:", "vbscript:", "file:"))
            ):
                return False
    return True


def _metadata_source(text):
    """Ignore only inert new math macros when locating literal metadata.

    The actual converter still receives every definition. Redefinitions,
    metadata-bearing bodies and unknown control sequences retain the existing
    metadata_unsupported refusal rather than being erased from the analysis.
    """
    math_commands = set(
        "frac dfrac tfrac sqrt sum prod int iint lim sin cos tan log ln exp alpha beta gamma delta epsilon theta lambda mu pi rho sigma tau phi chi psi omega Delta Sigma Omega Gamma left right cdot times div pm mp le ge neq infty mathbb mathcal mathrm mathbf operatorname text overline underline hat bar vec".split()
    )
    reserved = math_commands | set(
        "title author hypersetup DocumentMetadata documentclass usepackage RequirePackage LoadClass input include includegraphics bibliography addbibresource begin end foreignlanguage setdefaultlanguage tagpdfsetup newcommand renewcommand providecommand def let".split()
    )
    edits = []
    for match in re.finditer(r"\\newcommand\b", text):
        try:
            name, position = _group(text, match.end())
            if not re.fullmatch(r"\\[A-Za-z]+", name) or name[1:] in reserved:
                return text
            while position < len(text) and text[position].isspace():
                position += 1
            if position < len(text) and text[position] == "[":
                count, position = _group(text, position, "[", "]")
                if count not in {"1", "2", "3"}:
                    return text
            body, end = _group(text, position)
            if any(
                command not in math_commands
                for command in re.findall(r"\\([A-Za-z]+)", body)
            ):
                return text
            if re.search(r"[\x00%]", body):
                return text
            edits.append((match.start(), end))
        except ValueError:
            return text
    for start, end in reversed(edits):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def convert_project_html(project, output_dir: Path) -> ProjectConversion:
    """Convert one inspected project; no external files or TeX execution allowed."""
    receipt = ProjectProvenance(
        archive_sha256=project.archive_digest, source_sha256=project.source_digest
    )
    remaining_bytes = 2 * 1024 * 1024
    remaining_records = 128
    receipt.original_equations_complete = True
    for name, data in sorted(project.files.items()):
        if Path(name).suffix.lower() != ".tex":
            continue
        if len(receipt.original_equations) == 64 or len(data) > remaining_bytes:
            receipt.original_equations_complete = False
            break
        try:
            original = source_provenance(data.decode("utf-8"))
        except UnicodeError:
            receipt.original_equations_complete = False
            continue
        remaining_bytes -= len(data)
        size = (
            len(original.expressions)
            + len(original.relationships)
            + sum(len(e.rows) for e in original.expressions)
        )
        if size > remaining_records:
            receipt.original_equations_complete = False
            break
        remaining_records -= size
        if original.issues:
            receipt.original_equations_complete = False
        receipt.original_equations.append(
            ProjectSourceEquations(
                path_sha256=digest(name.encode("utf-8")), equations=original
            )
        )

    def result(path=None, reasons=()):
        if path and Path(path).stat().st_size > MAX_OUTPUT:
            path, reasons = None, ["project_export_limit"]
        receipt.status = "accepted" if path else "refused"
        receipt.reasons = sorted(set(reasons))[:32]
        if path:
            receipt.output_sha256 = digest(Path(path).read_bytes())
            receipt.equations = observe_representation(
                project.flattened_source, Path(path), "html"
            )
        return ProjectConversion(
            str(path) if path else None,
            receipt.model_dump(mode="json"),
            tuple(
                {
                    "code": reason,
                    "path": "",
                    "action": "Review the source project; this export cannot preserve the requested content.",
                }
                for reason in receipt.reasons
            ),
        )

    if project.issues or project.flattened_source is None:
        return result(reasons=["dependencies_unresolved"])
    text = project.flattened_source
    receipt.analysis_sha256 = digest(text.encode("utf-8"))
    receipt.source_equations = source_provenance(text)
    receipt.tool_version = _version()
    receipt.decision = decide_html(
        text,
        available={"pandoc"} if shutil.which("pandoc") else set(),
        versions={"pandoc": receipt.tool_version},
        project=True,
    )
    if receipt.decision.selected_route is None:
        return result(reasons=["package_route_unavailable"])
    receipt.transformations.append(
        Transformation(
            kind="literal_dependency_expansion",
            input_sha256=project.source_digest,
            output_sha256=receipt.analysis_sha256,
        )
    )
    # Inventory/retrieval includes bibliography files, but this HTML profile has
    # no source-bound citation renderer. Do not silently omit a bibliography.
    if any(
        d["command"] in {"bibliography", "addbibresource", "bibliographystyle"}
        for d in project.dependencies
    ):
        return result(reasons=["bibliography_export_unsupported"])
    if len(text.encode("utf-8")) > MAX_OUTPUT:
        return result(reasons=["project_export_limit"])
    semantics = extract_semantics(text)
    if semantics.issues:
        return result(reasons=semantics.issues)
    if any(
        Path(graphic.asset).suffix.lower() not in {".png", ".jpg", ".jpeg"}
        for graphic in semantics.graphics
    ):
        return result(reasons=["semantics_unsupported"])
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="project-html-", dir=output_dir))
    analysis_root = root / "analysis"
    analysis_root.mkdir(mode=0o700)
    # Only inspected assets are materialized. There are no included TeX files
    # available to the converter: it receives the complete analysis view.
    for name, data in project.files.items():
        if Path(name).suffix.lower() in {".png", ".jpg", ".jpeg"}:
            target = analysis_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    with tempfile.NamedTemporaryFile(
        prefix=".analysis-", suffix=".tex", dir=analysis_root, delete=False
    ) as stream:
        source = Path(stream.name)
        stream.write(text.encode("utf-8"))
    output, log = root / "body.html", root / "converter.log"
    code, error = _run_pandoc(source, output, log)
    receipt.tool_version = _version()
    if error:
        return result(reasons=[error])
    if not output.is_file() or not log.is_file():
        return result(reasons=["candidate_missing"])
    if output.stat().st_size > MAX_OUTPUT or log.stat().st_size > MAX_LOG:
        return result(reasons=["project_export_limit"])
    diagnostics = log.read_text(encoding="utf-8", errors="replace")
    # Pandoc reports dropped LaTeX as INFO, even with a successful exit.
    if re.search(r"\[INFO\]\s+Skipped\b", diagnostics):
        return result(reasons=["unsupported_command"])
    # These package commands are handled by Pandoc's LaTeX reader, without
    # loading their TeX implementations. Any other missing package stays a
    # refusal; uploaded/local packages were expanded by the project inspector.
    handled_packages = {
        "graphicx",
        "graphics",
        "amsmath",
        "amssymb",
        "amsfonts",
        "babel",
        "hyperref",
    }
    declared_packages = {
        dependency["requested"]
        for dependency in project.dependencies
        if dependency["resolution"] == "system_profile"
        and dependency["command"] in {"usepackage", "RequirePackage"}
    }
    diagnostics = re.sub(
        r"\[INFO\] Could not load include file ([A-Za-z0-9_-]+)\.sty at [^\n]*\n",
        lambda match: (
            "" if match[1] in handled_packages & declared_packages else match[0]
        ),
        diagnostics,
    )
    # Sandboxed Pandoc cannot load its optional translation data. This profile
    # adds no generated TOC/abstract labels; original metadata is applied below.
    diagnostics = re.sub(
        r"\[WARNING\] Could not load translations for en-US\n\s+data file translations/en.yaml not found\n",
        "",
        diagnostics,
    )
    diagnostics = diagnostics.replace(
        "[WARNING] The term Abstract has no translation defined.\n", ""
    )
    reasons = [
        d.code
        for d in classify("", diagnostics, exit_code=code)
        if d.severity == "error"
    ]
    if reasons:
        return result(reasons=reasons)
    body = output.read_text(encoding="utf-8")
    if not body.strip():
        return result(reasons=["candidate_missing"])
    if not _inactive_html(body):
        return result(reasons=["active_content"])
    candidate = root / "project.html"
    candidate.write_text(
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        + body
        + "</body></html>",
        encoding="utf-8",
    )
    raw_digest = digest(candidate.read_bytes())
    receipt.transformations.append(
        Transformation(
            kind="html_conversion",
            input_sha256=receipt.analysis_sha256,
            output_sha256=raw_digest,
        )
    )
    metadata = extract_metadata(_metadata_source(text))
    if metadata.issues or not save_html_metadata(candidate, metadata):
        return result(reasons=list(metadata.issues) or ["metadata_not_preserved"])
    try:
        preserved = save_html_semantics(source, candidate)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return result(reasons=["missing_asset"])
    if not preserved:
        return result(reasons=["semantics_not_preserved"])
    with conversion_session():
        inspection = inspect_candidate(source, candidate)
    if inspection.blocked:
        return result(
            reasons=[d.code for d in inspection.diagnostics if d.severity == "error"]
        )
    receipt.transformations.append(
        Transformation(
            kind="authored_metadata_and_relationships",
            input_sha256=raw_digest,
            output_sha256=digest(candidate.read_bytes()),
        )
    )
    return result(candidate)
