"""A generated description is used unchanged or refused, never rewritten.

A prompt that asks for plain spoken words removes LaTeX from a model's answer. A
description that still carries mathematical markup is not trusted, and the image
is left for a person. These tests pin what counts as markup, what must never be
taken for it, and that nothing is ever converted.
"""

from __future__ import annotations

import pytest

from src.education.math_speech import holds_math_markup, refuse_math_markup

# ---------------------------------------------------------------------------
# Text without mathematical markup comes back as the same string
# ---------------------------------------------------------------------------

UNCHANGED = [
    "",
    "A photograph of a laboratory bench with three beakers.",
    "Line one\nLine two with  double  spaces and a tab\there.",
    "Naïve café menu: crème brûlée, 12°C, 5 × 4 and 10 ÷ 2.",
    # Units in parentheses, which a dollar-pair reading would swallow.
    "Bar chart of revenue ($) and cost ($) by year",
    "Revenue ($M) and profit ($M) by quarter",
    "Revenue ($K) vs expenses ($K)",
    "Salaries ($) and bonuses ($)",
    "Plans: $0, 1 user ... $29 per month",
    "Restaurant rated $$ or $$$ overall",
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
    # Code and symbols in prose with no markup in them.
    'Call print("hello") and then x = a * b.',
    "The block if (x > 1) { return; } runs once.",
    'A JSON object looks like {"key": "value"}.',
    "Use snake_case_name for a variable and array[0] for the first item.",
    "Open my_file.txt and the_other_one.txt",
    "Press ^C to stop, or Ctrl + C.",
    "Run echo $HOME and echo $PATH, or use $HOME/$USER.",
    "Unbalanced { brace and } another",
    "A chart of x and y with a title",
]


@pytest.mark.parametrize("text", UNCHANGED)
def test_text_without_markup_is_returned_as_the_same_string(text):
    assert holds_math_markup(text) is False
    result = refuse_math_markup(text)
    assert result == text
    assert result is text


# ---------------------------------------------------------------------------
# Mathematical markup is refused
# ---------------------------------------------------------------------------

REFUSED = [
    # A backslash command.
    r"The equation \frac{a}{b} = c",
    r"The area is \pi r^2",
    r"y = x^2 + \sin x",
    r"Greek \alpha, \beta and \Omega",
    r"All \forall x here",
    r"Reals \mathbb{R} here",
    r"Words \text{if} here",
    r"Longer \alphabet here",
    r"Open the Documents\Projects folder",
    r"The regex \d+ matches digits",
    r"Doubled \\frac{a}{b} here",
    # A path rule that must not swallow LaTeX after a colon.
    r"Part B:\frac{1}{2}",
    r"Part B:\frac12",
    r"Case A:\alpha + 1",
    # Display and bracket delimiters.
    r"Value \(a+b\) here",
    r"Value \[a+b\] here",
    r"Display $$x^{2}+1$$ shown",
    "Display\n$$\n\\frac{a}{b}\n$$\nends",
    # A braced script.
    r"x^{2} and y",
    r"The term a_{n} grows",
    r"A file named image_{index}.png",
    # A bare script with a symbol on both sides.
    r"The slope r^2 matters",
    r"Then x_i is positive",
    r"The index a_ij and b_10",
    r"The value (a+b)^2 is large",
    r"Growth is 2^10 per year",
    # A dollar span with mathematics between the signs.
    r"$\int_0^1 x\,dx$",
    r"$|x|$",
    r"The value $|x|$ is positive",
    r"$a+b$ is the sum",
    r"$f(x) = 2x + 3$ is a line",
    r"$x^2$",
    r"$x$ is a variable",
    r"Both $a$ and $b$ appear",
    r"$a \ne b$",
    r"Shown as $\sqrt{x}$ here",
    r"$$x^2$$",
]


@pytest.mark.parametrize("text", REFUSED)
def test_mathematical_markup_is_refused(text):
    assert holds_math_markup(text) is True
    assert refuse_math_markup(text) is None


def test_nothing_is_ever_rewritten():
    """Every answer is the string that went in, or a refusal."""
    for text in [*UNCHANGED, *REFUSED]:
        result = refuse_math_markup(text)
        assert result is None or result is text


# Forms that an earlier version converted to spoken words. They are refused now.
PREVIOUSLY_CONVERTED = [
    r"Plot of $x^{2}+1$.",
    r"Graph of $$x^{2}+y^{2}=r^{2}$$ shown",
    r"Value \(\alpha+\beta\) here",
    r"Value \[\frac{1}{2}+y=4\] here",
    r"Both $\frac{a}{b}$ and $\sqrt{x}$ appear",
    r"$f(x) = 2x + 3$ is a line",
    r"The equation \frac{a}{b} = c",
    r"The equation \dfrac{a}{b}.",
    r"Area is \pi r^{2} here",
    r"Roots of \sqrt{x} and \binom{n}{k}",
    r"The term a_{n} grows",
    r"Values x^{3} and y^{n}",
    r"Greek \alpha, \beta and \Omega",
    r"Total \sum_{i} x_{i}",
    r"a$x^{2}$b",
    r"The solution is $x \le 3$ for $\sin x = 0$",
]


@pytest.mark.parametrize("text", PREVIOUSLY_CONVERTED)
def test_a_form_that_used_to_be_converted_is_refused_not_rewritten(text):
    assert refuse_math_markup(text) is None


def test_one_span_of_markup_refuses_the_whole_description():
    assert refuse_math_markup(r"A fine description, but \frac12 is not") is None
    assert refuse_math_markup(r"Fine $5 and $10 but $x^2$ is not") is None


# ---------------------------------------------------------------------------
# Windows paths, and the rule that keeps LaTeX from hiding in one
# ---------------------------------------------------------------------------


def test_a_path_next_to_markup_does_not_excuse_the_markup():
    text = r"Open C:\Users\bob\notes.txt to see \frac{a}{b} there"
    assert refuse_math_markup(text) is None


def test_a_drive_path_that_starts_with_a_latex_command_is_not_a_path():
    for text in (
        r"Part B:\frac{1}{2}",
        r"Part B:\frac12",
        r"Item C:\sqrt{2}",
        r"Item C:\pi r",
    ):
        assert holds_math_markup(text) is True


def test_a_real_drive_path_with_a_space_in_it_stays_whole():
    for text in (
        r"C:\Program Files\Common Files\app.dll",
        r"Open D:\My Documents\Reports\q3.xlsx now",
    ):
        assert refuse_math_markup(text) is text


def test_a_value_that_is_not_text_is_refused():
    assert refuse_math_markup(None) is None
    assert refuse_math_markup(5) is None


# ---------------------------------------------------------------------------
# Independently compiled cases, used as data
# ---------------------------------------------------------------------------

# Spans an independent list expected a spoken-label table to refuse, plus spans it
# expected to convert. None is rewritten here, so every one of them is refused as
# markup. Wrapped in dollar signs, as a model writes them.
INDEPENDENT_SPANS = [
    r"\frac{x^{2}}{y}",
    r"\frac13",
    r"\frac{a}{\sqrt{b}}",
    r"\sqrt{x^{2}}",
    r"x^{\frac{a}{b}}",
    r"x_{i_{j}}",
    r"\frac{90}{\sum_{k=0}^{m} 5}",
    r"A^{t} > B^{\lim_{n \to \infty} V}",
    r"x^10",
    r"x_10",
    r"\sqrt2",
    r"\frac{a}b",
    r"x^\alpha",
    r"x^{2}y",
    r"\frac{a}{b}c",
    r"a\frac{b}{c}",
    r"d\theta",
    r"2\pi",
    r"\sqrt[3]{x}",
    r"\sin x",
    r"\lim_{n \to \infty} V",
    r"\int_{0}^{1} x \, dx",
    r"u \le v",
    r"\text{hello}",
    r"\left( x \right)",
    r"{a}{b}",
    r"\frac{a}{b",
    r"\frac{a}{b}",
    r"x^{2}",
    r"x^2",
    r"x^3",
    r"x^{n}",
    r"x^{10}",
    r"x_{i}",
    r"x_1",
    r"\sqrt{x}",
    r"\alpha + \beta = \gamma",
    r"\pi r^2",
    r"\sum_{k=1}^{m} 87",
    r"\int_{1}^{x} z",
    r"\Theta_{4}^{m} \neq \sqrt{0 \times y + 9}",
    r"(s \times 4 \times \Phi)^{\sigma^{6}}",
]


@pytest.mark.parametrize("latex", INDEPENDENT_SPANS)
def test_every_independently_compiled_span_is_refused_not_converted(latex):
    assert refuse_math_markup(f"${latex}$") is None
    if latex != "{a}{b}":
        # Braces alone, with no command, script or dollar sign, are ordinary text.
        assert refuse_math_markup(f"The image shows {latex} here") is None


MODEL_ANSWERS_REFUSED = [
    r"Mathematical inequality: $A^t > B^{\lim_{n \to \infty} V}$.",
    "An inequality written in mathematical notation: \n\n"
    r"$$\sum_{k=1}^{m} 87 > \int_{1}^{x} z \, d\theta + \Sigma$$",
    r"Mathematical inequality: $u \le \frac{90}{\sum_{k=0}^m 5}$",
    r"Inequality ($\Theta^\Delta \le Y_0 \times \Psi$).",
    r"Mathematical equation: $\sqrt{w} = \arctan((\log(X)))$",
    r"A mathematical inequality: \Theta_4^m \neq \sqrt{0 \times y + 9}",
    r"Written as: (4) < (s \times 4 \times \Phi)^{\sigma^6}.",
    r"Mathematical inequality: $\frac{\Omega^{5.32}}{(0.90)} \ge \int_{1}^{-\infty} 4.11 \, du$",
]


@pytest.mark.parametrize("answer", MODEL_ANSWERS_REFUSED)
def test_model_answers_with_markup_are_refused(answer):
    assert refuse_math_markup(answer) is None


MODEL_ANSWERS_PLAIN = [
    "An inequality: the sum from k equals 1 to m of 87 is greater than the integral.",
    "The graph of x squared opens upward.",
    "Capital Theta with superscript m is not equal to the square root of 0 times y plus 9.",
    "The mathematical equation is: H sub t equals open parenthesis 1.99 close parenthesis.",
    "A to the power of t is greater than B to the power of the limit of V.",
]


@pytest.mark.parametrize("answer", MODEL_ANSWERS_PLAIN)
def test_model_answers_in_plain_words_pass_unchanged(answer):
    assert refuse_math_markup(answer) is answer
