"""MCID-only reading order must reach scans and saved-file verification."""

from contextlib import nullcontext
from hashlib import sha256
from io import BytesIO

import fitz
import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from src.education.pdf_checks.completeness import IncompletePDFScanError
from src.education.pdf_checks.reading_order import ReadingOrderVerifier
from src.education.pdf_processor import PDFProcessor
from src.education.remediation.base import RemediationConfig
from src.education.remediation.pdf_remediator import PdfRemediator


def tagged_document(
    path, *, order=(0, 1), refs=(0, 1), parent_only=False, language=None
):
    """Two distinct visible paragraphs, with independently ordered ownership."""
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text(
            (50, 60),
            "First course paragraph describes accessible instruction and learning.",
        )
        page.insert_text(
            (50, 110),
            "Second assessment paragraph explains the weekly course requirements.",
        )
        raw = doc.tobytes()
    with pikepdf.Pdf.open(BytesIO(raw)) as pdf:
        page = pdf.pages[0]
        streams = list(page.Contents)
        assert len(streams) == 2
        page.Contents = Array(
            [
                pdf.make_stream(
                    f"/P <</MCID {index}>> BDC\n".encode()
                    + stream.read_bytes()
                    + b"\nEMC"
                )
                for index, stream in enumerate(streams)
            ]
        )
        root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        paragraphs = []
        for ref in refs:
            element = pdf.make_indirect(
                Dictionary(Type=Name.StructElem, S=Name.P, K=ref, P=root)
            )
            if not parent_only:
                element.Pg = page.obj
            paragraphs.append(element)
        root.K = Array([paragraphs[index] for index in order])
        root.ParentTree = pdf.make_indirect(
            Dictionary(Nums=Array([0, Array(paragraphs)]))
        )
        page.StructParents = 0
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        if language:
            pdf.Root.Lang = String(language)
        pdf.save(path)
    return path


@pytest.mark.parametrize("parent_only", [False, True])
def test_mcid_order_reaches_aggregate_scan(tmp_path, parent_only):
    source = tagged_document(tmp_path / "ordered.pdf", parent_only=parent_only)
    saved = tagged_document(
        tmp_path / "reordered.pdf", order=(1, 0), parent_only=parent_only
    )
    digests = [sha256(path.read_bytes()).hexdigest() for path in (source, saved)]
    checker = ReadingOrderVerifier()
    assert checker.check(str(source)).issues == []
    assert checker.check(str(saved)).issues
    scanner = PDFProcessor(require_complete_scan=True)
    before = scanner.process_pdf(str(source))
    after = scanner.process_pdf(str(saved))
    assert not any(
        i.get("issue_type") == "reading_order_mismatch" for i in before.issues
    )
    assert any(i.get("issue_type") == "reading_order_mismatch" for i in after.issues)
    assert after.compliance_score < before.compliance_score
    assert digests == [
        sha256(path.read_bytes()).hexdigest() for path in (source, saved)
    ]


@pytest.mark.parametrize("refs", [(7, 8), (0, 8)])
def test_unresolved_mcid_is_visible_in_partial_scan_and_refused_in_strict_scan(
    tmp_path, refs
):
    path = tagged_document(tmp_path / "missing.pdf", refs=refs)
    partial = PDFProcessor(require_complete_scan=False).process_pdf(str(path))
    assert any(i.get("issue_type") == "reading_order_mismatch" for i in partial.issues)
    with pytest.raises(IncompletePDFScanError, match="reading_order"):
        PDFProcessor(require_complete_scan=True).process_pdf(str(path))


@pytest.mark.parametrize("order", [(0,), (0, 0, 1)])
def test_missing_or_duplicated_content_reaches_aggregate_scan(tmp_path, order):
    path = tagged_document(tmp_path / "changed.pdf", order=order)
    scan = PDFProcessor(require_complete_scan=False).process_pdf(str(path))
    assert any(i.get("issue_type") == "reading_order_mismatch" for i in scan.issues)
    if order == (0, 0, 1):
        with pytest.raises(IncompletePDFScanError, match="reading_order"):
            PDFProcessor(require_complete_scan=True).process_pdf(str(path))
    else:
        strict = PDFProcessor(require_complete_scan=True).process_pdf(str(path))
        assert any(
            i.get("issue_type") == "reading_order_mismatch" for i in strict.issues
        )


@pytest.mark.parametrize("outcome", ["unchanged", "reordered", "unresolved"])
def test_real_mcid_paired_verification(tmp_path, monkeypatch, outcome):
    source = tagged_document(tmp_path / "source.pdf")
    options = {"reordered": {"order": (1, 0)}, "unresolved": {"refs": (7, 8)}}
    saved = tagged_document(
        tmp_path / "saved.pdf", language="en-US", **options.get(outcome, {})
    )
    source_scan = PDFProcessor(require_complete_scan=True).process_pdf(str(source))
    remediator = PdfRemediator(
        str(source), source_scan.issues, config=RemediationConfig(use_ai=False)
    )
    language_issue = next(
        i for i in remediator.issues if i.category.value == "language"
    )
    remediator._add_fixed_issue(language_issue, "en-US", "rule_based")
    # Supply genuine saved bytes at the existing borrowed-artifact boundary;
    # both scans and Matterhorn validation remain real.
    monkeypatch.setattr(
        remediator,
        "_materialize_output_claim_for_verification",
        lambda: nullcontext(str(saved)),
    )
    verification = remediator._verify_fixes(str(saved))
    if outcome == "unresolved":
        assert verification.passed is False
        assert remediator.result.remediated_compliance_score is None
        assert remediator.result.score_provenance is None
        assert remediator.result.fixed_count == 0
        assert remediator.result.score_verification_reason == "output_scan_failed"
    else:
        assert remediator.result.score_provenance == "scanner_rescan"
        assert remediator.result.remediated_compliance_score is not None
        assert verification.unavailable_checks == []
        if outcome == "reordered":
            assert verification.passed is False
            assert any("Reading order" in r for r in verification.regressions)
        else:
            assert verification.regressions == []
            assert verification.passed is True
            assert remediator.result.fixed_count == 1


def test_untagged_document_retains_document_level_finding_without_duplicate(tmp_path):
    path = tmp_path / "untagged.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((50, 60), "Course information")
        doc.save(path)
    result = PDFProcessor(require_complete_scan=True).process_pdf(str(path))
    assert any(i.get("issue_type") == "missing_structure_tree" for i in result.issues)
    assert not any(
        i.get("issue_type") == "reading_order_mismatch" for i in result.issues
    )
