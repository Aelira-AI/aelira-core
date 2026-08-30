"""Fail-closed semantic planning for complete multi-equation raster groups."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.education.multi_equation_region import (
    MultiEquationRegionGroupV1,
    child_pixel_payload_sha256,
)
from src.education.multi_equation_semantics import (
    MultiEquationSavedEvidenceV1,
    MultiEquationSemanticContractV1,
    MultiEquationSemanticOwnerV1,
    build_multi_equation_semantic_contract,
    build_multi_equation_semantic_owner,
    validate_multi_equation_semantic_plan,
)
from src.education.pdf_checks.multi_equation_region_detector import (
    MultiEquationRegionDetector,
)
from src.education.remediation.equation_image_source import (
    ValidatedEquationRaster,
    _deterministic_jpeg,
)
from src.education.remediation.equation_recognizer import EquationRecognizer
from src.education.remediation.equation_verifier import EquationVerifier
from src.education.remediation.math_fixer import generate_equation_alt_text
from src.education.visual_semantic_contract import (
    MathMLExpressionV1,
    PrintedEquationRoundtripEvidenceV1,
)


class MultiEquationSemanticRejected(ValueError):
    """A complete semantic plan could not be proved without partial output."""


_MAX_TRANSACTION_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class ValidatedMultiEquationRaster:
    """Provider-safe normalized raster for one exact whole-system union."""

    jpeg_bytes: bytes
    mime_type: str
    normalized_sha256: str
    width: int
    height: int


def _union_bbox(group: MultiEquationRegionGroupV1, field: str):
    boxes = [getattr(child, field) for child in group.children]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def extract_whole_system_raster(
    document: Any, group: MultiEquationRegionGroupV1
) -> ValidatedMultiEquationRaster:
    """Crop the deterministic union from the revalidated original image stream."""

    from src.education.pdf_checks.equation_region_detector import (
        RasterEquationRegionDetector,
    )

    loaded = RasterEquationRegionDetector._load_source(document, group.image_xref)
    if loaded is None:
        raise MultiEquationSemanticRejected("multi_equation_source_unavailable")
    image, source_sha256 = loaded
    try:
        if source_sha256 != group.source_sha256 or image.size != (
            group.source_width,
            group.source_height,
        ):
            raise MultiEquationSemanticRejected("multi_equation_source_changed")
        pixel_bbox = _union_bbox(group, "pixel_bbox")
        crop = image.crop(pixel_bbox)
        try:
            jpeg = _deterministic_jpeg(crop)
        finally:
            crop.close()
    finally:
        image.close()
    return ValidatedMultiEquationRaster(
        jpeg_bytes=jpeg,
        mime_type="image/jpeg",
        normalized_sha256=hashlib.sha256(jpeg).hexdigest(),
        width=pixel_bbox[2] - pixel_bbox[0],
        height=pixel_bbox[3] - pixel_bbox[1],
    )


def extract_multi_equation_child_raster(
    document: Any, locator: Any
) -> ValidatedMultiEquationRaster:
    """Normalize one child only after exact source and crop revalidation."""

    from src.education.pdf_checks.equation_region_detector import (
        RasterEquationRegionDetector,
    )

    loaded = RasterEquationRegionDetector._load_source(document, locator.image_xref)
    if loaded is None:
        raise MultiEquationSemanticRejected("multi_equation_source_unavailable")
    image, source_sha256 = loaded
    try:
        if source_sha256 != locator.source_sha256 or image.size != (
            locator.source_width,
            locator.source_height,
        ):
            raise MultiEquationSemanticRejected("multi_equation_source_changed")
        crop = image.crop(locator.pixel_bbox)
        try:
            if (
                child_pixel_payload_sha256(crop.mode, crop.size, crop.tobytes())
                != locator.crop_pixel_sha256
            ):
                raise MultiEquationSemanticRejected("multi_equation_crop_changed")
            jpeg = _deterministic_jpeg(crop)
        finally:
            crop.close()
    finally:
        image.close()
    return ValidatedMultiEquationRaster(
        jpeg_bytes=jpeg,
        mime_type="image/jpeg",
        normalized_sha256=hashlib.sha256(jpeg).hexdigest(),
        width=locator.pixel_bbox[2] - locator.pixel_bbox[0],
        height=locator.pixel_bbox[3] - locator.pixel_bbox[1],
    )


class MultiEquationSemanticPlanner:
    """Recognize and verify every owner required by one revalidated group."""

    def __init__(
        self,
        recognizer: EquationRecognizer,
        verifier: EquationVerifier,
        *,
        detector: Optional[MultiEquationRegionDetector] = None,
        child_source: Optional[Callable[[Any, Any], ValidatedEquationRaster]] = None,
        system_source: Optional[
            Callable[[Any, MultiEquationRegionGroupV1], ValidatedEquationRaster]
        ] = None,
        alt_text_builder: Callable[[str], str] = generate_equation_alt_text,
    ) -> None:
        self.recognizer = recognizer
        self.verifier = verifier
        self.detector = detector or MultiEquationRegionDetector()
        self.child_source = child_source or extract_multi_equation_child_raster
        self.system_source = system_source or extract_whole_system_raster
        self.alt_text_builder = alt_text_builder

    def plan(
        self, document: Any, value: Any
    ) -> tuple[MultiEquationSemanticOwnerV1, ...]:
        """Return all required owners or reject without returning a subset."""

        try:
            group = MultiEquationRegionGroupV1.model_validate(value)
            if self.detector.revalidate_group(document, group) != group:
                raise MultiEquationSemanticRejected("multi_equation_group_stale")
            if group.disposition == "split_children":
                sources = tuple(
                    (
                        "multi_equation_child_v1",
                        index,
                        (child.region_id,),
                        child.pixel_bbox,
                        child.pdf_bbox,
                        self.child_source(document, child),
                    )
                    for index, child in enumerate(group.children)
                )
            else:
                sources = (
                    (
                        "multi_equation_system_v1",
                        0,
                        tuple(child.region_id for child in group.children),
                        _union_bbox(group, "pixel_bbox"),
                        _union_bbox(group, "pdf_bbox"),
                        self.system_source(document, group),
                    ),
                )
            owners = tuple(self._recognize(*source) for source in sources)
            expected = (
                len(group.children) if group.disposition == "split_children" else 1
            )
            if len(owners) != expected:
                raise MultiEquationSemanticRejected("multi_equation_result_incomplete")
            return owners
        except MultiEquationSemanticRejected:
            raise
        except Exception as exc:
            raise MultiEquationSemanticRejected(
                "multi_equation_semantics_rejected"
            ) from exc

    def _recognize(
        self,
        owner_kind: str,
        ordinal: int,
        region_ids: tuple[str, ...],
        pixel_bbox: tuple[int, int, int, int],
        pdf_bbox: tuple[float, float, float, float],
        source: ValidatedEquationRaster,
    ) -> MultiEquationSemanticOwnerV1:
        recognition = self.recognizer.recognize(source)
        if recognition.classification != "printed_equation" or not recognition.latex:
            raise MultiEquationSemanticRejected("multi_equation_recognition_failed")
        evidence = self.verifier.verify(source, recognition.latex)
        if not evidence.passed or evidence.source_sha256 != source.normalized_sha256:
            raise MultiEquationSemanticRejected("multi_equation_verification_failed")
        mathml = self.verifier.canonicalize_mathml(
            self.verifier.converter(recognition.latex)
        )
        semantic = MathMLExpressionV1(
            semantic_kind="mathml_expression_v1",
            mathml=mathml,
            alt_text=self.alt_text_builder(recognition.latex),
            mathml_sha256=hashlib.sha256(mathml.encode("utf-8")).hexdigest(),
        )
        bounded = PrintedEquationRoundtripEvidenceV1(
            evidence_kind="printed_equation_roundtrip_v1",
            **asdict(evidence),
        )
        return build_multi_equation_semantic_owner(
            owner_kind=owner_kind,  # type: ignore[arg-type]
            ordinal=ordinal,
            region_ids=region_ids,
            pixel_bbox=pixel_bbox,
            pdf_bbox=pdf_bbox,
            semantic_output=semantic,
            normalized_source_sha256=source.normalized_sha256,
            verification_evidence=bounded,
            provider=recognition.provider,
            model=recognition.model,
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            if total > _MAX_TRANSACTION_BYTES:
                raise MultiEquationSemanticRejected(
                    "multi_equation_transaction_byte_limit"
                )
            digest.update(chunk)
    return digest.hexdigest()


def commit_multi_equation_transaction(
    input_path: str | Path,
    output_path: str | Path,
    group: Any,
    owners: Any,
    *,
    associate: Callable[
        [Path, MultiEquationRegionGroupV1, tuple[MultiEquationSemanticOwnerV1, ...]],
        None,
    ],
    verify_saved: Callable[
        [Path, MultiEquationRegionGroupV1, tuple[MultiEquationSemanticOwnerV1, ...]],
        MultiEquationSavedEvidenceV1,
    ],
) -> MultiEquationSemanticContractV1:
    """Commit only a fully reverse-verified disposable PDF transaction."""

    try:
        validated_group, validated_owners = validate_multi_equation_semantic_plan(
            group, owners
        )
    except Exception as exc:
        raise MultiEquationSemanticRejected("multi_equation_plan_invalid") from exc
    source = Path(input_path)
    destination = Path(output_path)
    if not source.is_file() or not destination.parent.is_dir():
        raise MultiEquationSemanticRejected("multi_equation_transaction_path_invalid")
    source_sha256 = _file_sha256(source)
    descriptor, candidate_name = tempfile.mkstemp(
        prefix=f".{destination.name}.multi-equation-",
        suffix=".pdf",
        dir=str(destination.parent),
    )
    os.close(descriptor)
    candidate = Path(candidate_name)
    try:
        shutil.copyfile(source, candidate)
        if (
            _file_sha256(source) != source_sha256
            or _file_sha256(candidate) != source_sha256
        ):
            raise MultiEquationSemanticRejected("multi_equation_source_changed")
        associate(candidate, validated_group, validated_owners)
        saved = MultiEquationSavedEvidenceV1.model_validate(
            verify_saved(candidate, validated_group, validated_owners)
        )
        if saved.saved_file_sha256 != _file_sha256(candidate):
            raise MultiEquationSemanticRejected("multi_equation_saved_digest_mismatch")
        contract = build_multi_equation_semantic_contract(
            group=validated_group,
            owners=validated_owners,
            saved_evidence=saved,
        )
        os.replace(candidate, destination)
        return contract
    except MultiEquationSemanticRejected:
        raise
    except Exception as exc:
        raise MultiEquationSemanticRejected(
            "multi_equation_transaction_rejected"
        ) from exc
    finally:
        candidate.unlink(missing_ok=True)


__all__ = [
    "MultiEquationSemanticPlanner",
    "MultiEquationSemanticRejected",
    "ValidatedMultiEquationRaster",
    "commit_multi_equation_transaction",
    "extract_multi_equation_child_raster",
    "extract_whole_system_raster",
]
