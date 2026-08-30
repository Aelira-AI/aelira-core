"""HMER routing through the existing image-equation association seam."""

from __future__ import annotations

import hashlib
import io
import dataclasses
from pathlib import Path
from types import SimpleNamespace

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
    from tests.test_image_equation_math_fixer import Recognizer, Verifier

    class NeverHmer:
        def recognize(self, *_args):
            raise AssertionError("accepted printed equations must not invoke HMER")

        def verify(self, *_args):
            raise AssertionError("accepted printed equations must not invoke HMER")

    candidate = _fixer("legible-linear", Client("unused"))
    mathml = candidate._convert_to_mathml("x^2 + 1 = 0")
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
