"""The equation label and its ambiguity guard, in the shared spoken-word module."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.education import math_speech
from src.education.math_speech import (
    equation_alt_text_is_ambiguous,
    generate_equation_alt_text,
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


# Recorded from the generator before it moved. The move changed nothing. Two
# labels have since been corrected on purpose, the cube root and the partial
# derivative symbol. The third column is the verdict of the guard as it was then;
# the guard has since been tightened and is tested below.
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
    ("\\sqrt[3]{x}", "cube root of x", False),
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
    ("\\partial f", "partial f", False),
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


@pytest.mark.parametrize(("latex", "label", "_ambiguous"), EQUATION_PATH)
def test_the_equation_label_is_byte_identical_after_the_move(latex, label, _ambiguous):
    assert generate_equation_alt_text(latex) == label


# ---------------------------------------------------------------------------
# The ambiguity guard: no two equations it accepts read the same
# ---------------------------------------------------------------------------

# A group that runs into what follows reads as one word ("x squaredy"), and the
# same words could belong to a different equation.
RUN_TOGETHER = [
    r"3x^{2}y + 2xy^{2} = 0",
    r"y = \frac{1}{2}x + 3",
    r"x^{n}y",
    r"a_{n}x^{n}",
    r"\sqrt{2}x",
    r"x^{1}2",
    r"x^2{y}",
    r"\frac{a}{b}c",
    r"\frac{a}{b}{c}",
    r"\binom{n}{k}m",
    r"x^{2}\alpha",
    r"x^{2}\times y",
    r"\frac{a}{b}\pi",
    r"x_{i}_{j}",
    r"x^{2}^{3}",
    r"\frac{a}{b}^{2}",
    r"\sqrt{x}_{i}",
]

# The words of a group that holds more than one token do not say where it ends.
COMPOUND_ARGUMENT = [
    r"\sqrt{x+1}",
    r"\sqrt{0 \times y + 9}",
    r"\sqrt{2x}",
    r"\frac{a+b}{c}",
    r"\frac{a}{b+c}",
    r"\frac{a}{b c}",
    r"\frac{2x}{3}",
    r"\frac{1}{2x}",
    r"\binom{n+1}{k}",
    r"x^{n+1}",
    r"x^{-1}",
    r"e^{-x}",
    r"x^{2n}",
    r"x^{ab}",
    r"a_{n-1}",
    r"a_{ij}",
    r"\sum_{k=1}^{m} 87",
    r"\sqrt[3]{x+1}",
    r"\sqrt[n+1]{x}",
]

# The label drops braces, so a brace group that is not an argument loses its scope.
BARE_GROUP = [
    r"{ab}^2",
    r"{-}x",
    r"a{+}b",
    r"{x}+1",
    r"{a}{b}",
    r"x{y}",
    "x}",
    r"\frac{a}{b",
]

# Commands with no spoken form, which the label would erase without trace.
ERASED_COMMAND = [
    r"\bar{x} = 5",
    r"\hat{x} + 1",
    r"\vec{v} + 1",
    r"\overline{x} + 1",
    r"{1 \over 3}",
    r"1 \over 3",
    r"\text{if } x",
    r"\mathbb{R} + 1",
    r"\forall x + 1",
    r"\lim_{n} x",
    r"\sinh x",
    r"\arctan x",
    r"\to x",
    r"\in A",
    r"\begin{matrix} a \end{matrix}",
    r"\left\{ x \right\}",
    r"a\\b",
    r"\leftarrow x",
]

# Braceless arguments, and a fraction, root or script that is not written out.
BRACELESS = [r"\frac13", r"\frac{a}b", r"\sqrt2", r"x^10", r"x_10", r"x^\alpha"]

# A script directly after an integral, sum, product or contour integral is a
# limit, which the label would read as a script on the symbol.
LIMITS = [
    r"\int_{0}^{1} x^{2} dx",
    r"\int_0^1 x dx",
    r"\int_{1}^{x} z",
    r"\int_a^b f",
    r"\sum_{i} x_{i}",
    r"\sum_{n}^{m} a",
    r"\prod_{k} a_{k}",
    r"\oint_{C} f",
    r"\int^{1} x",
]

# A decimal point or comma with a digit behind it reads as a longer number:
# ``x^{1}.5`` and ``x^{1.5}`` would say the same thing.
DECIMALS = [
    r"x^1.5",
    r"x^{1}.5",
    r"\frac{1}{2}.5",
    r"\sqrt{2}.5",
    r"x_{1}.5",
    r"x^2.5",
    r"x^{1},5",
    r"x^{2}.5",
    r"\frac{a}{b},5",
]

# A spoken form that is not closed is accepted only at the end, before a closing
# parenthesis, a comma or a relation. Anything else could be read into it.
OPEN_ENDED = [
    r"x^{n}+1",
    r"\sqrt{x}+1",
    r"\frac{a}{b}+c",
    r"\frac{a}{b}+\frac{c}{d}",
    r"x^{n} + 1",
    r"\frac{a}{b} c",
    r"\frac{a}{b} \cdot c",
    r"\frac{a}{b} - c",
    r"a_{n} + b",
    r"x_{i}^{2}",
    r"x_i^2",
    r"\log_{2} 8 = 3",
    r"\binom{n}{k} + 1",
    r"y = \frac{1}{2} x + 3",
    r"\frac{a}{b}\,dx",
    r"\sqrt[3]{x} + 1",
    r"x^{n} y",
    r"\frac{a}{b} / c",
]


@pytest.mark.parametrize(
    "latex",
    [
        *RUN_TOGETHER,
        *COMPOUND_ARGUMENT,
        *BARE_GROUP,
        *ERASED_COMMAND,
        *BRACELESS,
        *LIMITS,
        *DECIMALS,
        *OPEN_ENDED,
    ],
)
def test_the_guard_refuses_what_the_label_would_garble(latex):
    assert equation_alt_text_is_ambiguous(latex) is True


# What the label can say without guessing, with the exact words it says.
SPOKEN = [
    # Groups with one token in each argument.
    (r"\frac{a}{b}", "a over b"),
    (r"\frac{12}{3}", "12 over 3"),
    (r"\frac{1.5}{2}", "1.5 over 2"),
    (r"\frac{\pi}{2}", "pi over 2"),
    (r"x^{2}", "x squared"),
    (r"x^{3}", "x cubed"),
    (r"x^{n}", "x to the power n"),
    (r"x^{10}", "x to the power 10"),
    (r"x^{\pi}", "x to the power pi"),
    (r"a_{n}", "a sub n"),
    (r"\sqrt{2}", "square root of 2"),
    (r"\sqrt{x}", "square root of x"),
    (r"x^{1.5}", "x to the power 1.5"),
    # Closed forms may be followed by anything the other rules allow.
    (r"x^{2}+y^{2}=r^{2}", "x squared+y squared=r squared"),
    (r"x^{3} - 1", "x cubed - 1"),
    (r"x^{2}_{i}", "x squared sub i"),
    (r"x^2_{i}", "x squared sub i"),
    (r"x^3_i", "x cubed sub i"),
    (r"y + \frac{1}{2} = 4", "y + 1 over 2 = 4"),
    (r"1 - \sqrt{x} = 3", "1 - square root of x = 3"),
    (r"x_{1} = x_{2}", "x sub 1 = x sub 2"),
    # An open-ended form at the end, before a relation, a comma or a parenthesis.
    (r"\frac{a}{b} = c", "a over b = c"),
    (r"x^{n} = 1", "x to the power n = 1"),
    (r"x^{n} \le y", "x to the power n less than or equal to y"),
    (r"\frac{a}{b}, c", "a over b, c"),
    (r"(\frac{a}{b})", "(a over b)"),
    (r"a_{n} \neq b", "a sub n not equal to b"),
    (r"x^{2} y", "x squared y"),
    # A spoken form is kept apart from the letter or digit before it.
    (r"2\pi r", "2 pi r"),
    (r"d\theta", "d theta"),
    (r"\alpha\beta", "alpha beta"),
    (r"a\times b", "a times b"),
    (r"a\frac{b}{c}", "a b over c"),
    (r"1\frac{2}{3}", "1 2 over 3"),
    (r"a\sqrt{b}", "a square root of b"),
    # Relations and operators, one command at a time.
    (r"x \le 3", "x less than or equal to 3"),
    (r"x \leq 3", "x less than or equal to 3"),
    (r"x \ge 3", "x greater than or equal to 3"),
    (r"x \geq 3", "x greater than or equal to 3"),
    (r"x \ne 0", "x not equal to 0"),
    (r"x \neq 0", "x not equal to 0"),
    (r"a \approx b", "a approximately equal to b"),
    (r"a \pm b", "a plus or minus b"),
    (r"a \times b", "a times b"),
    (r"a \cdot b", "a dot b"),
    (r"1 \cdots n", "1 centered ellipsis n"),
    (r"1 \ldots n", "1 ellipsis n"),
    (r"\partial f", "partial f"),
    (r"\infty + 1", "infinity + 1"),
    # A root index is a word, or a letter.
    (r"\sqrt[2]{x}", "square root of x"),
    (r"\sqrt[3]{x}", "cube root of x"),
    (r"\sqrt[4]{x}", "fourth root of x"),
    (r"\sqrt[10]{x}", "tenth root of x"),
    (r"\sqrt[n]{x}", "nth root of x"),
    # Functions, one command at a time.
    (r"\sin x = 0", "sine x = 0"),
    (r"\cos x = 0", "cosine x = 0"),
    (r"\tan x = 0", "tangent x = 0"),
    (r"\log x = 0", "log x = 0"),
    (r"\ln x = 0", "natural log x = 0"),
    (r"y = \log_{b}", "y = log sub b"),
    (r"\sin^{2} x", "sine squared x"),
    # Commands that only change how the equation is drawn.
    (r"\left( x+1 \right)", "( x+1 )"),
    (r"\left(\frac{a}{b}\right)", "(a over b)"),
    # A closing parenthesis ends an open-ended form only when it closes one opened
    # earlier and the text after it ends the form too.
    (r"(x^{n})", "(x to the power n)"),
    (r"(x^{n}) = 1", "(x to the power n) = 1"),
    (r"f(x^{n}, y)", "f(x to the power n, y)"),
    (r"((x^{n}))", "((x to the power n))"),
    (r"\left( x^{n} \right)", "( x to the power n )"),
    (r"(x^{2}) + 1", "(x squared) + 1"),
    (r"(x_{i}), (y_{i})", "(x sub i), (y sub i)"),
    (r"(\sqrt{x})", "(square root of x)"),
    (r"\left. x+1 \right|", "x+1 |"),
    (r"\displaystyle x+1", "x+1"),
    (r"\mathrm{d}x + 1", "dx + 1"),
    (r"a\quad b", "a b"),
    (r"a\qquad b", "a b"),
    (r"a\,b", "a b"),
    (r"a\;b", "a b"),
    (r"a\:b", "a b"),
    (r"a\!b", "a b"),
    (r"\int x^{2}\,dx", "integral x squared dx"),
    (r"\int x \, dx", "integral x dx"),
]


@pytest.mark.parametrize(("latex", "words"), SPOKEN)
def test_what_the_label_can_say_is_accepted_with_exact_words(latex, words):
    assert equation_alt_text_is_ambiguous(latex) is False
    assert generate_equation_alt_text(latex) == words


def test_every_command_the_vocabulary_names_has_exactly_one_spoken_form():
    commands = {
        "le": "less than or equal to",
        "leq": "less than or equal to",
        "ge": "greater than or equal to",
        "geq": "greater than or equal to",
        "ne": "not equal to",
        "neq": "not equal to",
        "approx": "approximately equal to",
        "pm": "plus or minus",
        "times": "times",
        "cdot": "dot",
        "cdots": "centered ellipsis",
        "ldots": "ellipsis",
        "partial": "partial",
        "infty": "infinity",
        "sin": "sine",
        "cos": "cosine",
        "tan": "tangent",
        "log": "log",
        "ln": "natural log",
    }
    for command, words in commands.items():
        label = generate_equation_alt_text("a \\" + command + " b")
        assert label == f"a {words} b", command
        assert math_speech._unspoken_commands("\\" + command) == [], command


def test_no_two_commands_in_the_vocabulary_share_a_spoken_form_unless_they_are_aliases():
    forms = {
        "le": "leq",
        "ge": "geq",
        "ne": "neq",
    }
    seen: dict[str, str] = {}
    for command in (
        "times", "cdot", "cdots", "ldots", "pm", "mp", "div", "approx", "equiv",
        "sum", "prod", "int", "oint", "partial", "nabla", "infty", "sin", "cos",
        "tan", "log", "ln", "le", "leq", "ge", "geq", "ne", "neq",
    ):  # fmt: skip
        label = generate_equation_alt_text("a \\" + command + " b")
        previous = seen.setdefault(label, command)
        assert previous == command or forms.get(previous) == command, (
            previous,
            command,
        )


def test_a_root_index_without_a_word_is_refused():
    for latex in (
        r"\sqrt[11]{x}",
        r"\sqrt[1.5]{x}",
        r"\sqrt[\alpha]{x}",
        r"\sqrt[0]{x}",
    ):
        assert equation_alt_text_is_ambiguous(latex) is True, latex
    assert generate_equation_alt_text(r"\sqrt[3]{x}") == "cube root of x"


def test_the_typography_commands_are_the_only_ones_dropped_without_words():
    for command in ("left", "right", "displaystyle", "quad", "qquad"):
        assert math_speech._unspoken_commands("\\" + command) == [], command
    assert math_speech._unspoken_commands(r"\mathrm{d}") == []
    for command in ("bar", "hat", "vec", "over", "text", "mathbb", "underline"):
        assert math_speech._unspoken_commands("\\" + command) == ["\\" + command]


def test_a_longer_command_is_not_mistaken_for_one_the_vocabulary_names():
    for longer in (
        r"\sinh x",
        r"\leftarrow x",
        r"\lessim x",
        r"\nexists x",
        r"\logo x",
    ):
        assert equation_alt_text_is_ambiguous(longer) is True, longer


# ---------------------------------------------------------------------------
# Property: different MathML never reads the same
# ---------------------------------------------------------------------------


def _canonical(latex: str) -> str:
    from latex2mathml.converter import convert

    from src.education.remediation.equation_verifier import canonicalize_mathml

    return canonicalize_mathml(convert(latex))


def _refused(latex: str) -> bool:
    return equation_alt_text_is_ambiguous(latex)


# Pairs whose canonical MathML differs and whose words used to be identical or
# garbled. ``refused`` says which side the guard refuses; ``neither`` means both
# are said and the words differ.
COLLISIONS = [
    (r"y = \frac{1}{2}x + 3", r"y = \frac{1}{2x} + 3", "both"),
    (r"x^{n}y", r"x^{ny}", "both"),
    (r"x^{n} y", r"x^{ny}", "both"),
    (r"\sqrt{2}x", r"\sqrt{2x}", "both"),
    (r"a_{n}x^{n}", r"a_{nx}^{n}", "both"),
    (r"\frac{a}{b}^{2}", r"\frac{a}{b^{2}}", "both"),
    (r"\frac{a}{b} c", r"\frac{a}{bc}", "both"),
    (r"x_{i}^{2}", r"x_{i^{2}}", "both"),
    (r"\sqrt{x}+1", r"\sqrt{x+1}", "both"),
    (r"x^{n}+1", r"x^{n+1}", "both"),
    (r"a_{n}+1", r"a_{n+1}", "both"),
    (r"\frac{a}{b}+c", r"\frac{a}{b+c}", "both"),
    (r"a+\frac{b}{c}", r"\frac{a+b}{c}", "right"),
    (r"a\frac{b}{c}", r"\frac{ab}{c}", "right"),
    (r"ab^{2}", r"{ab}^{2}", "right"),
    (r"x^{1}.5", r"x^{1.5}", "left"),
    (r"\frac{1}{2}.5", r"\frac{1}{2.5}", "left"),
    (r"\frac{1}{2} 3", r"\frac{1}{23}", "left"),
    (r"1\frac{2}{3}", r"\frac{12}{3}", "neither"),
    (r"\sin x^{2}", r"\sin^{2} x", "neither"),
    (r"a \cdot b", r"a \times b", "neither"),
    (r"1 \cdots n", r"1 \ldots n", "neither"),
    # Pairs that read the same and were both accepted before: a \mathrm group of
    # several letters, a letter run that spells a spoken word, and a script on a
    # parenthesis. The first of each pair is accepted and says what it says.
    (r"ab^2", r"\mathrm{ab}^2", "right"),
    (r"2x^2", r"\mathrm{2x}^2", "right"),
    (r"\frac{a}{b}", r"a \mathrm{over} b", "right"),
    (r"\frac{a}{b}", r"a over b", "right"),
    (r"x_i", r"x \mathrm{sub} i", "right"),
    (r"2x^2", r"(2x)^2", "right"),
    (r"a \times b", r"a times b", "right"),
    (r"\pi", r"pi", "right"),
]


@pytest.mark.parametrize(("left", "right", "refused"), COLLISIONS)
def test_a_pair_that_used_to_read_the_same_is_refused_or_reads_differently(
    left, right, refused
):
    assert _canonical(left) != _canonical(right), "not a pair worth testing"
    sides = {"left": _refused(left), "right": _refused(right)}
    assert (
        sides
        == {
            "both": {"left": True, "right": True},
            "left": {"left": True, "right": False},
            "right": {"left": False, "right": True},
            "neither": {"left": False, "right": False},
        }[refused]
    )
    assert (
        sides["left"]
        or sides["right"]
        or (generate_equation_alt_text(left) != generate_equation_alt_text(right))
    )


# The single-symbol and different-pair corpora of the other test modules. Every
# member the guard refuses is named here, so a new refusal or a new acceptance
# shows up as a failure.
REUSED_REFUSED = {
    r"\frac12",
    r"\frac21",
    "x^{ab}",
    "x^a b",
    "{-}x",
    "a{+}b",
    "{ab}^2",
}


def test_the_reused_corpora_are_refused_or_read_differently_and_every_refusal_is_listed():
    from tests.test_equation_canonical_form import DIFFERENT_PAIRS
    from tests.test_printed_equation_second_reading import SINGLE_SYMBOL_EDITS

    pairs = [*DIFFERENT_PAIRS, *SINGLE_SYMBOL_EDITS]
    assert len(pairs) >= 15
    refused: set[str] = set()
    for left, right in pairs:
        assert _canonical(left) != _canonical(right), (left, right)
        refused |= {side for side in (left, right) if _refused(side)}
        assert (
            _refused(left)
            or _refused(right)
            or generate_equation_alt_text(left) != generate_equation_alt_text(right)
        ), (left, right)
    assert refused == REUSED_REFUSED


BASES = [
    r"\frac{a}{b}",
    r"\frac{1}{2}",
    r"\frac{12}{3}",
    r"\frac{1.5}{2}",
    r"\frac{1}{2.5}",
    r"\frac{a+b}{c}",
    r"\frac{a}{b+c}",
    r"\frac{ab}{c}",
    r"\sqrt{x}",
    r"\sqrt{x+1}",
    r"\sqrt{2x}",
    r"\sqrt{2}",
    r"\sqrt{2.5}",
    r"\sqrt[3]{x}",
    r"\sqrt[n]{x}",
    r"x^{n}",
    r"x^{n+1}",
    r"x^{ny}",
    r"x^{2}",
    r"x^{3}",
    r"x^{10}",
    r"x^{1}",
    r"x^{1.5}",
    r"x^2",
    r"x_i",
    r"x_{1}",
    r"a_{n}",
    r"a_{n+1}",
    r"a_{nx}",
    r"\sin x",
    r"\sin^{2} x",
    r"\binom{n}{k}",
    r"\alpha",
    r"\pi",
    r"\partial f",
    r"a \cdot b",
    r"a \times b",
    r"a\cdot b",
    r"a\times b",
    r"1 \cdots n",
    r"1 \ldots n",
    r"{ab}",
    r"{x}",
]
BEFORE = ["", "a", "1", "a ", "1 ", "a+", "x", r"\alpha", "(", "y = ", "1."]
AFTER = [
    "",
    "b",
    "1",
    " b",
    " 1",
    "+c",
    " + c",
    "^{2}",
    "_{i}",
    r"\,y",
    ")",
    ") + c",
    ") = c",
    ")x",
    ".5",
    ",5",
    ", c",
    " = c",
    r" \le c",
    r" \cdot c",
    r" \times c",
]

_RELATION_WORDS = (
    "less than or equal to",
    "greater than or equal to",
    "not equal to",
    "approximately equal to",
    "equivalent to",
)
_OPEN_PHRASE = re.compile(
    r"\b(?:over|choose|root of|to the power|sub)\s+([^\s=<>,)+*/-]+)"
)


def _open_phrases_are_terminal(words: str) -> bool:
    """Whether every open-ended spoken group ends where nothing can be read in.

    This reads the words alone, not the LaTeX: after the last word of "over",
    "choose", "root of", "to the power" or "sub" there must be the end, a closing
    parenthesis, a comma, or a relation.
    """
    for match in _OPEN_PHRASE.finditer(words):
        rest = words[match.end() :].lstrip()
        # A closing parenthesis is not spoken, so what follows it counts.
        while rest.startswith(")"):
            rest = rest[1:].lstrip()
        if rest and rest[0] not in "=<>," and not rest.startswith(_RELATION_WORDS):
            return False
    return True


def _accepted_placements():
    for base in BASES:
        for before in BEFORE:
            for after in AFTER:
                latex = f"{before}{base}{after}"
                if not _refused(latex):
                    yield latex


def test_no_two_accepted_equations_with_different_mathml_share_their_words():
    """Generate every placement of each base and group what the label says."""
    by_words: dict[str, dict[str, str]] = {}
    accepted = 0
    for latex in _accepted_placements():
        try:
            mathml = _canonical(latex)
        except Exception:
            continue
        accepted += 1
        by_words.setdefault(generate_equation_alt_text(latex), {})[mathml] = latex
    assert accepted > 400, "the corpus no longer exercises the guard"
    collisions = {
        words: sorted(readings.values())
        for words, readings in by_words.items()
        if len(readings) > 1
    }
    assert collisions == {}


def test_every_accepted_open_ended_group_is_terminal():
    """No accepted text reads naturally as a form the guard refuses."""
    checked = 0
    for latex in _accepted_placements():
        words = generate_equation_alt_text(latex)
        assert _open_phrases_are_terminal(words), (latex, words)
        checked += 1
    assert checked > 400


def test_the_terminal_check_itself_catches_an_open_ended_group_followed_by_more():
    assert _open_phrases_are_terminal("x to the power n = 1")
    assert _open_phrases_are_terminal("a over b, c")
    assert _open_phrases_are_terminal("f(x to the power n) = 1")
    assert not _open_phrases_are_terminal("(1 plus x to the power n) plus 2")
    assert _open_phrases_are_terminal("x sub 1 less than or equal to y")
    assert not _open_phrases_are_terminal("x to the power n+1")
    assert not _open_phrases_are_terminal("(a over b) squared")
    assert not _open_phrases_are_terminal("a over b c")
    assert not _open_phrases_are_terminal("square root of x plus 1")
    assert not _open_phrases_are_terminal("x sub i squared")


# ---------------------------------------------------------------------------
# Constructions from the whole-branch audit. Each was accepted before.
# ---------------------------------------------------------------------------

# A bare script takes one ASCII letter or digit and nothing else, and a caret or
# an underscore never survives into the label.
NON_ALPHANUMERIC_SCRIPT_ARGUMENTS = [
    "x^*",
    r"\mathrm{H}^+",
    r"\mathrm{Cl}^-",
    "x_+",
    "x^'",
    "x^+",
    "x_-",
    "x^#",
    "x^é",
]

# A \mathrm group holds one letter, so a longer one cannot hide where a base
# begins or pass for a spoken word.
LONG_MATHRM_GROUPS = [
    r"\mathrm{ab}^2",
    r"\mathrm{2x}^2",
    r"a \mathrm{over} b",
    r"x \mathrm{sub} i",
    r"\mathrm{Cl}",
    r"\mathrm{12}",
]

# A script stands on a letter, a digit or a group of its own, not on a closing
# parenthesis or bracket, whose parenthesis a screen reader does not speak, and
# not on a \mathrm group.
SCRIPT_ON_A_CLOSING_BASE = [
    "(2x)^2",
    "(a+b)^{2}",
    "(a+b)^{3}",
    "[x]^2",
    r"\left(\frac{a}{b}\right)^{2}",
    r"\left( x \right)^{2}",
    "(x)_i",
    "(x)_{i}",
    r"\mathrm{d}^2",
    r"\mathrm{d}_i",
]

# A run of letters that spells a word the labels use reads like the construct
# that produces the word.
SPOKEN_WORD_RUNS = [
    "a over b",
    "a Over b",
    "x sub i",
    "x squared",
    "2 cubed",
    "pi r",
    "a times b",
    "a choose b",
    "x power y",
    "x root y",
    "a sum b",
    "a dot b",
    "x integral y",
    "a log b",
    "a or b",
    "alpha beta",
]

# A closing parenthesis is terminal only when it closes one opened before the
# group, and what follows it must end the form as well.
PARENTHESES_THAT_DO_NOT_END_A_FORM = [
    "x^{n}) + 1",
    r"\left. x^{n} \right) + 1",
    "(1 + x^{n}) + 2",
    r"(\frac{1}{2})x",
    "(2x)^2",
    "(x^{n}) y",
    "(x^{n}) + 1",
    "((x^{n})) + 1",
    "(x^{n}))",
    r"(\frac{a}{b}) c",
    r"(\sqrt{x}) + 1",
    "(x_{i}) + 1",
    r"\left( x^{n} \right) + 1",
]


@pytest.mark.parametrize(
    "latex",
    [
        *NON_ALPHANUMERIC_SCRIPT_ARGUMENTS,
        *LONG_MATHRM_GROUPS,
        *SCRIPT_ON_A_CLOSING_BASE,
        *SPOKEN_WORD_RUNS,
        *PARENTHESES_THAT_DO_NOT_END_A_FORM,
    ],
)
def test_the_guard_refuses_the_audit_constructions(latex):
    assert equation_alt_text_is_ambiguous(latex) is True


@pytest.mark.parametrize(
    "latex", [item for item in NON_ALPHANUMERIC_SCRIPT_ARGUMENTS if item.isascii()]
)
def test_a_script_mark_never_survives_into_an_accepted_label(latex):
    label = generate_equation_alt_text(latex)
    assert "^" in label or "_" in label, "not a case the label check is for"
    assert equation_alt_text_is_ambiguous(latex) is True


def test_no_accepted_label_holds_a_caret_or_an_underscore_or_a_backslash():
    checked = 0
    for latex in _accepted_placements():
        label = generate_equation_alt_text(latex)
        assert not {"^", "_", "\\"} & set(label), (latex, label)
        checked += 1
    assert checked > 400


def test_a_single_letter_mathrm_group_is_still_said_as_the_letter():
    for latex, words in (
        (r"\mathrm{d}x", "dx"),
        (r"\mathrm{e}", None),
        (r"a\mathrm{b}", "ab"),
    ):
        if words is None:
            assert equation_alt_text_is_ambiguous(latex) is True  # a lone symbol
            continue
        assert equation_alt_text_is_ambiguous(latex) is False
        assert generate_equation_alt_text(latex) == words
        # The same MathML as the plain letters, so no two equations are confused.
        plain = latex.replace(r"\mathrm{", "").replace("}", "")
        assert _canonical(latex) == _canonical(plain)


def test_the_keyword_check_looks_at_whole_runs_of_letters_only():
    for latex in ("a overx", "ab^2", "mc^2 + 1", "dx + 1", "x_i + y_j", "E = mc^{2}"):
        assert "over" not in latex.split() or equation_alt_text_is_ambiguous(latex)
    for accepted in ("ab^2", "dx + 1", "mc^2 + 1", "E = mc^{2}"):
        assert equation_alt_text_is_ambiguous(accepted) is False
    # The same letters in a command are not a run of letters.
    assert equation_alt_text_is_ambiguous(r"\pi r^2") is False
    assert equation_alt_text_is_ambiguous(r"a \times b") is False


def test_every_word_a_spoken_form_can_say_is_a_refused_run_of_letters():
    assert {"over", "sub", "squared", "cubed", "power", "choose", "root"} <= set(
        math_speech._SPOKEN_WORDS
    )
    for word in sorted(math_speech._SPOKEN_WORDS):
        assert equation_alt_text_is_ambiguous(f"a {word} b") is True, word


def test_the_guard_comment_claims_no_more_than_the_rules_do():
    text = Path(math_speech.__file__).read_text(encoding="utf-8")
    assert "not a proof" in text
    assert "accepts an equation only when no other equation" not in text
    assert r"``\mathrm`` group holds one letter" in text


def test_the_command_list_is_shared_with_the_json_repair():
    from src.education import latex_commands, model_json

    assert model_json.LATEX_COMMAND_NAMES is latex_commands.LATEX_COMMAND_NAMES
    assert math_speech._KNOWN_COMMANDS is latex_commands.LATEX_COMMAND_NAMES
