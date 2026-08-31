"""Purpose-bound recognition for bounded chemical-formula PDF visuals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.education.canonical_json import canonical_sha256
from src.education.chemical_formula import (
    ChemicalFormulaRejected,
    VerifiedChemicalNotationV1,
    verify_chemical_notation,
)
from src.education.visual_semantic_contract import (
    ChemicalFormulaPdfContract,
    ChemicalFormulaRecognitionEvidenceV1,
    ChemicalFormulaSemanticV1,
    EmbeddedImageOccurrenceLocator,
    FrozenPageRasterRegionLocator,
    ScannedRegionChemicalFormulaSavedEvidenceV1,
    StandaloneChemicalFormulaSavedEvidenceV1,
    VisualLocator,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_IMAGE_BYTES = 4_194_304
_MAX_RESPONSE_CHARS = 16_384
_MAX_IDENTITY_CHARS = 200
_VERIFIER_VERSION = "chemical-formula-pdf-v1"
_PROMPT = """Classify this one bounded visual region. Return exactly one JSON object and no markdown or prose. The accepted forms are {"classification":"chemical_formula","source_notation":"..."} or {"classification":"not_chemical_formula","source_notation":null}. For a formula or reaction, source_notation must use only the bounded ASCII grammar accepted by chemical_formula_v1: case-correct element symbols, integer counts, parenthesized groups, optional caret isotope and charge notation, optional (s)/(l)/(g)/(aq) state, and for reactions one ->, <=>, or <-> arrow with optional bracketed conditions. Transcribe only visible notation. Do not balance, name, solve, infer, normalize, or return speech, MathML, confidence, typed terms, prose, molecular structures, or any other fields."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChemicalFormulaRecognitionRejected(ValueError):
    """Recognition was unavailable or failed a deterministic contract."""


class ChemicalFormulaRecognitionRequestV1(_FrozenModel):
    """One exact typed chemical candidate and its bounded raster identity."""

    request_kind: Literal["chemical_formula_recognition_v1"]
    candidate_kind: Literal["chemical_formula"]
    locator: VisualLocator
    mime_type: Literal["image/jpeg"]
    image_bytes: bytes = Field(min_length=4, max_length=_MAX_IMAGE_BYTES)
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_payload_identity(self) -> "ChemicalFormulaRecognitionRequestV1":
        if not self.image_bytes.startswith(
            b"\xff\xd8"
        ) or not self.image_bytes.endswith(b"\xff\xd9"):
            raise ValueError("image_bytes must contain one bounded JPEG")
        if (
            hashlib.sha256(self.image_bytes).hexdigest()
            != self.normalized_source_sha256
        ):
            raise ValueError("normalized_source_sha256 does not match image_bytes")
        return self


class ChemicalFormulaRecognitionV1(_FrozenModel):
    """One provider transcription accepted independently by #225."""

    recognition_kind: Literal["chemical_formula_recognition_v1"]
    verified_notation: VerifiedChemicalNotationV1
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=_MAX_IDENTITY_CHARS)
    model: str = Field(min_length=1, max_length=_MAX_IDENTITY_CHARS)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_version: Literal["chemical-formula-pdf-v1"]
    attempts: int = Field(ge=1, le=2, strict=True)

    @field_validator("provider", "model")
    @classmethod
    def _bounded_identity(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("provider identity must be trimmed printable text")
        return value

    @model_validator(mode="after")
    def _validate_verified_notation(self) -> "ChemicalFormulaRecognitionV1":
        try:
            expected = verify_chemical_notation(self.verified_notation.source_notation)
        except (ChemicalFormulaRejected, TypeError, ValueError) as exc:
            raise ValueError(
                "recognition notation is not independently verified"
            ) from exc
        if expected != self.verified_notation:
            raise ValueError("recognition notation projections disagree")
        return self


class ChemicalFormulaPendingAssociationV1(_FrozenModel):
    """One verified notation bound to the exact visual awaiting association."""

    pending_kind: Literal["chemical_formula_pdf_association_v1"]
    locator: VisualLocator
    semantic_output: ChemicalFormulaSemanticV1
    recognition: ChemicalFormulaRecognitionV1

    @model_validator(mode="after")
    def _validate_one_recognition(self) -> "ChemicalFormulaPendingAssociationV1":
        if self.recognition.verified_notation != self.semantic_output.verified_notation:
            raise ValueError("recognition and semantic output disagree")
        return self

    @property
    def alt_text(self) -> str:
        return self.semantic_output.verified_notation.speech

    @property
    def mathml_string(self) -> str:
        return self.semantic_output.verified_notation.mathml

    @property
    def metadata_sha256(self) -> str:
        notation = self.semantic_output.verified_notation
        return canonical_sha256(
            {
                "notation_kind": notation.notation.notation_kind,
                "source_sha256": notation.source_sha256,
                "semantic_sha256": notation.semantic_sha256,
                "speech_sha256": notation.speech_sha256,
                "mathml_sha256": notation.mathml_sha256,
            }
        )


def chemical_formula_semantic_output(source: str) -> ChemicalFormulaSemanticV1:
    """Build public semantics exclusively from #225 verification."""

    verified = verify_chemical_notation(source)
    return ChemicalFormulaSemanticV1(
        semantic_kind="chemical_formula_semantic_v1",
        verified_notation=verified,
    )


class ChemicalFormulaRecognizer:
    """Recover one #225 notation through the existing alt-text boundary."""

    def __init__(
        self,
        alt_text_client: Any,
        *,
        max_response_chars: int = _MAX_RESPONSE_CHARS,
    ) -> None:
        self.alt_text_client = alt_text_client
        self.max_response_chars = max_response_chars

    def recognize(
        self, request: ChemicalFormulaRecognitionRequestV1
    ) -> ChemicalFormulaRecognitionV1:
        request = ChemicalFormulaRecognitionRequestV1.model_validate(request)
        client = self.alt_text_client
        if client is None or getattr(client, "purpose", None) != "alt_text":
            raise ChemicalFormulaRecognitionRejected("purpose_mismatch")
        provider = self._identity(getattr(client, "provider", None))
        model = self._identity(getattr(client, "model", None))
        if provider is None or model is None:
            raise ChemicalFormulaRecognitionRejected("identity_missing")

        provider_failed = False
        for attempts in (1, 2):
            try:
                response = client.analyze_image_sync(
                    image_data=request.image_bytes,
                    prompt=_PROMPT,
                    max_tokens=2048,
                )
            except Exception:
                provider_failed = True
                continue
            if not isinstance(response, dict) or response.get("success") is not True:
                provider_failed = True
                continue
            content = response.get("content")
            if not isinstance(content, str):
                provider_failed = True
                continue
            parsed = self._parse(content)
            if parsed["classification"] == "not_chemical_formula":
                raise ChemicalFormulaRecognitionRejected("not_chemical_formula")
            try:
                verified = verify_chemical_notation(parsed["source_notation"])
            except (ChemicalFormulaRejected, TypeError, ValueError) as exc:
                raise ChemicalFormulaRecognitionRejected("notation_rejected") from exc
            return ChemicalFormulaRecognitionV1(
                recognition_kind="chemical_formula_recognition_v1",
                verified_notation=verified,
                normalized_source_sha256=request.normalized_source_sha256,
                provider=provider,
                model=model,
                response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                verifier_version=_VERIFIER_VERSION,
                attempts=attempts,
            )
        if provider_failed:
            raise ChemicalFormulaRecognitionRejected("provider_failure")
        raise ChemicalFormulaRecognitionRejected("recognition_unavailable")

    def _parse(self, content: str) -> dict[str, Any]:
        if (
            not content
            or len(content) > self.max_response_chars
            or content.strip() != content
        ):
            raise ChemicalFormulaRecognitionRejected("invalid_provider_response")

        def reject_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ChemicalFormulaRecognitionRejected(
                        "invalid_provider_response"
                    )
                result[key] = value
            return result

        try:
            parsed = json.loads(content, object_pairs_hook=reject_duplicates)
        except ChemicalFormulaRecognitionRejected:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ChemicalFormulaRecognitionRejected(
                "invalid_provider_response"
            ) from exc
        if not isinstance(parsed, dict) or set(parsed) != {
            "classification",
            "source_notation",
        }:
            raise ChemicalFormulaRecognitionRejected("invalid_provider_response")
        classification = parsed["classification"]
        source_notation = parsed["source_notation"]
        if classification == "not_chemical_formula":
            if source_notation is not None:
                raise ChemicalFormulaRecognitionRejected("invalid_provider_response")
            return parsed
        if classification != "chemical_formula" or not isinstance(source_notation, str):
            raise ChemicalFormulaRecognitionRejected("invalid_provider_response")
        return parsed

    @staticmethod
    def _identity(value: Any) -> str | None:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _MAX_IDENTITY_CHARS
            or value != value.strip()
            or not value.isprintable()
        ):
            return None
        return value


def build_chemical_formula_pdf_contract(
    path: str | Path,
    pending: Any,
    association: Any,
) -> ChemicalFormulaPdfContract:
    """Build a durable contract only from a reopened, reverse-verified PDF."""

    import fitz

    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.remediation.content_tagger_v2 import (
        verify_image_chemical_formula_association,
        verify_scanned_region_chemical_formula_association,
    )

    pending = ChemicalFormulaPendingAssociationV1.model_validate(pending)
    locator = pending.locator
    is_region = locator.source_kind == "page_raster_region"
    verified = (
        verify_scanned_region_chemical_formula_association(path, pending, association)
        if is_region
        else verify_image_chemical_formula_association(path, pending, association)
    )
    if not verified:
        raise ChemicalFormulaRecognitionRejected("saved_pdf_verification_failed")

    saved_file_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    notation = pending.semantic_output.verified_notation
    recognition = pending.recognition
    recognition_evidence = ChemicalFormulaRecognitionEvidenceV1(
        evidence_kind="chemical_formula_recognition_v1",
        passed=True,
        normalized_source_sha256=recognition.normalized_source_sha256,
        source_sha256=notation.source_sha256,
        semantic_sha256=notation.semantic_sha256,
        speech_sha256=notation.speech_sha256,
        mathml_sha256=notation.mathml_sha256,
        provider=recognition.provider,
        model=recognition.model,
        response_sha256=recognition.response_sha256,
        verifier_version=recognition.verifier_version,
        attempts=recognition.attempts,
    )
    alt_text_sha256 = hashlib.sha256(pending.alt_text.encode("utf-8")).hexdigest()

    with fitz.open(str(path)) as document:
        occurrences = _displayed_image_occurrences(
            document[locator.page_number - 1], locator.page_number
        )
        if is_region:
            saved_occurrences = []
            for occurrence in occurrences:
                try:
                    source = document.extract_image(occurrence["image_xref"])["image"]
                except Exception:
                    continue
                if (
                    occurrence["image_index"] == locator.image_index
                    and occurrence["occurrence_ordinal"] == locator.occurrence_ordinal
                    and isinstance(source, bytes)
                    and hashlib.sha256(source).hexdigest() == locator.source_sha256
                    and all(
                        abs(float(left) - float(right)) <= 1e-6
                        for left, right in zip(occurrence["bbox"], locator.parent_bbox)
                    )
                ):
                    saved_occurrences.append(occurrence)
            if len(saved_occurrences) != 1:
                raise ChemicalFormulaRecognitionRejected("saved_pdf_source_ambiguous")
            saved_occurrence = saved_occurrences[0]
            durable_locator = FrozenPageRasterRegionLocator.model_validate(
                locator.model_dump(mode="json")
            )
            saved_evidence = ScannedRegionChemicalFormulaSavedEvidenceV1(
                evidence_kind="scanned_region_chemical_formula_saved_v1",
                passed=True,
                saved_file_sha256=saved_file_sha256,
                page_number=association.page_number,
                image_xref=saved_occurrence["image_xref"],
                resource_name=association.resource_name,
                struct_parent=association.struct_parent,
                mcid=association.mcid,
                source_sha256=association.source_sha256,
                semantic_sha256=association.semantic_sha256,
                speech_sha256=association.speech_sha256,
                mathml_sha256=association.mathml_sha256,
                alt_text_sha256=alt_text_sha256,
                image_stream_sha256=locator.source_sha256,
                metadata_sha256=association.metadata_sha256,
                formula_bbox=association.formula_bbox,
                render_signatures=association.render_signatures,
                ocr_resource_name=association.ocr_resource_name,
                ocr_struct_parent=association.ocr_struct_parent,
                ocr_group_owners=association.ocr_group_owners,
                ocr_before_mcids=association.ocr_before_mcids,
                ocr_after_mcids=association.ocr_after_mcids,
                ocr_payload_sha256=association.ocr_payload_sha256,
                ocr_font_sha256=association.ocr_font_sha256,
                page_text_sha256=association.page_text_sha256,
            )
        else:
            saved_occurrences = []
            for occurrence in occurrences:
                try:
                    source = document.extract_image(occurrence["image_xref"])["image"]
                except Exception:
                    continue
                if (
                    occurrence["image_index"] == locator.image_index
                    and occurrence["occurrence_ordinal"] == locator.occurrence_ordinal
                    and isinstance(source, bytes)
                    and hashlib.sha256(source).hexdigest()
                    == locator.image_stream_sha256
                    and all(
                        abs(float(left) - float(right)) <= 1e-6
                        for left, right in zip(occurrence["bbox"], locator.bbox)
                    )
                ):
                    saved_occurrences.append(occurrence)
            if len(saved_occurrences) != 1:
                raise ChemicalFormulaRecognitionRejected("saved_pdf_source_ambiguous")
            saved_occurrence = saved_occurrences[0]
            durable_locator = EmbeddedImageOccurrenceLocator(
                source_kind="embedded_image_occurrence",
                page_number=saved_occurrence["page_number"],
                image_xref=saved_occurrence["image_xref"],
                image_index=saved_occurrence["image_index"],
                occurrence_ordinal=saved_occurrence["occurrence_ordinal"],
                bbox=tuple(saved_occurrence["bbox"]),
                image_stream_sha256=locator.image_stream_sha256,
                occurrence_id=saved_occurrence["occurrence_id"],
            )
            saved_evidence = StandaloneChemicalFormulaSavedEvidenceV1(
                evidence_kind="standalone_chemical_formula_saved_v1",
                passed=True,
                saved_file_sha256=saved_file_sha256,
                page_number=saved_occurrence["page_number"],
                image_xref=saved_occurrence["image_xref"],
                occurrence_ordinal=saved_occurrence["occurrence_ordinal"],
                struct_parent=association.struct_parent,
                mcid=association.mcid,
                source_sha256=association.source_sha256,
                semantic_sha256=association.semantic_sha256,
                speech_sha256=association.speech_sha256,
                mathml_sha256=association.mathml_sha256,
                alt_text_sha256=alt_text_sha256,
                image_stream_sha256=locator.image_stream_sha256,
                metadata_sha256=association.metadata_sha256,
                render_signatures=association.render_signatures,
            )

    specialist_fields = {
        "contract_kind": "chemical_formula",
        "locator": durable_locator,
        "semantic_output": pending.semantic_output,
        "normalized_source_sha256": recognition.normalized_source_sha256,
    }
    specialist_material = {
        **specialist_fields,
        "recognition_evidence": recognition_evidence,
    }
    specialist_sha256 = canonical_sha256(specialist_material)
    contract_material = {
        **specialist_fields,
        "verification_evidence": (recognition_evidence, saved_evidence),
        "specialist_sha256": specialist_sha256,
    }
    return ChemicalFormulaPdfContract(
        **contract_material,
        contract_sha256=canonical_sha256(contract_material),
    )


__all__ = [
    "ChemicalFormulaPendingAssociationV1",
    "ChemicalFormulaRecognitionRejected",
    "ChemicalFormulaRecognitionRequestV1",
    "ChemicalFormulaRecognitionV1",
    "ChemicalFormulaRecognizer",
    "build_chemical_formula_pdf_contract",
    "chemical_formula_semantic_output",
]
