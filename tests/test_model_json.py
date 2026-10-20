"""Model answers whose JSON strings carry LaTeX written with single backslashes."""

from __future__ import annotations

import json

import pytest

from src.education.model_json import (
    double_unescaped_backslashes,
    loads_tolerant,
    swallowed_escape,
)

BS = "\\"


def _answer(body: str) -> str:
    """A JSON object whose one string holds ``body`` exactly as written."""
    return '{"text": "' + body + '"}'


@pytest.mark.parametrize(
    ("written", "meant"),
    [
        (BS + "ne", BS + "ne"),
        (BS + "nabla f", BS + "nabla f"),
        (BS + "neq", BS + "neq"),
        ("x" + BS + "times y", "x" + BS + "times y"),
        ("a" + BS + "to b", "a" + BS + "to b"),
        (BS + "frac{a}{b}", BS + "frac{a}{b}"),
        (BS + "beta", BS + "beta"),
        (BS + "rho", BS + "rho"),
        (BS + "theta", BS + "theta"),
        # An invalid escape fails the strict parse and is repaired the same way.
        (BS + "alpha", BS + "alpha"),
        (BS + "sqrt{x}", BS + "sqrt{x}"),
    ],
)
def test_a_command_written_with_one_backslash_survives_as_that_command(written, meant):
    assert loads_tolerant(_answer(written))["text"] == meant


def test_the_strict_parse_alone_would_have_lost_the_command():
    strict = json.loads(_answer(BS + "ne"))["text"]
    assert strict == "\ne"
    assert swallowed_escape(strict) is True


def test_a_correctly_escaped_answer_is_not_repaired():
    content = json.dumps({"text": BS + "frac{a}{b} and " + BS + "ne"})
    assert loads_tolerant(content)["text"] == BS + "frac{a}{b} and " + BS + "ne"


@pytest.mark.parametrize(
    "text",
    [
        "First paragraph.\n\nSecond paragraph.",
        "- one\n- two\n1. three",
        "A line that was hard-wrapped before the word\nnothing and then\nother words",
        "It does\nnot rise and the line\nneeds a label",
        "Windows line breaks\r\nare fine",
        "Name\r\nvalue",
    ],
)
def test_ordinary_line_breaks_are_not_taken_for_a_swallowed_command(text):
    assert swallowed_escape(text) is False
    content = json.dumps({"text": text})
    assert loads_tolerant(content)["text"] == text


@pytest.mark.parametrize(
    "text",
    ["a\x0crac", "\x08eta", "x\times y", "a\rho", "a \ne b", "\nabla f", "\neq"],
)
def test_the_traces_of_a_swallowed_command_are_recognised(text):
    assert swallowed_escape(text) is True


def test_an_answer_that_cannot_be_parsed_either_way_raises():
    with pytest.raises(json.JSONDecodeError):
        loads_tolerant('{"text": "unterminated')


def test_unicode_escapes_stay_escapes_when_the_rest_is_repaired():
    content = _answer("\\u03b1 and " + BS + "ne")
    assert loads_tolerant(content)["text"] == "α and " + BS + "ne"


def test_doubling_leaves_an_even_run_of_backslashes_alone():
    assert double_unescaped_backslashes(BS * 2 + "ne") == BS * 2 + "ne"
    assert double_unescaped_backslashes(BS + "ne") == BS * 2 + "ne"
    assert double_unescaped_backslashes(BS * 3 + "ne") == BS * 4 + "ne"


def test_the_equation_reading_parser_uses_the_same_repair():
    from src.education.remediation import equation_reading_parser

    assert (
        equation_reading_parser.double_unescaped_backslashes
        is double_unescaped_backslashes
    )
