"""Purpose-bound recognition for bounded commutative-diagram PDF visuals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.education.commutative_diagram import (
    VerifiedCommutativeDiagramV1,
    describe_commutative_diagram,
    render_commutative_diagram_html,
    verify_commutative_diagram,
)
from src.education.visual_semantic_contract import (
    CommutativeDiagramPdfContract,
    CommutativeDiagramRecognitionEvidenceV1,
    CommutativeDiagramSemanticV1,
    EmbeddedImageOccurrenceLocator,
    FrozenPageRasterRegionLocator,
    ScannedRegionDiagramSavedEvidenceV1,
    StandaloneDiagramSavedEvidenceV1,
    VisualLocator,
    canonical_sha256,
)
from src.education.canonical_json import canonical_json_bytes

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_IMAGE_BYTES = 4_194_304
_MAX_RESPONSE_CHARS = 131_072
_MAX_IDENTITY_CHARS = 200
_VERIFIER_VERSION = "commutative-diagram-v1"
_PROMPT = """Classify this one bounded visual region. Return exactly one JSON object and no markdown or prose. The accepted forms are {"classification":"commutative_diagram","graph":{...}} or {"classification":"not_commutative_diagram","graph":null}. For a commutative diagram, graph must use exactly the commutative_diagram_v1 schema with typed nodes, directed or bidirectional edges, attached labels, ordered composition paths, declared commutativity relations, optional layout, and an empty unresolved_crossings list. Do not return prose, confidence, inferred proof claims, or arrow counts."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommutativeDiagramRecognitionRejected(ValueError):
    """Recognition was unavailable or failed a deterministic contract."""


class CommutativeDiagramRecognitionRequestV1(_FrozenModel):
    """One exact bounded raster and typed PDF source identity."""

    request_kind: Literal["commutative_diagram_recognition_v1"]
    locator: VisualLocator
    mime_type: Literal["image/jpeg"]
    image_bytes: bytes = Field(min_length=4, max_length=_MAX_IMAGE_BYTES)
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_payload_identity(self) -> "CommutativeDiagramRecognitionRequestV1":
        if not self.image_bytes.startswith(
            b"\xff\xd8"
        ) or not self.image_bytes.endswith(b"\xff\xd9"):
            raise ValueError("image_bytes must contain one bounded JPEG")
        digest = hashlib.sha256(self.image_bytes).hexdigest()
        if digest != self.normalized_source_sha256:
            raise ValueError("normalized_source_sha256 does not match image_bytes")
        return self


class CommutativeDiagramRecognitionV1(_FrozenModel):
    """Verified graph proposal with bounded provider provenance."""

    recognition_kind: Literal["commutative_diagram_recognition_v1"]
    graph: VerifiedCommutativeDiagramV1
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    normalized_source_sha256: str = Field(pattern=_SHA256_PATTERN)
    provider: str = Field(min_length=1, max_length=_MAX_IDENTITY_CHARS)
    model: str = Field(min_length=1, max_length=_MAX_IDENTITY_CHARS)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)
    verifier_version: Literal["commutative-diagram-v1"]
    attempts: int = Field(ge=1, le=2, strict=True)

    @field_validator("provider", "model")
    @classmethod
    def _bounded_identity(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("provider identity must be trimmed printable text")
        return value

    @model_validator(mode="after")
    def _validate_graph_digest(self) -> "CommutativeDiagramRecognitionV1":
        verified = verify_commutative_diagram(self.graph)
        if self.graph_sha256 != verified.canonical_sha256:
            raise ValueError("graph_sha256 does not match the verified graph")
        return self


class CommutativeDiagramPendingAssociationV1(_FrozenModel):
    """One recognition proposal bound to the exact visual awaiting PDF association."""

    pending_kind: Literal["commutative_diagram_pdf_association_v1"]
    locator: VisualLocator
    semantic_output: CommutativeDiagramSemanticV1
    recognition: CommutativeDiagramRecognitionV1

    @model_validator(mode="after")
    def _validate_one_recognition(self) -> "CommutativeDiagramPendingAssociationV1":
        if (
            self.recognition.graph_sha256 != self.semantic_output.graph_sha256
            or self.recognition.graph != self.semantic_output.graph
        ):
            raise ValueError("recognition and semantic output disagree")
        return self

    @property
    def graph_attachment_bytes(self) -> bytes:
        return canonical_json_bytes(self.semantic_output.graph.model_dump(mode="json"))

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
                *description.objects,
                *description.arrows,
                *description.paths,
                *description.relations,
            )
        )

    @property
    def metadata_sha256(self) -> str:
        return canonical_sha256(
            {
                "graph_sha256": self.semantic_output.graph_sha256,
                "description_sha256": self.semantic_output.description_sha256,
                "rendered_html_sha256": self.semantic_output.rendered_html_sha256,
            }
        )

    @property
    def page_number(self) -> int:
        return int(self.locator.page_number)

    @property
    def image_xref(self) -> int:
        return int(self.locator.image_xref)

    @property
    def image_index(self) -> int:
        return int(self.locator.image_index)

    @property
    def occurrence_ordinal(self) -> int:
        return int(self.locator.occurrence_ordinal)

    @property
    def occurrence_id(self) -> str:
        return str(
            getattr(
                self.locator,
                "occurrence_id",
                getattr(self.locator, "parent_occurrence_id", ""),
            )
        )


def commutative_diagram_semantic_output(
    value: Any,
) -> CommutativeDiagramSemanticV1:
    """Build both accessible outputs from one verified canonical graph."""
    graph = verify_commutative_diagram(value)
    description = describe_commutative_diagram(graph)
    rendered_html = render_commutative_diagram_html(graph)
    return CommutativeDiagramSemanticV1(
        semantic_kind="commutative_diagram_semantic_v1",
        graph=graph,
        graph_sha256=graph.canonical_sha256,
        description=description,
        description_sha256=canonical_sha256(description.model_dump(mode="json")),
        rendered_html=rendered_html,
        rendered_html_sha256=hashlib.sha256(rendered_html.encode("utf-8")).hexdigest(),
    )


class CommutativeDiagramRecognizer:
    """Recover one graph proposal through the existing alt-text purpose boundary."""

    def __init__(
        self,
        alt_text_client: Any,
        *,
        max_response_chars: int = _MAX_RESPONSE_CHARS,
    ) -> None:
        self.alt_text_client = alt_text_client
        self.max_response_chars = max_response_chars

    def recognize(
        self, request: CommutativeDiagramRecognitionRequestV1
    ) -> CommutativeDiagramRecognitionV1:
        request = CommutativeDiagramRecognitionRequestV1.model_validate(request)
        client = self.alt_text_client
        if client is None or getattr(client, "purpose", None) != "alt_text":
            raise CommutativeDiagramRecognitionRejected("purpose_mismatch")
        provider = self._identity(getattr(client, "provider", None))
        model = self._identity(getattr(client, "model", None))
        if provider is None or model is None:
            raise CommutativeDiagramRecognitionRejected("identity_missing")

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
            if parsed["classification"] == "not_commutative_diagram":
                raise CommutativeDiagramRecognitionRejected("not_commutative_diagram")
            try:
                graph = verify_commutative_diagram(parsed["graph"])
            except (TypeError, ValueError) as exc:
                raise CommutativeDiagramRecognitionRejected("graph_rejected") from exc
            return CommutativeDiagramRecognitionV1(
                recognition_kind="commutative_diagram_recognition_v1",
                graph=graph,
                graph_sha256=graph.canonical_sha256,
                normalized_source_sha256=request.normalized_source_sha256,
                provider=provider,
                model=model,
                response_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                verifier_version=_VERIFIER_VERSION,
                attempts=attempts,
            )
        if last_provider_failure:
            raise CommutativeDiagramRecognitionRejected("provider_failure")
        raise CommutativeDiagramRecognitionRejected("recognition_unavailable")

    def _parse(self, content: str) -> dict[str, Any]:
        if (
            not content
            or len(content) > self.max_response_chars
            or content.strip() != content
        ):
            raise CommutativeDiagramRecognitionRejected("invalid_provider_response")

        def reject_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise CommutativeDiagramRecognitionRejected(
                        "invalid_provider_response"
                    )
                result[key] = value
            return result

        try:
            parsed = json.loads(content, object_pairs_hook=reject_duplicates)
        except CommutativeDiagramRecognitionRejected:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CommutativeDiagramRecognitionRejected(
                "invalid_provider_response"
            ) from exc
        if not isinstance(parsed, dict) or set(parsed) != {"classification", "graph"}:
            raise CommutativeDiagramRecognitionRejected("invalid_provider_response")
        classification = parsed["classification"]
        graph = parsed["graph"]
        if classification == "not_commutative_diagram":
            if graph is not None:
                raise CommutativeDiagramRecognitionRejected("invalid_provider_response")
            return parsed
        if classification != "commutative_diagram" or not isinstance(graph, dict):
            raise CommutativeDiagramRecognitionRejected("invalid_provider_response")
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


def build_commutative_diagram_pdf_contract(
    path: str | Path,
    pending: Any,
    association: Any,
) -> CommutativeDiagramPdfContract:
    """Build a durable contract only from a reopened, reverse-verified PDF."""
    import fitz

    from src.education.pdf_checks.image_checker import _displayed_image_occurrences
    from src.education.remediation.content_tagger_v2 import (
        verify_image_commutative_diagram_association,
        verify_scanned_region_commutative_diagram_association,
    )

    pending = CommutativeDiagramPendingAssociationV1.model_validate(pending)
    locator = pending.locator
    is_region = locator.source_kind == "page_raster_region"
    verified = (
        verify_scanned_region_commutative_diagram_association(
            path, pending, association
        )
        if is_region
        else verify_image_commutative_diagram_association(path, pending, association)
    )
    if not verified:
        raise CommutativeDiagramRecognitionRejected("saved_pdf_verification_failed")

    file_bytes = Path(path).read_bytes()
    saved_file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    semantic = pending.semantic_output
    recognition = pending.recognition
    recognition_evidence = CommutativeDiagramRecognitionEvidenceV1(
        evidence_kind="commutative_diagram_recognition_v1",
        passed=True,
        normalized_source_sha256=recognition.normalized_source_sha256,
        graph_sha256=recognition.graph_sha256,
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
                raise CommutativeDiagramRecognitionRejected(
                    "saved_pdf_source_ambiguous"
                )
            saved_occurrence = saved_occurrences[0]
            durable_locator = FrozenPageRasterRegionLocator.model_validate(
                locator.model_dump(mode="json")
            )
            saved_evidence = ScannedRegionDiagramSavedEvidenceV1(
                evidence_kind="scanned_region_diagram_saved_v1",
                passed=True,
                saved_file_sha256=saved_file_sha256,
                page_number=association.page_number,
                image_xref=saved_occurrence["image_xref"],
                resource_name=association.resource_name,
                struct_parent=association.struct_parent,
                mcid=association.mcid,
                graph_sha256=association.graph_sha256,
                description_sha256=association.description_sha256,
                rendered_html_sha256=association.rendered_html_sha256,
                alt_text_sha256=alt_text_sha256,
                image_stream_sha256=locator.source_sha256,
                attachment_sha256=association.attachment_sha256,
                metadata_sha256=association.metadata_sha256,
                diagram_bbox=association.diagram_bbox,
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
                raise CommutativeDiagramRecognitionRejected(
                    "saved_pdf_source_ambiguous"
                )
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
            saved_evidence = StandaloneDiagramSavedEvidenceV1(
                evidence_kind="standalone_diagram_saved_v1",
                passed=True,
                saved_file_sha256=saved_file_sha256,
                page_number=saved_occurrence["page_number"],
                image_xref=saved_occurrence["image_xref"],
                occurrence_ordinal=saved_occurrence["occurrence_ordinal"],
                struct_parent=association.struct_parent,
                mcid=association.mcid,
                graph_sha256=association.graph_sha256,
                description_sha256=association.description_sha256,
                rendered_html_sha256=association.rendered_html_sha256,
                alt_text_sha256=alt_text_sha256,
                image_stream_sha256=locator.image_stream_sha256,
                attachment_sha256=association.attachment_sha256,
                metadata_sha256=association.metadata_sha256,
                render_signatures=association.render_signatures,
            )

    specialist_fields = {
        "contract_kind": "commutative_diagram",
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
    return CommutativeDiagramPdfContract(
        **contract_material,
        contract_sha256=canonical_sha256(contract_material),
    )


__all__ = [
    "CommutativeDiagramPendingAssociationV1",
    "CommutativeDiagramRecognitionRejected",
    "CommutativeDiagramRecognitionRequestV1",
    "CommutativeDiagramRecognitionV1",
    "CommutativeDiagramRecognizer",
    "commutative_diagram_semantic_output",
    "build_commutative_diagram_pdf_contract",
]
