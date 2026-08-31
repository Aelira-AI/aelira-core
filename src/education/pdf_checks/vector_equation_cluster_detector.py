"""Fail-closed discovery of exact cue-bound PDF vector-equation clusters."""

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import fitz
import pikepdf
from pikepdf import Array, Name

from src.education.canonical_json import canonical_sha256
from src.education.vector_equation_cluster import (
    VectorEquationClusterV1,
    VectorObjectIdentityV1,
    VectorOperatorSpanV1,
    VectorResourceIdentityV1,
    build_vector_equation_cluster,
)

logger = logging.getLogger(__name__)

MAX_VECTOR_PAGES = 1_000
MAX_VECTOR_DRAWINGS_PER_PAGE = 2_000
MAX_VECTOR_OPERATORS_PER_STREAM = 100_000
MAX_VECTOR_CANDIDATES_PER_PAGE = 16
MAX_VECTOR_CANDIDATES_PER_DOCUMENT = 64
MAX_VECTOR_STREAM_BYTES = 64 * 1024 * 1024
MAX_VECTOR_RESOURCE_DEPTH = 4
VECTOR_RASTER_SCALE = 2.0

_CUE = re.compile(r"\b(?:equation|formula)\b", re.IGNORECASE)
_PATH_CONSTRUCTION = frozenset({"m", "l", "c", "v", "y", "h", "re"})
_PATH_PAINT = frozenset({"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"})
_PATH_DISCARD = frozenset({"n"})
_PASSIVE_STATE_OPERATORS = frozenset(
    {
        "q",
        "Q",
        "cm",
        "w",
        "J",
        "j",
        "M",
        "d",
        "ri",
        "i",
        "G",
        "g",
        "RG",
        "rg",
        "K",
        "k",
        "W",
        "W*",
    }
)
_PASSIVE_TEXT_OPERATORS = frozenset(
    {
        "BT",
        "ET",
        "Tc",
        "Tw",
        "Tz",
        "TL",
        "Tf",
        "Tr",
        "Ts",
        "Td",
        "TD",
        "Tm",
        "T*",
        "Tj",
        "TJ",
        "'",
        '"',
        "BMC",
        "BDC",
        "EMC",
        "MP",
        "DP",
    }
)
_UNSUPPORTED_DRAWING_KEYS = (
    "fill_opacity",
    "stroke_opacity",
)


@dataclass(frozen=True)
class _PaintedSpan:
    stream: VectorObjectIdentityV1
    first_operator: int
    last_operator: int
    operator_count: int
    operators_sha256: str
    graphics_state_sha256: str
    resources: tuple[VectorResourceIdentityV1, ...] = ()


class VectorEquationClusterDetector:
    """Discover passive vector candidates without recognition or PDF mutation."""

    def find_clusters(self, path: str | Path) -> tuple[VectorEquationClusterV1, ...]:
        """Return all exact supported clusters or no partial document result."""

        try:
            source_path = Path(path)
            with fitz.open(source_path) as fitz_doc, pikepdf.open(source_path) as pdf:
                if len(fitz_doc) != len(pdf.pages) or len(fitz_doc) > MAX_VECTOR_PAGES:
                    return ()
                candidates: list[VectorEquationClusterV1] = []
                for page_index, page in enumerate(fitz_doc):
                    page_candidates = self._page_clusters(
                        page_index=page_index,
                        fitz_page=page,
                        pdf_page=pdf.pages[page_index],
                    )
                    if len(page_candidates) > MAX_VECTOR_CANDIDATES_PER_PAGE:
                        return ()
                    candidates.extend(page_candidates)
                    if len(candidates) > MAX_VECTOR_CANDIDATES_PER_DOCUMENT:
                        return ()
                return tuple(
                    sorted(
                        candidates,
                        key=lambda item: (
                            item.page_number,
                            item.pdf_bbox[1],
                            item.pdf_bbox[0],
                            item.cluster_id,
                        ),
                    )
                )
        except Exception as exc:
            logger.debug("Vector-equation discovery failed closed: %s", exc)
            return ()

    def revalidate(self, path: str | Path, candidate: VectorEquationClusterV1) -> bool:
        """Reopen the source and require one byte-for-byte typed candidate match."""

        try:
            expected = VectorEquationClusterV1.model_validate(candidate)
        except (TypeError, ValueError):
            return False
        matches = [
            current
            for current in self.find_clusters(path)
            if current.cluster_id == expected.cluster_id
        ]
        return len(matches) == 1 and matches[0] == expected

    def _page_clusters(
        self,
        *,
        page_index: int,
        fitz_page: fitz.Page,
        pdf_page: Any,
    ) -> list[VectorEquationClusterV1]:
        if fitz_page.rotation != 0 or not _valid_rect(fitz_page.rect):
            return []
        drawings = list(fitz_page.get_drawings())
        if len(drawings) > MAX_VECTOR_DRAWINGS_PER_PAGE:
            raise ValueError("vector drawing budget exceeded")
        painted_spans = _page_painted_spans(pdf_page)
        if len(painted_spans) != len(drawings):
            return []

        words = list(fitz_page.get_text("words"))
        page_identity = _object_identity(
            pdf_page.obj,
            str(pdf_page.obj).encode("utf-8"),
        )
        candidates: list[VectorEquationClusterV1] = []
        for drawing_index, drawing in enumerate(drawings):
            rect = fitz.Rect(drawing.get("rect"))
            if (
                not _supported_drawing(drawing)
                or not _equation_geometry(drawing)
                or not _has_local_cue(fitz_page, rect)
                or _text_overlaps(words, rect)
                or _other_drawing_overlaps(drawings, drawing_index, rect)
                or _other_drawing_is_ambiguously_near(drawings, drawing_index, rect)
            ):
                continue
            painted = painted_spans[drawing_index]
            span = VectorOperatorSpanV1(
                stream=painted.stream,
                first_operator=painted.first_operator,
                last_operator=painted.last_operator,
                operator_count=painted.operator_count,
                operators_sha256=painted.operators_sha256,
                graphics_state_sha256=painted.graphics_state_sha256,
                painted_bbox=tuple(float(value) for value in rect),
            )
            raster_png = _rasterize(fitz_page, rect)
            candidates.append(
                build_vector_equation_cluster(
                    page_number=page_index + 1,
                    page_object=page_identity,
                    content_streams=(painted.stream,),
                    operator_spans=(span,),
                    resources=painted.resources,
                    pdf_bbox=tuple(float(value) for value in rect),
                    raster_png=raster_png,
                    raster_scale=VECTOR_RASTER_SCALE,
                )
            )
        return candidates


def _page_painted_spans(page: Any) -> list[_PaintedSpan]:
    contents = page.obj.get(Name.Contents)
    if contents is None:
        return []
    streams = list(contents) if isinstance(contents, Array) else [contents]
    painted: list[_PaintedSpan] = []
    for stream in streams:
        raw = stream.read_raw_bytes()
        if len(raw) > MAX_VECTOR_STREAM_BYTES:
            raise ValueError("vector content stream exceeds byte budget")
        identity = _object_identity(stream, raw)
        instructions = list(pikepdf.parse_content_stream(stream))
        if len(instructions) > MAX_VECTOR_OPERATORS_PER_STREAM:
            raise ValueError("vector content stream exceeds operator budget")
        encoded = [_instruction_identity(item) for item in instructions]
        path_start: int | None = None
        for operator_index, instruction in enumerate(instructions):
            operator = str(instruction.operator)
            if operator in _PATH_CONSTRUCTION and path_start is None:
                path_start = operator_index
            if operator in _PATH_DISCARD:
                path_start = None
                continue
            if operator == "Do":
                if path_start is not None or len(instruction.operands) != 1:
                    raise ValueError("ambiguous vector resource invocation")
                resources = _resolve_single_form_paint(
                    owner=page.obj,
                    resource_name=instruction.operands[0],
                    scope="",
                    depth=0,
                    seen=frozenset(),
                )
                if resources is None:
                    raise ValueError("unsupported page resource invocation")
                painted.append(
                    _PaintedSpan(
                        stream=identity,
                        first_operator=operator_index,
                        last_operator=operator_index,
                        operator_count=1,
                        operators_sha256=canonical_sha256([encoded[operator_index]]),
                        graphics_state_sha256=canonical_sha256(
                            encoded[: operator_index + 1]
                        ),
                        resources=resources,
                    )
                )
                continue
            if operator not in (
                _PATH_CONSTRUCTION
                | _PATH_PAINT
                | _PATH_DISCARD
                | _PASSIVE_STATE_OPERATORS
                | _PASSIVE_TEXT_OPERATORS
            ):
                raise ValueError("unsupported page content operator")
            if operator not in _PATH_PAINT:
                continue
            if path_start is None:
                raise ValueError("path paint lacks a local construction span")
            span_identity = encoded[path_start : operator_index + 1]
            painted.append(
                _PaintedSpan(
                    stream=identity,
                    first_operator=path_start,
                    last_operator=operator_index,
                    operator_count=operator_index - path_start + 1,
                    operators_sha256=canonical_sha256(span_identity),
                    graphics_state_sha256=canonical_sha256(
                        encoded[: operator_index + 1]
                    ),
                )
            )
            path_start = None
    return painted


def _resolve_single_form_paint(
    *,
    owner: Any,
    resource_name: Any,
    scope: str,
    depth: int,
    seen: frozenset[tuple[int, int]],
) -> tuple[VectorResourceIdentityV1, ...] | None:
    if depth >= MAX_VECTOR_RESOURCE_DEPTH:
        raise ValueError("vector resource nesting exceeds budget")
    resources = owner.get(Name.Resources)
    xobjects = resources.get(Name.XObject) if hasattr(resources, "get") else None
    if not hasattr(xobjects, "get"):
        return None
    form = xobjects.get(resource_name)
    if form is None or str(form.get(Name.Subtype, "")) != "/Form":
        return None
    object_key = tuple(form.objgen)
    if object_key[0] <= 0 or object_key in seen:
        raise ValueError("vector resource ownership is recursive or direct")
    raw = form.read_raw_bytes()
    if len(raw) > MAX_VECTOR_STREAM_BYTES:
        raise ValueError("vector resource exceeds byte budget")
    scoped_name = f"{scope}{resource_name}"
    own_identity = VectorResourceIdentityV1(
        resource_kind="xobject",
        resource_name=scoped_name,
        object_identity=_object_identity(form, _passive_stream_bytes(form, raw)),
    )
    instructions = list(pikepdf.parse_content_stream(form))
    if len(instructions) > MAX_VECTOR_OPERATORS_PER_STREAM:
        raise ValueError("vector resource exceeds operator budget")
    terminal_paints = 0
    nested_resources: tuple[VectorResourceIdentityV1, ...] = ()
    path_open = False
    for instruction in instructions:
        operator = str(instruction.operator)
        if operator in _PATH_CONSTRUCTION:
            path_open = True
            continue
        if operator in _PATH_DISCARD:
            path_open = False
            continue
        if operator in _PATH_PAINT:
            if not path_open:
                raise ValueError("form path paint lacks construction")
            terminal_paints += 1
            path_open = False
            continue
        if operator == "Do":
            if path_open or len(instruction.operands) != 1:
                raise ValueError("ambiguous nested vector resource")
            nested = _resolve_single_form_paint(
                owner=form,
                resource_name=instruction.operands[0],
                scope=f"{scoped_name}",
                depth=depth + 1,
                seen=seen | {object_key},
            )
            if nested is None:
                raise ValueError("unsupported nested vector resource")
            if nested_resources:
                raise ValueError("multiple nested vector sources are ambiguous")
            nested_resources = nested
            continue
        if operator not in _PASSIVE_STATE_OPERATORS:
            raise ValueError("unsupported operator in vector resource")
    total_paints = terminal_paints + (1 if nested_resources else 0)
    if total_paints != 1:
        raise ValueError("vector resource must own exactly one painted cluster")
    return (own_identity, *nested_resources)


def _instruction_identity(instruction: Any) -> dict[str, object]:
    return {
        "operator": str(instruction.operator),
        "operands": [str(operand) for operand in instruction.operands],
    }


def _object_identity(value: Any, passive_bytes: bytes) -> VectorObjectIdentityV1:
    object_number, generation = tuple(value.objgen)
    if object_number <= 0:
        raise ValueError("direct PDF objects are unsupported")
    return VectorObjectIdentityV1(
        object_number=object_number,
        generation=generation,
        passive_sha256=hashlib.sha256(passive_bytes).hexdigest(),
    )


def _passive_stream_bytes(stream: Any, raw: bytes) -> bytes:
    return str(stream).encode("utf-8") + b"\x00" + raw


def _supported_drawing(drawing: dict[str, Any]) -> bool:
    rect = drawing.get("rect")
    if not _valid_rect(rect):
        return False
    if drawing.get("layer") not in (None, ""):
        return False
    for key in _UNSUPPORTED_DRAWING_KEYS:
        value = drawing.get(key, 1.0)
        if value is not None and (
            not isinstance(value, (int, float))
            or not math.isclose(float(value), 1.0, abs_tol=1e-9)
        ):
            return False
    items = drawing.get("items")
    return bool(
        isinstance(items, list)
        and 1 <= len(items) <= MAX_VECTOR_OPERATORS_PER_STREAM
        and all(item and item[0] in {"l", "c", "re", "qu"} for item in items)
    )


def _equation_geometry(drawing: dict[str, Any]) -> bool:
    items = drawing.get("items", [])
    if len(items) < 4:
        return False
    item_boxes = [_drawing_item_bbox(item) for item in items]
    horizontal: list[tuple[int, float, float, float, float]] = []
    for index, (item, item_bbox) in enumerate(zip(items, item_boxes)):
        if item_bbox is None:
            continue
        x0, y0, x1, y1 = item_bbox
        if item[0] == "l" and y1 - y0 <= 1.0 and x1 - x0 >= 4.0:
            horizontal.append((index, x0, (y0 + y1) / 2.0, x1, x1 - x0))
        elif item[0] == "re" and y1 - y0 <= 4.0 and x1 - x0 >= 4.0:
            horizontal.append((index, x0, (y0 + y1) / 2.0, x1, x1 - x0))
    for pair_index, first in enumerate(horizontal):
        for second in horizontal[pair_index + 1 :]:
            vertical_gap = abs(first[2] - second[2])
            overlap = min(first[3], second[3]) - max(first[1], second[1])
            shorter = min(first[4], second[4])
            equals_left = max(first[1], second[1])
            equals_right = min(first[3], second[3])
            excluded = {first[0], second[0]}
            has_left = any(
                box is not None and box[2] < equals_left - 1.0
                for index, box in enumerate(item_boxes)
                if index not in excluded
            )
            has_right = any(
                box is not None and box[0] > equals_right + 1.0
                for index, box in enumerate(item_boxes)
                if index not in excluded
            )
            if (
                2.0 <= vertical_gap <= 14.0
                and overlap >= shorter * 0.8
                and abs(first[4] - second[4]) <= max(2.0, shorter * 0.2)
                and has_left
                and has_right
            ):
                return True
    return False


def _drawing_item_bbox(
    item: tuple[Any, ...],
) -> tuple[float, float, float, float] | None:
    kind = item[0]
    if kind == "l" and len(item) >= 3:
        points = (item[1], item[2])
    elif kind == "c" and len(item) >= 5:
        points = item[1:5]
    elif kind == "re" and len(item) >= 2:
        rect = fitz.Rect(item[1])
        return tuple(float(value) for value in rect)
    elif kind == "qu" and len(item) >= 2:
        rect = fitz.Quad(item[1]).rect
        return tuple(float(value) for value in rect)
    else:
        return None
    xs = [float(point.x) for point in points]
    ys = [float(point.y) for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _has_local_cue(page: fitz.Page, rect: fitz.Rect) -> bool:
    cue_area = fitz.Rect(
        max(page.rect.x0, rect.x0 - 36.0),
        max(page.rect.y0, rect.y0 - 72.0),
        min(page.rect.x1, rect.x1 + 36.0),
        min(page.rect.y1, rect.y0),
    )
    return (
        not cue_area.is_empty
        and _CUE.search(page.get_text("text", clip=cue_area)) is not None
    )


def _text_overlaps(words: Iterable[tuple[Any, ...]], rect: fitz.Rect) -> bool:
    return any(
        _positive_overlap(rect, fitz.Rect(word[:4])) for word in words if len(word) >= 4
    )


def _other_drawing_overlaps(
    drawings: list[dict[str, Any]], drawing_index: int, rect: fitz.Rect
) -> bool:
    return any(
        index != drawing_index
        and _valid_rect(other.get("rect"))
        and _positive_overlap(rect, fitz.Rect(other["rect"]))
        for index, other in enumerate(drawings)
    )


def _other_drawing_is_ambiguously_near(
    drawings: list[dict[str, Any]], drawing_index: int, rect: fitz.Rect
) -> bool:
    expanded = fitz.Rect(rect.x0 - 4.0, rect.y0 - 4.0, rect.x1 + 4.0, rect.y1 + 4.0)
    return any(
        index != drawing_index
        and _valid_rect(other.get("rect"))
        and not _positive_overlap(rect, fitz.Rect(other["rect"]))
        and _positive_overlap(expanded, fitz.Rect(other["rect"]))
        for index, other in enumerate(drawings)
    )


def _positive_overlap(left: fitz.Rect, right: fitz.Rect) -> bool:
    intersection = left & right
    return not intersection.is_empty and intersection.width * intersection.height > 1e-6


def _valid_rect(value: Any) -> bool:
    try:
        rect = fitz.Rect(value)
    except (TypeError, ValueError):
        return False
    return bool(
        not rect.is_empty
        and not rect.is_infinite
        and all(math.isfinite(float(item)) for item in rect)
        and rect.width > 0
        and rect.height > 0
    )


def _rasterize(page: fitz.Page, rect: fitz.Rect) -> bytes:
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(VECTOR_RASTER_SCALE, VECTOR_RASTER_SCALE),
        clip=rect,
        colorspace=fitz.csRGB,
        alpha=False,
        annots=False,
    )
    return pixmap.tobytes("png")


__all__ = [
    "MAX_VECTOR_CANDIDATES_PER_DOCUMENT",
    "MAX_VECTOR_CANDIDATES_PER_PAGE",
    "MAX_VECTOR_DRAWINGS_PER_PAGE",
    "MAX_VECTOR_OPERATORS_PER_STREAM",
    "MAX_VECTOR_PAGES",
    "MAX_VECTOR_RESOURCE_DEPTH",
    "MAX_VECTOR_STREAM_BYTES",
    "VECTOR_RASTER_SCALE",
    "VectorEquationClusterDetector",
]
