"""Deterministic discovery of ordered equation regions in one page raster."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Callable, Literal, Mapping, Optional, Sequence

import fitz
import numpy as np
from PIL import Image

from src.education.multi_equation_region import (
    MAX_MULTI_EQUATION_CHILDREN,
    MAX_MULTI_EQUATION_GROUP_PIXELS,
    MultiEquationRegionGroupV1,
    build_multi_equation_group,
)
from src.education.visual_semantic_contract import FrozenPageRasterRegionLocator
from src.education.pdf_checks.equation_region_detector import (
    MAX_CONNECTED_COMPONENTS,
    MIN_CANDIDATE_OCR_CONFIDENCE,
    MOAT_PIXELS,
    OCR_CONFIG,
    OCR_LANGUAGE,
    OCR_TIMEOUT_SECONDS,
    THRESHOLD_VERSION,
    RasterEquationRegionDetector,
    _bounded_component_count,
    _canonical_digest,
    _MATH_SIGNAL,
)

MULTI_EQUATION_DETECTOR_VERSION = "multi-equation-region-v1"
MAX_MULTI_EQUATION_VALIDATION_PIXELS = MAX_MULTI_EQUATION_GROUP_PIXELS
MAX_MULTI_EQUATION_PAGES_PER_DOCUMENT = 8
_SYSTEM_MARKERS = ("{", "[", "⎧", "⎨", "⎩")


class MultiEquationRegionDetector:
    """Find one complete ordered multi-equation group or fail closed."""

    def __init__(
        self,
        *,
        ocr_data: Optional[Callable[..., Mapping[str, Sequence[Any]]]] = None,
        ocr_version: Optional[Callable[[], Any]] = None,
        ocr_tessdata_sha256: Optional[Callable[[], str]] = None,
    ) -> None:
        self._base = RasterEquationRegionDetector(
            ocr_data=ocr_data,
            ocr_version=ocr_version,
            ocr_tessdata_sha256=ocr_tessdata_sha256,
        )

    def find_document_groups(
        self, doc: fitz.Document
    ) -> tuple[MultiEquationRegionGroupV1, ...]:
        """Find bounded groups across eligible pages without partial overflow."""

        from src.education.pdf_checks.equation_region_detector import (
            is_full_page_raster_occurrence,
        )
        from src.education.pdf_checks.image_checker import (
            _displayed_image_occurrences,
        )

        sources: list[tuple[fitz.Page, Mapping[str, Any]]] = []
        try:
            for page_index, page in enumerate(doc):
                occurrences = _displayed_image_occurrences(page, page_index + 1)
                if len(occurrences) != 1 or not is_full_page_raster_occurrence(
                    page, occurrences[0]
                ):
                    continue
                sources.append((page, occurrences[0]))
                if len(sources) > MAX_MULTI_EQUATION_PAGES_PER_DOCUMENT:
                    return ()
            groups = []
            for page, occurrence in sources:
                group = self.find_group(doc, page, occurrence)
                if group is not None:
                    groups.append(group)
            return tuple(groups)
        except Exception:
            return ()

    def find_group(
        self,
        doc: fitz.Document,
        page: fitz.Page,
        occurrence: Mapping[str, Any],
    ) -> MultiEquationRegionGroupV1 | None:
        """Return one exact group only when every child is bounded and owned."""

        try:
            eligible = self._base._eligible_page(page, occurrence)
            if eligible is None:
                return None
            parent_bbox, transform = eligible
            loaded = self._base._load_source(doc, int(occurrence["image_xref"]))
            if loaded is None:
                return None
            image, source_sha256 = loaded
            ocr_identity = self._base._resolve_ocr_identity()
            if ocr_identity is None:
                return None
            version, tessdata_sha256 = ocr_identity
            raw_data = self._base._ocr_data(
                image,
                lang=OCR_LANGUAGE,
                config=OCR_CONFIG,
                timeout=OCR_TIMEOUT_SECONDS,
            )
            lines = self._base._parse_lines(raw_data, image.size)
            if lines is None:
                return None
            signaled_lines = [line for line in lines if _MATH_SIGNAL.search(line.text)]
            math_lines = [
                line
                for line in signaled_lines
                if line.confidence >= MIN_CANDIDATE_OCR_CONFIDENCE
                and len(line.tokens) <= 16
                and _MATH_SIGNAL.search(line.text)
                and sum(
                    1 for token in line.tokens if token.isalpha() and len(token) > 1
                )
                <= 2
            ]
            if (
                len(math_lines) != len(signaled_lines)
                or not 2 <= len(math_lines) <= MAX_MULTI_EQUATION_CHILDREN
            ):
                return None
            disposition = self._disposition(math_lines)
            if disposition is None:
                return None
            gray = np.asarray(image.convert("L"))
            ink = gray < 245
            if self._has_unowned_ink(ink, lines):
                return None
            component_count = _bounded_component_count(ink)
            if component_count is None or component_count > MAX_CONNECTED_COMPONENTS:
                return None
            pixel_boxes = self._child_boxes(image, ink, lines, math_lines)
            if pixel_boxes is None:
                return None
            children = []
            for pixel_bbox in pixel_boxes:
                crop = image.crop(pixel_bbox)
                crop_sha256 = self._pixel_digest(crop)
                pdf_bbox = self._base._map_to_pdf_bbox(
                    pixel_bbox, image.size, parent_bbox
                )
                if pdf_bbox is None:
                    return None
                evidence: dict[str, Any] = {
                    "source_kind": "page_raster_region",
                    "page_number": int(occurrence["page_number"]),
                    "parent_occurrence_id": str(occurrence["occurrence_id"]),
                    "image_xref": int(occurrence["image_xref"]),
                    "image_index": int(occurrence["image_index"]),
                    "occurrence_ordinal": int(occurrence["occurrence_ordinal"]),
                    "parent_bbox": [round(value, 6) for value in parent_bbox],
                    "pixel_bbox": list(pixel_bbox),
                    "pdf_bbox": [round(value, 6) for value in pdf_bbox],
                    "source_sha256": source_sha256,
                    "crop_pixel_sha256": crop_sha256,
                    "source_width": image.width,
                    "source_height": image.height,
                    "detector_version": MULTI_EQUATION_DETECTOR_VERSION,
                    "threshold_version": THRESHOLD_VERSION,
                    "ocr_engine_version": version,
                    "ocr_tessdata_sha256": tessdata_sha256,
                    "ocr_language": OCR_LANGUAGE,
                    "ocr_config": OCR_CONFIG,
                    "transform": [round(value, 6) for value in transform],
                }
                evidence["region_id"] = (
                    "eqregion-v1-" + _canonical_digest(evidence)[:24]
                )
                children.append(FrozenPageRasterRegionLocator.model_validate(evidence))
            return build_multi_equation_group(
                disposition=disposition,
                children=tuple(children),
            )
        except Exception:
            return None

    @staticmethod
    def _has_unowned_ink(ink: np.ndarray, lines: Sequence[Any]) -> bool:
        covered = np.zeros_like(ink, dtype=bool)
        height, width = ink.shape
        for line in lines:
            x0, y0, x1, y1 = line.bbox
            covered[
                max(0, y0 - 1) : min(height, y1 + 1),
                max(0, x0 - 1) : min(width, x1 + 1),
            ] = True
        return bool(np.any(ink & ~covered))

    @staticmethod
    def _disposition(
        lines: Sequence[Any],
    ) -> Literal["split_children", "whole_system"] | None:
        if any(line.text.lstrip().startswith(_SYSTEM_MARKERS) for line in lines):
            return "whole_system"
        centers = [(line.bbox[1] + line.bbox[3]) / 2 for line in lines]
        heights = [line.bbox[3] - line.bbox[1] for line in lines]
        max_height = max(heights)
        if max(centers) - min(centers) <= max_height:
            return "split_children"
        gaps = [
            lines[index + 1].bbox[1] - lines[index].bbox[3]
            for index in range(len(lines) - 1)
        ]
        if any(gap < 0 for gap in gaps):
            return None
        x_origins = [line.bbox[0] for line in lines]
        aligned = max(x_origins) - min(x_origins) <= max(8, max_height)
        if aligned and all(gap <= math.ceil(max_height * 1.5) for gap in gaps):
            return "whole_system"
        if all(gap >= math.ceil(max_height * 2.5) for gap in gaps):
            return "split_children"
        return None

    @staticmethod
    def _child_boxes(
        image: Image.Image,
        ink: np.ndarray,
        all_lines: Sequence[Any],
        math_lines: Sequence[Any],
    ) -> list[tuple[int, int, int, int]] | None:
        boxes: list[tuple[int, int, int, int]] = []
        total_pixels = 0
        for line in sorted(
            math_lines, key=lambda item: (item.bbox[1], item.bbox[0], item.key)
        ):
            x0, y0, x1, y1 = line.bbox
            pixel_bbox = (
                x0 - MOAT_PIXELS,
                y0 - MOAT_PIXELS,
                x1 + MOAT_PIXELS,
                y1 + MOAT_PIXELS,
            )
            if not RasterEquationRegionDetector._valid_pixel_bbox(
                pixel_bbox, image.size
            ):
                return None
            px0, py0, px1, py1 = pixel_bbox
            region = ink[py0:py1, px0:px1]
            if not np.any(region):
                return None
            border = np.concatenate(
                (region[0, :], region[-1, :], region[:, 0], region[:, -1])
            )
            if np.any(border):
                return None
            for other in boxes:
                if MultiEquationRegionDetector._overlap(pixel_bbox, other):
                    return None
            for other_line in all_lines:
                if other_line is line or other_line in math_lines:
                    continue
                if MultiEquationRegionDetector._overlap(pixel_bbox, other_line.bbox):
                    return None
            total_pixels += (px1 - px0) * (py1 - py0)
            if total_pixels > MAX_MULTI_EQUATION_VALIDATION_PIXELS:
                return None
            boxes.append(pixel_bbox)
        return boxes

    @staticmethod
    def _overlap(left: Sequence[int], right: Sequence[int]) -> bool:
        return (
            left[0] < right[2]
            and right[0] < left[2]
            and left[1] < right[3]
            and right[1] < left[3]
        )

    @staticmethod
    def _pixel_digest(image: Image.Image) -> str:
        header = f"{image.mode}|{image.width}|{image.height}|".encode("ascii")
        return hashlib.sha256(header + image.tobytes()).hexdigest()

    def revalidate_group(
        self, doc: fitz.Document, value: Any
    ) -> MultiEquationRegionGroupV1 | None:
        """Reopen and re-hash every child against the exact displayed parent."""

        try:
            group = MultiEquationRegionGroupV1.model_validate(value)
            if group.page_number > len(doc):
                return None
            page = doc[group.page_number - 1]
            from src.education.pdf_checks.image_checker import (
                _displayed_image_occurrences,
            )

            matches = [
                item
                for item in _displayed_image_occurrences(page, group.page_number)
                if item["occurrence_id"] == group.parent_occurrence_id
            ]
            if len(matches) != 1:
                return None
            occurrence = matches[0]
            if (
                occurrence["image_xref"] != group.image_xref
                or occurrence["image_index"] != group.image_index
                or occurrence["occurrence_ordinal"] != group.occurrence_ordinal
            ):
                return None
            eligible = self._base._eligible_page(page, occurrence)
            if eligible is None:
                return None
            parent_bbox, _ = eligible
            loaded = self._base._load_source(doc, group.image_xref)
            if loaded is None:
                return None
            image, source_sha256 = loaded
            if source_sha256 != group.source_sha256 or image.size != (
                group.source_width,
                group.source_height,
            ):
                return None
            total_pixels = 0
            for child in group.children:
                crop = image.crop(child.pixel_bbox)
                total_pixels += crop.width * crop.height
                if (
                    total_pixels > MAX_MULTI_EQUATION_VALIDATION_PIXELS
                    or self._pixel_digest(crop) != child.crop_pixel_sha256
                ):
                    return None
                mapped = self._base._map_to_pdf_bbox(
                    child.pixel_bbox, image.size, parent_bbox
                )
                if mapped is None or any(
                    abs(actual - wanted) > 1e-6
                    for actual, wanted in zip(mapped, child.pdf_bbox)
                ):
                    return None
            return group
        except Exception:
            return None


__all__ = [
    "MAX_MULTI_EQUATION_PAGES_PER_DOCUMENT",
    "MAX_MULTI_EQUATION_VALIDATION_PIXELS",
    "MULTI_EQUATION_DETECTOR_VERSION",
    "MultiEquationRegionDetector",
]
