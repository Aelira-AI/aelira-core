"""Deterministic, fail-closed suitability evidence for handwritten math crops.

Suitability is deliberately narrower than recognition: an eligible crop may be
sent to the later HMER specialist, but no semantic claim is made here.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from io import BytesIO
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CORPUS_SCHEMA_VERSION = "handwritten-math-corpus-v1"
POLICY_VERSION = "handwritten-math-suitability-v1"
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_DIMENSION = 4096
MAX_IMAGE_PIXELS = 1_048_576
MAX_COMPONENTS = 512

_POLICY = MappingProxyType(
    {
        "eligible_contrast_span_min": 96,
        "eligible_ink_ratio_ppm_min": 2_000,
        "eligible_ink_ratio_ppm_max": 350_000,
        "eligible_component_count_min": 5,
        "eligible_component_count_max": 96,
        "eligible_equals_pair_count_min": 1,
        "eligible_line_band_count_max": 1,
        "human_review_long_component_width_ppm_min": 700_000,
        "ink_threshold_span_ppm": 550_000,
        "minimum_component_pixels": 3,
        "supported_formats": ("JPEG", "PNG"),
    }
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def suitability_policy_sha256() -> str:
    """Return the immutable digest of the public v1 suitability policy."""
    return hashlib.sha256(
        _canonical_json_bytes({"policy_version": POLICY_VERSION, **_POLICY})
    ).hexdigest()


POLICY_SHA256 = suitability_policy_sha256()


class SuitabilityInputRejected(ValueError):
    """The crop or its suitability evidence failed a bounded public contract."""


SuitabilityDisposition = Literal["eligible", "human_review", "unsupported"]
SuitabilityReason = Literal[
    "annotation_or_overdraw",
    "dense_or_fragmented_ink",
    "insufficient_contrast",
    "insufficient_symbol_structure",
    "multiple_lines",
    "no_math_structure_signal",
]


class HandwrittenMathSuitabilityMetrics(BaseModel):
    """Integer-only pixel measurements used by the frozen policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contrast_span: int = Field(ge=0, le=255, strict=True)
    ink_pixels: int = Field(ge=0, le=MAX_IMAGE_PIXELS, strict=True)
    ink_ratio_ppm: int = Field(ge=0, le=1_000_000, strict=True)
    component_count: int = Field(ge=0, le=MAX_COMPONENTS, strict=True)
    line_band_count: int = Field(ge=0, le=MAX_IMAGE_DIMENSION, strict=True)
    equals_pair_count: int = Field(ge=0, le=MAX_COMPONENTS, strict=True)
    longest_component_width_ppm: int = Field(ge=0, le=1_000_000, strict=True)


class HandwrittenMathSuitabilityEvidence(BaseModel):
    """Digest-bound public decision consumed by the later HMER specialist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["handwritten-math-suitability-evidence-v1"]
    policy_version: Literal["handwritten-math-suitability-v1"]
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pixel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_format: Literal["JPEG", "PNG"]
    width: int = Field(ge=1, le=MAX_IMAGE_DIMENSION, strict=True)
    height: int = Field(ge=1, le=MAX_IMAGE_DIMENSION, strict=True)
    metrics: HandwrittenMathSuitabilityMetrics
    disposition: SuitabilityDisposition
    reason_codes: tuple[SuitabilityReason, ...]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("reason_codes")
    @classmethod
    def _ordered_unique_reasons(
        cls, value: tuple[SuitabilityReason, ...]
    ) -> tuple[SuitabilityReason, ...]:
        if tuple(sorted(set(value))) != value:
            raise ValueError("reason codes must be unique and canonically ordered")
        return value

    @model_validator(mode="after")
    def _validate_identity(self) -> "HandwrittenMathSuitabilityEvidence":
        if self.policy_sha256 != POLICY_SHA256:
            raise ValueError("policy digest does not match the frozen policy")
        if self.disposition == "eligible" and self.reason_codes:
            raise ValueError("eligible evidence cannot contain rejection reasons")
        if self.disposition != "eligible" and not self.reason_codes:
            raise ValueError("non-eligible evidence requires a reason code")
        expected = _evidence_digest(self.model_dump(mode="json"))
        if self.evidence_sha256 != expected:
            raise ValueError("evidence digest does not match the evidence")
        return self


class HandwrittenMathCorpusFixture(BaseModel):
    """One redistributable fixture and its frozen evaluation expectation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    path: str = Field(pattern=r"^images/[a-z0-9][a-z0-9-]*\.png$", max_length=128)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    author: Literal["Aelira project contributors"]
    generation_method: Literal["deterministic synthetic stroke drawing"]
    license: Literal["AGPL-3.0-only"]
    source_class: Literal["project_authored_synthetic"]
    category: Literal[
        "annotation",
        "diagram",
        "legible_handwriting",
        "low_contrast",
        "multiple_lines",
        "non_math_handwriting",
        "strike_through",
        "unsupported_style",
        "visually_similar_non_math",
    ]
    expected_disposition: SuitabilityDisposition
    always_human_review: bool
    ci: bool

    @field_validator("path")
    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("fixture path must stay within the corpus")
        return value

    @model_validator(mode="after")
    def _review_flag_matches_disposition(self) -> "HandwrittenMathCorpusFixture":
        if self.always_human_review != (self.expected_disposition != "eligible"):
            raise ValueError("human-review flag does not match expected disposition")
        return self


class HandwrittenMathMetricGates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_disposition_accuracy_min_ppm: Literal[1_000_000]
    eligible_false_positives_max: Literal[0]


class HandwrittenMathCorpusManifest(BaseModel):
    """Exact, bounded manifest for the project-authored evaluation corpus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["handwritten-math-corpus-v1"]
    corpus_version: Literal["synthetic-strokes-v1"]
    policy_version: Literal["handwritten-math-suitability-v1"]
    license: Literal["AGPL-3.0-only"]
    generated_by: Literal["scripts/generate_handwritten_math_corpus.py"]
    fixtures: tuple[HandwrittenMathCorpusFixture, ...] = Field(
        min_length=1, max_length=256
    )
    ci_subset: tuple[str, ...] = Field(min_length=1, max_length=128)
    metric_gates: HandwrittenMathMetricGates

    @model_validator(mode="after")
    def _validate_unique_complete_identity(self) -> "HandwrittenMathCorpusManifest":
        ids = [fixture.id for fixture in self.fixtures]
        paths = [fixture.path for fixture in self.fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("fixture ids must be unique")
        if len(paths) != len(set(paths)):
            raise ValueError("fixture paths must be unique")
        if len(self.ci_subset) != len(set(self.ci_subset)):
            raise ValueError("CI fixture ids must be unique")
        by_id = {fixture.id: fixture for fixture in self.fixtures}
        if set(self.ci_subset) - set(by_id):
            raise ValueError("CI subset references an unknown fixture")
        if any(not by_id[fixture_id].ci for fixture_id in self.ci_subset):
            raise ValueError("CI subset and per-fixture flags disagree")
        if {fixture.id for fixture in self.fixtures if fixture.ci} != set(
            self.ci_subset
        ):
            raise ValueError("every CI fixture must appear in ci_subset")
        return self


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_corpus_manifest(path: str | Path) -> HandwrittenMathCorpusManifest:
    """Load an exact bounded corpus manifest without resolving fixture paths."""
    manifest_path = Path(path)
    try:
        payload = manifest_path.read_bytes()
    except OSError as exc:
        raise ValueError("corpus manifest unavailable") from exc
    if not payload or len(payload) > 512 * 1024:
        raise ValueError("corpus manifest exceeds the bounded size")
    try:
        parsed = json.loads(
            payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("corpus manifest is not exact JSON") from exc
    return HandwrittenMathCorpusManifest.model_validate(parsed)


def _evidence_digest(value: dict[str, Any]) -> str:
    material = dict(value)
    material.pop("evidence_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(material)).hexdigest()


def _pixel_digest(image: Image.Image) -> str:
    header = f"L|{image.width}|{image.height}|".encode("ascii")
    return hashlib.sha256(header + image.tobytes()).hexdigest()


def _decode_image(payload: bytes) -> tuple[Image.Image, str]:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_SOURCE_BYTES:
        raise SuitabilityInputRejected("source_size_unsupported")
    try:
        with Image.open(BytesIO(payload)) as opened:
            image_format = opened.format
            if image_format not in _POLICY["supported_formats"]:
                raise SuitabilityInputRejected("image_format_unsupported")
            width, height = opened.size
            if (
                width < 1
                or height < 1
                or width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise SuitabilityInputRejected("image_dimensions_unsupported")
            opened.load()
            return opened.convert("L"), str(image_format)
    except SuitabilityInputRejected:
        raise
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError):
        raise SuitabilityInputRejected("image_decode_failed") from None


def _components(ink: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    height, width = ink.shape
    visited = np.zeros_like(ink, dtype=bool)
    components: list[tuple[int, int, int, int, int]] = []
    minimum_pixels = int(_POLICY["minimum_component_pixels"])
    for y in range(height):
        for x in range(width):
            if not ink[y, x] or visited[y, x]:
                continue
            queue: deque[tuple[int, int]] = deque([(x, y)])
            visited[y, x] = True
            min_x = max_x = x
            min_y = max_y = y
            pixels = 0
            while queue:
                current_x, current_y = queue.popleft()
                pixels += 1
                min_x = min(min_x, current_x)
                max_x = max(max_x, current_x)
                min_y = min(min_y, current_y)
                max_y = max(max_y, current_y)
                for next_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                    for next_x in range(
                        max(0, current_x - 1), min(width, current_x + 2)
                    ):
                        if ink[next_y, next_x] and not visited[next_y, next_x]:
                            visited[next_y, next_x] = True
                            queue.append((next_x, next_y))
            if pixels >= minimum_pixels:
                components.append((min_x, min_y, max_x + 1, max_y + 1, pixels))
                if len(components) > MAX_COMPONENTS:
                    raise SuitabilityInputRejected("component_budget_exceeded")
    return components


def _line_band_count(ink: np.ndarray) -> int:
    occupied = np.any(ink, axis=1)
    starts = occupied & ~np.concatenate(([False], occupied[:-1]))
    return int(np.count_nonzero(starts))


def _equals_pair_count(components: list[tuple[int, int, int, int, int]]) -> int:
    horizontal: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1, _pixels in components:
        width = x1 - x0
        height = y1 - y0
        if width >= 10 and height <= 7 and width >= height * 3:
            horizontal.append((x0, y0, x1, y1))
    pairs = 0
    for index, first in enumerate(horizontal):
        first_width = first[2] - first[0]
        first_center = (first[0] + first[2]) / 2
        for second in horizontal[index + 1 :]:
            second_width = second[2] - second[0]
            second_center = (second[0] + second[2]) / 2
            vertical_gap = max(second[1] - first[3], first[1] - second[3])
            if (
                2 <= vertical_gap <= 18
                and abs(first_width - second_width) <= max(3, first_width // 3)
                and abs(first_center - second_center) <= max(4, first_width // 4)
            ):
                pairs += 1
    return pairs


def _metrics(image: Image.Image) -> HandwrittenMathSuitabilityMetrics:
    pixels = np.asarray(image, dtype=np.uint8)
    minimum = int(pixels.min())
    maximum = int(pixels.max())
    contrast_span = maximum - minimum
    threshold = minimum + math.floor(
        contrast_span * int(_POLICY["ink_threshold_span_ppm"]) / 1_000_000
    )
    ink = pixels <= threshold if contrast_span else np.zeros_like(pixels, dtype=bool)
    ink_pixels = int(np.count_nonzero(ink))
    components = _components(ink)
    longest_width = max((x1 - x0 for x0, _y0, x1, _y1, _p in components), default=0)
    return HandwrittenMathSuitabilityMetrics(
        contrast_span=contrast_span,
        ink_pixels=ink_pixels,
        ink_ratio_ppm=round(ink_pixels * 1_000_000 / (image.width * image.height)),
        component_count=len(components),
        line_band_count=_line_band_count(ink),
        equals_pair_count=_equals_pair_count(components),
        longest_component_width_ppm=round(longest_width * 1_000_000 / image.width),
    )


def _disposition(
    metrics: HandwrittenMathSuitabilityMetrics,
) -> tuple[SuitabilityDisposition, tuple[SuitabilityReason, ...]]:
    unsupported: set[SuitabilityReason] = set()
    review: set[SuitabilityReason] = set()
    if metrics.component_count < int(_POLICY["eligible_component_count_min"]):
        unsupported.add("insufficient_symbol_structure")
    if not (
        int(_POLICY["eligible_ink_ratio_ppm_min"])
        <= metrics.ink_ratio_ppm
        <= int(_POLICY["eligible_ink_ratio_ppm_max"])
    ):
        unsupported.add("insufficient_symbol_structure")
    if unsupported:
        return "unsupported", tuple(sorted(unsupported))
    if metrics.longest_component_width_ppm >= int(
        _POLICY["human_review_long_component_width_ppm_min"]
    ):
        review.add("annotation_or_overdraw")
    if review:
        return "human_review", tuple(sorted(review))
    if metrics.equals_pair_count < int(_POLICY["eligible_equals_pair_count_min"]):
        return "unsupported", ("no_math_structure_signal",)
    if metrics.contrast_span < int(_POLICY["eligible_contrast_span_min"]):
        review.add("insufficient_contrast")
    if metrics.line_band_count > int(_POLICY["eligible_line_band_count_max"]):
        review.add("multiple_lines")
    if metrics.component_count > int(_POLICY["eligible_component_count_max"]):
        review.add("dense_or_fragmented_ink")
    if review:
        return "human_review", tuple(sorted(review))
    return "eligible", ()


def classify_handwritten_math_suitability(
    image_bytes: bytes,
) -> HandwrittenMathSuitabilityEvidence:
    """Classify one bounded crop from pixels alone under the frozen v1 policy."""
    image, image_format = _decode_image(image_bytes)
    metrics = _metrics(image)
    disposition, reason_codes = _disposition(metrics)
    material = {
        "schema_version": "handwritten-math-suitability-evidence-v1",
        "policy_version": POLICY_VERSION,
        "policy_sha256": POLICY_SHA256,
        "source_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "pixel_sha256": _pixel_digest(image),
        "image_format": image_format,
        "width": image.width,
        "height": image.height,
        "metrics": metrics.model_dump(mode="json"),
        "disposition": disposition,
        "reason_codes": reason_codes,
    }
    return HandwrittenMathSuitabilityEvidence.model_validate(
        {**material, "evidence_sha256": _evidence_digest(material)}
    )


def ensure_hmer_eligible(
    image_bytes: bytes,
    evidence: HandwrittenMathSuitabilityEvidence | dict[str, Any],
) -> None:
    """Fail closed unless evidence belongs to these pixels and permits HMER."""
    try:
        validated = HandwrittenMathSuitabilityEvidence.model_validate(evidence)
    except (TypeError, ValueError) as exc:
        raise SuitabilityInputRejected("evidence_invalid") from exc
    if hashlib.sha256(image_bytes).hexdigest() != validated.source_sha256:
        raise SuitabilityInputRejected("source_mismatch")
    expected = classify_handwritten_math_suitability(image_bytes)
    if validated.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise SuitabilityInputRejected("evidence_mismatch")
    if validated.disposition != "eligible":
        raise SuitabilityInputRejected("not_eligible")


__all__ = [
    "CORPUS_SCHEMA_VERSION",
    "POLICY_SHA256",
    "POLICY_VERSION",
    "HandwrittenMathCorpusFixture",
    "HandwrittenMathCorpusManifest",
    "HandwrittenMathSuitabilityEvidence",
    "HandwrittenMathSuitabilityMetrics",
    "SuitabilityDisposition",
    "SuitabilityInputRejected",
    "classify_handwritten_math_suitability",
    "ensure_hmer_eligible",
    "load_corpus_manifest",
    "suitability_policy_sha256",
]
