"""Single-backslash LaTeX in equation readings, for every equation recogniser."""

from __future__ import annotations

import json

import pytest

from src.education.remediation.equation_reading_parser import (
    EquationReadingRejected,
    double_unescaped_backslashes,
    parse_equation_reading,
)
from tests.test_handwritten_math_recognition import (
    Client as HandwrittenClient,
    _evidence,
    _raster,
    _success,
)
from tests.test_image_equation_recognition import (
    PAYLOAD,
    Client as PrintedClient,
    success,
)

BS = "\\"


def _printed(body: str) -> str:
    """One printed answer whose LaTeX text is written into JSON verbatim."""
    return '{"classification":"printed_equation","latex":"' + body + '"}'


def _handwritten(body: str) -> str:
    return '{"classification":"handwritten_equation","latex":"' + body + '"}'


def _parse(content: str, **overrides: object):
    options: dict[str, object] = {
        "equation_classification": "printed_equation",
        "null_classifications": frozenset({"not_equation"}),
        "max_latex_chars": 4096,
        "max_response_chars": 8192,
    }
    options.update(overrides)
    return parse_equation_reading(content, **options)


# Each entry is the LaTeX the model meant, written with single backslashes.
SINGLE_BACKSLASH_LATEX = [
    r"\frac{1}{2}",
    r"\beta",
    r"\nu",
    r"\rho",
    r"\theta",
    r"\approx",
    r"\sum",
    r"\int",
    r"\int_0^1 \frac{1}{x} \, dx \approx \beta",
    r"\sum_{n=1}^{\infty} \frac{1}{n^{2}} = \frac{\pi^{2}}{6}",
    r"\upsilon + \underline{x}",
    r"\(x^2 + 1\)",
    r"\[ y = \nu \]",
]


@pytest.mark.parametrize("latex", SINGLE_BACKSLASH_LATEX)
def test_single_backslash_latex_is_recovered(latex: str):
    classification, parsed = _parse(_printed(latex))

    assert classification == "printed_equation"
    assert parsed == latex


@pytest.mark.parametrize("latex", SINGLE_BACKSLASH_LATEX)
def test_the_unrepaired_strict_parse_really_loses_these_answers(latex: str):
    """Pins the defect: without the repair these answers are not usable."""
    try:
        strict = json.loads(_printed(latex))["latex"]
    except ValueError:
        return
    assert strict != latex
    assert not strict.isprintable()


@pytest.mark.parametrize("latex", SINGLE_BACKSLASH_LATEX)
def test_doubled_backslashes_parse_to_the_same_latex_without_repair(latex: str):
    escaped = json.dumps(latex)[1:-1]
    assert json.loads(_printed(escaped))["latex"] == latex

    assert _parse(_printed(escaped)) == ("printed_equation", latex)


@pytest.mark.parametrize(
    ("run", "expected"),
    [(1, 1), (2, 1), (3, 2), (4, 2), (5, 3), (6, 3)],
)
def test_odd_and_even_runs_of_backslashes_are_handled(run: int, expected: int):
    """An even run is escaped backslashes; only the last of an odd run is bare."""
    _, parsed = _parse(_printed(BS * run + "frac{1}{2}"))

    assert parsed == BS * expected + "frac{1}{2}"


@pytest.mark.parametrize("run", [1, 2, 3, 4])
def test_runs_of_backslashes_before_a_non_escape_character(run: int):
    _, parsed = _parse(_printed("x" + BS * run + "approx y"))

    assert parsed == "x" + BS * ((run + 1) // 2) + "approx y"


def test_a_strict_valid_answer_with_printable_latex_is_never_repaired():
    # ``\\nu`` is a correctly escaped backslash followed by nu. Repairing it
    # would turn it into two backslashes.
    assert _parse(_printed(BS * 2 + "nu"))[1] == BS + "nu"
    assert _parse(_printed(BS * 4 + "nu"))[1] == BS * 2 + "nu"
    # A strict-valid answer that happens to contain a bare-looking backslash
    # before a quote or slash keeps its JSON meaning.
    assert _parse(_printed("a" + BS + '"b'))[1] == 'a"b'
    assert _parse(_printed("a" + BS + "/b"))[1] == "a/b"


def test_a_valid_unicode_escape_keeps_its_meaning():
    assert _parse(_printed(BS + "u03b1 + " + BS + BS + "beta"))[1] == (
        "\u03b1 + " + BS + "beta"
    )
    assert _parse(_printed(BS + "u00e9"))[1] == "\u00e9"


def test_a_unicode_escape_is_kept_when_other_backslashes_need_repair():
    assert _parse(_printed(BS + "u03b1 + " + BS + "beta"))[1] == (
        "\u03b1 + " + BS + "beta"
    )
    # Commands that begin with u are not unicode escapes.
    assert _parse(_printed(BS + "upsilon " + BS + "uparrow " + BS + "underline{x}"))[
        1
    ] == (BS + "upsilon " + BS + "uparrow " + BS + "underline{x}")


def test_an_escaped_quote_inside_latex_survives_the_repair():
    _, parsed = _parse(_printed("x " + BS + "approx " + BS + '"y' + BS + '"'))

    assert parsed == "x " + BS + 'approx "y"'


def test_the_repair_is_only_a_backslash_change():
    assert double_unescaped_backslashes(BS + "frac") == BS * 2 + "frac"
    assert double_unescaped_backslashes(BS * 2 + "frac") == BS * 2 + "frac"
    assert double_unescaped_backslashes(BS * 3 + "frac") == BS * 4 + "frac"
    assert double_unescaped_backslashes(BS + '"') == BS + '"'
    assert double_unescaped_backslashes(BS + "/") == BS + "/"
    assert double_unescaped_backslashes(BS + BS) == BS + BS
    assert double_unescaped_backslashes(BS + "u03b1") == BS + "u03b1"
    assert double_unescaped_backslashes(BS + "u03b") == BS * 2 + "u03b"
    assert double_unescaped_backslashes("no backslash") == "no backslash"


def test_repair_is_not_attempted_when_the_strict_parse_succeeds(monkeypatch):
    from src.education.remediation import equation_reading_parser

    def forbidden(content: str) -> str:
        raise AssertionError("a strict-valid answer must not be repaired")

    monkeypatch.setattr(
        equation_reading_parser, "double_unescaped_backslashes", forbidden
    )

    assert _parse(_printed("x^2 + 1 = 0"))[1] == "x^2 + 1 = 0"
    assert _parse('{"classification":"not_equation","latex":null}') == (
        "not_equation",
        None,
    )


@pytest.mark.parametrize(
    "content",
    [
        # Duplicate keys, with and without a backslash that needs repair.
        '{"classification":"printed_equation","latex":"x","latex":"y"}',
        '{"classification":"printed_equation","latex":"\\frac1","latex":"y"}',
        '{"classification":"printed_equation","classification":"not_equation",'
        '"latex":null}',
        # Extra and missing keys.
        '{"classification":"printed_equation","latex":"\\frac{1}{2}","extra":1}',
        '{"classification":"printed_equation"}',
        # Wrong shapes.
        '{"classification":"unknown","latex":"\\approx"}',
        '{"classification":"not_equation","latex":"\\approx"}',
        '{"classification":"printed_equation","latex":null}',
        '{"classification":"printed_equation","latex":""}',
        '{"classification":"printed_equation","latex":7}',
        '[{"classification":"printed_equation","latex":"\\frac1"}]',
        '"\\frac"',
        # Prose, fences and padding around the object.
        '```json\n{"classification":"printed_equation","latex":"\\frac1"}\n```',
        'prefix {"classification":"printed_equation","latex":"\\frac1"}',
        '{"classification":"printed_equation","latex":"\\frac1"} suffix',
        ' {"classification":"printed_equation","latex":"\\frac1"}',
        # Non-printable content the repair cannot remove: a raw control
        # character, raw invisible marks, and escaped ones.
        '{"classification":"printed_equation","latex":"x\n+\\approx"}',
        '{"classification":"printed_equation","latex":"x\u200by \\approx"}',
        '{"classification":"printed_equation","latex":"x\u202ey"}',
        '{"classification":"printed_equation","latex":"x\u0085y"}',
        '{"classification":"printed_equation","latex":"x\\u0085y \\approx"}',
        '{"classification":"printed_equation","latex":"x\\u202ey \\frac1"}',
        '{"classification":"printed_equation","latex":"x\\u200by"}',
        '{"classification":"printed_equation","latex":"x\\u0000y"}',
        # Truncated and invalid JSON.
        '{"classification":"printed_equation","latex":"\\frac1"',
        "",
        "{}",
        "[",
    ],
)
def test_answers_that_must_still_be_rejected(content: str):
    with pytest.raises(EquationReadingRejected, match="invalid_provider_response"):
        _parse(content)


def test_length_limits_still_apply_before_and_after_repair():
    long_latex = BS + "frac" + "x" * 20
    with pytest.raises(EquationReadingRejected):
        _parse(_printed(long_latex), max_latex_chars=20)
    assert _parse(_printed(long_latex), max_latex_chars=len(long_latex))[1] == (
        long_latex
    )
    with pytest.raises(EquationReadingRejected):
        _parse(_printed(BS + "frac"), max_response_chars=20)


def test_oversized_response_is_rejected_before_any_parse(monkeypatch):
    from src.education.remediation import equation_reading_parser

    monkeypatch.setattr(
        equation_reading_parser.json,
        "loads",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("oversized response must not be parsed")
        ),
    )
    with pytest.raises(EquationReadingRejected):
        _parse("x" * 65, max_response_chars=64)


def test_clean_latex_option_rejects_padding_and_invisible_marks_after_repair():
    clean = {"require_clean_latex": True}
    assert _parse(_printed(r"\frac{1}{2}"), **clean)[1] == r"\frac{1}{2}"
    for body in (r" \frac{1}{2}", r"\frac{1}{2} "):
        with pytest.raises(EquationReadingRejected):
            _parse(_printed(body), **clean)
        # The printed parser has never required trimmed LaTeX.
        assert _parse(_printed(body))[1] == body


@pytest.mark.parametrize("latex", [r"\frac{1}{2}", r"x \approx \beta", r"\sum \nu"])
def test_printed_recognizer_accepts_single_backslash_latex(latex: str):
    from src.education.remediation.equation_recognizer import EquationRecognizer

    client = PrintedClient(success(_printed(latex)))
    result = EquationRecognizer(client).recognize(PAYLOAD)

    assert result.classification == "printed_equation"
    assert result.latex == latex
    assert len(client.calls) == 1


def test_printed_system_recognizer_accepts_single_backslash_latex():
    from src.education.remediation.equation_recognizer import EquationRecognizer

    latex = r"\begin{cases} x \approx 1 \\ y = \beta \end{cases}"
    escaped_rows = latex.replace("\\\\", "\\\\\\\\")
    client = PrintedClient(success(_printed(escaped_rows)))

    assert EquationRecognizer(client).recognize_system(PAYLOAD).latex == latex


def test_printed_recognizer_still_rejects_what_the_repair_cannot_save():
    from src.education.remediation.equation_recognizer import (
        EquationRecognitionRejected,
        EquationRecognizer,
    )

    for content in (
        '{"classification":"printed_equation","latex":"x","latex":"y"}',
        '{"classification":"printed_equation","latex":"\\frac1","extra":1}',
        '{"classification":"printed_equation","latex":"x\u200b \\approx"}',
    ):
        with pytest.raises(
            EquationRecognitionRejected, match="invalid_provider_response"
        ):
            EquationRecognizer(PrintedClient(success(content))).recognize(PAYLOAD)


@pytest.mark.parametrize("latex", [r"\frac{1}{2}", r"x \approx \beta", r"\sum \nu"])
def test_handwritten_recognizer_accepts_single_backslash_latex(latex: str):
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )

    raster = _raster()
    client = HandwrittenClient(_success(_handwritten(latex)))
    result = HandwrittenEquationRecognizer(client).recognize(raster, _evidence(raster))

    assert result.classification == "handwritten_equation"
    assert result.latex == latex


def test_handwritten_verifier_accepts_single_backslash_latex_and_agrees():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerifier,
    )

    raster = _raster()
    suitability = _evidence(raster)
    primary = HandwrittenEquationRecognizer(
        HandwrittenClient(_success(_handwritten(r"\frac{1}{2} \approx \beta")))
    ).recognize(raster, suitability)
    verifier_client = HandwrittenClient(
        _success(_handwritten(r"\frac{1}{2} \approx \beta"))
    )

    evidence = HandwrittenEquationVerifier(verifier_client).verify(
        raster, suitability, primary
    )

    assert evidence.passed is True
    assert evidence.primary_mathml_sha256 == evidence.verifier_mathml_sha256
    assert primary.latex == r"\frac{1}{2} \approx \beta"


def test_handwritten_verifier_single_backslash_disagreement_is_still_caught():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognizer,
    )
    from src.education.remediation.handwritten_equation_verifier import (
        HandwrittenEquationVerificationRejected,
        HandwrittenEquationVerifier,
    )

    raster = _raster()
    suitability = _evidence(raster)
    primary = HandwrittenEquationRecognizer(
        HandwrittenClient(_success(_handwritten(r"\frac{1}{2}")))
    ).recognize(raster, suitability)

    with pytest.raises(
        HandwrittenEquationVerificationRejected, match="semantic_disagreement"
    ):
        HandwrittenEquationVerifier(
            HandwrittenClient(_success(_handwritten(r"\frac{1}{3}")))
        ).verify(raster, suitability, primary)


def test_handwritten_paths_still_reject_what_the_repair_cannot_save():
    from src.education.remediation.handwritten_equation_recognizer import (
        HandwrittenEquationRecognitionRejected,
        HandwrittenEquationRecognizer,
    )

    raster = _raster()
    for content in (
        '{"classification":"handwritten_equation","latex":"x","latex":"y"}',
        '{"classification":"handwritten_equation","latex":"\\frac1","extra":1}',
        '{"classification":"handwritten_equation","latex":" \\frac1"}',
        '{"classification":"handwritten_equation","latex":"x\u200b \\approx"}',
        '{"classification":"handwritten_equation","latex":"x\\u202e \\approx"}',
        '{"classification":"not_handwritten_math","latex":"\\frac1"}',
    ):
        with pytest.raises(
            HandwrittenEquationRecognitionRejected, match="invalid_provider_response"
        ):
            HandwrittenEquationRecognizer(
                HandwrittenClient(_success(content))
            ).recognize(raster, _evidence(raster))
