"""Generated image descriptions speak mathematics or are refused (#710).

A vision model that meets an equation tends to answer in LaTeX, which a screen
reader announces as backslashes, braces and dollar signs. Every description a
generator proposes passes through one check before it can be used, and every
prompt that asks for one asks for plain spoken words.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from src.db.models import ScanFix, VisualAnalysis
from src.education.alt_text_quality import MAX_ALT_TEXT_LENGTH
from src.education.docx_processor import DocxProcessor
from src.education.image_alt_text import (
    ImageAltTextGenerator,
    _analysis_request_fingerprint,
)
from src.education.math_speech import (
    IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON,
    MATH_NOT_SPEAKABLE_ERROR,
    SPOKEN_MATH_INSTRUCTION,
)
from src.education.pdf_processor import PDFProcessor
from src.education.pptx_processor import PowerPointProcessor
from src.education.remediation.base import IssueCategory
from src.education.remediation.docx_remediator import DocxRemediator
from src.education.remediation.outcome_explanations import manual_explanation
from src.education.remediation.pdf_remediator import PdfRemediator
from src.education.remediation.pptx_remediator import PptxRemediator
from src.services.visual_analysis_service import _classify_result_failure
from tests.test_alt_text_no_text_only_fallback import (
    _alt_issues,
    _config,
    _make_docx,
    _make_pptx,
    _office_text,
    _pdf_contains,
)
from tests.test_pdf_existing_figure_alt import _fixture as _make_tagged_pdf
from tests.test_visual_analysis_lifecycle import (
    _db,
    _png_bytes,
    _recorder,
    _request,
)

SPOKEN = "The equation a over b = c"
LATEX = r"The equation \frac{a}{b} = c"
AMBIGUOUS_LATEX = r"The equation \frac12 = c"


class Vision:
    """A purpose-bound vision client that records every prompt it receives."""

    provider = "gemini"
    model = "vision-safe"

    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []
        self.text_calls = 0

    def analyze_image_sync(self, *, image_data, prompt, max_tokens):
        self.prompts.append(prompt)
        return {
            "success": True,
            "content": self.content,
            "provider": "gemini",
            "model": "vision-safe",
        }

    def generate_text_sync(self, *_args, **_kwargs):
        self.text_calls += 1
        return {"success": True, "content": "An invented description"}


def _image(tmp_path: Path) -> str:
    path = tmp_path / "image.png"
    Image.new("RGB", (10, 10), color="blue").save(path)
    return str(path)


def _chart_answer(**fields) -> str:
    base = {
        "chart_type": "line graph",
        "title": "Growth",
        "short_description": "A line rising to the right",
        "detailed_description": "The line rises steadily across the chart.",
        "data_summary": {"x_axis": "years", "data_points": ["2022", "2026"]},
        "insights": ["Growth is steady"],
        "visual_elements": {"legend_items": []},
        "accessibility_note": "Use a long description",
    }
    base.update(fields)
    return json.dumps(base)


# ---------------------------------------------------------------------------
# Every alternative-text prompt asks for mathematics in plain spoken words
# ---------------------------------------------------------------------------


def test_the_instruction_asks_for_words_and_forbids_latex_markdown_and_delimiters():
    assert "plain spoken words" in SPOKEN_MATH_INSTRUCTION
    assert "LaTeX" in SPOKEN_MATH_INSTRUCTION
    assert "markdown" in SPOKEN_MATH_INSTRUCTION
    assert "delimiters" in SPOKEN_MATH_INSTRUCTION
    # The prompt must not show the notation it forbids.
    assert "\\" not in SPOKEN_MATH_INSTRUCTION
    assert "$" not in SPOKEN_MATH_INSTRUCTION


@pytest.mark.asyncio
@pytest.mark.parametrize("educational", [True, False])
async def test_the_generic_prompt_asks_for_spoken_mathematics(tmp_path, educational):
    client = Vision("A blue square")
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.generate_alt_text(
        _image(tmp_path), educational_context=educational
    )

    assert result["success"] is True
    assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]


@pytest.mark.asyncio
async def test_the_chart_and_diagram_prompt_asks_for_spoken_mathematics(tmp_path):
    client = Vision(_chart_answer())
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is True
    assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]


@pytest.mark.asyncio
async def test_the_validation_prompt_asks_for_spoken_mathematics(tmp_path):
    client = Vision(
        '{"is_accurate": false, "accuracy_score": 0.2, "issues": [],'
        ' "suggested_improvement": "A blue square", "reasoning": "x"}'
    )
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.validate_alt_text(_image(tmp_path), "image")

    assert result["success"] is True
    assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]


@pytest.mark.asyncio
async def test_the_classification_prompt_is_left_alone(tmp_path):
    client = Vision(
        '{"is_decorative": false, "image_purpose": "informative", "confidence": 0.9,'
        ' "reasoning": "x", "recommended_alt": "", "visual_elements": []}'
    )
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.detect_image_type(_image(tmp_path))

    assert result["success"] is True
    assert SPOKEN_MATH_INSTRUCTION not in client.prompts[0]


# ---------------------------------------------------------------------------
# The generic path speaks the mathematics it is given, or refuses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latex_in_a_generic_description_is_spoken(tmp_path):
    generator = ImageAltTextGenerator(lms_client=Vision(LATEX))

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    assert result["alt_text"] == SPOKEN
    assert result["long_description"] == SPOKEN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        r"Plot of $x^{2}+1$.",
        r"Value \(\alpha+\beta\) here",
        r"Value \[\frac{1}{2}+y=4\] here",
        r"Area is \pi r^{2} here",
    ],
)
async def test_every_latex_form_is_spoken_before_use(tmp_path, answer):
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    for fragment in ("\\", "$", "^", "{", "}"):
        assert fragment not in result["alt_text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        AMBIGUOUS_LATEX,
        r"Growth is $x^10$ here",
        r"The index is $a_ij$ here",
        r"Only $x$ is labelled",
        r"All \forall x here",
        r"Set $\{x, y\}$ here",
        "Plot \x0crac{a}{b}",
    ],
)
async def test_a_description_that_cannot_be_spoken_is_refused(tmp_path, answer):
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is False
    assert "alt_text" not in result
    assert result["error"] in {
        MATH_NOT_SPEAKABLE_ERROR,
        "Unusable or incomplete alt text response",
    }
    assert answer not in json.dumps(result)


@pytest.mark.asyncio
async def test_a_refusal_for_mathematics_is_named_and_never_retried(tmp_path):
    generator = ImageAltTextGenerator(lms_client=Vision(AMBIGUOUS_LATEX))

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["error"] == MATH_NOT_SPEAKABLE_ERROR
    # The recorder retries only failures that look like a provider problem.
    assert _classify_result_failure(result) == ("invalid_provider_response", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "A photograph of a laboratory bench with three beakers.",
        "A receipt listing a $5 coupon and a $10 coupon",
        r"A screenshot of C:\Users\bob\notes.txt open in an editor",
        'A code listing that calls print("hello") inside a loop',
        "A snippet where a^b and snake_case_name are used",
    ],
)
async def test_a_description_without_mathematics_is_unchanged(tmp_path, answer):
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    assert result["alt_text"] == answer


@pytest.mark.asyncio
async def test_speaking_that_makes_a_description_too_long_leaves_it_unusable(tmp_path):
    answer = " ".join([r"\alpha"] * 120)
    assert len(answer) < MAX_ALT_TEXT_LENGTH * 2
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is False


# ---------------------------------------------------------------------------
# Chart and diagram descriptions: every field is spoken, or none is used
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latex_in_every_chart_field_is_spoken(tmp_path):
    answer = _chart_answer(
        title=r"Plot of $x^{2}$",
        short_description=LATEX,
        detailed_description=r"The curve is \frac{a}{b} at the origin.",
        insights=[r"Rises like $x^{3}$", "Steady"],
        data_summary={"x_axis": r"\alpha", "data_points": [r"$x^{2}$", "2026"]},
    )
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is True
    assert result["title"] == "Plot of x squared"
    assert result["short_description"] == SPOKEN
    assert result["detailed_description"] == "The curve is a over b at the origin."
    assert result["insights"] == ["Rises like x cubed", "Steady"]
    assert result["data_summary"] == {
        "x_axis": "alpha",
        "data_points": ["x squared", "2026"],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["short_description", "detailed_description", "title", "insights", "data_summary"],
)
async def test_one_unspeakable_chart_field_refuses_the_whole_description(
    tmp_path, field
):
    value = {
        "insights": [AMBIGUOUS_LATEX],
        "data_summary": {"data_points": [AMBIGUOUS_LATEX]},
    }.get(field, AMBIGUOUS_LATEX)
    generator = ImageAltTextGenerator(
        lms_client=Vision(_chart_answer(**{field: value}))
    )

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is False
    assert result["error"] == MATH_NOT_SPEAKABLE_ERROR
    assert "short_description" not in result


@pytest.mark.asyncio
async def test_a_chart_description_without_mathematics_is_unchanged(tmp_path):
    answer = _chart_answer()
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    expected = json.loads(answer)
    for key, value in expected.items():
        assert result[key] == value


@pytest.mark.asyncio
async def test_an_unparseable_chart_answer_is_spoken_or_refused_like_any_other(
    tmp_path,
):
    spoken = await ImageAltTextGenerator(
        lms_client=Vision("Not JSON. The slope is \\frac{a}{b} here.")
    ).describe_chart_or_graph(_image(tmp_path))
    assert spoken["success"] is True
    assert spoken["detailed_description"] == "Not JSON. The slope is a over b here."

    refused = await ImageAltTextGenerator(
        lms_client=Vision("Not JSON. The slope is \\frac12 here.")
    ).describe_chart_or_graph(_image(tmp_path))
    assert refused["success"] is False
    assert refused["error"] == MATH_NOT_SPEAKABLE_ERROR


@pytest.mark.asyncio
async def test_a_command_swallowed_by_json_decoding_refuses_the_chart(tmp_path):
    # A model that writes a single backslash inside a JSON string: json.loads
    # turns \frac into a form feed followed by "rac".
    raw = _chart_answer().replace("A line rising to the right", "Slope \\frac{a}{b}")
    assert "\\f" in raw
    generator = ImageAltTextGenerator(lms_client=Vision(raw))

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is False


# ---------------------------------------------------------------------------
# A suggested replacement is alternative text too
# ---------------------------------------------------------------------------


def _validation(suggestion) -> str:
    return json.dumps(
        {
            "is_accurate": False,
            "accuracy_score": 0.1,
            "issues": ["too generic"],
            "suggested_improvement": suggestion,
            "reasoning": "x",
        }
    )


@pytest.mark.asyncio
async def test_a_suggested_replacement_is_spoken(tmp_path):
    generator = ImageAltTextGenerator(lms_client=Vision(_validation(LATEX)))

    result = await generator.validate_alt_text(_image(tmp_path), "image")

    assert result["success"] is True
    assert result["suggested_improvement"] == SPOKEN


@pytest.mark.asyncio
async def test_an_unspeakable_suggestion_is_withheld_and_the_verdict_kept(tmp_path):
    generator = ImageAltTextGenerator(lms_client=Vision(_validation(AMBIGUOUS_LATEX)))

    result = await generator.validate_alt_text(_image(tmp_path), "image")

    assert result["success"] is True
    assert result["is_accurate"] is False
    assert result["issues"] == ["too generic"]
    assert result["suggested_improvement"] is None


@pytest.mark.asyncio
async def test_no_suggestion_and_a_plain_suggestion_are_unchanged(tmp_path):
    none = await ImageAltTextGenerator(
        lms_client=Vision(_validation(None))
    ).validate_alt_text(_image(tmp_path), "image")
    plain = await ImageAltTextGenerator(
        lms_client=Vision(_validation("A blue square on white"))
    ).validate_alt_text(_image(tmp_path), "image")

    assert none["suggested_improvement"] is None
    assert plain["suggested_improvement"] == "A blue square on white"


# ---------------------------------------------------------------------------
# The request hash and the durable recorder
# ---------------------------------------------------------------------------


def _legacy_fingerprint(method_name: str, args, kwargs) -> str:
    parameters = {
        "version": "image-alt-text-request-v1",
        "method": method_name,
        "positional": list(args[1:]),
        "keyword": {key: value for key, value in kwargs.items() if key != "image_path"},
    }
    encoded = json.dumps(
        parameters,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.parametrize(
    "method", ["generate_alt_text", "describe_chart_or_graph", "validate_alt_text"]
)
def test_a_method_whose_prompt_changed_gets_a_new_request_hash(method):
    args = ("image.png",)
    assert _analysis_request_fingerprint(method, args, {}) != _legacy_fingerprint(
        method, args, {}
    )


@pytest.mark.parametrize("method", ["detect_image_type", "score_alt_text_quality"])
def test_a_method_whose_prompt_did_not_change_keeps_its_request_hash(method):
    args = ("image.png", "context")
    kwargs = {"detail": 1}
    assert _analysis_request_fingerprint(method, args, kwargs) == _legacy_fingerprint(
        method, args, kwargs
    )


def test_the_request_hash_is_still_stable_and_sensitive_to_arguments():
    one = _analysis_request_fingerprint("generate_alt_text", ("a.png",), {"x": 1})
    again = _analysis_request_fingerprint("generate_alt_text", ("a.png",), {"x": 1})
    other = _analysis_request_fingerprint("generate_alt_text", ("a.png",), {"x": 2})
    assert one == again
    assert one != other
    unhashable = _analysis_request_fingerprint(
        "generate_alt_text", ("a.png",), {"x": object()}
    )
    assert len(unhashable) == 64


@pytest.mark.asyncio
async def test_the_recorder_stores_and_reviews_the_spoken_description(tmp_path):
    db = _db()
    try:
        image_path = tmp_path / "image.png"
        image_path.write_bytes(_png_bytes())
        generator = ImageAltTextGenerator(
            lms_client=Vision(LATEX), visual_analysis_recorder=_recorder(db)
        )

        result = await generator.generate_alt_text(
            str(image_path),
            analysis_locator={"kind": "page_image", "page_number": 2, "image_xref": 17},
        )

        assert result["alt_text"] == SPOKEN
        with Session(db.get_bind()) as verify:
            analysis = verify.query(VisualAnalysis).one()
            assert analysis.proposal["alt_text"] == SPOKEN
            fix = verify.get(ScanFix, analysis.review_fix_id)
            assert fix.fixed_content == SPOKEN
    finally:
        db.close()


@pytest.mark.asyncio
async def test_an_unspeakable_description_is_recorded_as_a_failure_with_no_review_fix(
    tmp_path,
):
    db = _db()
    try:
        image_path = tmp_path / "image.png"
        image_path.write_bytes(_png_bytes())
        generator = ImageAltTextGenerator(
            lms_client=Vision(AMBIGUOUS_LATEX), visual_analysis_recorder=_recorder(db)
        )

        result = await generator.generate_alt_text(
            str(image_path),
            analysis_locator={"kind": "page_image", "page_number": 2, "image_xref": 17},
        )

        assert result["success"] is False
        with Session(db.get_bind()) as verify:
            analysis = verify.query(VisualAnalysis).one()
            assert analysis.status == "terminal_failure"
            assert analysis.review_fix_id is None
            assert verify.query(ScanFix).count() == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_an_analysis_stored_under_the_old_prompt_is_not_replayed(tmp_path):
    """The recorder replays a stored result without calling the provider."""
    from src.services.visual_analysis_service import (
        claim_visual_analysis,
        complete_visual_analysis,
        enqueue_visual_analysis,
    )

    db = _db()
    try:
        image_path = tmp_path / "image.png"
        image_path.write_bytes(_png_bytes())
        locator = {"kind": "page_image", "page_number": 2, "image_xref": 17}
        stale = {"alt_text": LATEX, "long_description": LATEX, "image_type": "Photo"}
        analysis = enqueue_visual_analysis(
            db,
            _request(
                request_fingerprint=_legacy_fingerprint("generate_alt_text", ("p",), {})
            ),
        )
        claim = claim_visual_analysis(db, analysis.id)
        complete_visual_analysis(
            db, claim, proposal=stale, provider="gemini", model="vision-safe"
        )
        db.commit()

        client = Vision("A blue square")
        generator = ImageAltTextGenerator(
            lms_client=client, visual_analysis_recorder=_recorder(db)
        )
        result = await generator.generate_alt_text(
            str(image_path), analysis_locator=locator
        )

        # The old analysis, with raw LaTeX in it, was not served.
        assert client.prompts, "the provider was not called"
        assert result["alt_text"] == "A blue square"
        with Session(db.get_bind()) as verify:
            assert verify.query(VisualAnalysis).count() == 2
    finally:
        db.close()


# ---------------------------------------------------------------------------
# The Word, PowerPoint and PDF remediators ask for, and apply, the same rule
# ---------------------------------------------------------------------------


def _manual_alt(result):
    return [i for i in result.manual_issues if i.category == IssueCategory.ALT_TEXT]


def _fixed_alt(result):
    return [i for i in result.fixed_issues if i.category == IssueCategory.ALT_TEXT]


def _pptx_run(tmp_path, content):
    source = tmp_path / "source.pptx"
    _make_pptx(source)
    issues = _alt_issues(PowerPointProcessor().process_pptx(str(source)).issues)
    assert issues
    client = Vision(content)
    result = PptxRemediator(
        str(source), issues, _config(tmp_path), alt_text_client=client
    ).remediate()
    return result, client


def _docx_run(tmp_path, content):
    source = tmp_path / "source.docx"
    _make_docx(source)
    issues = _alt_issues(DocxProcessor().process_docx(str(source)).issues)
    assert issues
    client = Vision(content)
    result = DocxRemediator(
        str(source), issues, _config(tmp_path), alt_text_client=client
    ).remediate()
    return result, client


# The PowerPoint scanner reads a picture's alternative text from the picture
# element rather than from its non-visual properties, so its saved-file rescan
# does not see a description that is there. What the remediator wrote is checked
# in the saved file for both formats; the verified outcome is checked for Word.
@pytest.mark.parametrize(
    ("run", "verified"), [(_pptx_run, False), (_docx_run, True)], ids=["pptx", "docx"]
)
def test_an_office_remediator_writes_the_spoken_words(tmp_path, run, verified):
    result, client = run(tmp_path, LATEX)

    assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]
    written = _office_text(result.output_file)
    assert SPOKEN in written, "the spoken description was not written"
    assert "frac" not in written
    if verified:
        assert _fixed_alt(result)
        assert _manual_alt(result) == []


@pytest.mark.parametrize("run", [_pptx_run, _docx_run], ids=["pptx", "docx"])
@pytest.mark.parametrize(
    "content", [AMBIGUOUS_LATEX, r"The growth is $x^10$ here"], ids=["frac", "script"]
)
def test_an_office_remediator_leaves_an_unspeakable_description_for_review(
    tmp_path, run, content
):
    result, client = run(tmp_path, content)

    assert _fixed_alt(result) == []
    manual = _manual_alt(result)
    assert manual, "the image was not left for human review"
    assert manual[0].reason == IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON
    explanation = manual_explanation(manual[0])
    assert explanation["reason_code"] == IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON
    assert explanation["attempt"] == "not_applied"
    written = _office_text(result.output_file) if result.output_file else ""
    assert "frac" not in written
    assert "The equation" not in written
    assert "growth" not in written
    assert client.text_calls == 0


@pytest.mark.parametrize("run", [_pptx_run, _docx_run], ids=["pptx", "docx"])
def test_a_plain_description_is_written_exactly_as_before(tmp_path, run):
    result, _client = run(tmp_path, "A green square on a white page")

    assert "A green square on a white page" in _office_text(result.output_file)


def _pdf_run(tmp_path, content):
    source = tmp_path / "source.pdf"
    _make_tagged_pdf(source)
    issues = _alt_issues(PDFProcessor().process_pdf(str(source)).issues)
    assert issues
    client = Vision(content)
    result = PdfRemediator(
        str(source), issues, _config(tmp_path), alt_text_client=client
    ).remediate()
    return result, client


def test_the_pdf_remediator_writes_the_spoken_words(tmp_path):
    result, client = _pdf_run(tmp_path, LATEX)
    try:
        assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]
        assert _fixed_alt(result)
        assert _pdf_contains(result.output_file, SPOKEN)
        assert not _pdf_contains(result.output_file, "frac{a}")
    finally:
        result.close_output_claim()


def test_the_pdf_remediator_leaves_an_unspeakable_description_for_review(tmp_path):
    result, client = _pdf_run(tmp_path, AMBIGUOUS_LATEX)
    try:
        assert _fixed_alt(result) == []
        manual = _manual_alt(result)
        assert manual
        assert manual[0].reason == IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON
        assert (
            manual_explanation(manual[0])["reason_code"]
            == IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON
        )
        if result.output_file:
            assert not _pdf_contains(result.output_file, "The equation")
    finally:
        result.close_output_claim()


def test_the_public_explanation_for_a_refused_description_is_honest():
    from src.education.remediation.outcome_explanations import EXPLANATIONS

    reason, next_step, attempt = EXPLANATIONS[
        IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON
    ]
    assert "mathematical notation" in reason
    assert "no description was written" in reason
    assert "plain words" in next_step
    assert attempt == "not_applied"
