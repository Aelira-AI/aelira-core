"""Spoken text for the canonical MathML that verification checks (#698).

The alt text written to ``/Alt`` must say what the verified MathML says, not
what a regular expression over the recognised LaTeX happens to produce. These
tests walk the tree: the four regressions from the issue, the repo's spoken
voice for fractions, roots, binomials and scripts, the refusal of anything the
walk could not say without guessing, the fixer's use of the walk, and the
single-symbol corpus where every distinct canonical MathML must read
distinctly. The ambiguity guard of #693 refuses upstream and is unchanged; the
tests here restate its refusals for the broken cases so a loosening shows up.
"""

from __future__ import annotations

from typing import Optional

import pytest
from latex2mathml.converter import convert as latex_to_mathml

from src.education.math_speech import (
    equation_alt_text_is_ambiguous,
    generate_equation_alt_text,
    spoken_text_from_mathml,
)
from src.education.remediation.equation_verifier import canonicalize_mathml
from src.education.remediation.math_fixer import MathFixer


def _spoken(latex: str) -> Optional[str]:
    """The words of the canonical MathML for this LaTeX."""
    return spoken_text_from_mathml(canonicalize_mathml(latex_to_mathml(latex)))


# ---------------------------------------------------------------------------
# The four cases from the issue, read from the tree the verifier checked
# ---------------------------------------------------------------------------

ISSUE_CASES = [
    (r"\frac13", "1 over 3"),
    (r"\binom{n}{k}", "n choose k"),
    (r"\sqrt x", "square root of x"),
    ("x^10", "x to the power 1 0"),
]


@pytest.mark.parametrize(("latex", "announced"), ISSUE_CASES)
def test_the_issue_cases_are_announced_from_the_mathml(latex: str, announced: str):
    assert _spoken(latex) == announced


def test_x_to_the_first_power_then_zero_is_not_x_to_the_power_ten():
    # LaTeX reads x^10 as x to the first power followed by 0, and the tree
    # says so: an msup over 1, then a separate 0. x^{10} is one msup over 10.
    assert _spoken(r"x^{10}") == "x to the power 10"
    assert _spoken("x^10") == "x to the power 1 0"
    assert _spoken("x^10") != _spoken(r"x^{10}")


@pytest.mark.parametrize("latex", [r"\frac13", r"\sqrt x", "x^10"])
def test_the_693_guard_still_refuses_the_broken_cases(latex: str):
    # Deriving the label from MathML does not loosen the upstream refusal.
    assert equation_alt_text_is_ambiguous(latex) is True


def test_the_guard_still_accepts_the_braced_binomial():
    assert equation_alt_text_is_ambiguous(r"\binom{n}{k}") is False


# ---------------------------------------------------------------------------
# The repo's spoken voice, now produced from the tree
# ---------------------------------------------------------------------------

VOICE = [
    # Fractions, however the LaTeX was braced or styled.
    (r"\frac{a}{b}", "a over b"),
    (r"\frac{12}{3}", "12 over 3"),
    (r"\frac{1.5}{2}", "1.5 over 2"),
    (r"\frac{\pi}{2}", "pi over 2"),
    (r"\dfrac{1}{2}", "1 over 2"),
    (r"\tfrac{3}{4}", "3 over 4"),
    (r"\cfrac{p}{q}", "p over q"),
    (r"1\frac{2}{3}", "1 2 over 3"),
    (r"\frac{a}{b} = c", "a over b = c"),
    # Roots.
    (r"\sqrt{x}", "square root of x"),
    (r"\sqrt{2}", "square root of 2"),
    (r"\sqrt[3]{x}", "cube root of x"),
    (r"\sqrt[2]{x}", "square root of x"),
    (r"\sqrt[n]{x}", "nth root of x"),
    # Binomials: the parentheses the drawing brings are not announced.
    (r"\binom{n}{k} + 1", "n choose k + 1"),
    (r"\dbinom{n}{k}", "n choose k"),
    # Sub- and superscripts.
    (r"x^{2}", "x squared"),
    (r"x^{3} = 8", "x cubed = 8"),
    (r"x^{n}", "x to the power n"),
    (r"a_{n}", "a sub n"),
    (r"x_i", "x sub i"),
    (r"x^2_i", "x squared sub i"),
    (r"x^{n}_{i}", "x to the power n sub i"),
    # Letters, numbers and operators keep the table's words.
    (r"\alpha + \beta = \gamma", "alpha + beta = gamma"),
    (r"a \times b \div c", "a times b divided by c"),
    (r"a \leq b \neq c", "a less than or equal to b not equal to c"),
    (r"a \approx b \equiv c", "a approximately equal to b equivalent to c"),
    (r"\ln x", "natural log x"),
    (r"\infty", "infinity"),
    (r"1 \ldots n", "1 ellipsis n"),
    # Expressions whose exact words the pipeline asserts end to end.
    (r"x^{2} + 1 = 0", "x squared + 1 = 0"),
    (r"x^{2} + 3x - 7 = 0", "x squared + 3x - 7 = 0"),
    (r"y = \frac{1}{2}", "y = 1 over 2"),
    (r"f(x^{n}, y)", "f(x to the power n, y)"),
    (r"\left(\frac{a}{b}\right)", "(a over b)"),
]


@pytest.mark.parametrize(("latex", "announced"), VOICE)
def test_the_tree_is_spoken_in_the_repos_voice(latex: str, announced: str):
    assert _spoken(latex) == announced


# ---------------------------------------------------------------------------
# The walk refuses rather than guesses
# ---------------------------------------------------------------------------

REFUSALS = [
    # A compound argument is refused: the flat voice cannot show where the
    # fraction ends, which is how \frac{a+b}{c} and \frac{a}{b} + c would
    # otherwise read the same.
    r"\frac{a+b}{c}",
    r"\frac{a}{b+c}",
    r"\sqrt{x+1}",
    r"x^{n+1}",
    r"a_{n+1}",
    r"\binom{n+1}{k}",
    # A base that is not one token is refused: (ab)^2 and ab^2 would
    # otherwise read the same.
    r"{ab}^2",
    # Elements the walk has no words for.
    r"\hat{x}",
    r"\vec{v}",
    r"\begin{matrix}a&b\\c&d\end{matrix}",
    r"\frac{d}{dx} x^{2} = 2x",
]


@pytest.mark.parametrize("latex", REFUSALS)
def test_the_walk_refuses_what_it_cannot_say_without_guessing(latex: str):
    assert _spoken(latex) is None


def test_the_walk_refuses_text_and_unreadable_markup():
    # mtext is refused, which keeps \text{if } x on the LaTeX label it trusts.
    assert spoken_text_from_mathml(latex_to_mathml(r"\text{if } x")) is None
    assert spoken_text_from_mathml("") is None
    assert spoken_text_from_mathml("   ") is None
    assert spoken_text_from_mathml(None) is None
    assert spoken_text_from_mathml("<math><mi>x</math>") is None
    assert spoken_text_from_mathml("<mrow><mi>x</mi></mrow>") is None
    assert spoken_text_from_mathml("<math><mfrac><mi>a</mi></mfrac></math>") is None
    assert spoken_text_from_mathml("<math/>") is None
    assert spoken_text_from_mathml("<math><mspace/></math>") is None


# ---------------------------------------------------------------------------
# The fixer reads the verified MathML it already holds
# ---------------------------------------------------------------------------


def test_the_fixer_reads_the_mathml_it_verified_not_the_latex():
    mathml = canonicalize_mathml(latex_to_mathml("x^10"))
    # The regex over the LaTeX says "x to the power 10"; the tree says the
    # tree's truth.
    assert generate_equation_alt_text("x^10") == "x to the power 10"
    assert MathFixer._generate_aria_label("x^10", mathml=mathml) == (
        "x to the power 1 0"
    )


def test_the_fixer_announces_the_issue_cases_from_mathml():
    for latex, announced in ISSUE_CASES:
        mathml = canonicalize_mathml(latex_to_mathml(latex))
        assert MathFixer._generate_aria_label(latex, mathml=mathml) == announced


def test_the_fixer_keeps_the_latex_label_when_no_mathml_is_given():
    assert MathFixer._generate_aria_label(r"\frac{a}{b}") == "a over b"


def test_the_fixer_falls_back_when_the_walk_refuses():
    latex = r"\frac{a+b}{c}"
    mathml = canonicalize_mathml(latex_to_mathml(latex))
    # No wrong tree reading and no invented words: the fallback is exactly
    # the label the equation path has always produced.
    assert MathFixer._generate_aria_label(latex, mathml=mathml) == (
        generate_equation_alt_text(latex)
    )


def test_the_fixer_falls_back_to_the_generic_label():
    assert (
        MathFixer._generate_aria_label("", mathml="<math></math>")
        == "mathematical equation"
    )


def test_math_fixer_calls_the_shared_walk():
    from src.education.remediation import math_fixer

    assert math_fixer.spoken_text_from_mathml is spoken_text_from_mathml


# ---------------------------------------------------------------------------
# Single-symbol edits: distinct canonical MathML reads distinctly
# ---------------------------------------------------------------------------

# One corpus, each neighbour differing from another by a single symbol. A
# wrong reading that two different equations share is what a screen reader
# cannot survive, so every distinct MathML here must announce differently.
SINGLE_SYMBOL_CORPUS = [
    r"x^{2}",
    r"x^{3}",
    r"x^{4}",
    r"\frac{1}{2}",
    r"\frac{2}{2}",
    r"\frac{1}{3}",
    r"\sqrt{x}",
    r"\sqrt{y}",
    r"\sqrt[3]{x}",
    r"\sqrt[4]{x}",
    r"\binom{n}{k}",
    r"\binom{m}{k}",
    r"\binom{n}{j}",
    r"x_{1} + x_{2}",
    r"x_{1} + x_{3}",
    r"x^{2} + 3x - 7 = 0",
    r"x^{2} + 3x - 8 = 0",
    r"a < b",
    r"a > b",
    r"y = \frac{1}{2}",
    r"z = \frac{1}{2}",
    r"a_{n}",
    r"a_{m}",
]

SINGLE_SYMBOL_PAIRS = [
    (r"\frac{x}{y}", r"\frac{x}{z}"),
    (r"\sqrt{x} + 1", r"\sqrt{y} + 1"),
    (r"\binom{n}{k}", r"\binom{n}{m}"),
    (r"x_{1} = 2", r"x_{2} = 2"),
    (r"x^{5}", r"x^{6}"),
]


@pytest.mark.parametrize(("left", "right"), SINGLE_SYMBOL_PAIRS)
def test_a_single_symbol_edit_changes_the_words(left: str, right: str):
    left_mathml = canonicalize_mathml(latex_to_mathml(left))
    right_mathml = canonicalize_mathml(latex_to_mathml(right))
    assert left_mathml != right_mathml
    left_spoken = spoken_text_from_mathml(left_mathml)
    right_spoken = spoken_text_from_mathml(right_mathml)
    assert left_spoken is not None and right_spoken is not None
    assert left_spoken != right_spoken


def test_distinct_canonical_mathml_reads_distinctly_across_the_corpus():
    readings: dict[str, set[str]] = {}
    for latex in SINGLE_SYMBOL_CORPUS:
        mathml = canonicalize_mathml(latex_to_mathml(latex))
        spoken = spoken_text_from_mathml(mathml)
        assert spoken is not None, latex
        readings.setdefault(spoken, set()).add(mathml)
    for spoken, sources in readings.items():
        assert len(sources) == 1, f"{spoken!r} announced for {len(sources)} MathML"
    assert len(readings) >= 20
