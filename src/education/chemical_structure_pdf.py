"""Purpose-bound recognition for bounded chemical-structure PDF visuals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.education.canonical_json import canonical_sha256
from src.education.chemical_abbreviation import (
    ABBREVIATION_POLICY_VERSION,
    ChemicalAbbreviationEvidenceV1,
    verify_chemical_abbreviations,
)
from src.education.molecular_graph import (
    VerifiedMolecularGraphV1,
    canonical_molecular_graph_bytes,
    describe_molecular_graph,
    verify_molecular_graph,
)
from src.education.visual_semantic_contract import (
    ChemicalStructurePdfContract,
    ChemicalStructureRecognitionEvidenceV1,
    ChemicalStructureSemanticV1,
    EmbeddedImageOccurrenceLocator,
    FrozenPageRasterRegionLocator,
    ScannedRegionChemicalStructureSavedEvidenceV1,
    StandaloneChemicalStructureSavedEvidenceV1,
    VisualLocator,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_IMAGE_BYTES = 4_194_304
_MAX_RESPONSE_CHARS = 131_072
_MAX_IDENTITY_CHARS = 200
_VERIFIER_VERSION = "chemical-structure-v1"
_PROMPT = """Classify this one bounded visual region. Return exactly one JSON object and no markdown or prose. The accepted forms are {"classification":"chemical_structure","graph":{...},"abbreviations":[...]} or {"classification":"not_chemical_structure","graph":null,"abbreviations":[]}. For a chemical structure, graph must be one complete, connected, fully expanded molecular_graph_v1 object. Every displayed abbreviation must be expanded into ordinary element atoms and recorded in abbreviations as source_token, anchor_atom_id, and ordered atom_ids. The only abbreviation tokens allowed are Me, Et, n-Pr, i-Pr, t-Bu, and Ph. Do not guess ambiguous topology or stereochemistry. Do not return names, SMILES, InChI, prose, confidence, polymers, wildcard atoms, disconnected labels, or any other fields."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChemicalStructureRecognitionRejected(ValueError):
    """Recognition was unavailable or failed a deterministic contract."""


class ChemicalStructureRecognitionRequestV1(_FrozenModel):
    """One exact bounded raster and typed PDF source identity."""

    request_kind: Literal["chemical_structure_recognition_v1"]
    locator: VisualLocator
    mime_type: Literal["image/jpeg"]
    image_bytes: bytes = Field(min_length=4, max_length=_MAX_IMAGE_BYTES)
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_payload_identity(self) -> "ChemicalStructureRecognitionRequestV1":
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


class ChemicalStructureRecognitionV1(_FrozenModel):
    """Expanded graph proposal with bounded source-token provenance."""

    recognition_kind: Literal["chemical_structure_recognition_v1"]
    graph: VerifiedMolecularGraphV1
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    abbreviations: tuple[ChemicalAbbreviationEvidenceV1, ...]
    abbreviation_evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    abbreviation_policy_version: Literal["chemical-abbreviation-v1"]
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=_MAX_IDENTITY_CHARS)
    model: str = Field(min_length=1, max_length=_MAX_IDENTITY_CHARS)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_version: Literal["chemical-structure-v1"]
    attempts: int = Field(ge=1, le=2, strict=True)

    @field_validator("provider", "model")
    @classmethod
    def _bounded_identity(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("provider identity must be trimmed printable text")
        return value

    @model_validator(mode="after")
    def _validate_verified_evidence(self) -> "ChemicalStructureRecognitionV1":
        graph, evidence = verify_chemical_abbreviations(self.graph, self.abbreviations)
        if graph.canonical_sha256 != self.graph_sha256:
            raise ValueError("graph_sha256 does not match the verified graph")
        if evidence != self.abbreviations:
            raise ValueError("abbreviation evidence is not normalized")
        if self.abbreviation_evidence_sha256 != canonical_sha256(
            [item.model_dump(mode="json") for item in evidence]
        ):
            raise ValueError("abbreviation evidence digest does not match")
        return self


class ChemicalStructurePendingAssociationV1(_FrozenModel):
    """One recognition proposal bound to the exact visual awaiting association."""

    pending_kind: Literal["chemical_structure_pdf_association_v1"]
    locator: VisualLocator
    semantic_output: ChemicalStructureSemanticV1
    recognition: ChemicalStructureRecognitionV1

    @model_validator(mode="after")
    def _validate_one_recognition(self) -> "ChemicalStructurePendingAssociationV1":
        if (
            self.recognition.graph_sha256 != self.semantic_output.graph_sha256
            or self.recognition.graph != self.semantic_output.graph
        ):
            raise ValueError("recognition and semantic output disagree")
        return self

    @property
    def graph_attachment_bytes(self) -> bytes:
        return canonical_molecular_graph_bytes(self.semantic_output.graph)

    @property
    def graph_attachment_sha256(self) -> str:
        return hashlib.sha256(self.graph_attachment_bytes).hexdigest()

    @property
    def alt_text(self) -> str:
        return self.semantic_output.description.summary

    @property
    def accessible_text(self) -> str:
        description = self.semantic_output.description
        return " ".join(
            (
                description.summary,
                *description.atoms,
                *description.bonds,
                description.topology,
            )
        )

    @property
    def metadata_sha256(self) -> str:
        return canonical_sha256(
            {
                "graph_sha256": self.semantic_output.graph_sha256,
                "graph_identifier": self.semantic_output.graph.graph_identifier,
                "description_sha256": self.semantic_output.description_sha256,
                "attachment_sha256": self.graph_attachment_sha256,
                "abbreviation_evidence_sha256": (
                    self.recognition.abbreviation_evidence_sha256
                ),
                "abbreviation_policy_version": (
                    self.recognition.abbreviation_policy_version
                ),
            }
        )


def chemical_structure_semantic_output(value: Any) -> ChemicalStructureSemanticV1:
    """Build accessible output exclusively from one verified canonical graph."""

    graph = verify_molecular_graph(value)
    description = describe_molecular_graph(graph)
    return ChemicalStructureSemanticV1(
        semantic_kind="chemical_structure_semantic_v1",
        graph=graph,
        graph_sha256=graph.canonical_sha256,
        description=description,
        description_sha256=canonical_sha256(description.model_dump(mode="json")),
    )


class ChemicalStructureRecognizer:
    """Recover one expanded graph through the existing alt-text boundary."""

    def __init__(
        self,
        alt_text_client: Any,
        *,
        max_response_chars: int = _MAX_RESPONSE_CHARS,
    ) -> None:
        self.alt_text_client = alt_text_client
        self.max_response_chars = max_response_chars

    def recognize(
        self, request: ChemicalStructureRecognitionRequestV1
    ) -> ChemicalStructureRecognitionV1:
        request = ChemicalStructureRecognitionRequestV1.model_validate(request)
        client = self.alt_text_client
        if client is None or getattr(client, "purpose", None) != "alt_text":
            raise ChemicalStructureRecognitionRejected("purpose_mismatch")
        provider = self._identity(getattr(client, "provider", None))
        model = self._identity(getattr(client, "model", None))
        if provider is None or model is None:
            raise ChemicalStructureRecognitionRejected("identity_missing")

        last_provider_failure = False
        for attempts in (1, 2):
            try:
                response = client.analyze_image_sync(
                    image_data=request.image_bytes,
                    prompt=_PROMPT,
                    max_tokens=4096,
                )
            except Exception:
                last_provider_failure = True
                continue
            if not isinstance(response, dict) or response.get("success") is not True:
                last_provider_failure = True
                continue
            content = response.get("content")
            if not isinstance(content, str):
                last_provider_failure = True
                continue
            parsed = self._parse(content)
            if parsed["classification"] == "not_chemical_structure":
                raise ChemicalStructureRecognitionRejected("not_chemical_structure")
            try:
                graph, abbreviations = verify_chemical_abbreviations(
                    parsed["graph"], parsed["abbreviations"]
                )
            except (TypeError, ValueError) as exc:
                raise ChemicalStructureRecognitionRejected("graph_rejected") from exc
            abbreviation_material = [
                item.model_dump(mode="json") for item in abbreviations
            ]
            return ChemicalStructureRecognitionV1(
                recognition_kind="chemical_structure_recognition_v1",
                graph=graph,
                graph_sha256=graph.canonical_sha256,
                abbreviations=abbreviations,
                abbreviation_evidence_sha256=canonical_sha256(abbreviation_material),
                abbreviation_policy_version=ABBREVIATION_POLICY_VERSION,
                normalized_source_sha256=request.normalized_source_sha256,
                provider=provider,
                model=model,
                response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                verifier_version=_VERIFIER_VERSION,
                attempts=attempts,
            )
        if last_provider_failure:
            raise ChemicalStructureRecognitionRejected("provider_failure")
        raise ChemicalStructureRecognitionRejected("recognition_unavailable")

    def _parse(self, content: str) -> dict[str, Any]:
        if (
            not content
            or len(content) > self.max_response_chars
            or content.strip() != content
        ):
            raise ChemicalStructureRecognitionRejected("invalid_provider_response")

        def reject_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ChemicalStructureRecognitionRejected(
                        "invalid_provider_response"
                    )
                result[key] = value
            return result

        try:
            parsed = json.loads(content, object_pairs_hook=reject_duplicates)
        except ChemicalStructureRecognitionRejected:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ChemicalStructureRecognitionRejected(
                "invalid_provider_response"
            ) from exc
        if not isinstance(parsed, dict) or set(parsed) != {
            "classification",
            "graph",
            "abbreviations",
        }:
            raise ChemicalStructureRecognitionRejected("invalid_provider_response")
        classification = parsed["classification"]
        graph = parsed["graph"]
        abbreviations = parsed["abbreviations"]
        if classification == "not_chemical_structure":
            if graph is not None or abbreviations != []:
                raise ChemicalStructureRecognitionRejected("invalid_provider_response")
            return parsed
        if (
            classification != "chemical_structure"
            or not isinstance(graph, dict)
            or not isinstance(abbreviations, list)
        ):
            raise ChemicalStructureRecognitionRejected("invalid_provider_response")
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


def build_chemical_structure_pdf_contract(
    path: str | Path,
    pending: Any,
    association: Any,
) -> ChemicalStructurePdfContract:
    """Build a durable contract only from a reopened, reverse-verified PDF."""

    import fitz

    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.remediation.content_tagger_v2 import (
        verify_image_chemical_structure_association,
        verify_scanned_region_chemical_structure_association,
    )

    pending = ChemicalStructurePendingAssociationV1.model_validate(pending)
    locator = pending.locator
    is_region = locator.source_kind == "page_raster_region"
    verified = (
        verify_scanned_region_chemical_structure_association(path, pending, association)
        if is_region
        else verify_image_chemical_structure_association(path, pending, association)
    )
    if not verified:
        raise ChemicalStructureRecognitionRejected("saved_pdf_verification_failed")

    saved_file_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    semantic = pending.semantic_output
    recognition = pending.recognition
    recognition_evidence = ChemicalStructureRecognitionEvidenceV1(
        evidence_kind="chemical_structure_recognition_v1",
        passed=True,
        normalized_source_sha256=recognition.normalized_source_sha256,
        graph_sha256=recognition.graph_sha256,
        abbreviations=recognition.abbreviations,
        abbreviation_evidence_sha256=recognition.abbreviation_evidence_sha256,
        abbreviation_policy_version=recognition.abbreviation_policy_version,
        abbreviation_count=len(recognition.abbreviations),
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
                raise ChemicalStructureRecognitionRejected("saved_pdf_source_ambiguous")
            saved_occurrence = saved_occurrences[0]
            durable_locator = FrozenPageRasterRegionLocator.model_validate(
                locator.model_dump(mode="json")
            )
            saved_evidence = ScannedRegionChemicalStructureSavedEvidenceV1(
                evidence_kind="scanned_region_chemical_structure_saved_v1",
                passed=True,
                saved_file_sha256=saved_file_sha256,
                page_number=association.page_number,
                image_xref=saved_occurrence["image_xref"],
                resource_name=association.resource_name,
                struct_parent=association.struct_parent,
                mcid=association.mcid,
                graph_sha256=association.graph_sha256,
                description_sha256=association.description_sha256,
                abbreviation_evidence_sha256=(association.abbreviation_evidence_sha256),
                alt_text_sha256=alt_text_sha256,
                image_stream_sha256=locator.source_sha256,
                attachment_sha256=association.attachment_sha256,
                metadata_sha256=association.metadata_sha256,
                structure_bbox=association.structure_bbox,
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
                raise ChemicalStructureRecognitionRejected("saved_pdf_source_ambiguous")
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
            saved_evidence = StandaloneChemicalStructureSavedEvidenceV1(
                evidence_kind="standalone_chemical_structure_saved_v1",
                passed=True,
                saved_file_sha256=saved_file_sha256,
                page_number=saved_occurrence["page_number"],
                image_xref=saved_occurrence["image_xref"],
                occurrence_ordinal=saved_occurrence["occurrence_ordinal"],
                struct_parent=association.struct_parent,
                mcid=association.mcid,
                graph_sha256=association.graph_sha256,
                description_sha256=association.description_sha256,
                abbreviation_evidence_sha256=(association.abbreviation_evidence_sha256),
                alt_text_sha256=alt_text_sha256,
                image_stream_sha256=locator.image_stream_sha256,
                attachment_sha256=association.attachment_sha256,
                metadata_sha256=association.metadata_sha256,
                render_signatures=association.render_signatures,
            )

    specialist_fields = {
        "contract_kind": "chemical_structure",
        "locator": durable_locator,
        "semantic_output": semantic,
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
    return ChemicalStructurePdfContract(
        **contract_material,
        contract_sha256=canonical_sha256(contract_material),
    )


__all__ = [
    "ChemicalStructurePendingAssociationV1",
    "ChemicalStructureRecognitionRejected",
    "ChemicalStructureRecognitionRequestV1",
    "ChemicalStructureRecognitionV1",
    "ChemicalStructureRecognizer",
    "build_chemical_structure_pdf_contract",
    "chemical_structure_semantic_output",
]
