import dataclasses
import hashlib
from types import SimpleNamespace

import pytest

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.remediation.equation_verifier import canonicalize_mathml
from src.education.remediation.math_fixer import MathFixer
from tests.printed_equation_fixtures import (
    PRIMARY_RESPONSE_SHA256,
    StubSecondReader,
    primary_reading,
)

METADATA = {
    "issue_type": IMAGE_EQUATION_ISSUE_TYPE,
    "page_number": 1,
    "image_xref": 7,
    "image_index": 0,
    "occurrence_ordinal": 0,
    "bbox": (1.0, 2.0, 3.0, 4.0),
    "occurrence_id": "occ-1",
}


class Source:
    def __init__(self):
        self.payload = SimpleNamespace(
            identity=SimpleNamespace(
                **{key: METADATA[key] for key in METADATA if key != "issue_type"}
            ),
            source_sha256="c" * 64,
            normalized_sha256="a" * 64,
        )

    def extract(self, document, metadata):
        return self.payload


class Recognizer:
    def recognize(self, payload):
        return SimpleNamespace(
            classification="printed_equation",
            latex="x^2 + 1 = 0",
            provider="gemini",
            model="vision-model",
            response_sha256=PRIMARY_RESPONSE_SHA256,
        )


@dataclasses.dataclass(frozen=True)
class Evidence:
    passed: bool
    source_sha256: str
    rendered_sha256: str
    mathml_sha256: str
    renderer_version: str = "renderer-v1"
    comparator_version: str = "compare-v1"
    font_sha256: str = "f" * 64
    threshold_version: str = "threshold-v1"
    ink_iou: float = 1.0
    pixel_similarity: float = 1.0
    required_ink_iou: float = 0.9
    required_pixel_similarity: float = 0.98


@dataclasses.dataclass(frozen=True)
class LeakyEvidence(Evidence):
    latex: str = "private latex"
    jpeg_bytes: bytes = b"private image"


class Verifier:
    """A passing visual check over the canonical MathML the real one renders."""

    def __init__(self, mathml, *, passed=True):
        try:
            mathml = canonicalize_mathml(mathml)
        except ValueError:
            pass
        self.evidence = Evidence(
            passed=passed,
            source_sha256="a" * 64,
            rendered_sha256="b" * 64,
            mathml_sha256=hashlib.sha256(mathml.encode()).hexdigest(),
        )

    def verify(self, image, latex):
        return self.evidence

    def canonicalize_mathml(self, mathml):
        return canonicalize_mathml(mathml)


class StructTree:
    def add_formula(self, **kwargs):
        raise AssertionError("staged image equation must not mutate before Task 7")


def fixer(verifier, second_reader=None):
    return MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=StructTree(),
        alt_text_client=SimpleNamespace(purpose="alt_text"),
        image_source=Source(),
        equation_recognizer=Recognizer(),
        equation_verifier=verifier,
        printed_equation_second_reader=second_reader or StubSecondReader(),
    )


def test_verified_image_equation_stages_typed_mandatory_review_request():
    candidate = fixer(Verifier("unused"))
    mathml = canonicalize_mathml(candidate._convert_to_mathml("x^2 + 1 = 0"))
    candidate.equation_verifier = Verifier(mathml)

    result = candidate._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.success is False
    assert result.error == "image_equation_association_pending"
    assert result.source_kind == "image_equation"
    assert result.fix_method == "ai_vision"
    assert result.confidence == 0.55
    assert result.needs_review is True
    assert result.provider_used == "gemini"
    assert result.model_used == "vision-model"
    assert result.pending_association is not None
    assert result.verification_evidence is not None
    assert result.pending_association.occurrence_id == "occ-1"
    assert result.pending_association.provider_used == "gemini"
    assert result.pending_association.model_used == "vision-model"
    assert result.pending_association.mathml_string == mathml
    assert result.consensus_evidence is not None
    assert result.pending_association.consensus_evidence == result.consensus_evidence
    assert result.consensus_evidence.mathml_sha256 == (
        result.verification_evidence.mathml_sha256
    )
    assert "<mtext" not in mathml
    assert result.verification_evidence.passed is True
    assert not hasattr(result.verification_evidence, "latex")
    assert not hasattr(result.verification_evidence, "jpeg_bytes")


def test_image_conversion_failure_never_uses_mtext_or_mutates(monkeypatch):
    candidate = fixer(Verifier("<math><mi>x</mi></math>"))
    monkeypatch.setattr(candidate, "_convert_to_mathml", lambda latex: "")

    result = candidate._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert not result.success
    assert result.error == "image_equation_conversion_failed"
    assert result.pending_association is None
    assert result.has_mathml is False


def test_verifier_rejection_never_stages_association():
    candidate = fixer(Verifier("<math><mi>x</mi></math>", passed=False))

    result = candidate._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert not result.success
    assert result.error == "equation_verification_failed"
    assert result.pending_association is None


def test_mathml_digest_mismatch_fails_closed():
    candidate = fixer(Verifier("<math><mi>different</mi></math>"))

    result = candidate._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert not result.success
    assert result.error == "equation_verification_mismatch"
    assert result.pending_association is None


def test_injected_verification_extra_fields_cannot_enter_pending_evidence():
    mathml = canonicalize_mathml(
        fixer(Verifier("unused"))._convert_to_mathml("x^2 + 1 = 0")
    )

    class LeakyVerifier(Verifier):
        def __init__(self):
            base = Verifier(mathml).evidence
            self.evidence = LeakyEvidence(**dataclasses.asdict(base))

    result = fixer(LeakyVerifier())._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.error == "image_equation_association_pending"
    evidence = dataclasses.asdict(result.verification_evidence)
    assert set(evidence) == {
        "passed",
        "source_sha256",
        "rendered_sha256",
        "mathml_sha256",
        "renderer_version",
        "comparator_version",
        "font_sha256",
        "threshold_version",
        "ink_iou",
        "pixel_similarity",
        "required_ink_iou",
        "required_pixel_similarity",
    }
    assert "private latex" not in repr(result.pending_association)
    assert "private image" not in repr(result.pending_association)


def test_printed_path_embeds_exactly_the_canonical_form_the_verifier_rendered():
    from io import BytesIO

    from PIL import Image, ImageDraw

    from src.education.remediation.equation_verifier import (
        EquationVerifier,
        canonicalize_mathml,
    )
    from src.education.visual_semantic_contract import MathMLExpressionV1

    canvas = Image.new("RGB", (240, 90), "white")
    ImageDraw.Draw(canvas).line((20, 45, 220, 45), fill="black", width=5)
    jpeg = BytesIO()
    canvas.save(jpeg, "JPEG", quality=95, subsampling=0)
    payload = jpeg.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    rendered_inputs: list[str] = []

    class BracedRecognizer:
        def recognize(self, payload):
            return primary_reading("x^{2} + 1 = 0")

    candidate = fixer(Verifier("unused"))
    assert "<mrow><mn>2</mn></mrow>" in candidate._convert_to_mathml("x^{2} + 1 = 0")
    candidate.equation_recognizer = BracedRecognizer()
    candidate.image_source.payload.jpeg_bytes = payload
    candidate.image_source.payload.normalized_sha256 = digest
    # The second reading writes the same equation without braces.
    candidate.printed_equation_second_reader = StubSecondReader("x^2 + 1 = 0")
    candidate.equation_verifier = EquationVerifier(
        renderer=lambda mathml: rendered_inputs.append(mathml) or payload
    )

    result = candidate._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.error == "image_equation_association_pending"
    staged = result.pending_association.mathml_string
    assert rendered_inputs == [staged]
    assert "<mrow><mn>2</mn></mrow>" not in staged
    assert canonicalize_mathml(staged) == staged
    sha = hashlib.sha256(staged.encode("utf-8")).hexdigest()
    assert result.verification_evidence.mathml_sha256 == sha
    MathMLExpressionV1(
        semantic_kind="mathml_expression_v1",
        mathml=staged,
        alt_text=result.aria_label,
        mathml_sha256=sha,
    )


@pytest.mark.parametrize(
    ("reason", "error"),
    [
        ("renderer_dimension_limit", "equation_too_large_for_verification"),
        ("renderer_failed", "equation_verification_failed"),
        ("render_dimension_limit", "equation_verification_failed"),
        ("render_byte_limit", "equation_verification_failed"),
        ("blank_render", "equation_verification_failed"),
        ("comparison_failed", "equation_verification_failed"),
        ("conversion_failed", "equation_verification_failed"),
        ("invalid_mathml", "equation_verification_failed"),
    ],
)
def test_only_the_renderer_size_bound_reports_an_equation_too_large(reason, error):
    from src.education.remediation.equation_verifier import EquationVerificationRejected

    class Rejecting:
        def verify(self, image, latex):
            raise EquationVerificationRejected(reason)

    result = fixer(Rejecting())._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.success is False
    assert result.error == error
    assert result.pending_association is None


def test_an_untyped_failure_with_the_same_text_keeps_the_generic_error():
    class Crashing:
        def verify(self, image, latex):
            raise RuntimeError("renderer_dimension_limit")

    result = fixer(Crashing())._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.error == "equation_verification_failed"
    assert result.pending_association is None
