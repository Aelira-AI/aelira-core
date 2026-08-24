import dataclasses
import hashlib
from types import SimpleNamespace

from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
from src.education.remediation.math_fixer import MathFixer


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
            identity=SimpleNamespace(**{key: METADATA[key] for key in METADATA if key != "issue_type"}),
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


class Verifier:
    def __init__(self, mathml, *, passed=True):
        self.evidence = Evidence(
            passed=passed,
            source_sha256="a" * 64,
            rendered_sha256="b" * 64,
            mathml_sha256=hashlib.sha256(mathml.encode()).hexdigest(),
        )

    def verify(self, image, latex):
        return self.evidence


class StructTree:
    def add_formula(self, **kwargs):
        raise AssertionError("staged image equation must not mutate before Task 7")


def fixer(verifier):
    return MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=StructTree(),
        alt_text_client=SimpleNamespace(purpose="alt_text"),
        image_source=Source(),
        equation_recognizer=Recognizer(),
        equation_verifier=verifier,
    )


def test_verified_image_equation_stages_typed_mandatory_review_request():
    candidate = fixer(Verifier("unused"))
    mathml = candidate._convert_to_mathml("x^2 + 1 = 0")
    candidate.equation_verifier = Verifier(mathml)

    result = candidate._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.success is False
    assert result.error == "image_equation_association_pending"
    assert result.source_kind == "image_equation"
    assert result.fix_method == "ai_vision"
    assert result.confidence == 0.55
    assert result.needs_review is True
    assert result.model_used == "vision-model"
    assert result.pending_association["occurrence_id"] == "occ-1"
    assert result.pending_association["mathml_string"] == mathml
    assert "<mtext" not in mathml
    assert result.verification_evidence["passed"] is True
    assert "latex" not in result.verification_evidence
    assert "jpeg_bytes" not in result.verification_evidence


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
