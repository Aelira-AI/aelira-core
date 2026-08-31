"""Strict purpose-bound recognition for eligible handwritten equations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from src.education.handwritten_math_suitability import (
    HandwrittenMathSuitabilityEvidence,
    SuitabilityInputRejected,
    ensure_hmer_eligible,
)

from .equation_image_source import ValidatedEquationRaster

HandwrittenEquationClassification = Literal[
    "handwritten_equation", "not_handwritten_math", "unsupported_notation"
]

_PRIMARY_PROMPT = """Independently transcribe this single bounded handwritten mathematical expression. Return exactly one JSON object and no markdown or prose. The only accepted forms are {\"classification\":\"handwritten_equation\",\"latex\":\"...\"}, {\"classification\":\"not_handwritten_math\",\"latex\":null}, or {\"classification\":\"unsupported_notation\",\"latex\":null}. Use handwritten_equation only when the complete expression can be represented without guessing. Preserve mathematical meaning in LaTeX."""


class HandwrittenEquationRecognitionRejected(ValueError):
    """HMER was unavailable or failed its exact input/output contract."""


@dataclass(frozen=True)
class HandwrittenEquationRecognition:
    classification: HandwrittenEquationClassification
    latex: str | None
    source_sha256: str
    suitability_evidence_sha256: str
    provider: str
    model: str
    response_sha256: str
    latex_sha256: str | None

    @property
    def math_candidate_sha256(self) -> str:
        """Stable identity for the primary semantic candidate."""
        return self.latex_sha256 or ("0" * 64)


def _bounded_identity(value: Any) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or value != value.strip()
        or not value.isprintable()
        or "\x00" in value
    ):
        return None
    return value


def _validate_source(source: ValidatedEquationRaster) -> bytes:
    payload = getattr(source, "jpeg_bytes", None)
    if (
        not isinstance(payload, bytes)
        or not payload.startswith(b"\xff\xd8")
        or getattr(source, "mime_type", None) != "image/jpeg"
        or hashlib.sha256(payload).hexdigest()
        != getattr(source, "normalized_sha256", None)
    ):
        raise HandwrittenEquationRecognitionRejected("invalid_image_payload")
    return payload


def _parse_hmer_content(
    content: Any, *, max_latex_chars: int, max_response_chars: int
) -> tuple[HandwrittenEquationClassification, str | None]:
    if (
        not isinstance(content, str)
        or not content
        or len(content) > max_response_chars
        or content.strip() != content
    ):
        raise HandwrittenEquationRecognitionRejected("invalid_provider_response")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HandwrittenEquationRecognitionRejected(
                    "invalid_provider_response"
                )
            result[key] = value
        return result

    try:
        parsed = json.loads(content, object_pairs_hook=reject_duplicates)
    except HandwrittenEquationRecognitionRejected:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HandwrittenEquationRecognitionRejected(
            "invalid_provider_response"
        ) from exc
    if not isinstance(parsed, dict) or set(parsed) != {"classification", "latex"}:
        raise HandwrittenEquationRecognitionRejected("invalid_provider_response")
    classification = parsed["classification"]
    latex = parsed["latex"]
    if classification in {"not_handwritten_math", "unsupported_notation"}:
        if latex is not None:
            raise HandwrittenEquationRecognitionRejected("invalid_provider_response")
        return classification, None
    if classification != "handwritten_equation" or not isinstance(latex, str):
        raise HandwrittenEquationRecognitionRejected("invalid_provider_response")
    if (
        not latex
        or latex != latex.strip()
        or len(latex) > max_latex_chars
        or any(not character.isprintable() for character in latex)
        or re.search(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069]", latex)
    ):
        raise HandwrittenEquationRecognitionRejected("invalid_provider_response")
    return classification, latex


def _call_hmer_client(
    client: Any,
    source: ValidatedEquationRaster,
    *,
    prompt: str,
    max_latex_chars: int,
    max_response_chars: int,
) -> tuple[HandwrittenEquationClassification, str | None, str, str, str]:
    if client is None:
        raise HandwrittenEquationRecognitionRejected("alt_text_client_unavailable")
    if getattr(client, "purpose", None) != "alt_text":
        raise HandwrittenEquationRecognitionRejected("purpose_mismatch")
    payload = _validate_source(source)
    try:
        response = client.analyze_image_sync(
            image_data=payload,
            prompt=prompt,
            max_tokens=500,
        )
    except Exception:
        raise HandwrittenEquationRecognitionRejected("provider_failure") from None
    if not isinstance(response, dict) or response.get("success") is not True:
        raise HandwrittenEquationRecognitionRejected("provider_failure")
    content = response.get("content")
    classification, latex = _parse_hmer_content(
        content,
        max_latex_chars=max_latex_chars,
        max_response_chars=max_response_chars,
    )
    provider = _bounded_identity(response.get("provider")) or _bounded_identity(
        getattr(client, "provider", None)
    )
    model = _bounded_identity(response.get("model")) or _bounded_identity(
        getattr(client, "model", None)
    )
    if provider is None or model is None:
        raise HandwrittenEquationRecognitionRejected("provider_identity_unavailable")
    assert isinstance(content, str)
    return (
        classification,
        latex,
        provider,
        model,
        hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


class HandwrittenEquationRecognizer:
    """Recover one bounded handwritten LaTeX candidate after exact admission."""

    def __init__(
        self,
        alt_text_client: Any,
        *,
        max_latex_chars: int = 4096,
        max_response_chars: int = 8192,
    ) -> None:
        self.alt_text_client = alt_text_client
        self.max_latex_chars = max_latex_chars
        self.max_response_chars = max_response_chars

    def recognize(
        self,
        source: ValidatedEquationRaster,
        suitability: HandwrittenMathSuitabilityEvidence | dict[str, Any],
    ) -> HandwrittenEquationRecognition:
        payload = _validate_source(source)
        try:
            validated_suitability = HandwrittenMathSuitabilityEvidence.model_validate(
                suitability
            )
            ensure_hmer_eligible(payload, validated_suitability)
        except SuitabilityInputRejected as exc:
            raise HandwrittenEquationRecognitionRejected(str(exc)) from None
        except (TypeError, ValueError):
            raise HandwrittenEquationRecognitionRejected("evidence_invalid") from None
        classification, latex, provider, model, response_sha256 = _call_hmer_client(
            self.alt_text_client,
            source,
            prompt=_PRIMARY_PROMPT,
            max_latex_chars=self.max_latex_chars,
            max_response_chars=self.max_response_chars,
        )
        return HandwrittenEquationRecognition(
            classification=classification,
            latex=latex,
            source_sha256=str(source.normalized_sha256),
            suitability_evidence_sha256=validated_suitability.evidence_sha256,
            provider=provider,
            model=model,
            response_sha256=response_sha256,
            latex_sha256=(
                hashlib.sha256(latex.encode("utf-8")).hexdigest()
                if latex is not None
                else None
            ),
        )


__all__ = [
    "HandwrittenEquationClassification",
    "HandwrittenEquationRecognition",
    "HandwrittenEquationRecognitionRejected",
    "HandwrittenEquationRecognizer",
]
