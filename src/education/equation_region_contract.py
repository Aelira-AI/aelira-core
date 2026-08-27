"""Typed durable provenance for one equation crop within a page raster."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_IMAGE_PIXELS = 25_000_000
_REGION_ID_PREFIX = "eqregion-v1-"
_LOCATOR_FIELDS = (
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
    "region_id",
)


class PageRasterRegionLocator(BaseModel):
    """Exact, bounded identity of a crop inside a displayed page image."""

    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["page_raster_region"]
    page_number: int = Field(ge=1, le=25_000_000, strict=True)
    parent_occurrence_id: str = Field(pattern=r"^imgocc-v1-[0-9a-f]{24}$")
    image_xref: int = Field(ge=1, le=25_000_000, strict=True)
    image_index: int = Field(ge=0, le=25_000_000, strict=True)
    occurrence_ordinal: int = Field(ge=0, le=25_000_000, strict=True)
    parent_bbox: tuple[float, float, float, float]
    pixel_bbox: tuple[int, int, int, int]
    pdf_bbox: tuple[float, float, float, float]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    crop_pixel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_width: int = Field(ge=1, le=_MAX_IMAGE_PIXELS, strict=True)
    source_height: int = Field(ge=1, le=_MAX_IMAGE_PIXELS, strict=True)
    detector_version: str = Field(min_length=1, max_length=128)
    threshold_version: str = Field(min_length=1, max_length=128)
    ocr_engine_version: str = Field(min_length=1, max_length=128)
    ocr_tessdata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ocr_language: str = Field(min_length=1, max_length=32)
    ocr_config: str = Field(min_length=1, max_length=128)
    transform: tuple[float, float, float, float, float, float]
    region_id: str = Field(pattern=r"^eqregion-v1-[0-9a-f]{24}$")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PageRasterRegionLocator":
        """Validate an exact locator mapping; unknown fields are rejected."""
        return cls.model_validate(value)

    @classmethod
    def from_evidence(cls, value: Mapping[str, Any]) -> "PageRasterRegionLocator":
        """Extract the locator allowlist from a larger detector evidence envelope."""
        try:
            locator = {field: value[field] for field in _LOCATOR_FIELDS}
        except KeyError as exc:
            raise ValueError(f"region evidence is missing {exc.args[0]}") from exc
        return cls.model_validate(locator)

    @field_validator("parent_bbox", "pdf_bbox", "transform", mode="before")
    @classmethod
    def _finite_numeric_sequence(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
            or abs(float(item)) > 25_000_000
            for item in value
        ):
            raise ValueError("region geometry must contain bounded finite numbers")
        return value

    @field_validator("pixel_bbox", mode="before")
    @classmethod
    def _integer_pixel_bbox(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, int) or isinstance(item, bool) for item in value
        ):
            raise ValueError("pixel_bbox must contain integers")
        return value

    @field_validator(
        "parent_occurrence_id",
        "detector_version",
        "threshold_version",
        "ocr_engine_version",
        "ocr_language",
        "ocr_config",
    )
    @classmethod
    def _bounded_printable_text(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("region provenance text must be trimmed and printable")
        return value

    @model_validator(mode="after")
    def _validate_geometry_and_identity(self) -> "PageRasterRegionLocator":
        if self.source_width * self.source_height > _MAX_IMAGE_PIXELS:
            raise ValueError("source image exceeds the bounded pixel budget")
        px0, py0, px1, py1 = self.pixel_bbox
        if not (
            0 <= px0 < px1 <= self.source_width and 0 <= py0 < py1 <= self.source_height
        ):
            raise ValueError("pixel_bbox falls outside the source image")
        parent_x0, parent_y0, parent_x1, parent_y1 = self.parent_bbox
        pdf_x0, pdf_y0, pdf_x1, pdf_y1 = self.pdf_bbox
        if not parent_x0 < parent_x1 or not parent_y0 < parent_y1:
            raise ValueError("parent_bbox must have positive area")
        if not pdf_x0 < pdf_x1 or not pdf_y0 < pdf_y1:
            raise ValueError("pdf_bbox must have positive area")
        tolerance = 1e-6
        if not (
            parent_x0 - tolerance <= pdf_x0 < pdf_x1 <= parent_x1 + tolerance
            and parent_y0 - tolerance <= pdf_y0 < pdf_y1 <= parent_y1 + tolerance
        ):
            raise ValueError("pdf_bbox falls outside parent_bbox")
        mapped_pdf_bbox = (
            parent_x0 + (px0 / self.source_width) * (parent_x1 - parent_x0),
            parent_y0 + (py0 / self.source_height) * (parent_y1 - parent_y0),
            parent_x0 + (px1 / self.source_width) * (parent_x1 - parent_x0),
            parent_y0 + (py1 / self.source_height) * (parent_y1 - parent_y0),
        )
        if any(
            abs(current - expected) > tolerance
            for current, expected in zip(self.pdf_bbox, mapped_pdf_bbox)
        ):
            raise ValueError("pdf_bbox does not match the source pixel crop")
        expected_transform = (
            parent_x1 - parent_x0,
            0.0,
            0.0,
            parent_y1 - parent_y0,
            parent_x0,
            parent_y0,
        )
        if any(
            abs(current - expected) > 1e-3
            for current, expected in zip(self.transform, expected_transform)
        ):
            raise ValueError("transform does not match parent_bbox")
        if self.region_id != _REGION_ID_PREFIX + self.canonical_digest()[:24]:
            raise ValueError("region_id does not match canonical provenance")
        return self

    def canonical_identity(self) -> dict[str, Any]:
        """Return the exact detector identity fields, excluding the derived ID."""
        return self.model_dump(mode="json", exclude={"region_id"})

    def canonical_digest(self) -> str:
        """Reproduce the detector's stable canonical SHA-256 identity."""
        encoded = json.dumps(
            self.canonical_identity(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def canonical_region_locator(value: Any) -> dict[str, Any]:
    """Validate and return only the bounded durable locator allowlist."""
    return PageRasterRegionLocator.model_validate(value).model_dump(mode="json")


def valid_region_locator(value: Any) -> bool:
    """Return whether a raw durable locator satisfies the strict contract."""
    try:
        PageRasterRegionLocator.model_validate(value)
    except (TypeError, ValueError):
        return False
    return True
