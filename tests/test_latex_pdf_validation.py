"""PDF checks are evidence about exact bytes, never inferred conformance."""

import builtins
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pikepdf
import pytest

from src.education.remediation.latex_pdf_validation import validate_pdf_candidate


@pytest.fixture
def candidate(tmp_path):
    path = tmp_path / "candidate.pdf"
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page()
        page.Contents = pdf.make_stream(b"/P <</MCID 0>> BDC EMC")
        pdf.Root.Lang = "en"
        pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
        pdf.Root.StructTreeRoot = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.StructTreeRoot,
                K=pikepdf.Array(
                    [
                        pdf.make_indirect(
                            pikepdf.Dictionary(
                                Type=pikepdf.Name.StructElem,
                                S=pikepdf.Name.P,
                                Pg=page.obj,
                                K=0,
                            )
                        )
                    ]
                ),
            )
        )
        pdf.save(path)
    return path


class IndependentValidator:
    def validate(self, path):
        assert Path(path).is_file()
        return SimpleNamespace(
            compliant=True,
            profile_name="PDF/UA-1",
            failed_rules=0,
            failed_checks=0,
            passed_checks=1,
        )


def test_positive_control_binds_bytes_and_preserves_human_review(candidate):
    result = validate_pdf_candidate(candidate, independent=IndependentValidator())
    assert result.status == "passed"
    assert result.accepted
    assert result.candidate_sha256 == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert result.human_review_required is True
    assert result.profile == "ua1"


def test_missing_pikepdf_is_unavailable(candidate, monkeypatch):
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "pikepdf":
            raise ImportError("synthetic missing dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    result = validate_pdf_candidate(candidate, independent=IndependentValidator())
    assert result.status == "unavailable"
    assert result.reason == "structure_validator_unavailable"
    assert not result.accepted


@pytest.mark.parametrize(
    "damage", ["malformed", "untagged", "no-content", "wrong-mcid", "fake-k"]
)
def test_saved_pdf_negative_controls(candidate, damage):
    if damage == "malformed":
        candidate.write_bytes(b"not a PDF")
    else:
        with pikepdf.open(candidate, allow_overwriting_input=True) as pdf:
            if damage == "untagged":
                del pdf.Root.StructTreeRoot
            elif damage == "no-content":
                del pdf.Root.StructTreeRoot.K[0].K
            elif damage == "wrong-mcid":
                pdf.Root.StructTreeRoot.K[0].K = 42
            else:
                pdf.Root.StructTreeRoot.K[0].K = pikepdf.String(
                    "not a content reference"
                )
            pdf.save(candidate)
    result = validate_pdf_candidate(candidate, independent=IndependentValidator())
    assert result.status == "failed"
    assert not result.accepted


@pytest.mark.parametrize("outcome", ["disabled", "error", "failed", "empty", "changed"])
def test_independent_validation_cannot_fail_open(candidate, monkeypatch, outcome):
    class Validator(IndependentValidator):
        def validate(self, path):
            if outcome == "error":
                raise RuntimeError("private diagnostic must not be returned")
            result = super().validate(path)
            if outcome == "failed":
                result.compliant = False
            elif outcome == "empty":
                result.passed_checks = 0
            elif outcome == "changed":
                Path(path).write_bytes(b"changed during validation")
            return result

    if outcome == "disabled":
        from src.config import settings

        monkeypatch.setattr(
            settings, "get_settings", lambda: SimpleNamespace(verapdf_enabled=False)
        )
    result = validate_pdf_candidate(
        candidate, independent=None if outcome == "disabled" else Validator()
    )
    assert not result.accepted
    assert result.status in {"failed", "unavailable"}
    assert "private diagnostic" not in result.model_dump_json()


@pytest.mark.parametrize("exporter", ["lualatex", "latexml", "pdflatex"])
@pytest.mark.parametrize("valid", [True, False])
def test_each_exporter_has_same_gate(candidate, monkeypatch, exporter, valid):
    from src.education.remediation import latex_converter as module
    from src.education.remediation.latex_pdf_validation import validate_pdf_candidate

    converter = module.LaTeXConverter()
    converter.ALLOWED_DIRS = [str(candidate.parent)]
    for name in ("lualatex", "latexml", "pdflatex"):
        setattr(converter, f"{name}_available", name == exporter)
    source = candidate.with_suffix(".tex")
    source.write_text(r"\documentclass{article}\begin{document}x\end{document}")
    data = candidate.read_bytes() if valid else b"not a PDF"
    attempts = []

    def generate(tex, output):
        attempts.append(output)
        path = output / "candidate.pdf"
        assert not path.exists()
        path.write_bytes(data)
        return str(path)

    monkeypatch.setattr(converter, "_convert_with_lualatex", generate)
    monkeypatch.setattr(converter, "_convert_with_pdflatex", generate)

    def generate_html(tex, output):
        html = output / "fixture.html"
        html.write_text("<html><body>Fixture</body></html>")
        return str(html)

    monkeypatch.setattr(converter, "_convert_with_latexml", generate_html)

    def browser_double(html, path):
        generate(str(source), Path(path).parent)
        return True

    monkeypatch.setattr(converter, "_html_to_pdf_playwright_sync", browser_double)
    monkeypatch.setattr(
        module,
        "validate_pdf_candidate",
        lambda path: validate_pdf_candidate(path, independent=IndependentValidator()),
    )
    receipts = {}
    outputs = converter.convert_all_formats(
        str(source), ["tex", "pdf"], validation_receipts=receipts
    )
    assert bool(outputs["pdf"]) is valid
    assert receipts["pdf"].accepted is valid
    assert outputs["tex"] == str(source)
    assert attempts[0] != source.parent
    assert candidate.read_bytes() != b"not a PDF"  # stale sibling was not reused
    assert not hasattr(converter, "last_validation")


@pytest.mark.parametrize("engine", ["lualatex", "pdflatex"])
def test_nonzero_compile_cannot_return_partial_or_stale_pdf(
    candidate, monkeypatch, engine
):
    from src.education.remediation.latex_converter import LaTeXConverter
    import subprocess

    source = candidate.with_suffix(".tex")
    source.write_text("synthetic invalid source")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    converter = LaTeXConverter()
    assert (
        getattr(converter, f"_convert_with_{engine}")(str(source), source.parent)
        is None
    )


def test_structural_checker_exception_and_legacy_predicate_fail_closed(
    candidate, monkeypatch
):
    from src.education.remediation.latex_converter import LaTeXConverter

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic parser failure")

    monkeypatch.setattr(pikepdf, "parse_content_stream", broken)
    assert not LaTeXConverter()._verify_pdf_ua_structure(str(candidate))
    result = validate_pdf_candidate(candidate, independent=IndependentValidator())
    assert result.reason == "structure_check_failed"


@pytest.mark.parametrize(
    "change", ["missing-digest", "wrong-profile", "raw-error", "inconsistent-pass"]
)
def test_public_receipt_rejects_unbounded_or_inconsistent_data(candidate, change):
    from src.education.remediation.latex_pdf_validation import public_pdf_validation
    from src.jobs.contracts import public_job_result

    value = validate_pdf_candidate(
        candidate, independent=IndependentValidator()
    ).model_dump()
    if change == "missing-digest":
        value["candidate_sha256"] = None
    elif change == "wrong-profile":
        value["profile"] = "ua2"
    elif change == "raw-error":
        value["error"] = "private diagnostic"
    else:
        value["independent_status"] = "unavailable"
    assert public_pdf_validation(value) is None
    assert "latex_pdf_validation" not in (
        public_job_result({"latex_pdf_validation": value}) or {}
    )


@pytest.mark.parametrize("formats", [["pdf"], ["tex", "pdf"]])
def test_failed_pdf_is_not_silently_replaced_by_successful_tex(
    tmp_path, monkeypatch, formats
):
    from src.education.remediation import latex_remediator as module
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.latex_pdf_validation import LatexPDFValidation
    from src.education.latex_processor import LaTeXProcessor

    source = tmp_path / "source.tex"
    text = r"\documentclass{article}\begin{document}Text\end{document}"
    source.write_text(text)
    issue = next(
        row
        for row in LaTeXProcessor(use_ai=False).scan_source(text)["issues"]
        if row["issue_type"] == "missing_title"
    )
    issue.update(id="title", type="missing_title")
    receipt = LatexPDFValidation(
        status="unavailable", reason="independent_validator_disabled"
    )

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
            validation_receipts["pdf"] = receipt
            return {"pdf": None}

    monkeypatch.setattr(module, "get_latex_converter", Converter)
    remediator = module.LatexRemediator(
        str(source),
        [issue],
        RemediationConfig(use_ai=False, latex_output_formats=formats),
    )
    remediator._modified_content = text.replace(
        r"\begin{document}", r"\title{Document}\begin{document}"
    )
    remediator._add_fixed_issue(
        remediator.issues[0], fixed_content="Document", fix_method="rule"
    )
    saved = remediator._save_document(remediator._modified_content)
    remediator._verify_fixes(saved)
    assert remediator.result.verification_passed is ("tex" in formats)
    assert remediator.get_output_files()["pdf"] is None
    assert remediator.result.latex_pdf_validation == receipt
    assert source.read_text() == text


@pytest.mark.parametrize("compliant", [True, False])
def test_existing_verapdf_adapter_is_used_for_configured_validation(
    candidate, monkeypatch, compliant
):
    from src.config import settings
    from src.education.validation import verapdf
    from test_verapdf import (
        MOCK_VERAPDF_COMPLIANT_RESPONSE,
        MOCK_VERAPDF_NONCOMPLIANT_RESPONSE,
    )

    seen = []

    def post(url, *, files, **kwargs):
        seen.append(url)
        assert files["file"][1].read() == candidate.read_bytes()
        body = (
            MOCK_VERAPDF_COMPLIANT_RESPONSE
            if compliant
            else MOCK_VERAPDF_NONCOMPLIANT_RESPONSE
        )
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: SimpleNamespace(
            verapdf_enabled=True, verapdf_url="http://validator.invalid"
        ),
    )
    monkeypatch.setattr(verapdf.httpx, "post", post)
    result = validate_pdf_candidate(candidate)
    assert result.accepted is compliant
    assert seen == ["http://validator.invalid/api/validate/ua1"]


def test_failed_synchronous_compatibility_response_keeps_validation_receipt():
    from src.api.education.remediation_routes import _legacy_completed_result
    from src.education.remediation.latex_pdf_validation import LatexPDFValidation

    receipt = LatexPDFValidation(
        status="unavailable", reason="no_converter"
    ).model_dump(mode="json")
    result = _legacy_completed_result(
        {"status": "failed", "result_data": {"latex_pdf_validation": receipt}},
        scan_id="scan",
        job_id="job",
    )
    assert result["success"] is False
    assert result["latex_pdf_validation"] == receipt
    assert "download_url" not in result
