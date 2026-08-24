"""Strict purpose-bound recognition for validated equation images."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from .equation_image_source import ValidatedEquationImage


class EquationRecognitionRejected(ValueError):
    """Recognition was unavailable or did not satisfy the strict contract."""


@dataclass(frozen=True)
class EquationRecognition:
    classification: str
    latex: Optional[str]
    provider: Optional[str] = None
    model: Optional[str] = None


_PROMPT = """Classify this single bounded image. Return exactly one JSON object and no markdown or prose. The only accepted forms are {\"classification\":\"printed_equation\",\"latex\":\"...\"} or {\"classification\":\"not_equation\",\"latex\":null}. Use printed_equation only for one standalone printed mathematical equation. Preserve mathematical meaning in LaTeX."""


class EquationRecognizer:
    """Recover bounded LaTeX through one explicitly alt-text-purpose client."""

    def __init__(self, alt_text_client: Any, *, max_latex_chars: int = 4096) -> None:
        self.alt_text_client = alt_text_client
        self.max_latex_chars = max_latex_chars

    def recognize(self, image: ValidatedEquationImage) -> EquationRecognition:
        client = self.alt_text_client
        if client is None:
            raise EquationRecognitionRejected("alt_text_client_unavailable")
        if getattr(client, "purpose", None) != "alt_text":
            raise EquationRecognitionRejected("purpose_mismatch")
        if image.mime_type != "image/jpeg" or not image.jpeg_bytes.startswith(b"\xff\xd8"):
            raise EquationRecognitionRejected("invalid_image_payload")
        try:
            response = client.analyze_image_sync(
                image_data=image.jpeg_bytes,
                prompt=_PROMPT,
                max_tokens=500,
            )
        except Exception as exc:
            raise EquationRecognitionRejected("provider_failure") from exc
        if not isinstance(response, dict) or response.get("success") is not True:
            raise EquationRecognitionRejected("provider_failure")
        content = response.get("content")
        if not isinstance(content, str):
            raise EquationRecognitionRejected("provider_failure")
        parsed = self._parse(content)
        return EquationRecognition(
            classification=parsed["classification"],
            latex=parsed["latex"],
            provider=self._bounded_identity(getattr(client, "provider", None)),
            model=self._bounded_identity(getattr(client, "model", None)),
        )

    def _parse(self, content: str) -> dict[str, Any]:
        if not content or content.strip() != content:
            raise EquationRecognitionRejected("invalid_provider_response")

        def reject_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise EquationRecognitionRejected("invalid_provider_response")
                result[key] = value
            return result

        try:
            parsed = json.loads(content, object_pairs_hook=reject_duplicates)
        except EquationRecognitionRejected:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise EquationRecognitionRejected("invalid_provider_response") from exc
        if not isinstance(parsed, dict) or set(parsed) != {"classification", "latex"}:
            raise EquationRecognitionRejected("invalid_provider_response")
        classification = parsed["classification"]
        latex = parsed["latex"]
        if classification == "not_equation":
            if latex is not None:
                raise EquationRecognitionRejected("invalid_provider_response")
            return parsed
        if classification != "printed_equation" or not isinstance(latex, str):
            raise EquationRecognitionRejected("invalid_provider_response")
        if not latex or len(latex) > self.max_latex_chars:
            raise EquationRecognitionRejected("invalid_provider_response")
        if any(ord(character) < 32 or ord(character) == 127 for character in latex):
            raise EquationRecognitionRejected("invalid_provider_response")
        return parsed

    @staticmethod
    def _bounded_identity(value: Any) -> Optional[str]:
        if not isinstance(value, str) or not value or len(value) > 200:
            return None
        if not value.isprintable() or "\x00" in value:
            return None
        return value
