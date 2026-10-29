"""Reading-order text shown to users never carries internal codes or exceptions."""

import re
from pathlib import Path

import pymupdf as fitz
import pytest

from src.education import layout_order
from src.education.layout_order import (
    GENERIC_REFUSAL_REASON,
    LayoutAmbiguous,
    REFUSAL_REASONS,
    plain_refusal_reason,
)
from src.education.pdf_checks import reading_order
from src.education.pdf_checks.reading_order import ReadingOrderVerifier

pytestmark = pytest.mark.unit

CODES = sorted(
    set(
        re.findall(
            r'LayoutAmbiguous\("(reading_order_\w+)"\)',
            Path(layout_order.__file__).read_text(),
        )
    )
)


def test_every_refusal_code_in_layout_order_is_mapped():
    assert len(CODES) >= 12
    assert set(CODES) == set(REFUSAL_REASONS)


@pytest.mark.parametrize("code", CODES)
def test_each_code_maps_to_a_plain_sentence(code):
    sentence = plain_refusal_reason(code)
    assert "reading_order" not in sentence and "_" not in sentence
    assert sentence[0].isupper() and sentence.endswith(".")
    assert sentence != plain_refusal_reason("reading_order_unknown_code")


def test_unknown_code_uses_generic_sentence():
    sentence = plain_refusal_reason("reading_order_something_new")
    assert sentence.lower() == GENERIC_REFUSAL_REASON.lower()
    assert "reading_order" not in sentence


@pytest.mark.parametrize("code", CODES + ["reading_order_something_new"])
def test_review_requirement_text_has_no_internal_code(monkeypatch, code):
    def refuse(blocks, page_width=None):
        raise LayoutAmbiguous(code)

    monkeypatch.setattr(layout_order, "block_plan", refuse)
    visual = [
        {"text": "alpha", "bbox": (0, 0, 10, 10), "x": 0, "y": 0},
        {"text": "beta", "bbox": (0, 20, 10, 30), "x": 0, "y": 20},
    ]
    structure = [{"text": "alpha"}, {"text": "beta"}]
    issue = ReadingOrderVerifier()._compare_reading_orders(1, visual, structure)
    assert issue is not None and issue.review_only
    assert "reading_order_" not in issue.recommendation
    assert "regional reading order" in issue.recommendation
    assert plain_refusal_reason(code) in issue.recommendation


def test_unreadable_pdf_hides_exception_text_and_logs_it(tmp_path, caplog):
    secret = "/srv/internal/path/secret-token.pdf"
    path = tmp_path / "plain.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 100), "Body")
        pdf.save(path)

    def explode(self, file_path, page_num, page):
        raise RuntimeError(f"cannot open {secret}")

    original = ReadingOrderVerifier._verified_artifact_duplicates
    ReadingOrderVerifier._verified_artifact_duplicates = explode
    try:
        with caplog.at_level("WARNING"):
            result = ReadingOrderVerifier().check(str(path))
    finally:
        ReadingOrderVerifier._verified_artifact_duplicates = original
    (issue,) = result.issues
    assert issue.recommendation == reading_order.READ_FAILURE_RECOMMENDATION
    assert secret not in issue.recommendation
    assert "Error reading PDF" not in issue.recommendation
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(secret in r.getMessage() for r in warnings)
