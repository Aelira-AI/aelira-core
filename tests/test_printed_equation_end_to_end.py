"""A printed equation image goes from scan to the publication gate for real.

One untagged page carries an equation image rendered by the real renderer,
under an "Equation 1" cue. The scanner, the remediator (real renderer, real
visual check, real association writer, real saved-file rescan), the stored-fix
builder and the job's publication gate with the real Matterhorn validator all
run unchanged. Only the alt-text provider is a stub, because it would need a
model. It answers the equation prompt and the second reader's prompt by their
exact text and refuses every other prompt, so a description can reach the file
only through the equation path.

The first reading is correct, the second reading agrees, and the equation must
survive every check, stay review-gated and be accepted by the gate. A reading
that the visual check rejects must leave no equation content behind and no gate.
A first reading that is wrong by one symbol passes the visual check, and is
turned away by the second reading with the same result.
"""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pikepdf
import pymupdf as fitz
import pytest
from latex2mathml.converter import convert as latex_to_mathml
from PIL import Image

from src.db.models import RemediationOutcome, ScanFix, ScanResult
from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.pdf_checks.image_checker import _displayed_image_occurrences
from src.education.pdf_checks.image_semantics import structured_image_semantics
from src.education.pdf_processor import PDFProcessor
from src.education.remediation import image_equation_gate
from src.education.remediation.base import RemediationConfig
from src.education.remediation.equation_recognizer import _PROMPT as RECOGNIZER_PROMPT
from src.education.remediation.equation_verifier import (
    OfflineMathMLRenderer,
    canonicalize_mathml,
)
from src.education.remediation.image_equation_gate import contains_image_equation_fixes
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.printed_equation_verifier import (
    _VERIFIER_PROMPT as SECOND_READING_PROMPT,
)
from src.services.scan_fix_service import artifact_review_blockers, build_scan_fix
from tests.test_equation_alt_text_coordination import (
    _fixed,
    _issue_ids,
    _manual,
    _structure,
)
from tests.test_queued_pdf_output_claim import _artifact, _context

pytestmark = pytest.mark.unit

LATEX = "x^{2} + 3x - 7 = 0"
EQUATION_ALT = "x squared + 3x - 7 = 0"
# An equation that shares no structure with the printed one.
UNRELATED_LATEX = r"\frac{a}{b} = \sqrt{c}"
# One symbol away from the printed equation: x cubed for x squared. The visual
# check passes it (see test_printed_equation_single_symbol).
WRONG_BY_ONE_SYMBOL_LATEX = "x^{3} + 3x - 7 = 0"
IMAGE_INDEX = 0
BODY_TEXT = (
    "This worked example solves a polynomial in one variable. "
    "Read the statement below and then the steps that follow it."
)
REAL_GATE = image_equation_gate.require_image_equation_matterhorn_result
# Markers of equation content that must not reach a file whose equation failed.
EQUATION_CONTENT_MARKERS = (
    b"/Formula",
    b"<math",
    b"MathML",
    b"squared",
    b"cubed",
    b"square root",
)


@pytest.fixture(scope="module")
def equation_png() -> bytes:
    """The equation image, rendered the way the visual check renders a reading."""
    try:
        return OfflineMathMLRenderer().render(
            canonicalize_mathml(latex_to_mathml(LATEX))
        )
    except RuntimeError as exc:
        pytest.skip(f"pinned Chromium unavailable in this developer environment: {exc}")


@pytest.fixture
def source_pdf(tmp_path: Path, equation_png: bytes) -> Path:
    """One untagged page: body text, an "Equation 1" cue and the image below it.

    The body is long enough that the processor reads the page as text instead of
    falling back to OCR, and the image sits within 48 points under the cue.
    """
    with Image.open(io.BytesIO(equation_png)) as image:
        width, height = image.size
    path = tmp_path / "source.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(fitz.Rect(72, 72, 540, 130), BODY_TEXT, fontsize=11)
        page.insert_text((72, 160), "Equation 1", fontsize=11)
        page.insert_image(
            fitz.Rect(72, 170, 72 + 36 * width / height, 206), stream=equation_png
        )
        assert len(page.get_text().strip()) >= 100
        doc.save(path)
    return path


GREEN_SQUARE = "A green square"


@pytest.fixture
def equation_first_pdf(tmp_path: Path, equation_png: bytes) -> Path:
    """The equation image first, an ordinary image far below it.

    The ordinary image is more than 48 points from the cue, so only the first
    image is an equation candidate. Both images lack alternative text.
    """
    with Image.open(io.BytesIO(equation_png)) as image:
        width, height = image.size
    green = io.BytesIO()
    Image.new("RGB", (80, 80), "green").save(green, format="PNG")
    path = tmp_path / "equation-first.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(fitz.Rect(72, 72, 540, 130), BODY_TEXT, fontsize=11)
        page.insert_text((72, 160), "Equation 1", fontsize=11)
        page.insert_image(
            fitz.Rect(72, 170, 72 + 36 * width / height, 206), stream=equation_png
        )
        page.insert_image(fitz.Rect(72, 400, 132, 460), stream=green.getvalue())
        assert len(page.get_text().strip()) >= 100
        doc.save(path)
    return path


class _Reader:
    """An alt-text-purpose client that reads the equation and refuses all else.

    The first reading is ``latex``. The second reader sends its own prompt, which
    is answered with ``second_latex``, the truthful reading unless a test says so.
    """

    purpose = "alt_text"
    provider = "test-provider"
    model = "test-model"

    def __init__(self, latex: str, second_latex: str | None = None) -> None:
        self.latex = latex
        self.second_latex = latex if second_latex is None else second_latex
        self.kinds: list[str] = []

    def analyze_image_sync(self, *, image_data: bytes, prompt: str, **_: Any):
        assert image_data
        if prompt == RECOGNIZER_PROMPT:
            self.kinds.append("recognition")
            return {
                "success": True,
                "content": json.dumps(
                    {"classification": "printed_equation", "latex": self.latex}
                ),
            }
        if prompt == SECOND_READING_PROMPT:
            self.kinds.append("second_reading")
            return {
                "success": True,
                "content": json.dumps(
                    {"classification": "printed_equation", "latex": self.second_latex}
                ),
            }
        self.kinds.append("generic")
        return {"success": False, "content": ""}


class _DescribingReader(_Reader):
    """Reads the equation and describes any other image it is asked about."""

    def analyze_image_sync(self, *, image_data: bytes, prompt: str, **kwargs: Any):
        if prompt in (RECOGNIZER_PROMPT, SECOND_READING_PROMPT):
            return super().analyze_image_sync(
                image_data=image_data, prompt=prompt, **kwargs
            )
        self.kinds.append("generic")
        return {"success": True, "content": GREEN_SQUARE}


def _scan(source: Path, *, other_images: int = 0):
    """Scan the source and prove the probe sees both findings first."""
    scan = PDFProcessor().process_pdf(str(source))
    kinds = Counter(issue.get("issue_type") for issue in scan.issues)
    assert kinds["missing_alt_text"] == 1 + other_images, kinds
    assert kinds[IMAGE_EQUATION_ISSUE_TYPE] == 1, kinds
    for index, issue in enumerate(scan.issues):
        issue["id"] = f"source-{index}"
    return scan


def _remediate(
    source: Path,
    tmp_path: Path,
    reading: str,
    *,
    reader: _Reader | None = None,
    other_images: int = 0,
):
    scan = _scan(source, other_images=other_images)
    reader = reader or _Reader(reading)
    remediator = PdfRemediator(
        str(source),
        scan.issues,
        RemediationConfig(
            use_ai=True,
            allow_legacy_nested_ai=False,
            create_backup=False,
            verify_fixes=True,
            output_directory=str(tmp_path / "out"),
        ),
        ai_client=None,
        alt_text_client=reader,
    )
    return scan, remediator, remediator.remediate(), reader


def _carried_by_image(path: str) -> dict[int, tuple[bool, str | None]]:
    """What each image of the saved file carries, keyed by its embedded width.

    The width is read from the saved file for each displayed occurrence, so the
    answer follows the image itself and not its place in the document.
    """
    with fitz.open(path) as doc:
        occurrences = _displayed_image_occurrences(doc[0], 1)
        widths = {
            item["occurrence_id"]: doc.extract_image(item["image_xref"])["width"]
            for item in occurrences
        }
    semantics = structured_image_semantics(path, 0, occurrences)
    assert semantics is not None
    return {widths[key]: value for key, value in semantics.items()}


def _rescan_kinds(path: str) -> Counter:
    scan = PDFProcessor().process_pdf(path)
    return Counter(issue.get("issue_type") for issue in scan.issues)


def _readable_bytes(path: str, tmp_path: Path) -> bytes:
    """The whole file with every stream decoded, so content can be searched."""
    readable = tmp_path / "readable.pdf"
    with pikepdf.open(path) as pdf:
        pdf.save(
            readable,
            compress_streams=False,
            stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
        )
    return readable.read_bytes()


async def _publish(
    tmp_path: Path, source: Path, scan: Any, remediator: PdfRemediator, result: Any
) -> SimpleNamespace:
    """Run the real remediation result through the job with the real validator."""
    from src.jobs.remediation_job import process_remediation_job

    _, _, scan_row, cloud_file, db, job_data = _context(tmp_path)
    Path(job_data["file_path"]).write_bytes(source.read_bytes())
    db.values[ScanResult] = SimpleNamespace(issues=scan.issues)
    claimed = Path(result.output_file).read_bytes()
    artifact = _artifact(claimed)
    service = MagicMock()
    published: list[bytes] = []

    def publish(*_args: Any, **kwargs: Any):
        published.append(kwargs["source_stream"].read())
        cloud_file.current_remediation_artifact_id = artifact.id
        cloud_file.has_remediated_version = True
        return artifact

    service.claim_and_publish_stream.side_effect = publish
    gate_calls: list[SimpleNamespace] = []

    def gate(matterhorn: Any, source_failures: Any) -> None:
        call = SimpleNamespace(
            matterhorn=matterhorn, source_failures=source_failures, accepted=False
        )
        gate_calls.append(call)
        REAL_GATE(matterhorn, source_failures)
        call.accepted = True

    with (
        patch(
            "src.jobs.remediation_job._get_remediator_for_scan_type",
            return_value=MagicMock(remediate=MagicMock(return_value=result)),
        ),
        patch(
            "src.jobs.remediation_job.RemediationArtifactService.from_settings",
            return_value=service,
        ),
        patch(
            "src.jobs.remediation_job._download_cloud_file",
            new=AsyncMock(
                return_value={"success": True, "local_path": job_data["file_path"]}
            ),
        ),
        patch(
            "src.education.remediation.image_equation_gate."
            "require_image_equation_matterhorn_result",
            side_effect=gate,
        ),
    ):
        outcome = await process_remediation_job(job_data, db)
    publication = service.claim_and_publish_stream.call_args
    return SimpleNamespace(
        outcome=outcome,
        claimed=claimed,
        published=published,
        service=service,
        scan_row=scan_row,
        rows=[row for row in db.added if isinstance(row, ScanFix)],
        gate_calls=gate_calls,
        # Absent when the job withheld the output instead of publishing it.
        provider_result=publication.kwargs["provider_result"] if publication else None,
    )


def _failed_checkpoints(matterhorn: Any) -> Counter:
    return Counter(
        (checkpoint.id, checkpoint.page_number)
        for checkpoint in matterhorn.checkpoints
        if checkpoint.status.value == "fail"
    )


def test_correct_reading_survives_the_saved_file_rescan_and_stays_review_gated(
    tmp_path: Path, source_pdf: Path
) -> None:
    scan, remediator, result, reader = _remediate(source_pdf, tmp_path, LATEX)
    try:
        assert result.success, result.error_message
        assert result.verification_passed, result.verification_result
        # The equation was read twice, and nothing else was ever described.
        assert reader.kinds == ["recognition", "second_reading"]

        equation_fixes = [
            fix for fix in result.fixed_issues if fix.source_kind == "image_equation"
        ]
        assert len(equation_fixes) == 1
        (equation_fix,) = equation_fixes
        assert equation_fix.needs_review is True
        assert equation_fix.verification_passed is True
        assert equation_fix.fixed_content == EQUATION_ALT
        assert equation_fix.saved_file_verification["method_version"] == (
            "pdf-finding-presence-v1"
        )
        assert equation_fix.saved_file_verification["issue_id"] == equation_fix.issue_id

        # One /Formula carries the reading, and the image has no second owner.
        structure = _structure(result.output_file)
        assert [alt for role, alt in structure if role == "/Formula"] == [EQUATION_ALT]
        assert [role for role, _ in structure].count("/Figure") == 0

        # The saved file no longer reports the image or the candidate.
        assert _rescan_kinds(result.output_file)["missing_alt_text"] == 0
        assert _rescan_kinds(result.output_file)[IMAGE_EQUATION_ISSUE_TYPE] == 0

        # Both findings of the draw are fixed, and neither is left manual.
        ids = _issue_ids(remediator, IMAGE_INDEX)
        assert _fixed(result, ids[IMAGE_EQUATION_ISSUE_TYPE]) is equation_fix
        promoted = _fixed(result, ids["missing_alt_text"])
        assert promoted.fixed_content == EQUATION_ALT
        assert promoted.needs_review is True
        assert not [
            item for item in result.manual_issues if item.issue_id in set(ids.values())
        ]

        # The gate is derived from what the output contains.
        assert contains_image_equation_fixes(result.fixed_issues) is True
        assert isinstance(result.source_matterhorn_failures, list)
        row = build_scan_fix(scan.issues[0]["id"], equation_fix)
        assert row.review_status == "pending"
        assert row.source_kind == "image_equation"
        assert "image_equation_not_human_approved" in artifact_review_blockers([row])
    finally:
        result.close_output_claim()


async def test_correct_reading_is_accepted_by_the_gate_and_requires_approval(
    tmp_path: Path, source_pdf: Path
) -> None:
    scan, remediator, result, _ = _remediate(source_pdf, tmp_path, LATEX)
    ids = _issue_ids(remediator, IMAGE_INDEX)
    try:
        assert result.success, result.error_message
        assert contains_image_equation_fixes(result.fixed_issues) is True
        run = await _publish(tmp_path, source_pdf, scan, remediator, result)
    finally:
        result.close_output_claim()

    # The artifact is published, held for approval and not downloadable.
    assert run.outcome["success"] is True
    assert run.provider_result["requires_approval"] is True
    assert run.outcome["download_available"] is False
    assert run.outcome["human_review_required"] is True
    assert run.outcome["artifact_review_status"] == "pending"
    assert run.published == [run.claimed]
    run.service.abort_staging.assert_not_called()
    assert run.scan_row.remediation_outcome == RemediationOutcome.COMPLETED.value

    # The recorded outcome credits the equation and its image finding as fixed
    # changes that still need a human.
    outcomes = {item["issue_id"]: item for item in run.outcome["issue_outcomes"]}
    for issue_id in (ids[IMAGE_EQUATION_ISSUE_TYPE], ids["missing_alt_text"]):
        assert outcomes[issue_id]["status"] == "fixed"
        assert outcomes[issue_id]["needs_review"] is True

    # The real validator ran on the claimed bytes and the gate accepted them.
    (call,) = run.gate_calls
    assert call.accepted is True
    assert call.source_failures == result.source_matterhorn_failures
    output_failures = _failed_checkpoints(call.matterhorn)
    source_failures = Counter(tuple(item) for item in call.source_failures)
    # 06-003 (no PDF/UA identifier) fails before and after, and is not new.
    assert output_failures[("06-003", None)] >= 1
    assert source_failures[("06-003", None)] >= 1
    assert not output_failures - source_failures

    # Exactly one stored equation row, waiting for a human.
    (equation_row,) = [row for row in run.rows if row.source_kind == "image_equation"]
    assert equation_row.review_status == "pending"
    assert "image_equation_not_human_approved" in artifact_review_blockers(run.rows)


async def test_unrelated_reading_is_published_without_equation_content_or_gate(
    tmp_path: Path, source_pdf: Path
) -> None:
    scan, remediator, result, reader = _remediate(source_pdf, tmp_path, UNRELATED_LATEX)
    ids = _issue_ids(remediator, IMAGE_INDEX)
    try:
        assert result.success, result.error_message
        # The equation was read once; the failed reading is never turned into a
        # generic description of the same image.
        assert reader.kinds == ["recognition"]

        # The document keeps its verified language, title and structure fixes,
        # and neither finding of the image is credited.
        assert {fix.category.value for fix in result.fixed_issues} >= {
            "language",
            "title",
            "structure",
        }
        assert not [
            fix
            for fix in result.fixed_issues
            if fix.issue_id in set(ids.values()) or fix.source_kind == "image_equation"
        ]
        assert _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE]).reason == (
            "equation_verification_failed"
        )
        assert _manual(result, ids["missing_alt_text"]).reason == (
            "image_equation_alt_text_withheld"
        )
        # This is the value the job derives requires_approval from.
        assert contains_image_equation_fixes(result.fixed_issues) is False

        # The file holds no equation content, from either reading. The image
        # keeps only the alt-less Figure the generic tagger gives every image.
        structure = _structure(result.output_file)
        assert "/Formula" not in [role for role, _ in structure]
        assert [alt for role, alt in structure if role == "/Figure"] == [None]
        readable = _readable_bytes(result.output_file, tmp_path)
        for marker in EQUATION_CONTENT_MARKERS:
            assert marker not in readable, marker
        assert not re.search(rb"/Alt\s*[(<]", readable)

        # That undescribed Figure turns Matterhorn 13-004 from "cannot check
        # figures" (no structure tree in the source) into a count. The source
        # image was already undescribed and is recorded as manual work, so the
        # saved-file verification accepts the change and nothing is withheld.
        assert result.verification_passed is True, result.verification_result
        assert result.verification_result.unavailable_checks == []
        assert "Matterhorn 13-004" in result.verification_result.persistent_failures
        assert ("13-004", None) in result.source_matterhorn_failures
        run = await _publish(tmp_path, source_pdf, scan, remediator, result)
    finally:
        result.close_output_claim()

    # The job publishes the file. Nothing equation-related is gated, so the
    # approval gate is not involved and the artifact is not held for approval.
    assert run.outcome["success"] is True
    assert run.provider_result["requires_approval"] is False
    assert run.outcome["download_available"] is True
    assert run.published == [run.claimed]
    run.service.abort_staging.assert_not_called()
    assert run.gate_calls == []
    # The three verified fixes are stored, none with a review-gated kind, and
    # none belongs to the image.
    assert [row.source_kind for row in run.rows] == [None, None, None]
    assert not {row.issue_id for row in run.rows} & set(ids.values())
    assert "image_equation_not_human_approved" not in artifact_review_blockers(run.rows)

    # The recorded outcome lists the image as manual work, with the equation's
    # own reason, and withholds nothing.
    outcomes = {item["issue_id"]: item for item in run.outcome["issue_outcomes"]}
    assert outcomes[ids[IMAGE_EQUATION_ISSUE_TYPE]]["status"] == "manual"
    assert outcomes[ids["missing_alt_text"]]["status"] == "manual"
    assert outcomes[ids["missing_alt_text"]]["reason_code"] == (
        "image_equation_alt_text_withheld"
    )
    assert not [item for item in outcomes.values() if item["status"] == "withheld"]
    assert run.outcome["withheld_count"] == 0


async def test_reading_wrong_by_one_symbol_is_turned_away_by_the_second_reading(
    tmp_path: Path, source_pdf: Path
) -> None:
    """x cubed for x squared passes the visual check and still reaches no file.

    The first reading is one symbol away from the printed equation, so the
    visual round trip accepts it. The blind second reading sees the truth, the
    two disagree, and nothing of either reading is written or gated. The sibling
    test with a matching second reading shows the same harness accepting.
    """
    scan, remediator, result, reader = _remediate(
        source_pdf,
        tmp_path,
        WRONG_BY_ONE_SYMBOL_LATEX,
        reader=_Reader(WRONG_BY_ONE_SYMBOL_LATEX, second_latex=LATEX),
    )
    ids = _issue_ids(remediator, IMAGE_INDEX)
    try:
        assert result.success, result.error_message
        # The visual check let the wrong reading through, so the second reading
        # was asked for it, and no generic description was ever requested.
        assert reader.kinds == ["recognition", "second_reading"]

        assert {fix.category.value for fix in result.fixed_issues} >= {
            "language",
            "title",
            "structure",
        }
        assert not [
            fix
            for fix in result.fixed_issues
            if fix.issue_id in set(ids.values()) or fix.source_kind == "image_equation"
        ]
        assert _manual(result, ids[IMAGE_EQUATION_ISSUE_TYPE]).reason == (
            "printed_equation_semantic_disagreement"
        )
        assert _manual(result, ids["missing_alt_text"]).reason == (
            "image_equation_alt_text_withheld"
        )
        assert contains_image_equation_fixes(result.fixed_issues) is False

        # Neither reading is anywhere in the file: no Formula, no MathML, no
        # description, and the image keeps only the alt-less Figure.
        structure = _structure(result.output_file)
        assert "/Formula" not in [role for role, _ in structure]
        assert [alt for role, alt in structure if role == "/Figure"] == [None]
        readable = _readable_bytes(result.output_file, tmp_path)
        for marker in EQUATION_CONTENT_MARKERS:
            assert marker not in readable, marker
        assert not re.search(rb"/Alt\s*[(<]", readable)

        assert result.verification_passed is True, result.verification_result
        assert result.verification_result.unavailable_checks == []
        run = await _publish(tmp_path, source_pdf, scan, remediator, result)
    finally:
        result.close_output_claim()

    # Nothing equation-related is gated, so the artifact is not held for approval.
    assert run.outcome["success"] is True
    assert run.provider_result["requires_approval"] is False
    assert run.outcome["download_available"] is True
    assert run.gate_calls == []
    assert all(row.source_kind != "image_equation" for row in run.rows)
    assert not {row.issue_id for row in run.rows} & set(ids.values())
    outcomes = {item["issue_id"]: item for item in run.outcome["issue_outcomes"]}
    assert outcomes[ids[IMAGE_EQUATION_ISSUE_TYPE]]["status"] == "manual"
    assert outcomes[ids[IMAGE_EQUATION_ISSUE_TYPE]]["reason_code"] == (
        "printed_equation_semantic_disagreement"
    )
    assert outcomes[ids["missing_alt_text"]]["status"] == "manual"
    assert run.outcome["withheld_count"] == 0


async def test_equation_first_page_describes_the_second_image_on_its_own_figure(
    tmp_path: Path, equation_first_pdf: Path, equation_png: bytes
) -> None:
    """A rejected equation before an ordinary image no longer misplaces its text.

    The equation image stays undescribed and the description of the second image
    lands on that image's own Figure. The file verifies and publishes, the
    equation and its image finding are manual work, and the second image's fix
    is credited.
    """
    scan, remediator, result, reader = _remediate(
        equation_first_pdf,
        tmp_path,
        UNRELATED_LATEX,
        reader=_DescribingReader(UNRELATED_LATEX),
        other_images=1,
    )
    equation_ids = _issue_ids(remediator, IMAGE_INDEX)
    other_ids = _issue_ids(remediator, 1)
    try:
        assert result.success, result.error_message
        # One reading of the equation and one description, for the other image.
        assert reader.kinds == ["recognition", "generic"]

        # By image: the equation image carries nothing, the green image carries
        # its own description, whatever order the Figures come in.
        with Image.open(io.BytesIO(equation_png)) as image:
            equation_width = image.width
        carried = _carried_by_image(result.output_file)
        assert carried[equation_width] == (False, None)
        assert carried[80] == (True, GREEN_SQUARE)
        structure = _structure(result.output_file)
        assert [alt for role, alt in structure if role == "/Figure"] == [
            None,
            GREEN_SQUARE,
        ]
        assert "/Formula" not in [role for role, _ in structure]

        assert result.verification_passed, result.verification_result
        assert result.verification_result.unavailable_checks == []
        assert _manual(result, equation_ids[IMAGE_EQUATION_ISSUE_TYPE]).reason == (
            "equation_verification_failed"
        )
        assert _manual(result, equation_ids["missing_alt_text"]).reason == (
            "image_equation_alt_text_withheld"
        )
        assert _fixed(result, other_ids["missing_alt_text"]).fixed_content == (
            GREEN_SQUARE
        )
        assert contains_image_equation_fixes(result.fixed_issues) is False
        readable = _readable_bytes(result.output_file, tmp_path)
        for marker in EQUATION_CONTENT_MARKERS:
            assert marker not in readable, marker
        run = await _publish(tmp_path, equation_first_pdf, scan, remediator, result)
    finally:
        result.close_output_claim()

    assert run.outcome["success"] is True
    assert run.provider_result["requires_approval"] is False
    assert run.gate_calls == []
    outcomes = {item["issue_id"]: item for item in run.outcome["issue_outcomes"]}
    assert outcomes[equation_ids["missing_alt_text"]]["status"] == "manual"
    assert outcomes[equation_ids[IMAGE_EQUATION_ISSUE_TYPE]]["status"] == "manual"
    assert outcomes[other_ids["missing_alt_text"]]["status"] == "fixed"
    assert not [item for item in outcomes.values() if item["status"] == "withheld"]
