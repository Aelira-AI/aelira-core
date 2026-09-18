"""Bounded machine checks for generated PDFs; not a conformance certificate."""

import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class LatexPDFValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["passed", "failed", "unavailable"]
    reason: Literal[
        "checks_passed",
        "candidate_unreadable",
        "structure_validator_unavailable",
        "structure_check_failed",
        "independent_validator_disabled",
        "independent_validator_unavailable",
        "independent_check_failed",
        "candidate_changed",
        "conversion_failed",
        "no_converter",
    ]
    candidate_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    validator: Literal["pikepdf+veraPDF"] = "pikepdf+veraPDF"
    profile: Literal["ua1"] = "ua1"
    structure_profile: Literal["latex-pdf-content-references-v1"] = (
        "latex-pdf-content-references-v1"
    )
    structure_status: Literal["passed", "failed", "unavailable"] = "unavailable"
    independent_status: Literal["passed", "failed", "unavailable"] = "unavailable"
    human_review_required: Literal[True] = True

    @model_validator(mode="after")
    def require_complete_pass(self):
        if self.status == "passed" and not self.accepted:
            raise ValueError("passed validation requires complete byte-bound evidence")
        if self.status != "passed" and self.reason == "checks_passed":
            raise ValueError("checks_passed requires passed status")
        return self

    @property
    def accepted(self):
        return (
            self.status == "passed"
            and self.reason == "checks_passed"
            and self.candidate_sha256 is not None
            and self.structure_status == self.independent_status == "passed"
        )


def public_pdf_validation(value):
    """Allow only bounded validation fields across persistence/API boundaries."""
    if value is None:
        return None
    try:
        return LatexPDFValidation.model_validate(value).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        return None


def inspect_pdf_structure(data):
    """Check real page/marked-content references, leaving full rules to veraPDF."""
    try:
        import pikepdf
    except ImportError:
        return "unavailable"
    try:
        with pikepdf.open(BytesIO(data)) as pdf:
            if not str(pdf.Root.get("/Lang", "")).strip():
                return "failed"
            if not pdf.Root.get("/MarkInfo", {}).get("/Marked", False):
                return "failed"
            root = pdf.Root.get("/StructTreeRoot")
            if not isinstance(root, pikepdf.Dictionary):
                return "failed"
            page_mcids = {}
            for page in pdf.pages:
                ids = set()
                for operands, operator in pikepdf.parse_content_stream(page):
                    if str(operator) == "BDC" and len(operands) == 2:
                        props = operands[1]
                        if isinstance(props, pikepdf.Name):
                            props = (
                                page.get("/Resources", {})
                                .get("/Properties", {})
                                .get(props, {})
                            )
                        if isinstance(props, pikepdf.Dictionary):
                            mcid = props.get("/MCID")
                            if type(mcid) is int and mcid >= 0:
                                ids.add(mcid)
                page_mcids[page.obj.objgen] = ids
            visited = set()
            budget = [10000]

            def walk(node, page=None, depth=0):
                budget[0] -= 1
                if depth > 64 or budget[0] < 0:
                    return False
                if type(node) is int:
                    return (
                        node >= 0
                        and page is not None
                        and node in page_mcids.get(page.objgen, set())
                    )
                if isinstance(node, pikepdf.Array):
                    return len(node) > 0 and all(
                        walk(child, page, depth + 1) for child in node
                    )
                if not isinstance(node, pikepdf.Dictionary):
                    return False
                if node.is_indirect:
                    if node.objgen in visited:
                        return False
                    visited.add(node.objgen)
                page = node.get("/Pg", page)
                if node.get("/Type") == pikepdf.Name.MCR:
                    return walk(node.get("/MCID"), page, depth + 1)
                return walk(node.get("/K"), page, depth + 1)

            return "passed" if walk(root.get("/K")) else "failed"
    except Exception:
        return "failed"


def validate_pdf_candidate(path, *, independent=None):
    """Validate a private byte snapshot and reject changes to either copy."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return LatexPDFValidation(status="failed", reason="candidate_unreadable")
    digest = hashlib.sha256(data).hexdigest()
    structure = inspect_pdf_structure(data)
    fields = {"candidate_sha256": digest, "structure_status": structure}
    if structure != "passed":
        return LatexPDFValidation(
            **fields,
            status=structure,
            reason=(
                "structure_validator_unavailable"
                if structure == "unavailable"
                else "structure_check_failed"
            ),
        )
    try:
        if independent is None:
            from ...config.settings import get_settings
            from ..validation.verapdf import VeraPDFValidator

            if not get_settings().verapdf_enabled:
                return LatexPDFValidation(
                    **fields,
                    status="unavailable",
                    reason="independent_validator_disabled",
                )
            independent = VeraPDFValidator(flavour="ua1")
        with tempfile.TemporaryDirectory(prefix="aelira-pdf-validation-") as folder:
            snapshot = Path(folder) / "candidate.pdf"
            snapshot.write_bytes(data)
            result = independent.validate(str(snapshot))
            if snapshot.read_bytes() != data or Path(path).read_bytes() != data:
                return LatexPDFValidation(
                    **fields, status="failed", reason="candidate_changed"
                )
        if (
            result.profile_name not in {"PDF/UA-1", "PDF/UA-1 validation profile"}
            or result.compliant is not True
            or result.failed_rules != 0
            or result.failed_checks != 0
            or result.passed_checks <= 0
        ):
            return LatexPDFValidation(
                **fields,
                status="failed",
                reason="independent_check_failed",
                independent_status="failed",
            )
        return LatexPDFValidation(
            **fields,
            status="passed",
            reason="checks_passed",
            independent_status="passed",
        )
    except Exception:
        return LatexPDFValidation(
            **fields, status="unavailable", reason="independent_validator_unavailable"
        )


def latex_result_fields(result):
    """Optional API fields for a remediator result, preserving older shapes."""
    receipt = public_pdf_validation(getattr(result, "latex_pdf_validation", None))
    from ..latex_evidence import latex_evidence_fields

    return {
        **({"latex_pdf_validation": receipt} if receipt is not None else {}),
        **latex_evidence_fields(result),
    }


# Retain the previous helper for Python integrations.
pdf_validation_fields = latex_result_fields
