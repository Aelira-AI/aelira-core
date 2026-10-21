"""Generated alt text that spans several lines is folded, not discarded.

Vision models describe tables, lists and multi-part figures over several lines.
Line breaks and tabs are layout, so they become single spaces. Every other
control, format or surrogate character still rejects the description, because
those can hide content or mark a swallowed escape such as a LaTeX ``\\frac``.
"""

from unittest.mock import MagicMock

import pytest
from PIL import Image

from src.education.alt_text_quality import (
    FOLDED_WHITESPACE,
    MAX_ALT_TEXT_LENGTH,
    is_usable_alt_text,
    normalize_usable_alt_text,
)
from src.education.image_alt_text import ImageAltTextGenerator
from src.education.remediation.base import IssueCategory, RemediationIssue
from src.education.remediation.html_remediator import HtmlRemediator

pytestmark = pytest.mark.unit

TABLE = "Table of quiz marks by week:\nWeek 1: 72\nWeek 2: 80\nWeek 3: 91"
TABLE_FOLDED = "Table of quiz marks by week: Week 1: 72 Week 2: 80 Week 3: 91"


def test_multi_line_table_description_is_accepted_as_one_line():
    assert normalize_usable_alt_text(TABLE) == TABLE_FOLDED
    assert is_usable_alt_text(TABLE) is True


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Row 1\r\nRow 2", "Row 1 Row 2"),
        ("Row 1\rRow 2", "Row 1 Row 2"),
        ("Name\tScore\tGrade", "Name Score Grade"),
        ("Row 1 Row 2", "Row 1 Row 2"),
        ("Para one Para two", "Para one Para two"),
        ("a \n\t \r\n b", "a b"),
        ("\n\nA bar chart of marks\r\n", "A bar chart of marks"),
        (
            "Header\t|\tScore\r\nAda\t|\t91\r\nBo\t|\t84",
            "Header | Score Ada | 91 Bo | 84",
        ),
    ],
)
def test_line_breaks_and_tabs_fold_into_single_spaces(text, expected):
    assert normalize_usable_alt_text(text) == expected


def test_every_folded_character_becomes_one_space():
    assert set(FOLDED_WHITESPACE) == {"\n", "\r", "\t", " ", " "}
    for char in FOLDED_WHITESPACE:
        assert normalize_usable_alt_text(f"Left{char}right") == "Left right"


BAD_CHARACTERS = [
    "\x00",  # NUL
    "\x0c",  # form feed: a swallowed LaTeX \f escape
    "\x0b",  # vertical tab
    "\x1b",  # escape
    "\x85",  # next line, a C1 control
    "​",  # zero-width space
    "‍",  # zero-width joiner
    "﻿",  # byte order mark
    "‮",  # right-to-left override
    "⁦",  # left-to-right isolate
    "\ud800",  # lone surrogate
]


@pytest.mark.parametrize("char", BAD_CHARACTERS)
def test_other_control_format_and_surrogate_characters_still_reject(char):
    assert normalize_usable_alt_text(f"Blue{char}square") is None
    assert is_usable_alt_text(f"Blue{char}square") is False


@pytest.mark.parametrize("char", BAD_CHARACTERS)
def test_a_bad_character_still_rejects_text_that_also_has_line_breaks(char):
    assert normalize_usable_alt_text(f"Row 1\nBlue{char}square\r\nRow 2") is None


def test_a_swallowed_latex_escape_is_rejected():
    # "\frac" read as a JSON string escape leaves a form feed before "rac".
    assert normalize_usable_alt_text("The fraction \x0crac{1}{2} of the area") is None


@pytest.mark.parametrize(
    "text",
    [
        "Bar chart of weekly readings, rising from two to nine",
        "Blå square beside café 🟦",
        "棒グラフ: 週ごとの読書量",
        "Diagram — arrows (A → B) & labels, 3 < 5",
    ],
)
def test_single_line_text_is_unchanged_byte_for_byte(text):
    result = normalize_usable_alt_text(text)
    assert result == text
    assert result.encode("utf-8") == text.encode("utf-8")


def test_the_length_limit_applies_to_the_folded_text():
    # Raw length is over the limit, folded length is not.
    spread = "a" * 249 + "\r\n" * 20 + "b" * 249
    assert len(spread) > MAX_ALT_TEXT_LENGTH
    folded = normalize_usable_alt_text(spread)
    assert folded == "a" * 249 + " " + "b" * 249
    assert len(folded) == 499

    # Exactly at the limit once folded is accepted; one over is rejected.
    assert normalize_usable_alt_text("a" * 250 + " " + "b" * 249) is not None
    assert normalize_usable_alt_text("a" * 250 + "\n" + "b" * 250) is None


def test_a_custom_limit_is_checked_after_folding():
    assert normalize_usable_alt_text("ab\n\n\ncd", max_length=5) == "ab cd"
    assert normalize_usable_alt_text("ab\n\n\ncd", max_length=4) is None


def _image(tmp_path):
    path = tmp_path / "fixture.png"
    Image.new("RGB", (8, 6), "blue").save(path, format="PNG")
    return path


@pytest.mark.asyncio
async def test_a_multi_line_model_reply_becomes_usable_alt_text(tmp_path):
    client = MagicMock(provider="gemini")
    client.analyze_image_sync.return_value = {
        "success": True,
        "content": TABLE,
        "provider": "gemini",
    }
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.generate_alt_text(str(_image(tmp_path)))

    assert result["success"] is True
    assert result["alt_text"] == TABLE_FOLDED


@pytest.mark.asyncio
async def test_a_multi_line_reply_with_a_form_feed_is_still_discarded(tmp_path):
    client = MagicMock(provider="gemini")
    client.analyze_image_sync.return_value = {
        "success": True,
        "content": "Row 1\nThe fraction \x0crac{1}{2}",
        "provider": "gemini",
    }
    generator = ImageAltTextGenerator(lms_client=client)

    result = await generator.generate_alt_text(str(_image(tmp_path)))

    assert result["success"] is False


def _remediator(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(
        '<!DOCTYPE html><html><body><img src="chart.png">'
        '<button></button><input id="email" type="text"></body></html>'
    )
    remediator = HtmlRemediator(str(path), [])
    remediator._load_document()
    remediator._image_was_reachable = lambda _src: True
    return remediator


def _issue(category, description):
    return RemediationIssue(
        id="1",
        category=category,
        description=description,
        severity="high",
        location="chart.png",
    )


def test_html_remediator_writes_a_multi_line_description_as_one_line(tmp_path):
    remediator = _remediator(tmp_path)

    applied = remediator._apply_alt_text_fix(
        _issue(IssueCategory.ALT_TEXT, "missing alt"), TABLE
    )

    assert applied is True
    assert remediator._soup.find("img")["alt"] == TABLE_FOLDED


class _Client:
    def __init__(self, reply):
        self.reply = reply

    def generate_text_sync(self, **_kwargs):
        return {"success": True, "content": self.reply}


SINGLE_LINE_VALUES = [
    (IssueCategory.LANGUAGE, "Document is missing a language attribute", "en"),
    (IssueCategory.ARIA, "Button has no accessible name", "Close dialog"),
    (IssueCategory.FORM, "Form input missing label", "Email address"),
]

MULTI_LINE_REPLIES = [
    (
        IssueCategory.LANGUAGE,
        "Document is missing a language attribute",
        "en\n\nThis is the language code for English.",
    ),
    (
        IssueCategory.ARIA,
        "Button has no accessible name",
        "Close dialog\nNote: this describes the X button.",
    ),
    (
        IssueCategory.FORM,
        "Form input missing label",
        "Email address\n\nThe label explains what to type.",
    ),
    (IssueCategory.LANGUAGE, "Document is missing a language attribute", "en\r\nUS"),
    (IssueCategory.ARIA, "Button has no accessible name", "Close\tdialog"),
    (IssueCategory.FORM, "Form input missing label", "Email address"),
]


@pytest.mark.parametrize("category,description,reply", MULTI_LINE_REPLIES)
def test_multi_line_replies_for_single_line_values_are_left_for_a_human(
    tmp_path, category, description, reply
):
    """The usability gate folds line breaks, but these replies are applied raw."""
    remediator = _remediator(tmp_path)

    fix = remediator._get_ai_generated_fix(
        _issue(category, description), remediator._soup, client=_Client(reply)
    )

    assert fix is None


@pytest.mark.parametrize("category,description,reply", SINGLE_LINE_VALUES)
def test_single_line_replies_for_those_values_are_still_applied(
    tmp_path, category, description, reply
):
    remediator = _remediator(tmp_path)

    fix = remediator._get_ai_generated_fix(
        _issue(category, description), remediator._soup, client=_Client(reply)
    )

    assert fix == reply


def test_a_trailing_line_break_on_a_single_value_is_still_trimmed(tmp_path):
    remediator = _remediator(tmp_path)

    fix = remediator._get_ai_generated_fix(
        _issue(IssueCategory.LANGUAGE, "Document is missing a language attribute"),
        remediator._soup,
        client=_Client("en\n"),
    )

    assert fix == "en"
