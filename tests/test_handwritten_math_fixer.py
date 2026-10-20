"""HMER routing through the existing image-equation association seam."""

from __future__ import annotations

import hashlib
import io
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.remediation.equation_verifier import canonicalize_mathml
from src.education.remediation.math_fixer import MathFixer

ROOT = Path(__file__).resolve().parents[1]
METADATA = {
    "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
    "page_number": 1,
    "image_xref": 7,
    "image_index": 0,
    "occurrence_ordinal": 0,
    "bbox": (1.0, 2.0, 301.0, 102.0),
    "occurrence_id": "imgocc-v1-test",
}


def _jpeg(fixture_id: str) -> bytes:
    path = (
        ROOT
        / "tests"
        / "fixtures"
        / "handwritten_math"
        / "images"
        / f"{fixture_id}.png"
    )
    with Image.open(path) as image:
        output = io.BytesIO()
        image.convert("RGB").save(
            output,
            format="JPEG",
            quality=95,
            subsampling=0,
            optimize=False,
            progressive=False,
        )
    return output.getvalue()


class Source:
    def __init__(self, fixture_id: str) -> None:
        payload = _jpeg(fixture_id)
        self.payload = SimpleNamespace(
            jpeg_bytes=payload,
            mime_type="image/jpeg",
            source_sha256="c" * 64,
            normalized_sha256=hashlib.sha256(payload).hexdigest(),
            width=320,
            height=120,
            identity=SimpleNamespace(
                **{key: value for key, value in METADATA.items() if key != "issue_type"}
            ),
        )

    def extract(self, _document, _metadata):
        return self.payload


class PrintedDecline:
    def recognize(self, _payload):
        return SimpleNamespace(classification="not_equation", latex=None)


class Client:
    purpose = "alt_text"
    provider = "fixture-provider"
    model = "fixture-hmer-v1"

    def __init__(self, *latex: str) -> None:
        self.responses = list(latex)
        self.calls: list[dict[str, object]] = []

    def analyze_image_sync(self, **kwargs: object):
        self.calls.append(kwargs)
        latex = self.responses.pop(0)
        return {
            "success": True,
            "content": (
                '{"classification":"handwritten_equation","latex":"' + latex + '"}'
            ),
        }


def _fixer(fixture_id: str, client: Client) -> MathFixer:
    return MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=SimpleNamespace(),
        alt_text_client=client,
        image_source=Source(fixture_id),
        equation_recognizer=PrintedDecline(),
        equation_verifier=SimpleNamespace(canonicalize_mathml=canonicalize_mathml),
    )


def test_eligible_printed_decline_stages_hmer_with_mandatory_review():
    client = Client("x^2+y=10", "x^2 + y = 10")
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.success is False
    assert result.error == "image_equation_association_pending"
    assert result.source_kind == "image_equation"
    assert result.needs_review is True
    assert result.confidence == 0.55
    assert result.pending_association is not None
    assert (
        result.pending_association.verification_evidence is result.verification_evidence
    )
    assert result.verification_evidence.passed is True
    assert result.verification_evidence.agreement_count == 2
    assert len(client.calls) == 2


def test_noneligible_candidate_never_reaches_hmer_clients():
    client = Client("x", "x")
    result = _fixer("low-contrast", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "handwritten_math_not_eligible"
    assert result.pending_association is None
    assert client.calls == []


def test_hmer_disagreement_stays_manual_without_association():
    client = Client("x^2+y=10", "x^2+y=11")
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "handwritten_equation_verification_failed"
    assert result.pending_association is None
    assert result.verification_evidence is None
    assert len(client.calls) == 2


def test_printed_success_never_invokes_hmer():
    from src.education.printed_equation_policy import PRINTED_VERIFIER_PROMPT_SHA256
    from tests.test_image_equation_math_fixer import Recognizer, Verifier

    class NeverHmer:
        def recognize(self, *_args):
            raise AssertionError("accepted printed equations must not invoke HMER")

        def verify(self, *_args):
            raise AssertionError("accepted printed equations must not invoke HMER")

    # The only provider call is the printed second reading, answered as printed.
    client = RawClient('{"classification":"printed_equation","latex":"x^2 + 1 = 0"}')
    candidate = _fixer("legible-linear", client)
    mathml = canonicalize_mathml(candidate._convert_to_mathml("x^2 + 1 = 0"))
    candidate.equation_recognizer = Recognizer()
    printed_verifier = Verifier(mathml)
    printed_verifier.evidence = dataclasses.replace(
        printed_verifier.evidence,
        source_sha256=candidate.image_source.payload.normalized_sha256,
    )
    candidate.equation_verifier = printed_verifier
    candidate.handwritten_recognizer = NeverHmer()
    candidate.handwritten_verifier = NeverHmer()

    result = candidate._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.error == "image_equation_association_pending"
    assert not hasattr(result.verification_evidence, "agreement_count")
    # HMER is never called, and the second reader is called exactly once.
    assert len(client.calls) == 1
    assert (
        hashlib.sha256(str(client.calls[0]["prompt"]).encode()).hexdigest()
        == PRINTED_VERIFIER_PROMPT_SHA256
    )
    assert result.consensus_evidence is not None
    assert result.consensus_evidence.agreement_count == 2


class RawClient(Client):
    """Answers each call with the next JSON object exactly as written."""

    def analyze_image_sync(self, **kwargs: object):
        self.calls.append(kwargs)
        return {"success": True, "content": self.responses.pop(0)}


UNSUPPORTED = '{"classification":"unsupported_notation","latex":null}'
NOT_HANDWRITTEN = '{"classification":"not_handwritten_math","latex":null}'


@pytest.mark.parametrize(
    ("responses", "error", "recognised"),
    [
        ((UNSUPPORTED,), "handwritten_notation_unsupported", True),
        ((NOT_HANDWRITTEN,), "not_handwritten_equation", False),
    ],
)
def test_declined_handwriting_records_whether_an_equation_was_recognised(
    responses, error, recognised
):
    """Unsupported notation is an equation the reader could not take; a decline
    that the image is not handwritten math is not."""
    result = _fixer("legible-linear", RawClient(*responses))._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == error
    assert result.pending_association is None
    assert result.equation_recognized is recognised


def test_recognised_handwriting_without_latex_counts_as_a_recognised_equation():
    """The classification itself is what the specialist recognised."""
    fixer = _fixer("legible-linear", Client("unused"))
    fixer.handwritten_recognizer = SimpleNamespace(
        recognize=lambda *_args: SimpleNamespace(
            classification="handwritten_equation", latex=""
        )
    )

    result = fixer._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.error == "not_handwritten_equation"
    assert result.equation_recognized is True


def test_handwriting_that_fails_verification_still_counts_as_recognised():
    client = Client("x^2+y=10", "x^2+y=11")
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "handwritten_equation_verification_failed"
    assert result.pending_association is None
    assert result.equation_recognized is True


def test_verified_handwriting_counts_as_recognised_and_stays_pending():
    client = Client("x^2+y=10", "x^2 + y = 10")
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "image_equation_association_pending"
    assert result.pending_association is not None
    assert result.equation_recognized is True


def test_handwriting_refused_before_recognition_is_not_a_recognised_equation():
    ineligible = _fixer("low-contrast", Client("x", "x"))._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )
    assert ineligible.error == "handwritten_math_not_eligible"
    assert ineligible.equation_recognized is False

    class Failing:
        def recognize(self, *_args):
            raise RuntimeError("provider down")

    broken = _fixer("legible-linear", Client("unused"))
    broken.handwritten_recognizer = Failing()
    rejected = broken._fix_math_issue(SimpleNamespace(metadata=METADATA))
    assert rejected.error == "handwritten_equation_recognition_rejected"
    assert rejected.equation_recognized is False


def test_braced_and_unbraced_readings_agree_and_stage_the_canonical_mathml():
    from src.education.remediation.equation_verifier import canonicalize_mathml
    from src.education.visual_semantic_contract import MathMLExpressionV1

    client = Client("x^{2}+y=10", "x^2 + y = 10")
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "image_equation_association_pending"
    assert result.verification_evidence.agreement_count == 2
    mathml = result.pending_association.mathml_string
    assert "<mrow><mn>2</mn></mrow>" not in mathml
    assert canonicalize_mathml(mathml) == mathml
    digest = hashlib.sha256(mathml.encode("utf-8")).hexdigest()
    assert digest == result.verification_evidence.mathml_sha256
    # The staged string is also exactly what the stored contract will accept.
    MathMLExpressionV1(
        semantic_kind="mathml_expression_v1",
        mathml=mathml,
        alt_text=result.aria_label,
        mathml_sha256=digest,
    )


def test_a_real_difference_between_readings_still_stays_manual():
    client = Client("x^{2}+y=10", "x^{3}+y=10")
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "handwritten_equation_verification_failed"
    assert result.pending_association is None


# ---------------------------------------------------------------------------
# The alternative text is built by regular expressions, so a handwritten
# reading the label cannot translate is refused, exactly as for printed ones.
# ---------------------------------------------------------------------------


def _json_latex(latex: str) -> str:
    """Escape LaTeX so it can sit inside the fake provider's JSON string."""
    return latex.replace("\\", "\\\\")


AMBIGUOUS_HANDWRITTEN = [
    r"\frac12 + y = 4",
    r"\sqrt x + y = 4",
    r"\binom nk = 10",
    r"x^10 + y = 4",
    r"a_ij + y = 4",
    r"x^2y + y = 4",
    "x",
]


@pytest.mark.parametrize("latex", AMBIGUOUS_HANDWRITTEN)
def test_an_ambiguous_agreed_handwritten_reading_is_refused_before_any_association(
    latex, monkeypatch
):
    from src.education.remediation import math_fixer

    def never(*_args, **_kwargs):
        raise AssertionError("an ambiguous reading must not reach association")

    monkeypatch.setattr(
        math_fixer.MathFixer,
        "_pending_visual_equation_association",
        staticmethod(never),
    )
    client = Client(_json_latex(latex), _json_latex(latex))
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "image_equation_alt_text_ambiguous"
    assert result.success is False
    assert result.pending_association is None
    assert result.verification_evidence is None
    assert result.aria_label == ""
    # The equation was recognised, so a generic description must not stand in.
    assert result.equation_recognized is True
    # Both readings were taken: the refusal comes after agreement.
    assert len(client.calls) == 2


def test_an_ambiguous_first_reading_is_refused_even_when_the_second_is_clean():
    client = Client(_json_latex(r"y = \frac12"), _json_latex(r"y = \frac{1}{2}"))
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "image_equation_alt_text_ambiguous"
    assert result.pending_association is None


def test_an_ambiguous_second_reading_is_refused_even_when_the_first_is_clean():
    # Same equation written two ways; only the verifier's form is untranslatable.
    client = Client(_json_latex(r"y = \frac{1}{2}"), _json_latex(r"y = \frac12"))
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "image_equation_alt_text_ambiguous"
    assert result.pending_association is None
    assert result.verification_evidence is None
    assert result.equation_recognized is True
    assert len(client.calls) == 2


def test_clear_handwritten_readings_still_stage_with_spoken_alt_text():
    client = Client(_json_latex(r"y = \frac{1}{2}"), _json_latex(r"y=\frac{1}{2}"))
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "image_equation_association_pending"
    assert result.aria_label == "y = 1 over 2"
    assert result.pending_association is not None
    assert result.pending_association.alt_text == "y = 1 over 2"
    assert result.needs_review is True


def test_a_disagreement_is_still_reported_as_a_failed_verification():
    """An ambiguous first reading does not hide a real disagreement."""
    client = Client(_json_latex(r"\frac12 + y = 4"), _json_latex(r"\frac12 + y = 5"))
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.error == "handwritten_equation_verification_failed"
    assert result.pending_association is None


def test_a_handwritten_row_break_that_lost_a_backslash_is_never_staged():
    """Two backslashes in a JSON answer are one after parsing, so the reading changed."""
    reading = "a = b " + chr(92) * 2 + " c = d"
    client = Client(reading, reading)
    result = _fixer("legible-linear", client)._fix_math_issue(
        SimpleNamespace(metadata=METADATA)
    )

    assert result.pending_association is None
    assert result.success is False
    assert result.error in {
        "image_equation_alt_text_ambiguous",
        "handwritten_equation_verification_failed",
    }
