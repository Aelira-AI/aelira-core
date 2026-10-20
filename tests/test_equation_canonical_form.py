"""Canonical MathML form v2: redundant groups collapse, binomials are accepted.

Handwritten agreement compares two independent readings by canonical string
equality, so readings that differ only in LaTeX braces must canonicalise to the
same string while genuinely different readings must not.
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest
from latex2mathml.converter import convert

from src.education.remediation.equation_verifier import (
    CANONICAL_MATHML_VERSION,
    EquationVerificationRejected,
    canonicalize_mathml,
)

EQUAL_PAIRS = [
    (r"x^{2}", r"x^2"),
    (r"\frac{1}{2}", r"\frac12"),
    (r"\sqrt{x}", r"\sqrt x"),
    (r"x_{i}^{2}", r"x_i^2"),
    (r"{x}+1", r"x+1"),
    (r"{{x}}", r"x"),
    (r"{a+b}", r"a+b"),
]
DIFFERENT_PAIRS = [
    (r"x^2", r"x^3"),
    (r"\frac12", r"\frac21"),
    (r"a-b", r"a+b"),
    (r"x^{ab}", r"x^a b"),
    # A group around a lone operator or around several factors is not redundant.
    (r"{-}x", r"-x"),
    (r"a{+}b", r"a+b"),
    (r"{ab}^2", r"ab^2"),
]
IDEMPOTENT_LATEX = [
    *(latex for pair in EQUAL_PAIRS for latex in pair),
    *(latex for pair in DIFFERENT_PAIRS for latex in pair),
    r"\left(x\right)^{2}",
    r"{-}x",
    r"\binom{n}{k}",
    r"\dbinom{n}{k}",
    r"\tbinom{n}{k}",
    r"\frac{{a}}{{{b}}}",
    r"x^{2} + 3x - 7 = 0",
]


def _canonical(latex: str) -> str:
    return canonicalize_mathml(convert(latex))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("left", "right"), EQUAL_PAIRS)
def test_readings_that_differ_only_in_braces_canonicalise_identically(left, right):
    assert _canonical(left) == _canonical(right)


@pytest.mark.parametrize(("left", "right"), DIFFERENT_PAIRS)
def test_genuinely_different_readings_stay_different(left, right):
    assert _canonical(left) != _canonical(right)


def test_collapsed_form_is_exact_for_the_reported_reproduction():
    assert _canonical(r"x^{2}") == (
        '<math display="inline"><msup><mi>x</mi><mn>2</mn></msup></math>'
    )
    assert _canonical(r"\frac{1}{2}") == (
        '<math display="inline"><mfrac><mn>1</mn><mn>2</mn></mfrac></math>'
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param(
            '<math><mrow mathcolor="red"><mi>x</mi></mrow></math>',
            '<math><mrow mathcolor="red"><mi>x</mi></mrow></math>',
            id="mrow-with-attributes-is-kept",
        ),
        pytest.param(
            '<math><mrow><mrow mathcolor="red"><mi>x</mi></mrow></mrow></math>',
            '<math><mrow mathcolor="red"><mi>x</mi></mrow></math>',
            id="only-the-plain-outer-wrapper-collapses",
        ),
        pytest.param(
            "<math><mrow><mo>-</mo></mrow></math>",
            "<math><mrow><mo>-</mo></mrow></math>",
            id="lone-operator-keeps-its-row",
        ),
        pytest.param(
            "<math><mrow><mrow><mo>-</mo></mrow></mrow></math>",
            "<math><mrow><mo>-</mo></mrow></math>",
            id="nested-rows-around-an-operator-reduce-to-one",
        ),
        pytest.param(
            "<math><mrow><mi>a</mi><mi>b</mi></mrow></math>",
            "<math><mrow><mi>a</mi><mi>b</mi></mrow></math>",
            id="multi-child-row-is-kept",
        ),
        pytest.param(
            "<math><mrow>t<mi>x</mi></mrow></math>",
            "<math><mrow>t<mi>x</mi></mrow></math>",
            id="stray-leading-text-blocks-collapse",
        ),
        pytest.param(
            "<math><mrow><mi>x</mi>t</mrow></math>",
            "<math><mrow><mi>x</mi>t</mrow></math>",
            id="stray-trailing-text-blocks-collapse",
        ),
        pytest.param(
            "<math><mrow> <mi>x</mi> </mrow></math>",
            "<math><mi>x</mi></math>",
            id="whitespace-only-text-and-tails-collapse",
        ),
        pytest.param(
            "<math><mi>a</mi><mrow><mi>b</mi></mrow>t<mi>c</mi></math>",
            "<math><mi>a</mi><mi>b</mi>t<mi>c</mi></math>",
            id="the-collapsed-row-hands-its-tail-to-its-child",
        ),
    ],
)
def test_collapse_applies_only_to_meaningless_single_child_rows(source, expected):
    assert canonicalize_mathml(source) == expected


@pytest.mark.parametrize("latex", IDEMPOTENT_LATEX)
def test_canonicalisation_is_idempotent(latex):
    once = _canonical(latex)

    assert canonicalize_mathml(once) == once


def test_canonical_form_version_is_published():
    assert CANONICAL_MATHML_VERSION == "canonical-mathml-v2"


@pytest.mark.parametrize("latex", [r"\binom{n}{k}", r"\dbinom{n}{k}", r"\tbinom{n}{k}"])
def test_binomial_coefficients_are_accepted(latex):
    canonical = _canonical(latex)

    assert '<mfrac linethickness="0">' in canonical
    assert "<mi>n</mi>" in canonical and "<mi>k</mi>" in canonical
    assert "<mrow><mi>n</mi></mrow>" not in canonical
    assert canonicalize_mathml(canonical) == canonical


@pytest.mark.parametrize(
    "latex",
    [
        r"\dbinom{n}{k}",
        r"\tbinom{n}{k}",
        r"\dfrac{1}{2}",
        r"\tfrac{1}{2}",
        r"\cfrac{1}{2}",
        r"\cfrac{1}{1+\cfrac{1}{2}}",
        r"\displaystyle x",
        r"\textstyle x",
        r"\scriptstyle x",
        r"\scriptscriptstyle x",
        r"\displaystyle\sum_{i=1}^n x_i",
    ],
)
def test_style_wrappers_that_carry_only_displaystyle_and_scriptlevel_are_accepted(
    latex,
):
    canonical = _canonical(latex)

    styles = re.findall(r"<mstyle([^>]*)>", canonical)
    assert styles
    for attributes in styles:
        assert set(re.findall(r"([a-z]+)=", attributes)) <= {
            "displaystyle",
            "scriptlevel",
        }
    assert canonicalize_mathml(canonical) == canonical


@pytest.mark.parametrize(
    "latex",
    [
        r"\color{red}x",
        r"\textcolor{red}{x}",
        r"\large x",
        r"\Large x",
        r"\small x",
    ],
)
def test_colour_and_size_styling_from_latex_stays_rejected(latex):
    # latex2mathml puts these on an mstyle as mathcolor or mathsize.
    mathml = convert(latex)
    assert "<mstyle" in mathml

    with pytest.raises(EquationVerificationRejected, match="invalid_mathml"):
        canonicalize_mathml(mathml)


@pytest.mark.parametrize(
    "mathml",
    [
        '<math><mstyle mathcolor="red"><mi>x</mi></mstyle></math>',
        '<math><mstyle mathsize="2em"><mi>x</mi></mstyle></math>',
        '<math><mstyle mathbackground="red"><mi>x</mi></mstyle></math>',
        '<math><mstyle mathvariant="bold"><mi>x</mi></mstyle></math>',
        '<math><mstyle displaystyle="true" mathcolor="red"><mi>x</mi></mstyle></math>',
        '<math><mstyle linethickness="0"><mi>x</mi></mstyle></math>',
        '<math><mstyle onclick="run()"><mi>x</mi></mstyle></math>',
        '<math><mstyle scriptlevel="url(javascript:x)"><mi>x</mi></mstyle></math>',
    ],
)
def test_mstyle_accepts_only_displaystyle_and_scriptlevel(mathml):
    with pytest.raises(EquationVerificationRejected, match="invalid_mathml"):
        canonicalize_mathml(mathml)


def test_mstyle_with_displaystyle_and_scriptlevel_is_accepted():
    mathml = (
        '<math><mstyle displaystyle="true" scriptlevel="0"><mi>x</mi></mstyle></math>'
    )

    assert canonicalize_mathml(mathml) == mathml


@pytest.mark.parametrize(
    "mathml",
    [
        '<math><mi linethickness="0">x</mi></math>',
        '<math><msup linethickness="0"><mi>x</mi><mn>2</mn></msup></math>',
        '<math><mrow linethickness="0"><mi>x</mi><mi>y</mi></mrow></math>',
        '<math><mfrac linethickness="url(javascript:x)"><mi>a</mi><mi>b</mi></mfrac></math>',
        '<math><mfrac linethickness="http:x"><mi>a</mi><mi>b</mi></mfrac></math>',
        '<math><mfrac linethickness="https://example.invalid"><mi>a</mi><mi>b</mi></mfrac></math>',
        '<math><mfrac linethickness="data:x"><mi>a</mi><mi>b</mi></mfrac></math>',
        f'<math><mfrac linethickness="{"1" * 129}"><mi>a</mi><mi>b</mi></mfrac></math>',
        '<math><mfrac thickness="0"><mi>a</mi><mi>b</mi></mfrac></math>',
        '<math><mfrac onclick="run()"><mi>a</mi><mi>b</mi></mfrac></math>',
    ],
)
def test_linethickness_is_rejected_off_mfrac_and_hostile_values_are_rejected(mathml):
    with pytest.raises(EquationVerificationRejected, match="invalid_mathml"):
        canonicalize_mathml(mathml)


def test_linethickness_is_accepted_on_mfrac_only():
    accepted = canonicalize_mathml(
        '<math><mfrac linethickness="0"><mi>a</mi><mi>b</mi></mfrac></math>'
    )

    assert accepted == (
        '<math><mfrac linethickness="0"><mi>a</mi><mi>b</mi></mfrac></math>'
    )


def test_handwritten_verifier_agrees_on_readings_that_differ_only_in_braces():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerifier,
    )
    from tests.test_handwritten_math_recognition import (
        Client,
        _evidence,
        _raster,
        _success,
    )

    raster = _raster()
    suitability = _evidence(raster)
    primary = HandwrittenEquationRecognizer(
        Client(_success('{"classification":"handwritten_equation","latex":"x^{2}"}'))
    ).recognize(raster, suitability)
    evidence = HandwrittenEquationVerifier(
        Client(_success('{"classification":"handwritten_equation","latex":"x^2"}'))
    ).verify(raster, suitability, primary)

    expected = '<math display="inline"><msup><mi>x</mi><mn>2</mn></msup></math>'
    assert evidence.passed is True
    assert evidence.primary_mathml_sha256 == evidence.verifier_mathml_sha256
    assert evidence.mathml_sha256 == _sha256(expected)
    assert evidence.primary_latex_sha256 != evidence.verifier_latex_sha256


def test_handwritten_verifier_still_reports_disagreement_for_different_readings():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerificationRejected,
        HandwrittenEquationVerifier,
    )
    from tests.test_handwritten_math_recognition import (
        Client,
        _evidence,
        _raster,
        _success,
    )

    raster = _raster()
    suitability = _evidence(raster)
    primary = HandwrittenEquationRecognizer(
        Client(_success('{"classification":"handwritten_equation","latex":"x^{2}"}'))
    ).recognize(raster, suitability)

    with pytest.raises(
        HandwrittenEquationVerificationRejected, match="semantic_disagreement"
    ):
        HandwrittenEquationVerifier(
            Client(_success('{"classification":"handwritten_equation","latex":"x^3"}'))
        ).verify(raster, suitability, primary)


def test_handwritten_policy_identity_is_v2_and_binds_the_canonical_form():
    from src.education.handwritten_equation_policy import (
        HANDWRITTEN_VERIFIER_POLICY_SHA256,
        HANDWRITTEN_VERIFIER_POLICY_VERSION,
    )
    from src.education.handwritten_math_suitability import POLICY_SHA256

    def digest(material: dict[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()

    base = {
        "agreement": "exact_canonical_mathml",
        "required_agreement_count": 2,
        "suitability_policy_sha256": POLICY_SHA256,
    }
    v2 = digest(
        {
            "policy_version": "handwritten-equation-consensus-v2",
            "canonical_mathml_version": "canonical-mathml-v2",
            **base,
        }
    )
    v1 = digest({"policy_version": "handwritten-equation-consensus-v1", **base})

    assert HANDWRITTEN_VERIFIER_POLICY_VERSION == "handwritten-equation-consensus-v2"
    assert HANDWRITTEN_VERIFIER_POLICY_SHA256 == v2
    assert HANDWRITTEN_VERIFIER_POLICY_SHA256 != v1
    # Changing only the canonical-form version must change the policy digest.
    assert v2 != digest(
        {
            "policy_version": "handwritten-equation-consensus-v2",
            "canonical_mathml_version": "canonical-mathml-v1",
            **base,
        }
    )
