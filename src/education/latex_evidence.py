"""Representation-specific LaTeX evidence, never an accessibility certificate."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .latex_diagnostics import ConversionDiagnostics
from .latex_descriptions import LatexDescriptionEvidence

Representation = Literal["tex", "mathml", "html", "pdf", "docx"]


class LatexCheck(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )

    status: Literal[
        "not_assessed", "unknown", "unavailable", "completed", "passed", "failed"
    ] = "not_assessed"
    method: Literal[
        "none",
        "latex2mathml",
        "latex-source-v1",
        "latex-export-v1",
        "pikepdf+veraPDF/ua1",
    ] = "none"
    findings_count: int | None = Field(default=None, ge=0, strict=True)


class LatexRepresentationEvidence(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )

    schema_version: Literal[1] = 1
    representation: Representation
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    conversion: LatexCheck = Field(default_factory=LatexCheck)
    structural_validation: LatexCheck = Field(default_factory=LatexCheck)
    source_check: LatexCheck = Field(default_factory=LatexCheck)
    fidelity: LatexCheck = Field(default_factory=LatexCheck)
    human_review: LatexCheck = Field(default_factory=LatexCheck)
    assistive_technology: LatexCheck = Field(default_factory=LatexCheck)
    conversion_diagnostics: ConversionDiagnostics | None = None
    description: LatexDescriptionEvidence | None = None
    accessibility_status: Literal["not_verified"] = "not_verified"
    human_review_required: Literal[True] = True

    @model_validator(mode="after")
    def enforce_evidence_boundaries(self):
        if self.description is not None and (
            self.representation != "mathml"
            or self.description.source_sha256 != self.source_sha256
        ):
            raise ValueError("Description evidence must match the equation source")
        for check in (self.fidelity, self.human_review, self.assistive_technology):
            if check != LatexCheck():
                raise ValueError("No fidelity, human or AT verifier is implemented")
        if self.conversion.status not in {
            "completed",
            "failed",
            "not_assessed",
            "unavailable",
            "unknown",
        }:
            raise ValueError("Conversion cannot establish a validation pass")
        if self.conversion.status == "completed" and (
            self.candidate_sha256 is None
            or self.conversion.method not in {"latex2mathml", "latex-export-v1"}
        ):
            raise ValueError("Completed conversion requires candidate provenance")
        if self.source_check.status == "completed" and (
            self.representation != "tex"
            or self.candidate_sha256 is None
            or self.source_check.method != "latex-source-v1"
            or self.source_check.findings_count is None
        ):
            raise ValueError("Source checks require saved TEX evidence")
        if self.source_check.status in {"passed", "failed"}:
            raise ValueError("A source check reports findings, not conformance")
        if self.structural_validation.status in {"passed", "failed"} and (
            self.representation != "pdf"
            or self.candidate_sha256 is None
            or self.structural_validation.method != "pikepdf+veraPDF/ua1"
        ):
            raise ValueError("Structural validation requires PDF provenance")
        if self.structural_validation.status == "completed":
            raise ValueError("Structural validation must report a bounded outcome")
        return self


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def conversion_evidence(
    source,
    candidate,
    representation,
    *,
    method="latex-export-v1",
    status=None,
    description=None,
):
    """Record generation only; candidate presence says nothing about fidelity."""
    return LatexRepresentationEvidence(
        representation=representation,
        source_sha256=digest(source),
        candidate_sha256=digest(candidate) if candidate is not None else None,
        conversion=LatexCheck(
            status=status or ("completed" if candidate else "failed"), method=method
        ),
        description=description,
    )


def source_evidence(source, candidate, findings_count):
    return LatexRepresentationEvidence(
        representation="tex",
        source_sha256=digest(source),
        candidate_sha256=digest(candidate),
        source_check=LatexCheck(
            status="completed", method="latex-source-v1", findings_count=findings_count
        ),
    )


def public_latex_evidence(value):
    """Bound public data; malformed records cannot supply positive claims."""
    if not isinstance(value, dict):
        return {}
    safe = {}
    for kind in ("tex", "mathml", "html", "pdf", "docx"):
        try:
            receipt = LatexRepresentationEvidence.model_validate(value.get(kind))
            if receipt.representation == kind:
                safe[kind] = receipt.model_dump(mode="json")
        except (ValidationError, TypeError, ValueError):
            continue
    return safe


def latex_evidence_fields(result):
    value = (
        result.get("latex_evidence")
        if isinstance(result, dict)
        else getattr(result, "latex_evidence", None)
    )
    safe = public_latex_evidence(value)
    return {"latex_evidence": safe} if safe else {}


def equation_diagnostics(equations):
    """Unknown accessibility is review work, not a fabricated missing-label defect."""
    return [
        {
            "equation_id": eq.equation_id,
            "type": (
                "conversion_failed"
                if not eq.conversion_success
                else "accessibility_not_verified"
            ),
            "severity": "high" if not eq.conversion_success else "info",
            "reason": (
                "conversion_failed"
                if not eq.conversion_success
                else "fidelity_and_reader_checks_not_run"
            ),
        }
        for eq in equations
    ]


def scan_structure(result):
    return {
        "total_equations": result.total_equations,
        "successful_conversions": result.successful_conversions,
        "failed_conversions": result.failed_conversions,
        "conversion_success_rate": result.conversion_success_rate,
        "conversion_scope": "detected_equations_only",
        "conversion_issues": equation_diagnostics(result.equations),
        "accessibility_issues_found": len(result.source_issues),
        **latex_evidence_fields(result),
        "equations": [
            {
                "equation_id": eq.equation_id,
                "latex_source": eq.latex_source[:100],
                "latex_source_is_preview": len(eq.latex_source) > 100,
                "latex_source_chars": len(eq.latex_source),
                "conversion_success": eq.conversion_success,
                "wcag_compliant": False,
                "aria_label": None,
                **latex_evidence_fields(eq),
            }
            for eq in result.equations
        ],
    }


def public_scan_structure(value, scan_type):
    """Bound legacy media and LaTeX claims without rewriting stored scans."""
    from .current_compliance import is_retired_media_flash_issue

    kind = getattr(scan_type, "value", scan_type)
    if isinstance(kind, str) and kind.lower() in {"video", "multimedia"}:
        safe = dict(value) if isinstance(value, dict) else {}
        safe.pop("flashing_analysis", None)
        media_type = safe.get("media_type")
        prior_assessment = safe.get("flashing_assessment")
        if media_type == "audio":
            safe["flashing_assessment"] = {
                "state": "not_applicable",
                "version": "unassessed-v1",
            }
        else:
            assessment = prior_assessment
            reason = assessment.get("reason") if isinstance(assessment, dict) else None
            safe["flashing_assessment"] = {
                "state": "unassessed",
                "reason": (
                    reason
                    if reason in {"detector_unavailable", "disabled", "analysis_failed"}
                    else "detector_unavailable"
                ),
                "version": "unassessed-v1",
            }
        output = safe.get("transcription_output")
        if isinstance(output, dict):
            safe_output = dict(output)
            safe_output.pop("flashing_analysis", None)
            issues = safe_output.get("issues")
            if isinstance(issues, list):
                safe_issues = [
                    issue
                    for issue in issues
                    if not is_retired_media_flash_issue(scan_type, issue)
                ]
                safe_output["issues"] = safe_issues
                safe_output["issues_count"] = len(safe_issues)
            elif safe_output.get("issues_count"):
                safe_output["issues_count"] = None
            if media_type != "audio" or not (
                isinstance(prior_assessment, dict)
                and prior_assessment.get("state") == "not_applicable"
                and prior_assessment.get("version") == "unassessed-v1"
            ):
                safe_output["compliance_score"] = None
            safe_output["flashing_assessment"] = safe["flashing_assessment"]
            safe["transcription_output"] = safe_output
        return safe
    if (
        not isinstance(kind, str)
        or kind.lower() != "latex"
        or not isinstance(value, dict)
    ):
        return value
    safe = dict(value)
    safe["accessibility_status"] = "not_verified"
    safe["human_review_required"] = True
    safe["latex_evidence"] = public_latex_evidence(value.get("latex_evidence"))
    equations = value.get("equations")
    equations = equations if isinstance(equations, list) else []
    safe["equations"] = [
        {
            **eq,
            # Legacy generated descriptions have no semantic verification.
            "aria_label": None,
            "description_review_required": True,
            "latex_source_is_preview": eq.get("latex_source_is_preview", True),
            "wcag_compliant": False,
            "latex_evidence": public_latex_evidence(eq.get("latex_evidence")),
        }
        for eq in equations
        if isinstance(eq, dict)
    ]
    return safe
