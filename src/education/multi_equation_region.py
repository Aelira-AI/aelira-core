"""Typed immutable evidence for ordered equation regions in one page raster."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.education.canonical_json import canonical_sha256
from src.education.visual_semantic_contract import FrozenPageRasterRegionLocator

MAX_MULTI_EQUATION_CHILDREN = 8
MAX_MULTI_EQUATION_CHILD_PIXELS = 4_000_000
MAX_MULTI_EQUATION_GROUP_PIXELS = 12_000_000
MULTI_EQUATION_BUDGET_VERSION = "multi-equation-budget-v1"


class MultiEquationRegionRejected(ValueError):
    """A proposed group cannot prove exact bounded ownership."""


class MultiEquationRegionGroupV1(BaseModel):
    """One parent raster and its complete ordered equation-region group."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    group_kind: Literal["multi_equation_region_group_v1"]
    page_number: int = Field(ge=1, le=25_000_000, strict=True)
    parent_occurrence_id: str = Field(pattern=r"^imgocc-v1-[0-9a-f]{24}$")
    image_xref: int = Field(ge=1, le=25_000_000, strict=True)
    image_index: int = Field(ge=0, le=25_000_000, strict=True)
    occurrence_ordinal: int = Field(ge=0, le=25_000_000, strict=True)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_width: int = Field(ge=1, le=25_000_000, strict=True)
    source_height: int = Field(ge=1, le=25_000_000, strict=True)
    disposition: Literal["split_children", "whole_system"]
    budget_version: Literal["multi-equation-budget-v1"]
    children: tuple[FrozenPageRasterRegionLocator, ...] = Field(
        min_length=2, max_length=MAX_MULTI_EQUATION_CHILDREN
    )
    group_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    group_id: str = Field(pattern=r"^eqgroup-v1-[0-9a-f]{24}$")

    @model_validator(mode="after")
    def _validate_group_identity(self) -> "MultiEquationRegionGroupV1":
        if self.source_width * self.source_height > 25_000_000:
            raise ValueError("multi-equation source exceeds pixel budget")
        expected_parent = (
            self.page_number,
            self.parent_occurrence_id,
            self.image_xref,
            self.image_index,
            self.occurrence_ordinal,
            self.source_sha256,
            self.source_width,
            self.source_height,
        )
        region_ids: set[str] = set()
        total_pixels = 0
        previous_key: tuple[int, int, int, int, str] | None = None
        boxes: list[tuple[int, int, int, int]] = []
        for child in self.children:
            child_parent = (
                child.page_number,
                child.parent_occurrence_id,
                child.image_xref,
                child.image_index,
                child.occurrence_ordinal,
                child.source_sha256,
                child.source_width,
                child.source_height,
            )
            if child_parent != expected_parent:
                raise ValueError("multi-equation child parent identity differs")
            if child.region_id in region_ids:
                raise ValueError("multi-equation child identity is duplicated")
            region_ids.add(child.region_id)
            x0, y0, x1, y1 = child.pixel_bbox
            pixels = (x1 - x0) * (y1 - y0)
            if pixels > MAX_MULTI_EQUATION_CHILD_PIXELS:
                raise ValueError("multi-equation child exceeds pixel budget")
            total_pixels += pixels
            key = (y0, x0, y1, x1, child.region_id)
            if previous_key is not None and key <= previous_key:
                raise ValueError("multi-equation children are not in reading order")
            previous_key = key
            for other_x0, other_y0, other_x1, other_y1 in boxes:
                if x0 < other_x1 and other_x0 < x1 and y0 < other_y1 and other_y0 < y1:
                    raise ValueError("multi-equation child regions overlap")
            boxes.append(child.pixel_bbox)
        if total_pixels > MAX_MULTI_EQUATION_GROUP_PIXELS:
            raise ValueError("multi-equation group exceeds pixel budget")
        expected_digest = canonical_sha256(self.canonical_identity())
        if self.group_sha256 != expected_digest:
            raise ValueError("multi-equation group digest differs")
        if self.group_id != "eqgroup-v1-" + expected_digest[:24]:
            raise ValueError("multi-equation group id differs")
        return self

    def canonical_identity(self) -> dict[str, object]:
        """Return every passive group field except its two derived identities."""

        return self.model_dump(mode="json", exclude={"group_sha256", "group_id"})


def build_multi_equation_group(
    *,
    disposition: Literal["split_children", "whole_system"],
    children: tuple[FrozenPageRasterRegionLocator, ...],
) -> MultiEquationRegionGroupV1:
    """Build one exact group from children already sorted in reading order."""

    if not children:
        raise MultiEquationRegionRejected("multi_equation_children_missing")
    first = children[0]
    fields: dict[str, object] = {
        "group_kind": "multi_equation_region_group_v1",
        "page_number": first.page_number,
        "parent_occurrence_id": first.parent_occurrence_id,
        "image_xref": first.image_xref,
        "image_index": first.image_index,
        "occurrence_ordinal": first.occurrence_ordinal,
        "source_sha256": first.source_sha256,
        "source_width": first.source_width,
        "source_height": first.source_height,
        "disposition": disposition,
        "budget_version": MULTI_EQUATION_BUDGET_VERSION,
        "children": [child.model_dump(mode="json") for child in children],
    }
    digest = canonical_sha256(fields)
    fields["group_sha256"] = digest
    fields["group_id"] = "eqgroup-v1-" + digest[:24]
    try:
        return MultiEquationRegionGroupV1.model_validate(fields)
    except ValueError as exc:
        raise MultiEquationRegionRejected("multi_equation_group_rejected") from exc


def child_pixel_payload_sha256(mode: str, size: tuple[int, int], pixels: bytes) -> str:
    """Reproduce the canonical crop-pixel digest used by region locators."""

    header = f"{mode}|{size[0]}|{size[1]}|".encode("ascii")
    return sha256(header + pixels).hexdigest()


__all__ = [
    "MAX_MULTI_EQUATION_CHILDREN",
    "MAX_MULTI_EQUATION_CHILD_PIXELS",
    "MAX_MULTI_EQUATION_GROUP_PIXELS",
    "MULTI_EQUATION_BUDGET_VERSION",
    "MultiEquationRegionGroupV1",
    "MultiEquationRegionRejected",
    "build_multi_equation_group",
    "child_pixel_payload_sha256",
]
