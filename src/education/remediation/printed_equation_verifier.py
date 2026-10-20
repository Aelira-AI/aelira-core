"""Context-isolated second reading and exact agreement for printed equations.

The first reading of a printed equation is checked only by a visual round trip,
and a reading that differs from the image by one symbol can still pass that
comparison. A second reader therefore transcribes the same pixels without ever
seeing the first answer, and the equation is staged only when the two canonical
MathML forms are equal.

``PrintedEquationSecondReader.read`` has no parameter that could carry the first
reading, and it sends its own pinned prompt. The prompt must differ from the
recognizer's: the provider cache keys on prompt plus image, so an identical
prompt would return the first answer from the cache and agree with itself.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from latex2mathml.converter import convert as latex_to_mathml

from src.education.math_speech import strip_math_delimiters
from src.education.printed_equation_policy import (
    PRINTED_REQUIRED_AGREEMENT_COUNT,
    PRINTED_VERIFIER_POLICY_SHA256,
    PRINTED_VERIFIER_POLICY_VERSION,
    PRINTED_VERIFIER_PROMPT_SHA256,
)

from .equation_image_source import ValidatedEquationRaster
from .equation_reading_parser import EquationReadingRejected, parse_equation_reading
from .equation_recognizer import _PROMPT as _PRIMARY_PROMPT
from .equation_recognizer import _SYSTEM_PROMPT as _PRIMARY_SYSTEM_PROMPT
from .equation_verifier import canonicalize_mathml

_VERIFIER_PROMPT = """Independently transcribe the printed mathematics in this single bounded image into LaTeX. You are reading the pixels alone and have not been shown any other answer. Copy every symbol, sign, exponent, subscript, fraction and grouping exactly as printed; do not simplify, solve or correct anything. Return exactly one JSON object and no markdown or prose. The only accepted forms are {\"classification\":\"printed_equation\",\"latex\":\"...\"} or {\"classification\":\"not_equation\",\"latex\":null}. Use printed_equation only for one standalone printed mathematical equation. Write each LaTeX backslash as two backslashes so the JSON stays valid."""

VERIFIER_DECLINED = "verifier_declined"
SEMANTIC_DISAGREEMENT = "semantic_disagreement"

_SHA256 = re.compile(r"[0-9a-f]{64}")


class PrintedEquationVerificationRejected(ValueError):
    """The second reading did not establish exact agreement with the first.

    The message is one of a closed set of reason codes. ``verifier_declined``
    means the second reading did not see a printed equation, and
    ``semantic_disagreement`` means the two readings were not equal. Every other
    code means the second reading could not be completed or trusted.
    """


@dataclass(frozen=True)
class PrintedEquationSecondReading:
    classification: str
    latex: Optional[str]
    source_sha256: str
    provider: str
    model: str
    response_sha256: str
    latex_sha256: Optional[str]
    prompt_sha256: str


@dataclass(frozen=True)
class PrintedEquationConsensusEvidence:
    passed: bool
    source_sha256: str
    mathml_sha256: str
    policy_version: str
    policy_sha256: str
    agreement_count: int
    required_agreement_count: int
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
    verifier_prompt_sha256: str


def _bounded_identity(value: Any) -> Optional[str]:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 200
        or value != value.strip()
        or not value.isprintable()
    ):
        return None
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _validate_source(source: Any) -> bytes:
    payload = getattr(source, "jpeg_bytes", None)
    if (
        not isinstance(payload, bytes)
        or not payload.startswith(b"\xff\xd8")
        or getattr(source, "mime_type", None) != "image/jpeg"
        or hashlib.sha256(payload).hexdigest()
        != getattr(source, "normalized_sha256", None)
    ):
        raise PrintedEquationVerificationRejected("invalid_image_payload")
    return payload


class PrintedEquationSecondReader:
    """Read a validated equation image again, without any first reading."""

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

    def read(self, source: ValidatedEquationRaster) -> PrintedEquationSecondReading:
        client = self.alt_text_client
        if client is None:
            raise PrintedEquationVerificationRejected("alt_text_client_unavailable")
        if getattr(client, "purpose", None) != "alt_text":
            raise PrintedEquationVerificationRejected("purpose_mismatch")
        payload = _validate_source(source)
        prompt_sha256 = hashlib.sha256(_VERIFIER_PROMPT.encode("utf-8")).hexdigest()
        if prompt_sha256 != PRINTED_VERIFIER_PROMPT_SHA256:
            raise PrintedEquationVerificationRejected("verifier_prompt_unpinned")
        if _VERIFIER_PROMPT in (_PRIMARY_PROMPT, _PRIMARY_SYSTEM_PROMPT):
            raise PrintedEquationVerificationRejected("verifier_prompt_not_distinct")
        try:
            response = client.analyze_image_sync(
                image_data=payload,
                prompt=_VERIFIER_PROMPT,
                max_tokens=500,
            )
        except Exception:
            raise PrintedEquationVerificationRejected("provider_failure") from None
        if not isinstance(response, dict) or response.get("success") is not True:
            raise PrintedEquationVerificationRejected("provider_failure")
        content = response.get("content")
        try:
            classification, latex = parse_equation_reading(
                content,
                equation_classification="printed_equation",
                null_classifications=frozenset({"not_equation"}),
                max_latex_chars=self.max_latex_chars,
                max_response_chars=self.max_response_chars,
            )
        except EquationReadingRejected:
            raise PrintedEquationVerificationRejected(
                "invalid_provider_response"
            ) from None
        provider = _bounded_identity(response.get("provider")) or _bounded_identity(
            getattr(client, "provider", None)
        )
        model = _bounded_identity(response.get("model")) or _bounded_identity(
            getattr(client, "model", None)
        )
        if provider is None or model is None:
            raise PrintedEquationVerificationRejected("provider_identity_unavailable")
        assert isinstance(content, str)
        return PrintedEquationSecondReading(
            classification=classification,
            latex=latex,
            source_sha256=str(source.normalized_sha256),
            provider=provider,
            model=model,
            response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            latex_sha256=(
                hashlib.sha256(latex.encode("utf-8")).hexdigest()
                if latex is not None
                else None
            ),
            prompt_sha256=prompt_sha256,
        )


def primary_reading_is_complete(primary: Any) -> bool:
    """Whether a first reading carries everything the agreement evidence needs.

    The evidence names the first reading's provider, model and response digest.
    A reading that lacks one can never be agreed with, so this is checked before
    the second reading is paid for.
    """
    latex = getattr(primary, "latex", None)
    return (
        getattr(primary, "classification", None) == "printed_equation"
        and isinstance(latex, str)
        and bool(latex)
        and _bounded_identity(getattr(primary, "provider", None)) is not None
        and _bounded_identity(getattr(primary, "model", None)) is not None
        and _is_sha256(getattr(primary, "response_sha256", None))
    )


def _canonical_mathml(latex: str, converter: Callable[[str], str]) -> str:
    return canonicalize_mathml(converter(strip_math_delimiters(latex)))


def printed_equation_agreement(
    primary: Any,
    second: PrintedEquationSecondReading,
    *,
    converter: Callable[[str], str] = latex_to_mathml,
) -> PrintedEquationConsensusEvidence:
    """Return evidence only when both readings have equal canonical MathML.

    This is a pure comparison of two readings that already exist. It never
    calls a provider.
    """
    if not isinstance(second, PrintedEquationSecondReading):
        raise PrintedEquationVerificationRejected("second_reading_invalid")
    if second.classification != "printed_equation":
        raise PrintedEquationVerificationRejected(VERIFIER_DECLINED)
    if not primary_reading_is_complete(primary):
        raise PrintedEquationVerificationRejected("primary_evidence_incomplete")
    primary_latex = primary.latex
    primary_provider = _bounded_identity(primary.provider)
    primary_model = _bounded_identity(primary.model)
    primary_response_sha256 = primary.response_sha256
    if (
        not isinstance(second.latex, str)
        or not second.latex
        or second.latex_sha256
        != hashlib.sha256(second.latex.encode("utf-8")).hexdigest()
        or not _is_sha256(second.source_sha256)
        or not _is_sha256(second.response_sha256)
        or _bounded_identity(second.provider) is None
        or _bounded_identity(second.model) is None
    ):
        raise PrintedEquationVerificationRejected("second_reading_invalid")
    if second.prompt_sha256 != PRINTED_VERIFIER_PROMPT_SHA256:
        raise PrintedEquationVerificationRejected("verifier_prompt_unpinned")
    try:
        primary_mathml = _canonical_mathml(primary_latex, converter)
    except Exception:
        raise PrintedEquationVerificationRejected("primary_conversion_failed") from None
    try:
        second_mathml = _canonical_mathml(second.latex, converter)
    except Exception:
        # A reading that cannot be turned into passive MathML cannot equal one
        # that can, so the two readings did not agree.
        raise PrintedEquationVerificationRejected(SEMANTIC_DISAGREEMENT) from None
    if primary_mathml != second_mathml:
        raise PrintedEquationVerificationRejected(SEMANTIC_DISAGREEMENT)
    mathml_sha256 = hashlib.sha256(primary_mathml.encode("utf-8")).hexdigest()
    return PrintedEquationConsensusEvidence(
        passed=True,
        source_sha256=second.source_sha256,
        mathml_sha256=mathml_sha256,
        policy_version=PRINTED_VERIFIER_POLICY_VERSION,
        policy_sha256=PRINTED_VERIFIER_POLICY_SHA256,
        agreement_count=PRINTED_REQUIRED_AGREEMENT_COUNT,
        required_agreement_count=PRINTED_REQUIRED_AGREEMENT_COUNT,
        primary_mathml_sha256=mathml_sha256,
        verifier_mathml_sha256=hashlib.sha256(
            second_mathml.encode("utf-8")
        ).hexdigest(),
        primary_response_sha256=str(primary_response_sha256),
        verifier_response_sha256=second.response_sha256,
        primary_latex_sha256=hashlib.sha256(primary_latex.encode("utf-8")).hexdigest(),
        verifier_latex_sha256=second.latex_sha256,
        primary_provider=primary_provider,
        primary_model=primary_model,
        verifier_provider=second.provider,
        verifier_model=second.model,
        verifier_prompt_sha256=second.prompt_sha256,
    )


__all__ = [
    "PRINTED_VERIFIER_POLICY_SHA256",
    "PRINTED_VERIFIER_POLICY_VERSION",
    "PRINTED_VERIFIER_PROMPT_SHA256",
    "PrintedEquationConsensusEvidence",
    "PrintedEquationSecondReader",
    "PrintedEquationSecondReading",
    "PrintedEquationVerificationRejected",
    "SEMANTIC_DISAGREEMENT",
    "VERIFIER_DECLINED",
    "primary_reading_is_complete",
    "printed_equation_agreement",
    "strip_math_delimiters",
]
