"""Generated image descriptions carry no mathematical markup, or are refused (#710).

A vision model that meets an equation can answer in LaTeX, which a screen reader
announces as backslashes, braces and dollar signs. Every prompt that asks for a
description asks for plain spoken words, and every description a generator
proposes passes through one check before it can be used. The check never
rewrites: a description is used as it is, or refused.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy.orm import Session

from src.db.models import ScanFix, VisualAnalysis
from src.education.docx_processor import DocxProcessor
from src.education.image_alt_text import (
    ImageAltTextGenerator,
    _analysis_request_fingerprint,
)
from src.education.math_speech import (
    IMAGE_DESCRIPTION_MATH_NOT_SPEAKABLE_REASON,
    MATH_NOT_SPEAKABLE_ERROR,
    MATH_REFUSAL_MESSAGE,
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

LATEX = r"The equation \frac{a}{b} = c"
PLAIN = "A green square on a white page"


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


class Sequence:
    """A vision client that answers each call with the next content in turn."""

    provider = "gemini"
    model = "vision-safe"

    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.prompts: list[str] = []

    def analyze_image_sync(self, *, image_data, prompt, max_tokens):
        self.prompts.append(prompt)
        return {
            "success": True,
            "content": self.contents.pop(0),
            "provider": "gemini",
            "model": "vision-safe",
        }


def _raw_json(**fields) -> str:
    """A JSON answer in which every backslash was written as a single one.

    A model that writes LaTeX inside a JSON string does this, and JSON decoding
    then turns the first letter of some commands into control characters.
    """
    return json.dumps(fields).replace("\\\\", "\\")


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


def _detection(alt) -> str:
    return json.dumps(
        {
            "is_decorative": False,
            "image_purpose": "informative",
            "confidence": 0.9,
            "reasoning": "x",
            "recommended_alt": alt,
            "visual_elements": [],
        }
    )


@pytest.mark.asyncio
async def test_the_classification_prompt_asks_for_spoken_mathematics(tmp_path):
    """Its recommended alt text is alternative text, so its prompt asks too."""
    client = Vision(_detection(""))
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.detect_image_type(_image(tmp_path))

    assert result["success"] is True
    assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]


# ---------------------------------------------------------------------------
# The generic path uses a description as it is, or refuses it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        LATEX,
        r"The equation \frac12 = c",
        r"Plot of $x^{2}+1$.",
        r"Value \(\alpha+\beta\) here",
        r"Value \[\frac{1}{2}+y=4\] here",
        r"The area is \pi r^2",
        r"y = x^2 + \sin x",
        r"$\int_0^1 x\,dx$",
        r"The value $|x|$ is positive",
        r"Part B:\frac{1}{2}",
        r"All \forall x here",
        r"Set $\{x, y\}$ here",
    ],
)
async def test_a_description_with_mathematical_markup_is_refused(tmp_path, answer):
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is False
    assert "alt_text" not in result
    assert result["error"] == MATH_NOT_SPEAKABLE_ERROR
    assert answer not in json.dumps(result)


@pytest.mark.asyncio
async def test_a_refusal_for_mathematics_is_named_and_never_retried(tmp_path):
    generator = ImageAltTextGenerator(lms_client=Vision(LATEX))

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
        "Bar chart of revenue ($) and cost ($) by year",
        "Revenue ($M) and profit ($M) by quarter",
        "Revenue ($K) vs expenses ($K)",
        "Salaries ($) and bonuses ($)",
        r"A screenshot of C:\Users\bob\notes.txt open in an editor",
        'A code listing that calls print("hello") inside a loop',
        "A snippet where snake_case_name is used",
        "The graph of x squared opens upward.",
    ],
)
async def test_a_description_without_markup_is_used_exactly_as_it_came(
    tmp_path, answer
):
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.generate_alt_text(_image(tmp_path))

    assert result["success"] is True
    assert result["alt_text"] == answer
    assert result["long_description"] == answer


@pytest.mark.asyncio
async def test_the_description_is_never_converted(tmp_path):
    """A description with mathematics is refused whole, never rewritten."""
    for answer in (LATEX, r"Area is \pi r^{2} here", r"The solution is $x \le 3$"):
        result = await ImageAltTextGenerator(
            lms_client=Vision(answer)
        ).generate_alt_text(_image(tmp_path))
        assert result["success"] is False
        assert "alt_text" not in result


# ---------------------------------------------------------------------------
# Chart and diagram descriptions: any field with markup refuses the whole answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    ["short_description", "detailed_description", "title", "insights", "data_summary"],
)
async def test_one_chart_field_with_markup_refuses_the_whole_description(
    tmp_path, field
):
    value = {
        "insights": [LATEX],
        "data_summary": {"data_points": [r"$x^{2}$", "2026"]},
    }.get(field, LATEX)
    generator = ImageAltTextGenerator(
        lms_client=Vision(_chart_answer(**{field: value}))
    )

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is False
    assert result["error"] == MATH_NOT_SPEAKABLE_ERROR
    assert "short_description" not in result


@pytest.mark.asyncio
async def test_a_chart_description_without_markup_is_unchanged(tmp_path):
    answer = _chart_answer(detailed_description="Para one.\n\nPara two follows.")
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    expected = json.loads(answer)
    for key, value in expected.items():
        assert result[key] == value


@pytest.mark.asyncio
async def test_an_unparseable_chart_answer_with_markup_is_refused(tmp_path):
    refused = await ImageAltTextGenerator(
        lms_client=Vision("Not JSON. The slope is \\frac{a}{b} here.")
    ).describe_chart_or_graph(_image(tmp_path))
    assert refused["success"] is False
    assert refused["error"] == MATH_NOT_SPEAKABLE_ERROR

    plain = await ImageAltTextGenerator(
        lms_client=Vision("Not JSON. The slope rises to the right.")
    ).describe_chart_or_graph(_image(tmp_path))
    assert plain["success"] is True
    assert plain["detailed_description"] == "Not JSON. The slope rises to the right."


# ---------------------------------------------------------------------------
# LaTeX written with single backslashes inside JSON survives to be refused
# ---------------------------------------------------------------------------

# JSON decoding turns the start of these commands into a line feed, a tab, a form
# feed or a backspace, so the command is gone before the text can be checked.
SWALLOWED = [
    r"$a \ne b$",
    r"$\nabla f$",
    r"$x\times y$",
    r"$a\to b$",
    r"$\frac{a}{b}$",
    r"$\beta + 1$",
    r"$\rho$",
    r"$\theta$",
    r"$a \neq b$",
    r"The slope is \frac{a}{b} here",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("span", SWALLOWED)
async def test_latex_swallowed_by_json_decoding_is_refused_in_a_chart(tmp_path, span):
    raw = _raw_json(
        chart_type="line graph",
        title="Growth",
        short_description=f"A line where {span} holds",
        detailed_description="The line rises.",
        data_summary={},
        insights=[],
        visual_elements={},
        accessibility_note="x",
    )
    # The single backslash really is a JSON escape here, for the cases that matter.
    generator = ImageAltTextGenerator(lms_client=Vision(raw))

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is False
    assert result["error"] == MATH_NOT_SPEAKABLE_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("span", SWALLOWED)
async def test_latex_swallowed_by_json_decoding_is_withheld_from_a_suggestion(
    tmp_path, span
):
    raw = _raw_json(
        is_accurate=False,
        accuracy_score=0.1,
        issues=["too generic"],
        suggested_improvement=f"A line where {span} holds",
        reasoning="x",
    )
    generator = ImageAltTextGenerator(lms_client=Vision(raw))

    result = await generator.validate_alt_text(_image(tmp_path), "image")

    assert result["success"] is True
    assert result["is_accurate"] is False
    assert result["suggested_improvement"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("span", SWALLOWED)
async def test_latex_swallowed_by_json_decoding_is_withheld_from_a_recommendation(
    tmp_path, span
):
    raw = _raw_json(
        is_decorative=False,
        image_purpose="informative",
        confidence=0.9,
        reasoning="x",
        recommended_alt=f"A line where {span} holds",
        visual_elements=[],
    )
    generator = ImageAltTextGenerator(lms_client=Vision(raw))

    result = await generator.detect_image_type(_image(tmp_path))

    assert result["success"] is True
    assert result["recommended_alt"] is None
    assert result["image_purpose"] == "informative"


@pytest.mark.asyncio
async def test_a_correctly_escaped_answer_with_line_breaks_is_not_repaired(tmp_path):
    answer = _chart_answer(
        detailed_description="First paragraph.\n\nnothing else follows.\nother text"
    )
    generator = ImageAltTextGenerator(lms_client=Vision(answer))

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is True
    assert result["detailed_description"] == (
        "First paragraph.\n\nnothing else follows.\nother text"
    )


# ---------------------------------------------------------------------------
# A suggested replacement and a recommended alt text are alternative text too
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
async def test_a_suggestion_with_markup_is_withheld_and_the_verdict_kept(tmp_path):
    generator = ImageAltTextGenerator(lms_client=Vision(_validation(LATEX)))

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


@pytest.mark.asyncio
async def test_a_recommended_alt_with_markup_is_none_not_empty(tmp_path):
    refused = await ImageAltTextGenerator(
        lms_client=Vision(_detection(LATEX))
    ).detect_image_type(_image(tmp_path))
    decorative = await ImageAltTextGenerator(
        lms_client=Vision(_detection(""))
    ).detect_image_type(_image(tmp_path))
    plain = await ImageAltTextGenerator(
        lms_client=Vision(_detection("A blue square"))
    ).detect_image_type(_image(tmp_path))

    # Empty means decorative, so a withheld suggestion must not look like it.
    assert refused["recommended_alt"] is None
    assert decorative["recommended_alt"] == ""
    assert plain["recommended_alt"] == "A blue square"
    assert refused["success"] is True


# ---------------------------------------------------------------------------
# Comprehensive analysis keeps going when the description was refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_description_with_an_inaccurate_existing_alt_is_reported(
    tmp_path,
):
    client = Sequence(
        _detection(""),  # classification: informative
        LATEX,  # the description, refused
        _validation("A blue square"),  # the existing alt text is inaccurate
    )
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.analyze_image_comprehensive(
        _image(tmp_path), existing_alt_text="image"
    )

    assert result["success"] is True
    assert result["description"]["success"] is False
    assert result["description"]["error"] == MATH_NOT_SPEAKABLE_ERROR
    recommendation = result["recommendation"]
    assert recommendation["needs_update"] is True
    assert recommendation["validation_issues"] == ["too generic"]
    assert "alt_text" not in recommendation


@pytest.mark.asyncio
async def test_a_batch_counts_a_refused_description_that_needs_an_update(tmp_path):
    client = Sequence(_detection(""), LATEX, _validation(None))
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.batch_analyze_images(
        [_image(tmp_path)], include_existing_alt={_image(tmp_path): "image"}
    )

    assert result["summary"]["needs_update"] == 1


# ---------------------------------------------------------------------------
# The image routes say so honestly when a description was refused
# ---------------------------------------------------------------------------


def _route_module():
    from src.api.education import image_routes

    return image_routes


def test_a_refused_description_is_not_a_server_error():
    from fastapi import HTTPException

    routes = _route_module()
    with pytest.raises(HTTPException) as refused:
        routes._raise_if_description_refused(
            {"success": False, "error": MATH_NOT_SPEAKABLE_ERROR}
        )
    assert refused.value.status_code == 422
    assert refused.value.detail == MATH_REFUSAL_MESSAGE
    assert "mathematical notation" in MATH_REFUSAL_MESSAGE
    # Any other failure still falls through to the generic server error.
    routes._raise_if_description_refused({"success": False, "error": "timeout"})


class _Upload:
    filename = "image.png"

    def __init__(self, content: bytes) -> None:
        self._content = content

    async def read(self) -> bytes:
        return self._content

    async def seek(self, _offset: int) -> int:
        return 0


class _RefusingGenerator:
    async def generate_alt_text(self, **_kwargs):
        return {"success": False, "error": MATH_NOT_SPEAKABLE_ERROR}

    async def describe_chart_or_graph(self, **_kwargs):
        return {"success": False, "error": MATH_NOT_SPEAKABLE_ERROR}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["alt_text", "chart"])
async def test_the_alt_text_and_chart_routes_answer_422_for_a_refused_description(
    tmp_path, monkeypatch, route
):
    from fastapi import HTTPException

    routes = _route_module()
    image = Path(_image(tmp_path)).read_bytes()

    async def quota(*_args, **_kwargs):
        return None

    async def validated(_file, _db, _department):
        return image

    monkeypatch.setattr(
        routes, "_workspace_image_generator", lambda _id: _RefusingGenerator()
    )
    monkeypatch.setattr(routes, "check_image_analysis_quota", quota)
    monkeypatch.setattr(routes, "validate_uploaded_file", validated)
    api_key = (None, "user-1", "department-1")

    with pytest.raises(HTTPException) as error:
        if route == "alt_text":
            await routes.generate_image_alt_text(
                file=_Upload(image),
                context=None,
                educational_context=True,
                db=None,
                api_key_info=api_key,
            )
        else:
            await routes.describe_chart_or_graph(
                file=_Upload(image),
                context=None,
                detail_level="standard",
                api_key_info=api_key,
                db=None,
            )

    assert error.value.status_code == 422
    assert error.value.detail == MATH_REFUSAL_MESSAGE


# ---------------------------------------------------------------------------
# The request hash and the durable recorder
# ---------------------------------------------------------------------------


def _fingerprint_with(method_name: str, args, kwargs, revision=None) -> str:
    parameters = {
        "version": "image-alt-text-request-v1",
        "method": method_name,
        "positional": list(args[1:]),
        "keyword": {key: value for key, value in kwargs.items() if key != "image_path"},
    }
    if revision is not None:
        parameters["prompt_revision"] = revision
    encoded = json.dumps(
        parameters,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _legacy_fingerprint(method_name: str, args, kwargs) -> str:
    return _fingerprint_with(method_name, args, kwargs)


REVISED_METHODS = [
    "generate_alt_text",
    "describe_chart_or_graph",
    "validate_alt_text",
    "detect_image_type",
]


@pytest.mark.parametrize("method", REVISED_METHODS)
def test_a_method_whose_prompt_or_rules_changed_gets_a_new_request_hash(method):
    args = ("image.png",)
    current = _analysis_request_fingerprint(method, args, {})
    assert current != _legacy_fingerprint(method, args, {})
    # The first revision, which converted descriptions, is not replayed either.
    assert current != _fingerprint_with(method, args, {}, "spoken-math-v1")
    assert current == _fingerprint_with(method, args, {}, "spoken-math-v2")


def test_a_method_that_did_not_change_keeps_its_request_hash():
    args = ("image.png", "context")
    kwargs = {"detail": 1}
    assert _analysis_request_fingerprint(
        "score_alt_text_quality", args, kwargs
    ) == _legacy_fingerprint("score_alt_text_quality", args, kwargs)


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
async def test_the_recorder_stores_and_reviews_a_plain_description(tmp_path):
    db = _db()
    try:
        image_path = tmp_path / "image.png"
        image_path.write_bytes(_png_bytes())
        generator = ImageAltTextGenerator(
            lms_client=Vision(PLAIN), visual_analysis_recorder=_recorder(db)
        )

        result = await generator.generate_alt_text(
            str(image_path),
            analysis_locator={"kind": "page_image", "page_number": 2, "image_xref": 17},
        )

        assert result["alt_text"] == PLAIN
        with Session(db.get_bind()) as verify:
            analysis = verify.query(VisualAnalysis).one()
            assert analysis.proposal["alt_text"] == PLAIN
            assert verify.get(ScanFix, analysis.review_fix_id).fixed_content == PLAIN
    finally:
        db.close()


@pytest.mark.asyncio
async def test_a_description_with_markup_is_recorded_as_a_failure_with_no_review_fix(
    tmp_path,
):
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

        assert result["success"] is False
        with Session(db.get_bind()) as verify:
            analysis = verify.query(VisualAnalysis).one()
            assert analysis.status == "terminal_failure"
            assert analysis.review_fix_id is None
            assert verify.query(ScanFix).count() == 0
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [None, "spoken-math-v1"])
async def test_an_analysis_stored_under_earlier_rules_is_not_replayed(
    tmp_path, revision
):
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
                request_fingerprint=_fingerprint_with(
                    "generate_alt_text", ("p",), {}, revision
                )
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
# Video frames: a refused chart description does not fall back to unchecked text
# ---------------------------------------------------------------------------


class _ChartRefusingGenerator:
    """An image generator that classifies a frame as a chart, then refuses it."""

    async def detect_image_type(self, **_kwargs):
        return {
            "success": True,
            "image_type": "complex",
            "image_purpose": "chart",
            "is_decorative": False,
        }

    async def describe_chart_or_graph(self, **_kwargs):
        return {"success": False, "error": MATH_NOT_SPEAKABLE_ERROR}


def _frame_processor(tmp_path, monkeypatch, answer, recorder=None):
    from src.education.multimedia_processor import MultimediaProcessor

    frame = tmp_path / "frame.png"
    frame.write_bytes(_png_bytes())
    client = Vision(answer)
    processor = MultimediaProcessor(
        llm_client=client, visual_analysis_recorder=recorder
    )
    monkeypatch.setattr(
        processor, "_get_image_generator", lambda: _ChartRefusingGenerator()
    )
    return processor, client, str(frame)


def test_a_scene_description_with_markup_is_dropped_after_a_refused_chart(
    tmp_path, monkeypatch
):
    processor, client, frame = _frame_processor(tmp_path, monkeypatch, LATEX)

    result = processor._describe_keyframe(client, frame, 12.5)

    assert result is None
    # The fallback did run, and it was held to the same rule.
    assert client.prompts


def test_a_plain_scene_description_is_used_after_a_refused_chart(tmp_path, monkeypatch):
    answer = "A lecturer points to a projected chart"
    processor, client, frame = _frame_processor(tmp_path, monkeypatch, answer)

    result = processor._describe_keyframe(client, frame, 12.5)

    assert result is not None
    assert result.description == answer
    assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]


def test_an_audio_description_stored_under_the_old_prompt_is_not_replayed(
    tmp_path, monkeypatch
):
    from src.education.multimedia_processor import _AUDIO_DESCRIPTION_FINGERPRINT
    from src.services.visual_analysis_service import (
        claim_visual_analysis,
        complete_visual_analysis,
        enqueue_visual_analysis,
    )

    db = _db()
    try:
        locator = {"kind": "media_frame", "timestamp_ms": 12500}
        stale = _request(
            purpose="audio_description", source_locator=locator, source_kind="image"
        )
        analysis = enqueue_visual_analysis(db, stale)
        claim = claim_visual_analysis(db, analysis.id)
        complete_visual_analysis(
            db,
            claim,
            proposal={
                "description": r"The slope \frac{a}{b}",
                "scene_type": "setting",
                "importance": "medium",
                "timestamp_ms": 12500,
            },
            provider="gemini",
            model="vision-safe",
        )
        db.commit()
        assert stale.request_fingerprint != _AUDIO_DESCRIPTION_FINGERPRINT

        answer = "A lecturer points to a projected chart"
        processor, client, frame = _frame_processor(
            tmp_path, monkeypatch, answer, recorder=_recorder(db)
        )
        result = processor._describe_keyframe(client, frame, 12.5)

        assert client.prompts, "the provider was not called"
        assert result is not None and result.description == answer
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


# A description with markup after the first 125 characters, which a truncation
# before the check would cut off and then write.
LATE_MARKUP = "A long plain description of a green square on a white page. " * 3 + (
    r"It also shows \frac{a}{b}."
)
assert len(LATE_MARKUP.split(r"\frac")[0]) > 125


@pytest.mark.parametrize("run", [_pptx_run, _docx_run], ids=["pptx", "docx"])
@pytest.mark.parametrize(
    "content",
    [LATEX, r"The growth is $x^{n}$ here", LATE_MARKUP],
    ids=["frac", "script", "late"],
)
def test_an_office_remediator_leaves_a_description_with_markup_for_review(
    tmp_path, run, content
):
    result, client = run(tmp_path, content)

    assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]
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
    assert "long plain description" not in written
    assert client.text_calls == 0


@pytest.mark.parametrize(
    ("run", "verified"), [(_pptx_run, False), (_docx_run, True)], ids=["pptx", "docx"]
)
def test_an_office_remediator_writes_a_plain_description_exactly_as_it_came(
    tmp_path, run, verified
):
    # The PowerPoint scanner reads a picture's alternative text from the picture
    # element rather than from its non-visual properties, so its saved-file
    # rescan does not see a description that is there. What was written is
    # checked in the saved file for both formats, the verified outcome for Word.
    result, client = run(tmp_path, PLAIN)

    assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]
    assert PLAIN in _office_text(result.output_file)
    if verified:
        assert _fixed_alt(result)
        assert _manual_alt(result) == []


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


def test_the_pdf_remediator_writes_a_plain_description(tmp_path):
    result, client = _pdf_run(tmp_path, PLAIN)
    try:
        assert SPOKEN_MATH_INSTRUCTION in client.prompts[0]
        assert _fixed_alt(result)
        assert _pdf_contains(result.output_file, PLAIN)
    finally:
        result.close_output_claim()


@pytest.mark.parametrize("content", [LATEX, LATE_MARKUP], ids=["frac", "late"])
def test_the_pdf_remediator_leaves_a_description_with_markup_for_review(
    tmp_path, content
):
    result, client = _pdf_run(tmp_path, content)
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
            assert not _pdf_contains(result.output_file, "long plain description")
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


# ---------------------------------------------------------------------------
# The HTML code scanner's suggested replacement is held to the same rule
# ---------------------------------------------------------------------------


class _TextClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    def generate_text_sync(self, *, prompt, **_kwargs):
        self.prompts.append(prompt)
        return {"success": True, "content": self.content}


def _scanner(content: str):
    from src.education.code_scanner import CodeScanner

    scanner = CodeScanner.__new__(CodeScanner)
    scanner.llm_client = _TextClient(content)
    return scanner


def _validate(content: str):
    scanner = _scanner(content)
    return scanner, scanner._validate_alt_text_quality(
        "Line chart of enrollment by year", "chart.png"
    )


def test_a_code_scanner_suggestion_with_markup_is_withheld():
    answer = json.dumps(
        {
            "is_valid": False,
            "issues": ["too generic"],
            "reasoning": "x",
            "suggested_improvement": r"A line where $a \ne b$ holds",
        }
    )
    scanner, result = _validate(answer)

    assert SPOKEN_MATH_INSTRUCTION in scanner.llm_client.prompts[0]
    assert result["is_valid"] is False
    assert result["suggested_improvement"] is None


def test_a_code_scanner_suggestion_that_cannot_be_parsed_leaks_no_markup():
    # The scanner reads the first brace-delimited object, so a suggestion that
    # holds braces cannot be parsed and the heuristic result is used instead.
    answer = json.dumps(
        {
            "is_valid": False,
            "issues": ["too generic"],
            "reasoning": "x",
            "suggested_improvement": LATEX,
        }
    )
    _scanner_instance, result = _validate(answer)

    assert "frac" not in json.dumps(result)


def test_a_plain_code_scanner_suggestion_is_unchanged():
    answer = json.dumps(
        {
            "is_valid": False,
            "issues": ["too generic"],
            "reasoning": "x",
            "suggested_improvement": "A line rising to the right",
        }
    )
    _scanner_instance, result = _validate(answer)

    assert result["suggested_improvement"] == "A line rising to the right"
