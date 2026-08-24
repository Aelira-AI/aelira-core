"""Executable contract for verified PDF image-equation remediation.

Tasks after candidate discovery remain explicitly deferred until their plan slices land.
"""

from pathlib import Path

import pytest

from src.education.pdf_checks.models import PDFImageIssue
from src.education.remediation.base import IssueCategory


def test_canonical_candidate_has_exact_occurrence_identity():
    fields = PDFImageIssue.model_fields
    assert {
        "image_xref",
        "image_index",
        "occurrence_ordinal",
        "bbox",
        "occurrence_id",
    } <= fields.keys()
    assert fields["occurrence_id"].is_required()


def test_canonical_candidate_routes_as_structure():
    from src.api.education.remediation_routes import _infer_category

    assert _infer_category({"issue_type": "image_equation_inaccessible"}) == "structure"
    assert IssueCategory.STRUCTURE.value == "structure"


@pytest.mark.parametrize(
    "module_name",
    [
        "src.education.remediation.equation_image_source",
        "src.education.remediation.equation_recognizer",
    ],
)
def test_bounded_source_and_recognizer_modules_exist(module_name):
    __import__(module_name)


def test_verifier_contract_is_implemented():
    __import__("src.education.remediation.equation_verifier")


def test_exact_formula_association_contract():
    from src.education.remediation.content_tagger_v2 import associate_image_formula

    assert callable(associate_image_formula)


@pytest.mark.xfail(strict=True, reason="approved plan Tasks 8-10 are deferred")
def test_deferred_durable_review_and_claim_parity_contract():
    from src.education.remediation.base import FixedIssue

    assert {"source_kind", "verification_evidence"} <= FixedIssue.model_fields.keys()
    assert all(
        (Path(__file__).parent / filename).exists()
        for filename in (
            "test_direct_pdf_claim_publication.py",
            "test_queued_pdf_output_claim.py",
            "test_brightspace_pdf_output_claim.py",
        )
    )
