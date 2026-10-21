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


# ---------------------------------------------------------------------------
# Commands whose first letter is a JSON escape: the escape takes the letter
# ---------------------------------------------------------------------------

# Each decodes, written with one backslash, to a control character and the rest of
# the name. The line-feed group is the one the whole-branch audit found: a negation
# such as ``\nsubset`` arrived as a line break and "subset", and passed as text.
LINE_FEED_COMMANDS = [
    "nsubset", "nsupset", "nRightarrow", "nLeftarrow", "nleftrightarrow", "nVdash",
    "nsubseteqq", "ntriangleleft", "nonumber", "notag", "nearrow", "nwarrow",
    "natural", "newcommand", "newline", "nolimits", "notin", "not", "neg", "neq",
    "ne", "ni", "nu", "nabla", "nleq", "ngeq", "nmid", "nexists", "nvdash",
]  # fmt: skip
TAB_COMMANDS = [
    "theta", "times", "tau", "text", "tan", "to", "top", "triangle", "tilde",
    "therefore", "textbf", "tfrac", "tbinom", "tanh",
]  # fmt: skip
CARRIAGE_RETURN_COMMANDS = [
    "rho", "right", "rangle", "rbrace", "rceil", "rfloor", "rightarrow", "rVert",
    "rvert", "ref", "renewcommand",
]  # fmt: skip
BACKSPACE_COMMANDS = [
    "beta", "bar", "binom", "bm", "boldsymbol", "bot", "bullet", "begin", "big",
    "bigcup", "bmod", "because", "backslash",
]  # fmt: skip
FORM_FEED_COMMANDS = [
    "frac", "forall", "flat", "footnote", "frown", "fbox", "fallingdotseq",
]  # fmt: skip
SWALLOWED = [
    *LINE_FEED_COMMANDS,
    *TAB_COMMANDS,
    *CARRIAGE_RETURN_COMMANDS,
    *BACKSPACE_COMMANDS,
    *FORM_FEED_COMMANDS,
]


@pytest.mark.parametrize("name", SWALLOWED)
def test_every_command_the_json_escape_swallows_survives_as_that_command(name):
    from src.education.math_speech import refuse_math_markup

    written = '{"alt_text": "see ' + BS + name + ' here"}'
    decoded = json.loads(written)["alt_text"]
    assert swallowed_escape(decoded) is True, "the strict parse alone would lose it"
    text = loads_tolerant(written)["alt_text"]
    assert text == "see " + BS + name + " here"
    assert refuse_math_markup(text) is None


def test_the_audit_example_a_negation_is_not_announced_as_its_opposite():
    from src.education.math_speech import refuse_math_markup

    written = '{"alt_text": "A ' + BS + 'nsubset B"}'
    assert json.loads(written)["alt_text"] == "A \nsubset B"
    assert refuse_math_markup(json.loads(written)["alt_text"]) == "A \nsubset B"
    repaired = loads_tolerant(written)["alt_text"]
    assert repaired == "A " + BS + "nsubset B"
    assert refuse_math_markup(repaired) is None


@pytest.mark.parametrize(
    "text",
    [
        "A real line break\nsubsets are listed next",
        "First line.\nNegations come second.",
        "The values\nunder the bars are labelled\nnear the axis",
        "Column one\tcolumn two\tthree",
        "Item\treference list\tbibliography",
        "Old line ending\rbreaks here",
        "Rows:\r\nrho values\r\nbeta values",
        "Steps:\n1. nothing yet\n2. newly added",
    ],
)
def test_ordinary_line_breaks_tabs_and_returns_are_not_taken_for_a_command(text):
    assert swallowed_escape(text) is False
    assert loads_tolerant(json.dumps({"text": text}))["text"] == text


def test_a_real_line_break_before_a_word_that_completes_a_command_fails_closed():
    """The cost of the rule, accepted: ``\\nsubset`` and a line break look alike."""
    from src.education.math_speech import refuse_math_markup

    text = "The first set\nsubset of the second set"
    assert swallowed_escape(text) is True
    repaired = loads_tolerant(json.dumps({"text": text}))["text"]
    # The line break is lost to a backslash command, which is refused.
    assert refuse_math_markup(repaired) is None


def test_the_swallowed_letter_is_put_back_before_the_name_is_looked_up():
    # ``\nu`` is a line feed and ``u``; the letters alone are not a command.
    assert swallowed_escape("\nu") is True
    assert swallowed_escape("\nnu") is False
    # A tab and ``heta`` spell ``theta``; a tab and ``theta`` would spell ``ttheta``.
    assert swallowed_escape("\t" + "heta") is True
    assert swallowed_escape("\t" + "theta") is False


def test_the_command_names_are_matched_case_sensitively():
    assert swallowed_escape("\n" + "Rightarrow") is True
    assert swallowed_escape("\n" + "RIGHTARROW") is False
    assert swallowed_escape("\n" + "rightarrow") is True


def test_a_form_feed_or_a_backspace_is_always_a_swallowed_command():
    assert swallowed_escape("a\x0c b") is True
    assert swallowed_escape("a\x08 b") is True
