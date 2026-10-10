"""Image scoring comes from PDF occurrences, while AI is optional review data."""

from __future__ import annotations

import io
from pathlib import Path

import pikepdf
import pymupdf as fitz
import pytest
from PIL import Image

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.models import PDFProcessingResult
from src.education.pdf_processor import PDFProcessor

pytestmark = pytest.mark.unit


def _png(color: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 12), color).save(output, format="PNG")
    return output.getvalue()


def _source_pdf(path: Path) -> None:
    colors = ((220, 20, 20), (20, 220, 20), (20, 20, 220), (220, 220, 20))
    with fitz.open() as document:
        page = document.new_page()
        page.insert_textbox(
            fitz.Rect(40, 220, 550, 700),
            "Course material for accessibility review. " * 35,
        )
        xrefs = [
            page.insert_image(
                fitz.Rect(40 + index * 110, 40, 130 + index * 110, 130),
                stream=_png(color),
            )
            for index, color in enumerate(colors)
        ]
        page.insert_text((275, 155), "Equation 1")
        document.save(path)
    with pikepdf.open(path, allow_overwriting_input=True) as document:
        for _, image in document.pages[0].Resources.XObject.items():
            if image.objgen[0] == xrefs[-1]:
                image.Alt = pikepdf.String("Yellow square")
        document.save(path)


class _Vision:
    def __init__(self, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.seen_images = 0
        self.seen_existing_alt = 0

    @staticmethod
    def _color(image_path: str) -> tuple[int, int, int]:
        with Image.open(image_path) as image:
            return image.convert("RGB").getpixel((0, 0))

    async def detect_image_type(
        self, *, image_path: str, **_kwargs: object
    ) -> dict[str, object]:
        self.seen_images += 1
        color = self._color(image_path)
        if self.unavailable:
            return {"success": False, "error": "provider unavailable"}
        if color[0] > 100:
            return {"success": True, "image_type": "decorative", "is_decorative": True}
        if color[2] > 100:
            return {"success": True, "image_type": "complex", "is_decorative": False}
        return {"success": True, "image_type": "informative", "is_decorative": False}

    async def generate_alt_text(
        self, *, image_path: str, **_kwargs: object
    ) -> dict[str, object]:
        assert self._color(image_path)[1] > 100
        return (
            {"success": False, "error": "provider unavailable"}
            if self.unavailable
            else {"success": True, "alt_text": "Green square"}
        )

    async def describe_chart_or_graph(
        self, *, image_path: str, **_kwargs: object
    ) -> dict[str, object]:
        assert self._color(image_path)[2] > 100
        return {
            "success": True,
            "short_description": "Blue chart",
            "detailed_description": "Blue data",
        }

    async def validate_alt_text(
        self, *, image_path: str, existing_alt_text: str, **_kwargs: object
    ) -> dict[str, object]:
        assert self._color(image_path)[0] > 100
        assert existing_alt_text == "Yellow square"
        self.seen_existing_alt += 1
        return (
            {"success": False, "error": "provider unavailable"}
            if self.unavailable
            else {
                "success": True,
                "is_accurate": False,
                "accuracy_score": 0.2,
                "issues": ["May omit relevant context"],
                "suggested_improvement": "A yellow square",
            }
        )


def _scan(
    path: Path, *, generate: bool, validate: bool, unavailable: bool = False
) -> tuple[PDFProcessingResult, _Vision]:
    processor = PDFProcessor()
    processor.generate_alt_text = generate
    processor.validate_alt_text = validate
    vision = _Vision(unavailable)
    processor.image_generator = vision
    return processor.process_pdf(str(path)), vision


def _missing(issues: list[dict]) -> list[dict]:
    return [issue for issue in issues if issue.get("issue_type") == "missing_alt_text"]


def test_score_and_missing_occurrences_are_stable_across_ai_modes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "images.pdf"
    _source_pdf(path)
    baseline, _ = _scan(path, generate=False, validate=False)
    generated, generation_ai = _scan(path, generate=True, validate=False)
    validated, validation_ai = _scan(path, generate=False, validate=True)
    combined, combined_ai = _scan(path, generate=True, validate=True)
    unavailable, unavailable_ai = _scan(
        path, generate=True, validate=True, unavailable=True
    )
    repeated, _ = _scan(path, generate=True, validate=True)

    scans = (baseline, generated, validated, combined, unavailable, repeated)
    baseline_missing = _missing(baseline.issues)
    assert len(baseline_missing) == 3
    occurrence_ids = {issue["occurrence_id"] for issue in baseline_missing}
    assert len(occurrence_ids) == 3
    equation_ids = {
        issue["occurrence_id"]
        for issue in baseline.issues
        if issue.get("issue_type") == IMAGE_EQUATION_ISSUE_TYPE
    }
    assert len(equation_ids) == 1
    for scan in scans:
        missing = _missing(scan.issues)
        assert len(missing) == 3
        assert {issue["occurrence_id"] for issue in missing} == occurrence_ids
        assert all(issue["severity"] == "high" for issue in missing)
        assert scan.compliance_score == baseline.compliance_score
        assert {
            issue["occurrence_id"]
            for issue in scan.issues
            if issue.get("issue_type") == IMAGE_EQUATION_ISSUE_TYPE
        } == equation_ids
        assert len({issue.occurrence_id for issue in scan.image_issues}) == len(
            scan.image_issues
        )

    assert generation_ai.seen_images == combined_ai.seen_images == 3
    assert validation_ai.seen_images == 0
    assert validation_ai.seen_existing_alt == combined_ai.seen_existing_alt == 1
    assert unavailable_ai.seen_existing_alt == 1

    decorative = next(
        issue for issue in _missing(combined.issues) if issue["is_decorative"]
    )
    assert decorative["severity"] == "high"
    assert decorative["alt_text"] == ""
    assert any(issue["is_chart"] for issue in _missing(combined.issues))
    assert all(issue["alt_text"] is None for issue in _missing(unavailable.issues))

    reviews = [
        issue
        for issue in combined.issues
        if issue.get("issue_type") == "ai_alt_quality_review"
    ]
    assert len(reviews) == 1
    assert reviews[0]["assessment_type"] == "ai_alt_quality_review"
    assert reviews[0]["review_only"] is True
    assert reviews[0]["scoring_included"] is False
    assert reviews[0]["existing_alt_text"] == "Yellow square"
    assert "AI review suggests" in reviews[0]["message"]
    assert len([issue for issue in validated.issues if issue.get("review_only")]) == 1
    assert not any(issue.get("review_only") for issue in unavailable.issues)
    assert combined.issues == repeated.issues

    # All non-AI source checks survive each scan. AI review is the sole extra.
    source_issues = [
        issue
        for issue in baseline.issues
        if issue.get("issue_type") != "missing_alt_text"
    ]
    for scan in scans:
        assert [
            issue
            for issue in scan.issues
            if issue.get("issue_type")
            not in {"missing_alt_text", "ai_alt_quality_review"}
        ] == source_issues


def test_only_tagged_ai_quality_review_is_excluded_from_score() -> None:
    processor = PDFProcessor()
    ordinary = {"issue_type": "ordinary_rule", "severity": "high", "review_only": True}
    advice = {
        "issue_type": "ai_alt_quality_review",
        "assessment_type": "ai_alt_quality_review",
        "severity": "critical",
        "review_only": True,
        "scoring_included": False,
    }
    assert processor._calculate_compliance_score([ordinary, advice]) == (
        processor._calculate_compliance_score([ordinary])
    )
    assert processor._calculate_compliance_score([ordinary]) < (
        processor._calculate_compliance_score([])
    )


def test_failed_ai_validation_does_not_make_source_scan_incomplete(
    tmp_path: Path,
) -> None:
    path = tmp_path / "images.pdf"
    _source_pdf(path)

    class _FailingValidation(_Vision):
        async def validate_alt_text(
            self, *, image_path: str, existing_alt_text: str, **_kwargs: object
        ) -> dict[str, object]:
            assert self._color(image_path)[0] > 100
            assert existing_alt_text == "Yellow square"
            raise RuntimeError("provider unavailable")

    processor = PDFProcessor()
    processor.validate_alt_text = True
    processor.image_generator = _FailingValidation()
    result = processor.process_pdf(str(path))

    assert len(_missing(result.issues)) == 3
    assert not any(
        issue.get("issue_type") == "ai_alt_quality_review" for issue in result.issues
    )
