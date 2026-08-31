"""Immutable provenance for one exact PDF vector-equation cluster."""

from __future__ import annotations

import hashlib
import math
from io import BytesIO
from typing import Any, Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.education.canonical_json import canonical_sha256

MAX_VECTOR_OBJECT_NUMBER = 25_000_000
MAX_VECTOR_CONTENT_STREAMS = 32
MAX_VECTOR_OPERATOR_SPANS = 128
MAX_VECTOR_RESOURCES = 256
MAX_VECTOR_RASTER_DIMENSION = 16_384
MAX_VECTOR_RASTER_PIXELS = 25_000_000
MAX_VECTOR_RASTER_BYTES = 16 * 1024 * 1024
VECTOR_RASTER_RENDERER_VERSION = "pymupdf-vector-raster-v1"
VECTOR_RASTER_POLICY_VERSION = "vector-raster-policy-v1"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CLUSTER_ID_PREFIX = "eqvector-v1-"


class VectorEquationClusterRejected(ValueError):
    """A vector candidate cannot prove exact passive source ownership."""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class VectorObjectIdentityV1(_FrozenModel):
    """One indirect PDF object and the digest of its passive bytes."""

    object_number: int = Field(ge=1, le=MAX_VECTOR_OBJECT_NUMBER, strict=True)
    generation: int = Field(ge=0, le=65_535, strict=True)
    passive_sha256: str = Field(pattern=_SHA256_PATTERN)


class VectorResourceIdentityV1(_FrozenModel):
    """One named resource reached by the selected operator spans."""

    resource_kind: Literal[
        "xobject",
        "font",
        "extgstate",
        "colorspace",
        "pattern",
        "shading",
        "properties",
    ]
    resource_name: str = Field(min_length=2, max_length=256)
    object_identity: VectorObjectIdentityV1

    @field_validator("resource_name")
    @classmethod
    def _exact_resource_name(cls, value: str) -> str:
        if (
            not value.startswith("/")
            or value != value.strip()
            or not value.isascii()
            or not value.isprintable()
            or any(character.isspace() for character in value)
        ):
            raise ValueError("resource_name must be one exact PDF name")
        return value


class VectorOperatorSpanV1(_FrozenModel):
    """One contiguous ordered operator span inside one content stream."""

    stream: VectorObjectIdentityV1
    first_operator: int = Field(ge=0, le=10_000_000, strict=True)
    last_operator: int = Field(ge=0, le=10_000_000, strict=True)
    operator_count: int = Field(ge=1, le=1_000_000, strict=True)
    operators_sha256: str = Field(pattern=_SHA256_PATTERN)
    graphics_state_sha256: str = Field(pattern=_SHA256_PATTERN)
    painted_bbox: tuple[float, float, float, float]

    @field_validator("painted_bbox", mode="before")
    @classmethod
    def _bounded_bbox(cls, value: Any) -> Any:
        _validate_bbox(value, "painted_bbox")
        return value

    @model_validator(mode="after")
    def _exact_span(self) -> "VectorOperatorSpanV1":
        if self.last_operator < self.first_operator:
            raise ValueError("operator span is reversed")
        if self.operator_count != self.last_operator - self.first_operator + 1:
            raise ValueError("operator_count does not match the inclusive span")
        return self


class VectorRasterEvidenceV1(_FrozenModel):
    """One bounded deterministic PNG projection of the selected operators."""

    raster_kind: Literal["vector_equation_raster_v1"]
    mime_type: Literal["image/png"]
    png_bytes: bytes = Field(min_length=8, max_length=MAX_VECTOR_RASTER_BYTES)
    width: int = Field(ge=1, le=MAX_VECTOR_RASTER_DIMENSION, strict=True)
    height: int = Field(ge=1, le=MAX_VECTOR_RASTER_DIMENSION, strict=True)
    mode: Literal["L", "RGB", "RGBA"]
    scale: float = Field(ge=1.0, le=8.0, allow_inf_nan=False)
    renderer_version: Literal["pymupdf-vector-raster-v1"]
    policy_version: Literal["vector-raster-policy-v1"]
    pixel_sha256: str = Field(pattern=_SHA256_PATTERN)
    png_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _verify_png(self) -> "VectorRasterEvidenceV1":
        decoded = _decode_png(self.png_bytes)
        if decoded.size != (self.width, self.height) or decoded.mode != self.mode:
            raise ValueError("raster dimensions or mode differ from PNG")
        if _pixel_sha256(decoded) != self.pixel_sha256:
            raise ValueError("pixel_sha256 differs from decoded PNG")
        if hashlib.sha256(self.png_bytes).hexdigest() != self.png_sha256:
            raise ValueError("png_sha256 differs from PNG bytes")
        return self

    def canonical_identity(self) -> dict[str, object]:
        """Return passive raster identity without embedding the byte payload."""

        return self.model_dump(mode="json", exclude={"png_bytes"})


class VectorEquationClusterV1(_FrozenModel):
    """One exact vector source and its deterministic recognition raster."""

    cluster_kind: Literal["vector_equation_cluster_v1"]
    source_kind: Literal["pdf_vector_cluster"]
    page_number: int = Field(ge=1, le=MAX_VECTOR_OBJECT_NUMBER, strict=True)
    page_object: VectorObjectIdentityV1
    content_streams: tuple[VectorObjectIdentityV1, ...] = Field(
        min_length=1, max_length=MAX_VECTOR_CONTENT_STREAMS
    )
    operator_spans: tuple[VectorOperatorSpanV1, ...] = Field(
        min_length=1, max_length=MAX_VECTOR_OPERATOR_SPANS
    )
    resources: tuple[VectorResourceIdentityV1, ...] = Field(
        max_length=MAX_VECTOR_RESOURCES
    )
    pdf_bbox: tuple[float, float, float, float]
    raster: VectorRasterEvidenceV1
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    cluster_sha256: str = Field(pattern=_SHA256_PATTERN)
    cluster_id: str = Field(pattern=r"^eqvector-v1-[0-9a-f]{24}$")

    @field_validator("pdf_bbox", mode="before")
    @classmethod
    def _bounded_bbox(cls, value: Any) -> Any:
        _validate_bbox(value, "pdf_bbox")
        return value

    @model_validator(mode="after")
    def _verify_complete_identity(self) -> "VectorEquationClusterV1":
        if len(set(self.content_streams)) != len(self.content_streams):
            raise ValueError("content stream identity is duplicated")
        stream_order = {
            (stream.object_number, stream.generation, stream.passive_sha256): index
            for index, stream in enumerate(self.content_streams)
        }
        previous_key: tuple[int, int] | None = None
        previous_last_by_stream: dict[int, int] = {}
        for span in self.operator_spans:
            stream_key = (
                span.stream.object_number,
                span.stream.generation,
                span.stream.passive_sha256,
            )
            if stream_key not in stream_order:
                raise ValueError("operator span references an unowned stream")
            index = stream_order[stream_key]
            key = (index, span.first_operator)
            if previous_key is not None and key <= previous_key:
                raise ValueError("operator spans are not in source order")
            if span.first_operator <= previous_last_by_stream.get(index, -1):
                raise ValueError("operator spans overlap")
            previous_key = key
            previous_last_by_stream[index] = span.last_operator
        resource_keys = [
            (
                resource.resource_kind,
                resource.resource_name,
                resource.object_identity.object_number,
                resource.object_identity.generation,
                resource.object_identity.passive_sha256,
            )
            for resource in self.resources
        ]
        if resource_keys != sorted(resource_keys) or len(set(resource_keys)) != len(
            resource_keys
        ):
            raise ValueError("resources must be uniquely sorted")

        expected_bbox = _span_union(self.operator_spans)
        if any(
            abs(current - expected) > 1e-6
            for current, expected in zip(self.pdf_bbox, expected_bbox)
        ):
            raise ValueError("pdf_bbox differs from the complete painted extent")
        expected_source = canonical_sha256(self.canonical_source_identity())
        if self.source_sha256 != expected_source:
            raise ValueError("source_sha256 differs from source identity")
        expected_cluster = canonical_sha256(self.canonical_identity())
        if self.cluster_sha256 != expected_cluster:
            raise ValueError("cluster_sha256 differs from candidate identity")
        if self.cluster_id != _CLUSTER_ID_PREFIX + expected_cluster[:24]:
            raise ValueError("cluster_id differs from candidate identity")
        return self

    def canonical_source_identity(self) -> dict[str, object]:
        """Return the exact original-PDF source identity."""

        return {
            "source_kind": self.source_kind,
            "page_number": self.page_number,
            "page_object": self.page_object.model_dump(mode="json"),
            "content_streams": [
                stream.model_dump(mode="json") for stream in self.content_streams
            ],
            "operator_spans": [
                span.model_dump(mode="json") for span in self.operator_spans
            ],
            "resources": [
                resource.model_dump(mode="json") for resource in self.resources
            ],
            "pdf_bbox": list(self.pdf_bbox),
        }

    def canonical_identity(self) -> dict[str, object]:
        """Return source plus passive raster identity, excluding derived IDs."""

        return {
            **self.canonical_source_identity(),
            "cluster_kind": self.cluster_kind,
            "raster": self.raster.canonical_identity(),
            "source_sha256": self.source_sha256,
        }


def _validate_bbox(value: Any, label: str) -> tuple[float, float, float, float]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not math.isfinite(float(item))
            or abs(float(item)) > 25_000_000
            for item in value
        )
    ):
        raise ValueError(f"{label} must contain four bounded finite numbers")
    bbox = tuple(float(item) for item in value)
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise ValueError(f"{label} must have positive area")
    return bbox


def _decode_png(payload: bytes) -> Image.Image:
    if len(payload) > MAX_VECTOR_RASTER_BYTES or not payload.startswith(
        b"\x89PNG\r\n\x1a\n"
    ):
        raise ValueError("raster must contain one bounded PNG")
    try:
        with Image.open(BytesIO(payload)) as source:
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("animated raster is unsupported")
            if (
                source.width > MAX_VECTOR_RASTER_DIMENSION
                or source.height > MAX_VECTOR_RASTER_DIMENSION
                or source.width * source.height > MAX_VECTOR_RASTER_PIXELS
                or source.mode not in {"L", "RGB", "RGBA"}
            ):
                raise ValueError("raster exceeds the supported pixel contract")
            source.load()
            return source.copy()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("raster PNG cannot be decoded") from exc


def _pixel_sha256(image: Image.Image) -> str:
    header = f"{image.mode}|{image.width}|{image.height}|".encode("ascii")
    return hashlib.sha256(header + image.tobytes()).hexdigest()


def _span_union(
    spans: tuple[VectorOperatorSpanV1, ...],
) -> tuple[float, float, float, float]:
    return (
        min(span.painted_bbox[0] for span in spans),
        min(span.painted_bbox[1] for span in spans),
        max(span.painted_bbox[2] for span in spans),
        max(span.painted_bbox[3] for span in spans),
    )


def build_vector_equation_cluster(
    *,
    page_number: int,
    page_object: VectorObjectIdentityV1,
    content_streams: tuple[VectorObjectIdentityV1, ...],
    operator_spans: tuple[VectorOperatorSpanV1, ...],
    resources: tuple[VectorResourceIdentityV1, ...],
    pdf_bbox: tuple[float, float, float, float],
    raster_png: bytes,
    raster_scale: float,
) -> VectorEquationClusterV1:
    """Build and independently validate one complete vector candidate."""

    try:
        image = _decode_png(raster_png)
        raster = VectorRasterEvidenceV1(
            raster_kind="vector_equation_raster_v1",
            mime_type="image/png",
            png_bytes=raster_png,
            width=image.width,
            height=image.height,
            mode=image.mode,
            scale=raster_scale,
            renderer_version=VECTOR_RASTER_RENDERER_VERSION,
            policy_version=VECTOR_RASTER_POLICY_VERSION,
            pixel_sha256=_pixel_sha256(image),
            png_sha256=hashlib.sha256(raster_png).hexdigest(),
        )
        fields: dict[str, Any] = {
            "cluster_kind": "vector_equation_cluster_v1",
            "source_kind": "pdf_vector_cluster",
            "page_number": page_number,
            "page_object": page_object,
            "content_streams": content_streams,
            "operator_spans": operator_spans,
            "resources": tuple(
                sorted(
                    resources,
                    key=lambda item: (
                        item.resource_kind,
                        item.resource_name,
                        item.object_identity.object_number,
                        item.object_identity.generation,
                        item.object_identity.passive_sha256,
                    ),
                )
            ),
            "pdf_bbox": pdf_bbox,
            "raster": raster,
        }
        source_identity = {
            "source_kind": fields["source_kind"],
            "page_number": fields["page_number"],
            "page_object": page_object.model_dump(mode="json"),
            "content_streams": [
                stream.model_dump(mode="json") for stream in content_streams
            ],
            "operator_spans": [span.model_dump(mode="json") for span in operator_spans],
            "resources": [
                resource.model_dump(mode="json") for resource in fields["resources"]
            ],
            "pdf_bbox": list(pdf_bbox),
        }
        fields["source_sha256"] = canonical_sha256(source_identity)
        candidate_identity = {
            **source_identity,
            "cluster_kind": fields["cluster_kind"],
            "raster": raster.canonical_identity(),
            "source_sha256": fields["source_sha256"],
        }
        fields["cluster_sha256"] = canonical_sha256(candidate_identity)
        fields["cluster_id"] = _CLUSTER_ID_PREFIX + fields["cluster_sha256"][:24]
        return VectorEquationClusterV1.model_validate(fields)
    except (TypeError, ValueError) as exc:
        raise VectorEquationClusterRejected("vector_equation_cluster_rejected") from exc


__all__ = [
    "MAX_VECTOR_CONTENT_STREAMS",
    "MAX_VECTOR_OBJECT_NUMBER",
    "MAX_VECTOR_OPERATOR_SPANS",
    "MAX_VECTOR_RASTER_BYTES",
    "MAX_VECTOR_RASTER_DIMENSION",
    "MAX_VECTOR_RASTER_PIXELS",
    "MAX_VECTOR_RESOURCES",
    "VECTOR_RASTER_POLICY_VERSION",
    "VECTOR_RASTER_RENDERER_VERSION",
    "VectorEquationClusterRejected",
    "VectorEquationClusterV1",
    "VectorObjectIdentityV1",
    "VectorOperatorSpanV1",
    "VectorRasterEvidenceV1",
    "VectorResourceIdentityV1",
    "build_vector_equation_cluster",
]
