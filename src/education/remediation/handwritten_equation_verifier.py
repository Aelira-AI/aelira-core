"""Context-isolated semantic agreement verification for handwritten math."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from latex2mathml.converter import convert as latex_to_mathml

from src.education.handwritten_math_suitability import (
    POLICY_SHA256,
    HandwrittenMathSuitabilityEvidence,
    SuitabilityInputRejected,
    ensure_hmer_eligible,
)
from src.education.handwritten_equation_policy import (
    HANDWRITTEN_REQUIRED_AGREEMENT_COUNT,
    HANDWRITTEN_VERIFIER_POLICY_SHA256,
    HANDWRITTEN_VERIFIER_POLICY_VERSION,
)

from .equation_image_source import ValidatedEquationRaster
from .equation_verifier import canonicalize_mathml
from .handwritten_equation_recognizer import (
    HandwrittenEquationRecognition,
    HandwrittenEquationRecognitionRejected,
    _call_hmer_client,
    _validate_source,
)

_VERIFIER_PROMPT = """Independently transcribe this single bounded handwritten mathematical expression. You are verifying from the pixels alone and have not been shown another answer. Return exactly one JSON object and no markdown or prose. The only accepted forms are {\"classification\":\"handwritten_equation\",\"latex\":\"...\"}, {\"classification\":\"not_handwritten_math\",\"latex\":null}, or {\"classification\":\"unsupported_notation\",\"latex\":null}. Use handwritten_equation only when the complete expression can be represented without guessing. Preserve mathematical meaning in LaTeX."""


class HandwrittenEquationVerificationRejected(ValueError):
    """Independent HMER verification did not establish exact agreement."""


@dataclass(frozen=True)
class HandwrittenEquationVerificationEvidence:
    passed: bool
    source_sha256: str
    suitability_evidence: HandwrittenMathSuitabilityEvidence
    suitability_evidence_sha256: str
    suitability_policy_sha256: str
    verifier_policy_version: str
    verifier_policy_sha256: str
    agreement_count: int
    required_agreement_count: int
    mathml_sha256: str
    primary_mathml_sha256: str
    verifier_mathml_sha256: str
    primary_response_sha256: str
    verifier_response_sha256: str
    primary_latex_sha256: str
    verifier_latex_sha256: str
    primary_provider: str
    primary_model: str
    verifier_provider: str
    verifier_model: str


class HandwrittenEquationVerifier:
    """Require a fresh transcription to match the primary canonical semantics."""

    def __init__(
        self,
        alt_text_client: Any,
        *,
        converter: Callable[[str], str] = latex_to_mathml,
        max_latex_chars: int = 4096,
        max_response_chars: int = 8192,
    ) -> None:
        self.alt_text_client = alt_text_client
        self.converter = converter
        self.max_latex_chars = max_latex_chars
        self.max_response_chars = max_response_chars

    def verify(
        self,
        source: ValidatedEquationRaster,
        suitability: HandwrittenMathSuitabilityEvidence | dict[str, Any],
        primary: HandwrittenEquationRecognition,
    ) -> HandwrittenEquationVerificationEvidence:
        payload = _validate_source(source)
        try:
            validated_suitability = HandwrittenMathSuitabilityEvidence.model_validate(
                suitability
            )
            ensure_hmer_eligible(payload, validated_suitability)
        except SuitabilityInputRejected as exc:
            raise HandwrittenEquationVerificationRejected(str(exc)) from None
        except (TypeError, ValueError):
            raise HandwrittenEquationVerificationRejected("evidence_invalid") from None
        if (
            not isinstance(primary, HandwrittenEquationRecognition)
            or primary.classification != "handwritten_equation"
            or primary.latex is None
            or primary.latex_sha256
            != hashlib.sha256(primary.latex.encode("utf-8")).hexdigest()
            or primary.source_sha256 != source.normalized_sha256
            or primary.suitability_evidence_sha256
            != validated_suitability.evidence_sha256
        ):
            raise HandwrittenEquationVerificationRejected("primary_evidence_mismatch")
        try:
            classification, latex, provider, model, response_sha256 = _call_hmer_client(
                self.alt_text_client,
                source,
                prompt=_VERIFIER_PROMPT,
                max_latex_chars=self.max_latex_chars,
                max_response_chars=self.max_response_chars,
            )
        except HandwrittenEquationRecognitionRejected as exc:
            raise HandwrittenEquationVerificationRejected(str(exc)) from None
        if classification != "handwritten_equation" or latex is None:
            raise HandwrittenEquationVerificationRejected("verifier_declined")
        try:
            primary_mathml = canonicalize_mathml(self.converter(primary.latex))
            verifier_mathml = canonicalize_mathml(self.converter(latex))
        except Exception:
            raise HandwrittenEquationVerificationRejected("conversion_failed") from None
        primary_mathml_sha256 = hashlib.sha256(
            primary_mathml.encode("utf-8")
        ).hexdigest()
        verifier_mathml_sha256 = hashlib.sha256(
            verifier_mathml.encode("utf-8")
        ).hexdigest()
        if primary_mathml != verifier_mathml:
            raise HandwrittenEquationVerificationRejected(
                "semantic_disagreement"
            ) from None
        return HandwrittenEquationVerificationEvidence(
            passed=True,
            source_sha256=str(source.normalized_sha256),
            suitability_evidence=validated_suitability,
            suitability_evidence_sha256=validated_suitability.evidence_sha256,
            suitability_policy_sha256=POLICY_SHA256,
            verifier_policy_version=HANDWRITTEN_VERIFIER_POLICY_VERSION,
            verifier_policy_sha256=HANDWRITTEN_VERIFIER_POLICY_SHA256,
            agreement_count=HANDWRITTEN_REQUIRED_AGREEMENT_COUNT,
            required_agreement_count=HANDWRITTEN_REQUIRED_AGREEMENT_COUNT,
            mathml_sha256=primary_mathml_sha256,
            primary_mathml_sha256=primary_mathml_sha256,
            verifier_mathml_sha256=verifier_mathml_sha256,
            primary_response_sha256=primary.response_sha256,
            verifier_response_sha256=response_sha256,
            primary_latex_sha256=str(primary.latex_sha256),
            verifier_latex_sha256=hashlib.sha256(latex.encode("utf-8")).hexdigest(),
            primary_provider=primary.provider,
            primary_model=primary.model,
            verifier_provider=provider,
            verifier_model=model,
        )


__all__ = [
    "HANDWRITTEN_VERIFIER_POLICY_SHA256",
    "HANDWRITTEN_VERIFIER_POLICY_VERSION",
    "HandwrittenEquationVerificationEvidence",
    "HandwrittenEquationVerificationRejected",
    "HandwrittenEquationVerifier",
]
