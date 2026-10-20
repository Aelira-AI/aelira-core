"""Strict purpose-bound recognition for validated equation images."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Optional

from .equation_image_source import ValidatedEquationRaster
from .equation_reading_parser import EquationReadingRejected, parse_equation_reading


class EquationRecognitionRejected(ValueError):
    """Recognition was unavailable or did not satisfy the strict contract."""


@dataclass(frozen=True)
class EquationRecognition:
    classification: str
    latex: Optional[str]
    provider: Optional[str] = None
    model: Optional[str] = None
    # sha256 of the provider's answer exactly as received, before any parsing.
    response_sha256: Optional[str] = None


_PROMPT = """Classify this single bounded image. Return exactly one JSON object and no markdown or prose. The only accepted forms are {\"classification\":\"printed_equation\",\"latex\":\"...\"} or {\"classification\":\"not_equation\",\"latex\":null}. Use printed_equation only for one standalone printed mathematical equation. Preserve mathematical meaning in LaTeX."""

_SYSTEM_PROMPT = """Classify this single bounded image. Return exactly one JSON object and no markdown or prose. The only accepted forms are {\"classification\":\"printed_equation\",\"latex\":\"...\"} or {\"classification\":\"not_equation\",\"latex\":null}. Use printed_equation only when the complete image is one printed mathematical system whose lines belong together. Return one LaTeX representation preserving the full system, line order, alignment, braces, and mathematical meaning."""


class EquationRecognizer:
    """Recover bounded LaTeX through one explicitly alt-text-purpose client."""

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

    def recognize(self, image: ValidatedEquationRaster) -> EquationRecognition:
        return self._recognize(image, _PROMPT)

    def recognize_system(self, image: ValidatedEquationRaster) -> EquationRecognition:
        """Recover one semantic owner for a complete multi-line equation system."""

        return self._recognize(image, _SYSTEM_PROMPT)

    def _recognize(
        self, image: ValidatedEquationRaster, prompt: str
    ) -> EquationRecognition:
        client = self.alt_text_client
        if client is None:
            raise EquationRecognitionRejected("alt_text_client_unavailable")
        if getattr(client, "purpose", None) != "alt_text":
            raise EquationRecognitionRejected("purpose_mismatch")
        if image.mime_type != "image/jpeg" or not image.jpeg_bytes.startswith(
            b"\xff\xd8"
        ):
            raise EquationRecognitionRejected("invalid_image_payload")
        try:
            response = client.analyze_image_sync(
                image_data=image.jpeg_bytes,
                prompt=prompt,
                max_tokens=500,
            )
        except Exception:
            raise EquationRecognitionRejected("provider_failure") from None
        if not isinstance(response, dict) or response.get("success") is not True:
            raise EquationRecognitionRejected("provider_failure")
        content = response.get("content")
        if not isinstance(content, str):
            raise EquationRecognitionRejected("provider_failure")
        parsed = self._parse(content)
        # Identity comes from the provider's own answer first, then from what
        # the client declares. A client that routes to a provider chosen per
        # call, such as the LMS remediation client, has no fixed model to
        # declare, so its answer is the only place the model is named.
        return EquationRecognition(
            classification=parsed["classification"],
            latex=parsed["latex"],
            provider=self._bounded_identity(response.get("provider"))
            or self._bounded_identity(getattr(client, "provider", None)),
            model=self._bounded_identity(response.get("model"))
            or self._bounded_identity(getattr(client, "model", None)),
            response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def _parse(self, content: str) -> dict[str, Any]:
        try:
            classification, latex = parse_equation_reading(
                content,
                equation_classification="printed_equation",
                null_classifications=frozenset({"not_equation"}),
                max_latex_chars=self.max_latex_chars,
                max_response_chars=self.max_response_chars,
            )
        except EquationReadingRejected:
            raise EquationRecognitionRejected("invalid_provider_response") from None
        return {"classification": classification, "latex": latex}

    @staticmethod
    def _bounded_identity(value: Any) -> Optional[str]:
        if not isinstance(value, str) or not value or len(value) > 200:
            return None
        if value != value.strip():
            return None
        if not value.isprintable() or "\x00" in value:
            return None
        return value
