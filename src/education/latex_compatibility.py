"""Versioned, bounded HTML routing evidence; never a universal package promise."""

from hashlib import sha256
import re
import subprocess
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .latex_metadata import uncomment
from .latex_runtime import version_command
from .latex_semantics import _group

MATRIX_VERSION = "latex-html-controls-v1"
Tool = Literal["latexml", "pandoc"]
Status = Literal["supported", "partial", "unchecked", "observed_failure"]
Version = Annotated[str, Field(pattern=r"^(unknown|[0-9]{1,4}(?:\.[0-9]{1,4}){1,3})$")]
KnownName = Literal[
    "article",
    "amsmath",
    "amssymb",
    "amsfonts",
    "geometry",
    "physics",
    "siunitx",
    "babel",
    "hyperref",
    "graphicx",
    "tagpdf",
]
# Scope is the linked synthetic controls, never every command/options combination.
# Updated only with the required raw/preprocessed compatibility smoke evidence.
MATRIX = {
    "article": ("supported", "supported", "basic-math"),
    "amsmath": ("partial", "partial", "math-corpus"),
    "amssymb": ("partial", "partial", "math-corpus"),
    "amsfonts": ("partial", "partial", "math-corpus"),
    "geometry": ("partial", "partial", "M10-M12-P04"),
    "physics": ("partial", "observed_failure", "M10"),
    "siunitx": ("observed_failure", "partial", "M12"),
    "babel": ("partial", "partial", "metadata-language"),
    "hyperref": ("partial", "partial", "metadata-language"),
    "graphicx": ("observed_failure", "partial", "authored-relationships"),
    "tagpdf": ("observed_failure", "partial", "authored-relationships"),
}
MEASURED_VERSIONS = {"latexml": {"0.8.8"}, "pandoc": {"3.1.11.1"}}
MAX_SOURCE = 8 * 1024 * 1024
MAX_REQUIREMENTS = 64


class Requirement(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )
    kind: Literal["class", "package", "macro"]
    name_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    declaration_prefix_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    known_name: KnownName | None = None
    latexml: Status = "unchecked"
    pandoc: Status = "unchecked"
    evidence: Literal[
        "none",
        "basic-math",
        "math-corpus",
        "M10-M12-P04",
        "M10",
        "M12",
        "metadata-language",
        "authored-relationships",
    ] = "none"


class ConversionDecision(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )
    schema_version: Literal[1] = 1
    matrix_version: Literal["latex-html-controls-v1"] = MATRIX_VERSION
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selected_route: Tool | None = None
    profile: Literal["single-source-html", "sandboxed-project-html"]
    requirements: list[Requirement] = Field(
        default_factory=list, max_length=MAX_REQUIREMENTS
    )
    tool_versions: dict[Tool, Version] = Field(default_factory=dict, max_length=2)
    reasons: list[
        Literal[
            "partial_support",
            "unchecked_requirements",
            "unmeasured_tool_version",
            "authored_relationship_route",
            "measured_package_route",
            "diagnostic_probe_route",
            "package_route_unavailable",
            "requirements_conflict",
            "project_sandbox_required",
            "tool_unavailable",
            "requirements_limit",
            "requirements_unparsed",
        ]
    ] = Field(default_factory=list, max_length=12)
    fallback_policy: Literal["no_retry_after_selected_route"] = (
        "no_retry_after_selected_route"
    )
    support_scope: Literal["declared-synthetic-controls-only"] = (
        "declared-synthetic-controls-only"
    )
    fidelity: Literal["not_assessed"] = "not_assessed"


def digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def tool_versions(available):
    result = {}
    for tool in ("latexml", "pandoc"):
        if tool not in available:
            continue
        result[tool] = "unknown"
        try:
            probe = subprocess.run(
                version_command(tool), capture_output=True, text=True, timeout=5
            )
            match = re.search(
                r"\b([0-9]{1,4}(?:\.[0-9]{1,4}){1,3})\b",
                (probe.stdout or "")[:4096] + (probe.stderr or "")[:4096],
            )
            if probe.returncode == 0 and match:
                result[tool] = match[1]
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def _requirements(source, versions):
    if len(source.encode("utf-8")) > MAX_SOURCE:
        return [], "requirements_limit"
    text = uncomment(source)
    found = []
    pattern = r"\\(documentclass|LoadClass|usepackage|RequirePackage|newcommand|renewcommand|providecommand|DeclareMathOperator|def|let)\b"
    for match in re.finditer(pattern, text):
        if len(found) >= MAX_REQUIREMENTS:
            return found, "requirements_limit"
        command, pos = match[1], match.end()
        kind = "class" if command in {"documentclass", "LoadClass"} else "package"
        if command in {
            "newcommand",
            "renewcommand",
            "providecommand",
            "DeclareMathOperator",
            "def",
            "let",
        }:
            kind = "macro"
        while pos < len(text) and (text[pos].isspace() or text[pos] == "*"):
            pos += 1
        try:
            if pos < len(text) and text[pos] == "[":
                _, pos = _group(text, pos, "[", "]")
            if kind == "macro" and pos < len(text) and text[pos] == "\\":
                name_match = re.match(r"\\[A-Za-z]+", text[pos:])
                if not name_match:
                    return found, "requirements_unparsed"
                names, end = name_match[0], pos + name_match.end()
            else:
                names, end = _group(text, pos)
        except ValueError:
            return found, "requirements_unparsed"
        for name in names.split(",") if kind == "package" else [names]:
            if len(found) >= MAX_REQUIREMENTS:
                return found, "requirements_limit"
            name = name.strip()
            known = (
                name
                if (
                    (kind == "class" and name == "article")
                    or (kind == "package" and name != "article" and name in MATRIX)
                )
                else None
            )
            statuses = MATRIX.get(known, ("unchecked", "unchecked", "none"))
            found.append(
                Requirement(
                    kind=kind,
                    name_sha256=digest(name),
                    declaration_prefix_sha256=digest(text[match.start() : end]),
                    known_name=known,
                    latexml=(
                        statuses[0]
                        if versions.get("latexml") in MEASURED_VERSIONS["latexml"]
                        else "unchecked"
                    ),
                    pandoc=(
                        statuses[1]
                        if versions.get("pandoc") in MEASURED_VERSIONS["pandoc"]
                        else "unchecked"
                    ),
                    evidence=statuses[2],
                )
            )
    return found, None


def decide_html(source, *, available, versions, prefer_pandoc=False, project=False):
    requirements, error = _requirements(source, versions)
    reasons = []
    selected = None
    names = {r.known_name for r in requirements}
    physics = "physics" in names
    requires_pandoc = "siunitx" in names or (
        "babel" in names and bool(re.search(r"\\selectlanguage\b", uncomment(source)))
    )
    prefer_pandoc = prefer_pandoc or requires_pandoc
    if error:
        reasons.append(error)
    elif physics and (prefer_pandoc or project):
        reasons.append("requirements_conflict")
    elif physics:
        selected = "latexml" if "latexml" in available else None
        reasons.append(
            "measured_package_route" if selected else "package_route_unavailable"
        )
    elif prefer_pandoc or project:
        selected = "pandoc" if "pandoc" in available else None
        reasons.append(
            "measured_package_route"
            if requires_pandoc
            else (
                "authored_relationship_route"
                if prefer_pandoc
                else "diagnostic_probe_route"
            )
        )
        if selected is None:
            reasons.append("package_route_unavailable")
    else:
        selected = next(
            (tool for tool in ("latexml", "pandoc") if tool in available), None
        )
        reasons.append("diagnostic_probe_route")
    if project:
        reasons.append("project_sandbox_required")
    if not available:
        reasons.append("tool_unavailable")
    if any(
        version not in MEASURED_VERSIONS[tool] for tool, version in versions.items()
    ):
        reasons.append("unmeasured_tool_version")
    if any("unchecked" in (r.latexml, r.pandoc) for r in requirements):
        reasons.append("unchecked_requirements")
    if any("partial" in (r.latexml, r.pandoc) for r in requirements):
        reasons.append("partial_support")
    return ConversionDecision(
        source_sha256=digest(source),
        selected_route=selected,
        profile="sandboxed-project-html" if project else "single-source-html",
        requirements=requirements,
        tool_versions=versions,
        reasons=list(dict.fromkeys(reasons)),
    )
