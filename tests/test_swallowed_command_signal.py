"""A repaired JSON answer is not trusted as a description, whatever it then looks like.

JSON decoding turns the first letter of ``\\ne`` into a line feed. The repair gives
the backslash back, and ``categories.\\ne.g.`` then looks like a file path to the
path rule. ``parse_tolerant`` therefore reports that it found a swallowed command,
and every caller that takes a description from the answer refuses the description
on that report, with no regard for how the text looks afterwards.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from src.education.image_alt_text import ImageAltTextGenerator
from src.education.math_speech import MATH_NOT_SPEAKABLE_ERROR
from src.education.model_json import loads_tolerant, parse_tolerant

BS = "\\"

# A model that meant a line break and then "e.g." writes ``\ne.g.``, which is also
# how it writes the negation ``\ne``. The two cannot be told apart, so both are
# refused.
LOOKALIKES = [
    "Three categories." + BS + "ne.g. sales and costs",
    "Values" + BS + "ni.e. the mean",
    "Line one" + BS + "ne.g. two",
]


def _raw_json(**fields) -> str:
    """A JSON answer in which every backslash was written as a single one."""
    return json.dumps(fields).replace("\\\\", "\\")


class Vision:
    provider = "gemini"
    model = "vision-safe"

    def __init__(self, content: str) -> None:
        self.content = content

    def analyze_image_sync(self, *, image_data, prompt, max_tokens):
        return {
            "success": True,
            "content": self.content,
            "provider": "gemini",
            "model": "vision-safe",
        }


def _image(tmp_path: Path) -> str:
    path = tmp_path / "image.png"
    Image.new("RGB", (10, 10), color="blue").save(path)
    return str(path)


# ---------------------------------------------------------------------------
# The signal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", LOOKALIKES)
def test_a_repaired_swallowed_command_is_reported(text):
    raw = _raw_json(description=text)
    parsed, swallowed = parse_tolerant(raw)

    assert swallowed is True
    assert parsed["description"] == text
    assert loads_tolerant(raw) == parsed


def test_the_signal_does_not_depend_on_how_the_repaired_text_looks(monkeypatch):
    """The callers refuse on the flag even if the markup rule would let the text by."""
    from src.education import image_alt_text, math_speech

    monkeypatch.setattr(math_speech, "refuse_math_markup", lambda text: text)
    monkeypatch.setattr(image_alt_text, "refuse_math_markup", lambda text: text)
    for text in LOOKALIKES:
        assert math_speech.refuse_math_markup(text) == text
        assert parse_tolerant(_raw_json(description=text))[1] is True


def test_an_answer_that_also_has_an_invalid_escape_is_still_reported():
    raw = '{"d": "Saved to C:' + BS + "Users and three." + BS + 'ne.g. sales"}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)

    parsed, swallowed = parse_tolerant(raw)

    assert swallowed is True
    assert parsed["d"].startswith("Saved to C:" + BS + "Users and three.")


@pytest.mark.parametrize(
    "raw",
    [
        # A Windows path with invalid escapes is repaired and not suspect.
        '{"d": "Saved to C:' + BS + "Users" + BS + 'bob" }',
        '{"d": "Open C:' + BS + "Program Files" + BS + 'App"}',
        # A correctly escaped answer is never repaired.
        json.dumps({"d": "First line.\nSecond line.\n\nThird."}),
        json.dumps({"d": "A real tab\tand a real line break\nnothing odd"}),
        # A line break before a word that does not complete a command.
        '{"d": "Three categories.' + BS + 'nsales and costs"}',
    ],
)
def test_an_answer_that_holds_no_swallowed_command_is_not_reported(raw):
    assert parse_tolerant(raw)[1] is False


# ---------------------------------------------------------------------------
# Every caller refuses a description from a repaired answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("text", LOOKALIKES)
async def test_a_chart_description_from_a_repaired_answer_is_refused(tmp_path, text):
    raw = _raw_json(
        chart_type="bar chart",
        title="Sales",
        short_description=text,
        detailed_description="Bars of equal width.",
        data_summary={},
        insights=[],
        visual_elements={},
        accessibility_note="x",
    )
    generator = ImageAltTextGenerator(lms_client=Vision(raw))

    result = await generator.describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is False
    assert result["error"] == MATH_NOT_SPEAKABLE_ERROR


@pytest.mark.asyncio
async def test_a_chart_answer_whose_other_fields_are_clean_is_refused_as_a_whole(
    tmp_path,
):
    raw = _raw_json(
        chart_type="bar chart",
        title="Sales",
        short_description="Bars of equal width.",
        detailed_description="Three categories." + BS + "ne.g. sales and costs",
        data_summary={},
        insights=[],
        visual_elements={},
        accessibility_note="x",
    )

    result = await ImageAltTextGenerator(
        lms_client=Vision(raw)
    ).describe_chart_or_graph(_image(tmp_path))

    assert result["success"] is False
    assert result["error"] == MATH_NOT_SPEAKABLE_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("text", LOOKALIKES)
async def test_a_suggestion_from_a_repaired_answer_is_withheld(tmp_path, text):
    raw = _raw_json(
        is_accurate=False,
        accuracy_score=0.1,
        issues=["too generic"],
        suggested_improvement=text,
        reasoning="x",
    )

    result = await ImageAltTextGenerator(lms_client=Vision(raw)).validate_alt_text(
        _image(tmp_path), "image"
    )

    assert result["success"] is True
    assert result["is_accurate"] is False
    assert result["suggested_improvement"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", LOOKALIKES)
async def test_a_recommendation_from_a_repaired_answer_is_withheld(tmp_path, text):
    raw = _raw_json(
        is_decorative=False,
        image_purpose="informative",
        confidence=0.9,
        reasoning="x",
        recommended_alt=text,
        visual_elements=[],
    )

    result = await ImageAltTextGenerator(lms_client=Vision(raw)).detect_image_type(
        _image(tmp_path)
    )

    assert result["success"] is True
    assert result["recommended_alt"] is None
    assert result["image_purpose"] == "informative"


@pytest.mark.asyncio
async def test_a_description_with_a_path_in_an_answer_that_needed_only_a_path_repair_is_kept(
    tmp_path,
):
    raw = (
        '{"is_accurate": false, "accuracy_score": 0.2, "issues": [], '
        '"suggested_improvement": "A screenshot of C:' + BS + "Users" + BS + 'bob",'
        ' "reasoning": "x"}'
    )

    result = await ImageAltTextGenerator(lms_client=Vision(raw)).validate_alt_text(
        _image(tmp_path), "image"
    )

    assert result["suggested_improvement"] == (
        "A screenshot of C:" + BS + "Users" + BS + "bob"
    )


def _scanner(content: str):
    from src.education.code_scanner import CodeScanner

    class Text:
        def generate_text_sync(self, *, prompt, **_kwargs):
            return {"success": True, "content": content}

    scanner = CodeScanner.__new__(CodeScanner)
    scanner.llm_client = Text()
    return scanner


@pytest.mark.parametrize("text", LOOKALIKES)
def test_a_code_scanner_suggestion_from_a_repaired_answer_is_withheld(text):
    scanner = _scanner(
        _raw_json(
            is_valid=False,
            issues=["too generic"],
            reasoning="x",
            suggested_improvement=text,
        )
    )

    result = scanner._validate_alt_text_quality("Line chart of sales", "chart.png")

    assert result["is_valid"] is False
    assert result["suggested_improvement"] is None
