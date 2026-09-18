"""Conversion and source checks cannot certify fidelity or reader access."""

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.education.latex_evidence import (
    conversion_evidence,
    public_latex_evidence,
    public_scan_structure,
)
from src.education.latex_processor import LaTeXProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.latex_remediator import LatexRemediator

ROOT = Path(__file__).parent / "fixtures/latex_validation"
CASES = json.loads((ROOT / "corpus.json").read_text())["cases"]


def assert_unverified(evidence):
    assert evidence["accessibility_status"] == "not_verified"
    assert evidence["human_review_required"] is True
    for stage in ("fidelity", "human_review", "assistive_technology"):
        assert evidence[stage] == {
            "status": "not_assessed",
            "method": "none",
            "findings_count": None,
        }


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_corpus_real_processor_never_certifies_conversion(tmp_path, case):
    data = (ROOT / case["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == case["sha256"]
    path = tmp_path / case["file"]
    path.write_bytes(data)
    processor = LaTeXProcessor(use_ai=False)
    result = processor.process_document(str(path))
    raw = await processor.process_latex(data.decode())
    assert result.compliance_score == raw["compliance"]["score"]
    assert (
        result.compliance_score
        == processor.scan_source(data.decode())["compliance_score"]
    )
    evidence = public_latex_evidence(result.latex_evidence)
    assert evidence["tex"]["candidate_sha256"] == case["sha256"]
    assert evidence["tex"]["source_check"]["findings_count"] == len(
        result.source_issues
    )
    assert (
        evidence["html"]["candidate_sha256"]
        == hashlib.sha256(result.html_output.encode()).hexdigest()
    )
    for receipt in evidence.values():
        assert_unverified(receipt)
    for equation in result.equations:
        assert equation.wcag_compliant is False
        assert_unverified(public_latex_evidence(equation.latex_evidence)["mathml"])
    assert path.read_bytes() == data


@pytest.mark.parametrize(
    "text",
    [
        "$x+1$",
        r"$\unsupportedmacro{x}$",
        r"\begin{unknownmath}x\end{unknownmath}",
        "No equations",
    ],
)
def test_success_unsupported_and_missed_equations_are_not_certification(tmp_path, text):
    path = tmp_path / "source.tex"
    path.write_text(text)
    result = LaTeXProcessor(use_ai=False).process_document(str(path))
    if text == "$x+1$":
        assert result.successful_conversions == 1  # positive converter control
    if text == "No equations":
        assert result.total_equations == 0
    assert_unverified(public_latex_evidence(result.latex_evidence)["html"])
    assert all(eq.wcag_compliant is False for eq in result.equations)


def test_provenance_preserves_crlf_source_bytes(tmp_path):
    path = tmp_path / "source.tex"
    data = b"$x$\r\nText\r\n"
    path.write_bytes(data)
    result = LaTeXProcessor(use_ai=False).process_document(str(path))
    assert (
        result.latex_evidence["tex"].candidate_sha256
        == hashlib.sha256(data).hexdigest()
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"accessibility_status": "passed"},
        {"human_review_required": False},
        {"fidelity": {"status": "passed"}},
        {"human_review": {"status": "completed"}},
        {"assistive_technology": {"status": "passed"}},
        {
            "source_check": {
                "status": "completed",
                "method": "latex-source-v1",
                "findings_count": 0,
            }
        },
        {
            "structural_validation": {
                "status": "passed",
                "method": "pikepdf+veraPDF/ua1",
            }
        },
        {"candidate_sha256": None},
        {"internal_path": "/private/not-public"},
    ],
)
def test_public_receipt_rejects_unsupported_claims(mutation):
    receipt = conversion_evidence(
        b"x", b"<math/>", "mathml", method="latex2mathml"
    ).model_dump()
    assert public_latex_evidence({"mathml": receipt})  # sanitizer success control
    assert public_latex_evidence({"mathml": {**receipt, **mutation}}) == {}


def test_both_scan_storage_paths_preserve_source_findings(tmp_path):
    from src.db.scan_service import ScanService

    path = tmp_path / "source.tex"
    data = b"$x+1$"
    path.write_bytes(data)
    result = LaTeXProcessor(use_ai=False).process_document(str(path))
    db = MagicMock()
    ScanService.store_latex_scan(db, result, "user", "department", data)
    stored = db.add.call_args_list[-1].args[0]
    assert stored.issues == result.source_issues
    assert stored.compliance_score == result.compliance_score
    assert (
        stored.structure["conversion_issues"][0]["type"] == "accessibility_not_verified"
    )
    assert stored.structure["equations"][0]["wcag_compliant"] is False
    assert_unverified(stored.structure["latex_evidence"]["html"])


def test_legacy_scan_projection_does_not_mutate_storage_or_certify():
    old = {"equations": [{"wcag_compliant": True, "conversion_success": True}]}
    projected = public_scan_structure(old, "latex")
    assert projected["equations"][0]["wcag_compliant"] is False
    assert projected["latex_evidence"] == {}
    assert projected["accessibility_status"] == "not_verified"
    assert old["equations"][0]["wcag_compliant"] is True
    assert public_scan_structure(old, "pdf") is old


def test_saved_tex_source_check_cannot_be_used_for_html(tmp_path):
    source = tmp_path / "source.tex"
    source.write_text(r"\documentclass{article}\begin{document}Text\end{document}")
    remediator = LatexRemediator(str(source), [], RemediationConfig(use_ai=False))
    remediator._load_document()
    output = remediator._save_document(remediator._modified_content)
    remediator._verify_fixes(output)
    tex = public_latex_evidence(remediator.result.latex_evidence)["tex"]
    assert tex["source_check"]["status"] == "completed"
    assert tex["source_check"]["findings_count"] > 0
    assert_unverified(tex)
    html = tmp_path / "output.html"
    html.write_text("<p>Text</p>")
    remediator._verify_fixes(str(html))
    receipt = public_latex_evidence(remediator.result.latex_evidence)["html"]
    assert receipt["source_check"]["status"] == "not_assessed"
    assert_unverified(receipt)
    assert remediator.result.fixed_count == 0


def test_passing_pdf_machine_checks_still_require_fidelity_and_reader_review(
    tmp_path, monkeypatch
):
    from src.education.remediation import latex_remediator
    from src.education.remediation.latex_pdf_validation import LatexPDFValidation

    source = tmp_path / "source.tex"
    source.write_text("Text")
    candidate = b"synthetic byte-bound validator control"

    class Converter:
        def convert_all_formats(
            self,
            path,
            formats,
            output_dir,
            *,
            validation_receipts,
            conversion_receipts=None,
        ):
            pdf = Path(path).with_suffix(".pdf")
            pdf.write_bytes(candidate)
            validation_receipts["pdf"] = LatexPDFValidation(
                status="passed",
                reason="checks_passed",
                candidate_sha256=hashlib.sha256(candidate).hexdigest(),
                structure_status="passed",
                independent_status="passed",
            )
            return {"pdf": str(pdf)}

    monkeypatch.setattr(latex_remediator, "get_latex_converter", Converter)
    remediator = LatexRemediator(
        str(source), [], RemediationConfig(use_ai=False, latex_output_formats=["pdf"])
    )
    result = remediator.remediate()
    evidence = public_latex_evidence(result.latex_evidence)["pdf"]
    assert evidence["structural_validation"]["status"] == "passed"
    assert evidence["source_check"]["status"] == "not_assessed"
    assert_unverified(evidence)
    assert (
        result.verification_passed is False
    )  # PDF check cannot become source verification


def test_job_failure_and_compatibility_response_preserve_only_safe_evidence():
    from src.jobs.contracts import public_job_result
    from src.jobs.remediation_job import _safe_failure_result
    from src.api.education.remediation_routes import _legacy_completed_result

    receipt = conversion_evidence(b"x", None, "pdf").model_dump(mode="json")
    result = {"latex_evidence": {"pdf": receipt}, "fixed_count": 1, "total_issues": 1}
    assert public_job_result(result)["latex_evidence"] == result["latex_evidence"]
    safe = _safe_failure_result("remediation_failed", result, None)
    assert safe["fixed_count"] == 0
    assert safe["latex_evidence"] == result["latex_evidence"]
    response = _legacy_completed_result(
        {"status": "failed", "result_data": safe}, scan_id="scan", job_id="job"
    )
    assert response["latex_evidence"] == result["latex_evidence"]
    assert "latex_evidence" not in public_job_result({"success": False})


@pytest.mark.parametrize("mathml", ["", " "])
def test_empty_converter_output_is_not_counted_successful(monkeypatch, mathml):
    monkeypatch.setattr(
        "src.education.latex_processor.latex_to_mathml", lambda _: mathml
    )
    processor = LaTeXProcessor(use_ai=False)
    result = processor.convert_equation(processor.detect_equations("$x$")[0])
    assert result.conversion_success is False
    assert result.error_message == "empty_mathml_output"
    assert result.latex_evidence["mathml"].conversion.status == "failed"


def test_public_receipt_revalidates_model_instances():
    receipt = conversion_evidence(b"x", b"<math/>", "mathml")
    invalid = receipt.model_copy(update={"accessibility_status": "passed"})
    assert public_latex_evidence({"mathml": invalid}) == {}


def test_disabled_source_verification_and_changed_candidate_cannot_claim_check(
    tmp_path,
):
    source = tmp_path / "source.tex"
    source.write_text("Text")
    remediator = LatexRemediator(
        str(source), [], RemediationConfig(use_ai=False, verify_fixes=False)
    )
    result = remediator.remediate()
    assert result.latex_evidence["tex"].source_check.status == "not_assessed"
    remediator._verify_fixes(result.output_file)
    assert result.latex_evidence["tex"].source_check.status == "completed"
    Path(result.output_file).write_text("Changed after check")
    remediator._record_evidence(source_checked=True)
    assert result.latex_evidence["tex"].source_check.status == "unavailable"


@pytest.mark.parametrize(
    "reason,status,conversion",
    [
        ("conversion_failed", "failed", "failed"),
        ("no_converter", "unavailable", "unavailable"),
    ],
)
def test_pdf_conversion_failure_is_distinct_from_missing_converter(
    tmp_path, reason, status, conversion
):
    from src.education.remediation.latex_pdf_validation import LatexPDFValidation

    source = tmp_path / "source.tex"
    source.write_text("Text")
    remediator = LatexRemediator(str(source), [], RemediationConfig(use_ai=False))
    remediator.result.latex_pdf_validation = LatexPDFValidation(
        status=status, reason=reason
    )
    remediator._record_evidence()
    receipt = remediator.result.latex_evidence["pdf"]
    assert receipt.conversion.status == conversion
    assert receipt.structural_validation.status == "not_assessed"
    assert receipt.source_check.status == "not_assessed"
