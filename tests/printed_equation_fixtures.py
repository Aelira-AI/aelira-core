"""Shared stubs for the blind second reading of printed equations."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any, Optional

from src.education.printed_equation_policy import PRINTED_VERIFIER_PROMPT_SHA256
from src.education.remediation.printed_equation_verifier import (
    PrintedEquationSecondReading,
)

PRIMARY_RESPONSE_SHA256 = "d" * 64


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class StubSecondReader:
    """A second reader that records every source it is asked to read.

    ``read`` takes only the source, exactly like the real reader, so a test
    using this stub cannot hand the second reading the first one.
    """

    def __init__(
        self,
        latex: Optional[str] = "x^2 + 1 = 0",
        *,
        classification: str = "printed_equation",
        error: Optional[BaseException] = None,
        provider: str = "gemini",
        model: str = "vision-model",
    ) -> None:
        self.latex = latex
        self.classification = classification
        self.error = error
        self.provider = provider
        self.model = model
        self.sources: list[Any] = []

    @property
    def calls(self) -> int:
        return len(self.sources)

    def read(self, source: Any) -> PrintedEquationSecondReading:
        self.sources.append(source)
        if self.error is not None:
            raise self.error
        latex = self.latex if self.classification == "printed_equation" else None
        return PrintedEquationSecondReading(
            classification=self.classification,
            latex=latex,
            source_sha256=str(source.normalized_sha256),
            provider=self.provider,
            model=self.model,
            response_sha256=sha256_text(f"second:{self.classification}:{latex}"),
            latex_sha256=sha256_text(latex) if latex is not None else None,
            prompt_sha256=PRINTED_VERIFIER_PROMPT_SHA256,
        )


def primary_reading(
    latex: str = "x^2 + 1 = 0",
    *,
    provider: Optional[str] = "gemini",
    model: Optional[str] = "vision-model",
    response_sha256: Optional[str] = PRIMARY_RESPONSE_SHA256,
) -> SimpleNamespace:
    """A first printed reading carrying the response digest the contract needs."""
    return SimpleNamespace(
        classification="printed_equation",
        latex=latex,
        provider=provider,
        model=model,
        response_sha256=response_sha256,
    )

