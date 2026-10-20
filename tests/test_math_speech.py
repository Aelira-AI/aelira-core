"""The shared spoken-word module: conversion, refusal and what must not change."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.education import math_speech
from src.education.math_speech import (
    equation_alt_text_is_ambiguous,
    generate_equation_alt_text,
    speak_math_in_description,
)

ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# One copy of the spoken-label logic, and the equation path did not change
# ---------------------------------------------------------------------------


def test_the_equation_path_and_the_description_path_share_one_implementation():
    from src.education.remediation import math_fixer, printed_equation_verifier

    assert math_fixer.generate_equation_alt_text is generate_equation_alt_text
    assert math_fixer.equation_alt_text_is_ambiguous is equation_alt_text_is_ambiguous
    assert (
        printed_equation_verifier.strip_math_delimiters
        is math_speech.strip_math_delimiters
    )


def test_the_substitution_table_is_defined_in_exactly_one_place():
    definitions = [
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "src").rglob("*.py")
        if re.search(r"^_ARIA_SUBSTITUTIONS\b.*=", path.read_text(), re.MULTILINE)
    ]
    assert definitions == ["src/education/math_speech.py"]


# Recorded from the generator before it moved. The move changes nothing.
EQUATION_PATH = [
    ("x^{2} + 3x - 7 = 0", "x squared + 3x - 7 = 0", False),
    ("\\frac{a}{b}", "a over b", False),
    ("\\frac{1}{2} + y = 4", "1 over 2 + y = 4", False),
    ("\\dfrac{1}{2}", "1 over 2", False),
    ("\\tfrac{3}{4}", "3 over 4", False),
    ("\\cfrac{p}{q}", "p over q", False),
    ("\\binom{n}{k}", "n choose k", False),
    ("\\dbinom{n}{k} + 1", "n choose k + 1", False),
    ("\\sqrt{x}", "square root of x", False),
    ("\\sqrt[3]{x}", "3th root of x", False),
    ("E = mc^{2}", "E = mc squared", False),
    ("x^2 + y^2 = r^2", "x squared + y squared = r squared", False),
    ("a_{n}", "a sub n", False),
    ("a_n", "a sub n", False),
    ("\\alpha + \\beta = \\gamma", "alpha + beta = gamma", False),
    ("\\Gamma(\\Omega)", "Gamma(Omega)", False),
    ("a \\times b \\div c", "a times b divided by c", False),
    ("a \\pm b \\mp c", "a plus or minus b minus or plus c", False),
    (
        "a \\leq b \\geq c \\neq d",
        "a less than or equal to b greater than or equal to c not equal to d",
        False,
    ),
    ("a \\approx b \\equiv c", "a approximately equal to b equivalent to c", False),
    ("1 \\ldots n", "1 ellipsis n", False),
    ("\\infty", "infinity", False),
    ("\\partial f", "partial derivative f", False),
    ("\\nabla g", "nabla g", False),
    ("\\sum_{i} x_{i}", "sum sub i x sub i", False),
    ("\\int_0^1 x dx", "integral sub 0 to the power 1 x dx", False),
    ("\\oint f", "contour integral f", False),
    ("\\mathbb{R}", "mathematical equation", True),
    ("\\text{if } x", "if x", False),
    ("$x^2 + 1$", "x squared + 1", False),
    ("$$\\frac{1}{2}$$", "1 over 2", False),
    ("\\(a+b\\)", "a+b", False),
    ("\\[a-b\\]", "a-b", False),
    ("x", "mathematical equation", True),
    ("", "mathematical equation", True),
    ("\\frac12", "12", True),
    ("x^10", "x to the power 10", True),
]


@pytest.mark.parametrize(("latex", "label", "ambiguous"), EQUATION_PATH)
def test_the_equation_label_is_byte_identical_after_the_move(latex, label, ambiguous):
    assert generate_equation_alt_text(latex) == label
    assert equation_alt_text_is_ambiguous(latex) is ambiguous


# ---------------------------------------------------------------------------
# Every delimiter and command form is converted to spoken words
# ---------------------------------------------------------------------------

CONVERTED = [
    # Delimited spans.
    (r"Plot of $x^{2}+1$.", "Plot of x squared+1."),
    (
        r"Graph of $$x^{2}+y^{2}=r^{2}$$ shown",
        "Graph of x squared+y squared=r squared shown",
    ),
    (r"Value \(\alpha+\beta\) here", "Value alpha+beta here"),
    (r"Value \[\frac{1}{2}+y=4\] here", "Value 1 over 2+y=4 here"),
    ("Display\n$$\n\\frac{a}{b}\n$$\nends", "Display\na over b\nends"),
    (
        r"Both $\frac{a}{b}$ and $\sqrt{x}$ appear",
        "Both a over b and square root of x appear",
    ),
    (r"$f(x) = 2x + 3$ is a line", "f(x) = 2x + 3 is a line"),
    # Bare commands and braced scripts, with no delimiter.
    (r"The equation \frac{a}{b} = c", "The equation a over b = c"),
    (r"The equation \dfrac{a}{b}.", "The equation a over b."),
    (r"Area is \pi r^{2} here", "Area is pi r squared here"),
    (r"Roots of \sqrt{x} and \binom{n}{k}", "Roots of square root of x and n choose k"),
    (r"The term a_{n} grows", "The term a sub n grows"),
    (r"Values x^{3} and y^{n}", "Values x cubed and y to the power n"),
    (r"Greek \alpha, \beta and \Omega", "Greek alpha, beta and Omega"),
    (r"Total \sum_{i} x_{i}", "Total sum sub i x sub i"),
    # Mathematics written hard against a word is kept apart from it.
    (r"a$x^{2}$b", "a x squared b"),
]


@pytest.mark.parametrize(("text", "expected"), CONVERTED)
def test_mathematics_is_converted_to_spoken_words(text, expected):
    assert speak_math_in_description(text) == expected


def test_converted_text_holds_no_mathematics_so_converting_twice_changes_nothing():
    for text, expected in CONVERTED:
        assert speak_math_in_description(expected) == expected


# ---------------------------------------------------------------------------
# Ambiguous spans, commands the label would erase, and leftovers are refused
# ---------------------------------------------------------------------------

REFUSED = [
    # A span the equation path refuses as ambiguous.
    r"The equation \frac12 = c",
    r"Value $\frac12$ here",
    r"Value \(\sqrt x\) here",
    r"Value \[\binom nk\] here",
    r"Growth $x^10$ here",
    r"Index $a_ij$ here",
    r"Nested \frac{x^{2}}{y} here",
    r"Nested e^{x^{2}} here",
    r"Only $x$ here",
    # A command with no spoken form, which the label would delete without trace.
    r"All \forall x here",
    r"Reals \mathbb{R} here",
    r"Accent \hat{x} here",
    r"Arrow \vec{v} here",
    r"Words \text{if} here",
    r"Longer \alphabet here",
    r"Digit \alpha2 here",
    # A backslash that survives conversion.
    r"Set $\{x, y\}$ here",
    r"Doubled \\frac{a}{b} here",
]


@pytest.mark.parametrize("text", REFUSED)
def test_what_cannot_be_spoken_reliably_is_refused(text):
    assert speak_math_in_description(text) is None


def test_one_refused_span_refuses_the_whole_description():
    assert speak_math_in_description(r"Fine $x^{2}$ but \frac12 is not") is None
    assert (
        speak_math_in_description(r"Fine $\frac{a}{b}$ but $\mathbb{R}$ is not") is None
    )


def test_a_command_the_table_translates_is_never_reported_as_erased():
    for command in ("alpha", "pi", "times", "pm", "infty", "sum", "int"):
        assert math_speech._unspoken_commands("\\" + command) == []
    assert math_speech._unspoken_commands(r"\mathbb{R}") == ["\\mathbb"]


# Characters that json.loads makes of the first letter of a command written with
# a single backslash: the command itself is gone before the text arrives.
DECODED_ESCAPES = [
    "Plot of \x0crac{a}{b}",
    "Angle \x08eta here",
    "Three \ttimes four",
    "Density \rho here",
]


@pytest.mark.parametrize("text", DECODED_ESCAPES)
def test_a_command_swallowed_by_json_decoding_is_refused(text):
    assert speak_math_in_description(text) is None


# ---------------------------------------------------------------------------
# Text without mathematics comes back unchanged, byte for byte
# ---------------------------------------------------------------------------

UNCHANGED = [
    "",
    "A photograph of a laboratory bench with three beakers.",
    "Line one\nLine two with  double  spaces and a tab\there.",
    "Naïve café menu: crème brûlée, 12°C, 5 × 4 and 10 ÷ 2.",
    # Prices.
    "It costs $5 and $10.",
    "Between $5-$10 per item",
    "Prices $5/$10 and $5.00 or $6.50",
    "Total: $5 + $3 = $8",
    "A $5 coupon and a $10 coupon",
    "US$5, A$10 and NZ$15",
    "Cost is \\$5 and \\$10 here",
    # Windows paths.
    r"Open C:\Users\bob\notes.txt now",
    r"Open C:\Program Files\App\run.exe now",
    r"Saved to D:\Projects\report-final.docx.",
    r"Share \\fileserver\share\docs is mapped",
    r"File src\education\math.py changed",
    r'Path "C:\Temp\out.log" was written',
    # Code in prose, with no backslash command in it.
    'Call print("hello") and then x = a * b.',
    "The block if (x > 1) { return; } runs once.",
    'A JSON object looks like {"key": "value"}.',
    "Use snake_case_name for a variable and array[0] for the first item.",
    "The expression a^b and 2^10 are written with a caret.",
    "Run echo $HOME and echo $PATH, or use $HOME/$USER.",
    "The template image_name.png has no braces.",
    "Unbalanced { brace and } another",
]


@pytest.mark.parametrize("text", UNCHANGED)
def test_text_without_mathematics_is_unchanged_byte_for_byte(text):
    result = speak_math_in_description(text)
    assert result == text
    assert result is text


def test_a_path_next_to_mathematics_stays_a_path():
    text = r"Open C:\Users\bob\notes.txt to see \frac{a}{b} and $x^{2}$ there"
    assert speak_math_in_description(text) == (
        r"Open C:\Users\bob\notes.txt to see a over b and x squared there"
    )


def test_a_backslash_word_that_is_not_clearly_a_path_is_read_as_a_command():
    # The cautious reading: refuse and let a person look.
    assert speak_math_in_description(r"Open the Documents\Projects folder") is None
    assert speak_math_in_description(r"The regex \d+ matches digits") is None


def test_tab_and_return_characters_in_ordinary_text_are_left_alone():
    for text in ("Name\tScore", "alice\t90", "one\r\ntwo", "Col A\tCol B"):
        assert speak_math_in_description(text) is text


def test_a_value_that_is_not_text_is_refused():
    assert speak_math_in_description(None) is None
    assert speak_math_in_description(5) is None
