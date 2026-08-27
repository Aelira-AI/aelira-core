"""Fail-closed printed-equation region discovery for full-page raster scans.

The detector emits bounded review-gated candidates. It does not itself classify
a crop as mathematics or send page content to an AI provider.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import fitz
import numpy as np
import pytesseract
from PIL import Image

from src.education.math_contracts import SCANNED_EQUATION_REGION_ISSUE_TYPE

logger = logging.getLogger(__name__)

DETECTOR_VERSION = "raster-equation-region-v1"
THRESHOLD_VERSION = "grayscale-lt245-v1"
OCR_CONFIG = "--oem 3 --psm 6"
OCR_LANGUAGE = "eng"
OCR_TIMEOUT_SECONDS = 15
MAX_IMAGE_PIXELS = 25_000_000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_OCR_BOXES = 2_000
MAX_CONNECTED_COMPONENTS = 10_000
MAX_COMPONENT_RUNS = 250_000
MAX_CANDIDATES_PER_PAGE = 8
MAX_CANDIDATES_PER_DOCUMENT = 32
MAX_REGION_PAGES_PER_DOCUMENT = 8
MIN_PAGE_COVERAGE = 0.98
MIN_OCR_CONFIDENCE = 70.0
MIN_CANDIDATE_OCR_CONFIDENCE = 60.0
MOAT_PIXELS = 4
SUPPORTED_OCR_VERSIONS = frozenset({"5.3.0", "5.3.4", "5.5.1"})
SUPPORTED_ENG_TESSDATA_SHA256 = frozenset(
    {"7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"}
)

_CUE_LINE = re.compile(
    r"^(?:equation|formula)(?:\s+(?:\(?[a-z]?\d+(?:[.\-]\d+)*\)?|[a-z]))?\s*:?$",
    re.IGNORECASE,
)
_MATH_SIGNAL = re.compile(r"[=<>≤≥≠±×÷+*/^√∫∑∏]|(?<!\w)-(?!\w)")
_REGION_ID_FIELDS = (
    "source_kind",
    "page_number",
    "parent_occurrence_id",
    "image_xref",
    "image_index",
    "occurrence_ordinal",
    "parent_bbox",
    "pixel_bbox",
    "pdf_bbox",
    "source_sha256",
    "crop_pixel_sha256",
    "source_width",
    "source_height",
    "detector_version",
    "threshold_version",
    "ocr_engine_version",
    "ocr_tessdata_sha256",
    "ocr_language",
    "ocr_config",
    "transform",
)


@dataclass(frozen=True)
class _OCRLine:
    key: Tuple[int, int, int]
    text: str
    bbox: Tuple[int, int, int, int]
    confidence: float
    tokens: Tuple[str, ...]


@dataclass(frozen=True)
class ResolvedRasterEquationRegion:
    """Exact revalidated crop pixels from one immutable page raster."""

    crop_mode: str
    crop_size: Tuple[int, int]
    crop_pixels: bytes
    source_sha256: str
    crop_pixel_sha256: str


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pixel_digest(image: Image.Image) -> str:
    header = f"{image.mode}|{image.width}|{image.height}|".encode("ascii")
    return hashlib.sha256(header + image.tobytes()).hexdigest()


def _default_tessdata_sha256() -> str:
    """Hash the installed English model without exposing its local path."""
    candidates: List[Path] = []
    configured = os.environ.get("TESSDATA_PREFIX")
    if configured:
        root = Path(configured)
        candidates.extend((root / "eng.traineddata", root / "tessdata/eng.traineddata"))
    candidates.extend(
        (
            Path("/opt/homebrew/share/tessdata/eng.traineddata"),
            Path("/usr/local/share/tessdata/eng.traineddata"),
            Path("/usr/share/tesseract-ocr/5/tessdata/eng.traineddata"),
            Path("/usr/share/tesseract-ocr/4.00/tessdata/eng.traineddata"),
        )
    )
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_size <= MAX_SOURCE_BYTES:
                return hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            continue
    return ""


def _float_bbox(values: Sequence[Any]) -> Optional[Tuple[float, float, float, float]]:
    try:
        bbox = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    if (
        len(bbox) != 4
        or not all(math.isfinite(value) for value in bbox)
        or bbox[2] <= bbox[0]
        or bbox[3] <= bbox[1]
    ):
        return None
    return bbox


def _bounded_component_count(ink: np.ndarray) -> Optional[int]:
    """Count exact 8-connected ink components under a deterministic work cap."""
    if ink.ndim != 2:
        return None
    parents: List[int] = []
    previous: List[Tuple[int, int, int]] = []

    def find(component: int) -> int:
        while parents[component] != component:
            parents[component] = parents[parents[component]]
            component = parents[component]
        return component

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for raw_row in ink:
        row = np.asarray(raw_row, dtype=bool)
        starts = np.flatnonzero(row & ~np.concatenate(([False], row[:-1])))
        ends = np.flatnonzero(row & ~np.concatenate((row[1:], [False])))
        if len(starts) != len(ends):
            return None
        if len(parents) + len(starts) > MAX_COMPONENT_RUNS:
            return None

        current: List[Tuple[int, int, int]] = []
        previous_index = 0
        for start_value, end_value in zip(starts, ends):
            start = int(start_value)
            end = int(end_value)
            component = len(parents)
            parents.append(component)
            while (
                previous_index < len(previous)
                and previous[previous_index][1] < start - 1
            ):
                previous_index += 1
            overlap_index = previous_index
            while (
                overlap_index < len(previous) and previous[overlap_index][0] <= end + 1
            ):
                union(component, previous[overlap_index][2])
                overlap_index += 1
            current.append((start, end, component))
        previous = current

    roots = {find(component) for component in range(len(parents))}
    return len(roots)


class RasterEquationRegionDetector:
    """Discover one provable equation region on an eligible scan page."""

    def __init__(
        self,
        *,
        ocr_data: Optional[Callable[..., Mapping[str, Sequence[Any]]]] = None,
        ocr_version: Optional[Callable[[], Any]] = None,
        ocr_tessdata_sha256: Optional[Callable[[], str]] = None,
    ) -> None:
        self._ocr_data = ocr_data or self._default_ocr_data
        self._ocr_version = ocr_version or pytesseract.get_tesseract_version
        self._ocr_tessdata_sha256 = ocr_tessdata_sha256 or _default_tessdata_sha256
        self._ocr_identity_resolved = False
        self._ocr_identity: Optional[Tuple[str, str]] = None

    @staticmethod
    def _default_ocr_data(
        image: Image.Image, **kwargs: Any
    ) -> Mapping[str, Sequence[Any]]:
        return pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT, **kwargs
        )

    def find_regions(
        self,
        doc: fitz.Document,
        page: fitz.Page,
        occurrence: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        """Return either one bounded review-gated candidate or no finding."""
        try:
            eligible = self._eligible_page(page, occurrence)
        except Exception:
            return []
        if eligible is None:
            return []
        page_bbox, transform = eligible

        source = self._load_source(doc, int(occurrence["image_xref"]))
        if source is None:
            return []
        image, source_sha256 = source

        ocr_identity = self._resolve_ocr_identity()
        if ocr_identity is None:
            return []
        version, tessdata_sha256 = ocr_identity

        try:
            raw_data = self._ocr_data(
                image,
                lang=OCR_LANGUAGE,
                config=OCR_CONFIG,
                timeout=OCR_TIMEOUT_SECONDS,
            )
            lines = self._parse_lines(raw_data, image.size)
        except Exception:
            return []
        if lines is None:
            return []

        region = self._select_region(image, lines)
        if region is None:
            return []
        pixel_bbox, crop_sha256 = region
        pdf_bbox = self._map_to_pdf_bbox(pixel_bbox, image.size, page_bbox)
        if pdf_bbox is None:
            return []

        evidence: Dict[str, Any] = {
            "source_kind": "page_raster_region",
            "page_number": int(occurrence["page_number"]),
            "parent_occurrence_id": str(occurrence["occurrence_id"]),
            "image_xref": int(occurrence["image_xref"]),
            "image_index": int(occurrence["image_index"]),
            "occurrence_ordinal": int(occurrence["occurrence_ordinal"]),
            "parent_bbox": [round(value, 6) for value in page_bbox],
            "pixel_bbox": list(pixel_bbox),
            "pdf_bbox": [round(value, 6) for value in pdf_bbox],
            "source_sha256": source_sha256,
            "crop_pixel_sha256": crop_sha256,
            "source_width": image.width,
            "source_height": image.height,
            "detector_version": DETECTOR_VERSION,
            "threshold_version": THRESHOLD_VERSION,
            "ocr_engine_version": version,
            "ocr_tessdata_sha256": tessdata_sha256,
            "ocr_language": OCR_LANGUAGE,
            "ocr_config": OCR_CONFIG,
            "transform": [round(value, 6) for value in transform],
        }
        evidence["region_id"] = "eqregion-v1-" + _canonical_digest(evidence)[:24]
        metadata = {
            "issue_type": SCANNED_EQUATION_REGION_ISSUE_TYPE,
            "rule": "WCAG 1.1.1",
            **evidence,
        }
        return [
            {
                "id": evidence["region_id"],
                "category": "structure",
                "severity": "high",
                "rule": "WCAG 1.1.1",
                "message": "Printed equation region requires accessible math association",
                "impact": "Screen readers cannot interpret equation pixels as mathematical content",
                "location": (
                    f"Page {evidence['page_number']}, region {evidence['region_id']}"
                ),
                "element": "Scanned equation region",
                "suggested_fix": (
                    "Use exact subregion Formula association and require explicit "
                    "human approval before publication"
                ),
                "issue_type": SCANNED_EQUATION_REGION_ISSUE_TYPE,
                "page_number": evidence["page_number"],
                "bbox": evidence["pdf_bbox"],
                "region_id": evidence["region_id"],
                "metadata": metadata,
            }
        ]

    def _resolve_ocr_identity(self) -> Optional[Tuple[str, str]]:
        if self._ocr_identity_resolved:
            return self._ocr_identity
        self._ocr_identity_resolved = True
        try:
            version = str(self._ocr_version()).strip()
            tessdata_sha256 = str(self._ocr_tessdata_sha256()).strip().lower()
        except Exception:
            return None
        if (
            version not in SUPPORTED_OCR_VERSIONS
            or tessdata_sha256 not in SUPPORTED_ENG_TESSDATA_SHA256
        ):
            return None
        self._ocr_identity = (version, tessdata_sha256)
        return self._ocr_identity

    def resolve_evidence(
        self, doc: fitz.Document, metadata: Mapping[str, Any]
    ) -> Optional[ResolvedRasterEquationRegion]:
        """Re-resolve immutable source and return only the proven crop pixels."""
        try:
            page_number = int(metadata["page_number"])
            if page_number < 1 or page_number > len(doc):
                return None
            page = doc[page_number - 1]
            from src.education.pdf_checks.image_checker import (
                _displayed_image_occurrences,
            )

            matches = [
                item
                for item in _displayed_image_occurrences(page, page_number)
                if item["occurrence_id"] == metadata.get("parent_occurrence_id")
            ]
            if len(matches) != 1:
                return None
            try:
                eligible = self._eligible_page(page, matches[0])
            except Exception:
                return None
            if eligible is None:
                return None
            page_bbox, transform = eligible
            source = self._load_source(doc, int(matches[0]["image_xref"]))
            if source is None:
                return None
            image, source_sha256 = source
            if source_sha256 != metadata.get("source_sha256"):
                return None
            if metadata.get("parent_bbox") != [round(value, 6) for value in page_bbox]:
                return None
            if metadata.get("transform") != [round(value, 6) for value in transform]:
                return None
            if metadata.get("source_width") != image.width:
                return None
            if metadata.get("source_height") != image.height:
                return None
            for key in ("image_xref", "image_index", "occurrence_ordinal"):
                if metadata.get(key) != matches[0][key]:
                    return None
            if metadata.get("source_kind") != "page_raster_region":
                return None
            if metadata.get("detector_version") != DETECTOR_VERSION:
                return None
            if metadata.get("threshold_version") != THRESHOLD_VERSION:
                return None
            if metadata.get("ocr_language") != OCR_LANGUAGE:
                return None
            if metadata.get("ocr_config") != OCR_CONFIG:
                return None
            ocr_engine_version = metadata.get("ocr_engine_version")
            if ocr_engine_version not in SUPPORTED_OCR_VERSIONS:
                return None
            if metadata.get("ocr_tessdata_sha256") not in SUPPORTED_ENG_TESSDATA_SHA256:
                return None
            raw_bbox = metadata.get("pixel_bbox")
            if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
                return None
            if any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in raw_bbox
            ):
                return None
            pixel_bbox = tuple(int(value) for value in raw_bbox)
            if not self._valid_pixel_bbox(pixel_bbox, image.size):
                return None
            crop = image.crop(pixel_bbox)
            crop_pixel_sha256 = _pixel_digest(crop)
            if crop_pixel_sha256 != metadata.get("crop_pixel_sha256"):
                return None
            mapped = self._map_to_pdf_bbox(pixel_bbox, image.size, page_bbox)
            stored_pdf_bbox = _float_bbox(metadata.get("pdf_bbox", ()))
            if (
                mapped is None
                or stored_pdf_bbox is None
                or not all(
                    abs(left - right) <= 1e-6
                    for left, right in zip(mapped, stored_pdf_bbox)
                )
            ):
                return None
            try:
                identity = {key: metadata[key] for key in _REGION_ID_FIELDS}
            except KeyError:
                return None
            if metadata.get("region_id") != (
                "eqregion-v1-" + _canonical_digest(identity)[:24]
            ):
                return None
            return ResolvedRasterEquationRegion(
                crop_mode=crop.mode,
                crop_size=crop.size,
                crop_pixels=crop.tobytes(),
                source_sha256=source_sha256,
                crop_pixel_sha256=crop_pixel_sha256,
            )
        except Exception:
            return None

    def validate_evidence(self, file_path: str, metadata: Mapping[str, Any]) -> bool:
        """Compatibility wrapper for path-based evidence validation."""
        try:
            with fitz.open(file_path) as doc:
                if self.resolve_evidence(doc, metadata) is None:
                    return False
            return True
        except Exception:
            return False

    def _eligible_page(
        self, page: fitz.Page, occurrence: Mapping[str, Any]
    ) -> Optional[
        Tuple[
            Tuple[float, float, float, float],
            Tuple[float, float, float, float, float, float],
        ]
    ]:
        if int(getattr(page, "rotation", 0) or 0) != 0:
            return None
        page_rect = page.rect
        page_bbox = _float_bbox(occurrence.get("bbox", ()))
        if page_bbox is None or page_rect.is_empty:
            return None
        page_area = page_rect.width * page_rect.height
        image_area = (page_bbox[2] - page_bbox[0]) * (page_bbox[3] - page_bbox[1])
        intersection = fitz.Rect(page_bbox) & page_rect
        visible_area = max(0.0, intersection.width) * max(0.0, intersection.height)
        if (
            page_area <= 0
            or visible_area / page_area < MIN_PAGE_COVERAGE
            or image_area / page_area < MIN_PAGE_COVERAGE
            or image_area / page_area > 1.02
        ):
            return None

        infos = list(page.get_image_info(xrefs=True))
        if len(infos) != 1:
            return None
        info = infos[0]
        if int(info.get("xref") or 0) != int(occurrence.get("image_xref") or 0):
            return None
        info_bbox = _float_bbox(info.get("bbox", ()))
        if info_bbox is None or any(
            abs(left - right) > 1e-3 for left, right in zip(info_bbox, page_bbox)
        ):
            return None
        try:
            transform = tuple(float(value) for value in info.get("transform", ()))
        except (TypeError, ValueError):
            return None
        if (
            len(transform) != 6
            or not all(math.isfinite(value) for value in transform)
            or transform[0] <= 0
            or transform[3] <= 0
            or abs(transform[1]) > 1e-6
            or abs(transform[2]) > 1e-6
            or abs(transform[0] - (page_bbox[2] - page_bbox[0])) > 1e-3
            or abs(transform[3] - (page_bbox[3] - page_bbox[1])) > 1e-3
            or abs(transform[4] - page_bbox[0]) > 1e-3
            or abs(transform[5] - page_bbox[1]) > 1e-3
        ):
            return None
        if page.get_drawings():
            return None
        if page.get_text("words"):
            return None
        resources = [
            item
            for item in page.get_images(full=True)
            if int(item[0]) == int(occurrence["image_xref"])
        ]
        if len(resources) != 1:
            return None
        resource = resources[0]
        if len(resource) > 1 and int(resource[1] or 0) != 0:
            return None
        if len(resource) > 9 and int(resource[9] or 0) != 0:
            return None
        return page_bbox, transform  # type: ignore[return-value]

    @staticmethod
    def _load_source(
        doc: fitz.Document, image_xref: int
    ) -> Optional[Tuple[Image.Image, str]]:
        try:
            extracted = doc.extract_image(image_xref)
            source_bytes = extracted.get("image", b"")
            if (
                not isinstance(source_bytes, bytes)
                or not source_bytes
                or len(source_bytes) > MAX_SOURCE_BYTES
            ):
                return None
            source_sha256 = hashlib.sha256(source_bytes).hexdigest()
            with Image.open(BytesIO(source_bytes)) as opened:
                if getattr(opened, "n_frames", 1) != 1:
                    return None
                if opened.width <= 0 or opened.height <= 0:
                    return None
                if opened.width * opened.height > MAX_IMAGE_PIXELS:
                    return None
                opened.load()
                image = opened.copy()
            return image, source_sha256
        except Exception:
            return None

    @staticmethod
    def _parse_lines(
        data: Mapping[str, Sequence[Any]], image_size: Tuple[int, int]
    ) -> Optional[List[_OCRLine]]:
        required = (
            "text",
            "conf",
            "left",
            "top",
            "width",
            "height",
            "block_num",
            "par_num",
            "line_num",
        )
        if any(key not in data for key in required):
            return None
        lengths = {len(data[key]) for key in required}
        if len(lengths) != 1:
            return None
        count = lengths.pop()
        if count > MAX_OCR_BOXES:
            return None
        width, height = image_size
        grouped: Dict[
            Tuple[int, int, int], List[Tuple[str, float, int, int, int, int]]
        ] = {}
        total_text_length = 0
        for index in range(count):
            text = str(data["text"][index]).strip()
            if not text:
                continue
            total_text_length += len(text)
            if len(text) > 64 or total_text_length > 4096:
                return None
            try:
                confidence = float(data["conf"][index])
                left = int(data["left"][index])
                top = int(data["top"][index])
                box_width = int(data["width"][index])
                box_height = int(data["height"][index])
                key = (
                    int(data["block_num"][index]),
                    int(data["par_num"][index]),
                    int(data["line_num"][index]),
                )
            except (TypeError, ValueError, OverflowError):
                return None
            if (
                confidence < 0
                or confidence > 100
                or left < 0
                or top < 0
                or box_width <= 0
                or box_height <= 0
                or left + box_width > width
                or top + box_height > height
            ):
                return None
            grouped.setdefault(key, []).append(
                (text, confidence, left, top, left + box_width, top + box_height)
            )
        lines: List[_OCRLine] = []
        for key, words in grouped.items():
            words.sort(key=lambda item: (item[2], item[3], item[0]))
            bbox = (
                min(item[2] for item in words),
                min(item[3] for item in words),
                max(item[4] for item in words),
                max(item[5] for item in words),
            )
            lines.append(
                _OCRLine(
                    key=key,
                    text=" ".join(item[0] for item in words),
                    bbox=bbox,
                    confidence=min(item[1] for item in words),
                    tokens=tuple(item[0] for item in words),
                )
            )
        lines.sort(key=lambda line: (line.bbox[1], line.bbox[0], line.key))
        return lines

    @staticmethod
    def _select_region(
        image: Image.Image, lines: Sequence[_OCRLine]
    ) -> Optional[Tuple[Tuple[int, int, int, int], str]]:
        cues = [
            (index, line)
            for index, line in enumerate(lines)
            if line.confidence >= MIN_OCR_CONFIDENCE and _CUE_LINE.fullmatch(line.text)
        ]
        if len(cues) != 1:
            return None
        cue_index, cue = cues[0]
        following = [
            line for line in lines[cue_index + 1 :] if line.bbox[1] >= cue.bbox[3]
        ]
        if not following:
            return None
        candidate = following[0]
        cue_height = cue.bbox[3] - cue.bbox[1]
        gap = candidate.bbox[1] - cue.bbox[3]
        if gap < 0 or gap > max(24, cue_height * 3):
            return None
        if (
            candidate.confidence < MIN_CANDIDATE_OCR_CONFIDENCE
            or len(candidate.tokens) > 16
        ):
            return None
        prose_words = sum(
            1 for token in candidate.tokens if token.isalpha() and len(token) > 1
        )
        if prose_words > 2 or not _MATH_SIGNAL.search(candidate.text):
            return None
        nearby_math = [
            line
            for line in following[1:]
            if line.bbox[1] - candidate.bbox[3]
            <= max(24, candidate.bbox[3] - candidate.bbox[1])
            and _MATH_SIGNAL.search(line.text)
        ]
        if nearby_math:
            return None

        gray = np.asarray(image.convert("L"))
        ink = gray < 245
        component_count = _bounded_component_count(ink)
        if component_count is None or component_count > MAX_CONNECTED_COMPONENTS:
            return None

        x0, y0, x1, y1 = candidate.bbox
        line_height = y1 - y0
        x_padding = max(12, (x1 - x0) // 8)
        y_padding = max(8, line_height)
        corridor = (
            max(0, x0 - x_padding),
            max(0, y0 - y_padding),
            min(image.width, x1 + x_padding),
            min(image.height, y1 + y_padding),
        )
        cx0, cy0, cx1, cy1 = corridor
        # Tesseract's bottom coordinate can exclude one final antialiased row.
        # Tolerate that row only inside the cue's own narrow x-envelope; other
        # pixels could be detached equation content and must fail closed.
        gap_start = cue.bbox[3]
        tolerance_end = min(cy0, gap_start + 1)
        edge_row = ink[gap_start:tolerance_end, :]
        if np.any(edge_row):
            if gap_start <= 0:
                return None
            cue_left = max(0, cue.bbox[0] - 1)
            cue_right = min(image.width, cue.bbox[2] + 1)
            if np.any(edge_row[:, :cue_left]) or np.any(edge_row[:, cue_right:]):
                return None
            prior_row = np.zeros(image.width, dtype=bool)
            prior_row[cue_left:cue_right] = ink[gap_start - 1, cue_left:cue_right]
            connected = prior_row.copy()
            connected[1:] |= prior_row[:-1]
            connected[:-1] |= prior_row[1:]
            if np.any(edge_row[0] & ~connected):
                return None
        if cy0 > tolerance_end and np.any(ink[tolerance_end:cy0, :]):
            return None
        for line in lines:
            if line is cue or line is candidate:
                continue
            lx0, ly0, lx1, ly1 = line.bbox
            if lx0 < cx1 and lx1 > cx0 and ly0 < cy1 and ly1 > cy0:
                return None
        horizontal_band = ink[cy0:cy1, :]
        if np.any(horizontal_band[:, :cx0]) or np.any(horizontal_band[:, cx1:]):
            return None
        below_limit = min(image.height, cy1 + (2 * line_height))
        if below_limit > cy1 and np.any(ink[cy1:below_limit, :]):
            return None
        corridor_ink = ink[cy0:cy1, cx0:cx1]
        rows, cols = np.nonzero(corridor_ink)
        if len(rows) == 0:
            return None
        if (
            np.any(rows == 0)
            or np.any(cols == 0)
            or np.any(rows == corridor_ink.shape[0] - 1)
            or np.any(cols == corridor_ink.shape[1] - 1)
        ):
            return None
        ix0 = cx0 + int(cols.min())
        iy0 = cy0 + int(rows.min())
        ix1 = cx0 + int(cols.max()) + 1
        iy1 = cy0 + int(rows.max()) + 1
        content_bbox = (
            min(ix0, x0),
            min(iy0, y0),
            max(ix1, x1),
            max(iy1, y1),
        )
        pixel_bbox = (
            content_bbox[0] - MOAT_PIXELS,
            content_bbox[1] - MOAT_PIXELS,
            content_bbox[2] + MOAT_PIXELS,
            content_bbox[3] + MOAT_PIXELS,
        )
        if not RasterEquationRegionDetector._valid_pixel_bbox(pixel_bbox, image.size):
            return None
        px0, py0, px1, py1 = pixel_bbox
        ring = ink[py0:py1, px0:px1].copy()
        inner_x0 = content_bbox[0] - px0
        inner_y0 = content_bbox[1] - py0
        inner_x1 = content_bbox[2] - px0
        inner_y1 = content_bbox[3] - py0
        ring[inner_y0:inner_y1, inner_x0:inner_x1] = False
        if np.any(ring):
            return None
        crop = image.crop(pixel_bbox)
        return pixel_bbox, _pixel_digest(crop)

    @staticmethod
    def _valid_pixel_bbox(
        bbox: Tuple[int, int, int, int], image_size: Tuple[int, int]
    ) -> bool:
        x0, y0, x1, y1 = bbox
        width, height = image_size
        return 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height

    @staticmethod
    def _map_to_pdf_bbox(
        pixel_bbox: Tuple[int, int, int, int],
        image_size: Tuple[int, int],
        parent_bbox: Tuple[float, float, float, float],
    ) -> Optional[Tuple[float, float, float, float]]:
        if not RasterEquationRegionDetector._valid_pixel_bbox(pixel_bbox, image_size):
            return None
        width, height = image_size
        x0, y0, x1, y1 = pixel_bbox
        px0, py0, px1, py1 = parent_bbox
        return (
            px0 + (x0 / width) * (px1 - px0),
            py0 + (y0 / height) * (py1 - py0),
            px0 + (x1 / width) * (px1 - px0),
            py0 + (y1 / height) * (py1 - py0),
        )


def is_full_page_raster_occurrence(
    page: fitz.Page, occurrence: Mapping[str, Any]
) -> bool:
    """Return whether an occurrence covers nearly all of its page."""
    bbox = _float_bbox(occurrence.get("bbox", ()))
    if bbox is None or page.rect.is_empty:
        return False
    page_area = page.rect.width * page.rect.height
    image_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    return page_area > 0 and image_area / page_area >= MIN_PAGE_COVERAGE
