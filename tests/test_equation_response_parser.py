"""Regression tests for model-emitted LaTeX in JSON responses."""

from __future__ import annotations

import pytest

from src.education.remediation.equation_recognizer import (
    EquationRecognitionRejected,
    EquationRecognizer,
)
from src.education.remediation.handwritten_equation_recognizer import (
    HandwrittenEquationRecognitionRejected,
    _parse_hmer_content,
)


def _parse_printed(content: str) -> str:
    return EquationRecognizer(None)._parse(content)["latex"]


def _parse_handwritten(content: str) -> str:
    _, latex = _parse_hmer_content(
        content,
        max_latex_chars=4096,
        max_response_chars=8192,
    )
    return latex


@pytest.mark.parametrize(
    "parser",
    [_parse_printed, _parse_handwritten],
    ids=["printed", "handwritten"],
)
@pytest.mark.parametrize(
    "latex",
    [
        r"\frac{1}{2}",
        r"\beta+\nu+\rho+\theta",
        r"x\approx1",
        r"\sum_{i=1}^{n} i",
        r"\int_0^1 x\,dx",
        r"\(x^2+1=0\)",
    ],
)
def test_single_backslash_latex_commands_are_recovered(parser, latex):
    classification = (
        "printed_equation"
        if parser is _parse_printed
        else "handwritten_equation"
    )
    content = (
        '{"classification":"' + classification + '","latex":"' + latex + '"}'
    )

    assert parser(content) == latex


@pytest.mark.parametrize(
    "parser,classification",
    [
        (_parse_printed, "printed_equation"),
        (_parse_handwritten, "handwritten_equation"),
    ],
    ids=["printed", "handwritten"],
)
def test_already_doubled_latex_backslashes_are_preserved(parser, classification):
    content = (
        '{"classification":"' + classification + '","latex":"\\\\frac{1}{2}"}'
    )
    assert parser(content) == r"\frac{1}{2}"


@pytest.mark.parametrize("run_length", [1, 2, 3, 4])
@pytest.mark.parametrize(
    "parser,classification",
    [
        (_parse_printed, "printed_equation"),
        (_parse_handwritten, "handwritten_equation"),
    ],
    ids=["printed", "handwritten"],
)
def test_odd_and_even_backslash_runs_keep_their_literal_pairs(
    parser, classification, run_length
):
    raw_run = "\\" * run_length
    content = (
        '{"classification":"'
        + classification
        + '","latex":"'
        + raw_run
        + 'alpha"}'
    )

    assert parser(content) == ("\\" * ((run_length + 1) // 2)) + "alpha"


@pytest.mark.parametrize(
    "parser,classification",
    [
        (_parse_printed, "printed_equation"),
        (_parse_handwritten, "handwritten_equation"),
    ],
    ids=["printed", "handwritten"],
)
def test_escaped_quotes_are_not_rewritten(parser, classification):
    content = (
        '{"classification":"'
        + classification
        + '","latex":"x \\"quoted\\""}'
    )
    assert parser(content) == 'x "quoted"'


@pytest.mark.parametrize(
    "parser,classification,error_type",
    [
        (_parse_printed, "printed_equation", EquationRecognitionRejected),
        (
            _parse_handwritten,
            "handwritten_equation",
            HandwrittenEquationRecognitionRejected,
        ),
    ],
    ids=["printed", "handwritten"],
)
def test_literal_control_characters_and_duplicate_keys_still_fail_closed(
    parser, classification, error_type
):
    with pytest.raises(error_type, match="invalid_provider_response"):
        parser('{"classification":"' + classification + '","latex":"x\x01"}')

    duplicate = (
        '{"classification":"'
        + classification
        + '","latex":"x","latex":"y"}'
    )
    with pytest.raises(error_type, match="invalid_provider_response"):
        parser(duplicate)
