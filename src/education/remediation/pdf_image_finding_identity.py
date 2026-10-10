"""Reconcile display-only AI labels without weakening saved image preservation.

Only unique, fully specified missing-alt findings may use this compatibility
path. Output correspondence comes from the reopened PDFs, never disappearance
of a display label. Page paint and resource proofs ignore object numbers and
structural markers, but retain graphics, masks, draw order and visibility.
"""

from __future__ import annotations

import hashlib
import math
import re
import zlib
from io import BytesIO
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

import pikepdf
import pymupdf as fitz
from PIL import Image

from ..pdf_checks.image_checker import _displayed_image_occurrences
from ..pdf_checks.image_semantics import structured_image_semantics
from ..pdf_checks.completeness import require_complete_pdf_scan
from .base import IssueCategory, RemediationIssue
from .content_tagger_v2 import (
    _page_render_signature,
    _region_render_geometry,
)
from .pdf_font_text import require_bounded_page_streams

FindingKey = tuple[str, str, str]
_MAX_STREAM_BYTES = 8 * 1024 * 1024
_MAX_RESOURCE_BYTES = 32 * 1024 * 1024
_MAX_NODES = 20_000
_RENDER_DPI = (72, 144)
_MAX_RENDER_BYTES = 256 * 1024 * 1024
_MAX_IMAGE_WORK_BYTES = 256 * 1024 * 1024
_IGNORED_STREAM_KEYS = frozenset({"/Length", "/StructParent", "/StructParents"})


@dataclass(frozen=True)
class ImageFindingBinding:
    key: FindingKey
    page_number: int
    image_xref: int
    image_index: int
    occurrence_ordinal: int
    bbox: tuple[float, ...]
    occurrence_id: str


@dataclass(frozen=True)
class SavedImageCorrespondence:
    occurrences: dict[tuple[int, int], dict[str, Any]]
    accessible: frozenset[tuple[int, int]]


def _identity(issue: RemediationIssue) -> tuple[Any, ...] | None:
    metadata = issue.metadata
    values = [
        metadata.get(key)
        for key in ("page_number", "image_xref", "image_index", "occurrence_ordinal")
    ]
    if any(type(value) is not int for value in values):
        return None
    page, xref, index, ordinal = (cast(int, value) for value in values)
    if page < 1 or xref < 1 or index < 0 or ordinal < 0:
        return None
    bounds = metadata.get("bbox")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        return None
    if any(
        type(value) not in (int, float) or not math.isfinite(value) for value in bounds
    ):
        return None
    bbox = tuple(float(value) for value in bounds)
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    occurrence_id = metadata.get("occurrence_id")
    if not isinstance(occurrence_id, str) or not re.fullmatch(
        r"imgocc-v1-[0-9a-f]{24}", occurrence_id
    ):
        return None
    if issue.location != f"Page {page}, Image {index + 1}":
        return None
    return page, xref, index, ordinal, bbox, occurrence_id


def _is_image_finding(issue: RemediationIssue) -> bool:
    return issue.category in (IssueCategory.ALT_TEXT, IssueCategory.CHART) and (
        issue.metadata.get("rule") == "WCAG 1.1.1"
        or any(
            key in issue.metadata
            for key in ("image_xref", "image_index", "occurrence_id", "has_alt_text")
        )
        or issue.metadata.get("issue_type") == "missing_alt_text"
        or issue.description.startswith(
            (
                "Image missing alternative text",
                "AI-Generated Alt Text:",
                "Chart/Graph detected - Alt:",
                "Decorative image detected",
            )
        )
    )


def _is_missing_image_wrapper(issue: RemediationIssue) -> bool:
    metadata = issue.metadata
    if (
        not _is_image_finding(issue)
        or metadata.get("rule") != "WCAG 1.1.1"
        or ("has_alt_text" in metadata and metadata["has_alt_text"] is not False)
    ):
        return False
    kind = metadata.get("issue_type")
    if kind is not None:
        return kind == "missing_alt_text"
    return issue.description in (
        "Image missing alternative text",
        "Image missing alternative text - AI analysis pending",
        'Decorative image detected - use empty alt="" attribute',
    ) or issue.description.startswith(
        ('AI-Generated Alt Text: "', 'Chart/Graph detected - Alt: "')
    )


def bind_missing_image_findings(
    submitted: Sequence[RemediationIssue],
    fresh: Sequence[RemediationIssue],
    finding_key: Callable[[RemediationIssue], FindingKey],
) -> dict[str, ImageFindingBinding]:
    """Use fresh source findings as truth; reject contradictory or duplicate claims."""
    identities: dict[tuple[Any, ...], list[RemediationIssue]] = {}
    for issue in fresh:
        if (
            not _is_image_finding(issue)
            or issue.metadata.get("issue_type") != "missing_alt_text"
            or issue.metadata.get("has_alt_text") is not False
        ):
            continue
        identity = _identity(issue)
        if identity is not None:
            identities.setdefault(identity, []).append(issue)
    ids = [issue.id for issue in submitted]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate submitted finding identities")
    bindings: dict[str, ImageFindingBinding] = {}
    claimed: set[tuple[Any, ...]] = set()
    for issue in submitted:
        if not _is_image_finding(issue):
            continue
        identity = _identity(issue)
        matches = identities.get(identity, []) if identity is not None else []
        if (
            not _is_missing_image_wrapper(issue)
            or len(matches) != 1
            or identity in claimed
        ):
            raise ValueError(
                "Image finding could not be uniquely reproduced from source"
            )
        assert identity is not None
        bindings[issue.id] = ImageFindingBinding(finding_key(matches[0]), *identity)
        claimed.add(identity)
    return bindings


class _ResourceProof:
    """Bounded xref-independent graph; preserve every rendering dependency."""

    def __init__(self) -> None:
        self.nodes = 0
        self.bytes = 0
        self.decoded_image_bytes = 0
        self.draw_bytes = 0
        self.active: set[tuple[int, int]] = set()
        self.cache: dict[tuple[int, int], str] = {}

    def value(self, value: Any, depth: int = 0) -> str:
        self.nodes += 1
        if self.nodes > _MAX_NODES or depth > 50:
            raise ValueError("Image resource proof exceeds bounds")
        if value is None or type(value) is bool:
            return self._digest((str(value).encode(),))
        if isinstance(value, (int, float, Decimal)):
            if not math.isfinite(value):
                raise ValueError("Non-finite image resource")
            return self._digest(
                (b"number", str(Decimal(str(value)).normalize()).encode())
            )
        if isinstance(value, pikepdf.Name):
            return self._digest((b"name", str(value).encode()))
        if isinstance(value, pikepdf.String):
            data = bytes(value)
            self.bytes += len(data)
            if self.bytes > _MAX_RESOURCE_BYTES:
                raise ValueError("Image resource proof exceeds bounds")
            return self._digest((b"string", data))
        identity = value.objgen if getattr(value, "is_indirect", False) else None
        if identity in self.active:
            raise ValueError("Cyclic image rendering dependency")
        if identity in self.cache:
            return self.cache[identity]
        if identity is not None:
            self.active.add(identity)
        try:
            result: tuple[Any, ...]
            if isinstance(value, pikepdf.Array):
                result = ("array", tuple(self.value(item, depth + 1) for item in value))
            elif isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
                if any(key in value for key in ("/OC", "/OCProperties")):
                    raise ValueError("Optional image visibility is unsupported")
                ignored = (
                    _IGNORED_STREAM_KEYS
                    if isinstance(value, pikepdf.Stream)
                    else frozenset()
                )
                transport = (
                    value.get("/Filter") if isinstance(value, pikepdf.Stream) else None
                )
                if isinstance(value, pikepdf.Stream) and transport not in (
                    None,
                    pikepdf.Name.FlateDecode,
                    pikepdf.Name.DCTDecode,
                ):
                    raise ValueError("Unsupported image resource transport")
                if value.get("/Subtype") in (pikepdf.Name.Form, pikepdf.Name.PS):
                    raise ValueError("Executable image resource scope is unsupported")
                if isinstance(value, pikepdf.Stream) and transport in (
                    None,
                    pikepdf.Name.FlateDecode,
                ):
                    parameters = value.get("/DecodeParms")
                    if parameters is None or (
                        isinstance(parameters, pikepdf.Dictionary)
                        and not len(parameters)
                    ):
                        ignored = ignored | {"/Filter", "/DecodeParms"}
                    else:
                        # Flate decoding here retains predictor-encoded bytes.
                        # Keep the Filter so predictor application cannot collide
                        # with an unfiltered stream carrying ignored parameters.
                        if transport != pikepdf.Name.FlateDecode or not isinstance(
                            parameters, pikepdf.Dictionary
                        ):
                            raise ValueError("Unsupported image predictor scope")
                        if any(
                            key
                            not in {
                                "/Predictor",
                                "/Colors",
                                "/Columns",
                                "/BitsPerComponent",
                            }
                            for key in parameters.keys()
                        ):
                            raise ValueError("Unsupported image predictor parameters")
                        predictor = parameters.get("/Predictor", 1)
                        colors = parameters.get("/Colors", 1)
                        columns = parameters.get("/Columns", 1)
                        bits = parameters.get("/BitsPerComponent", 8)
                        if (
                            any(
                                type(number) is not int
                                for number in (predictor, colors, columns, bits)
                            )
                            or predictor not in (1, 2, 10, 11, 12, 13, 14, 15)
                            or not (
                                1 <= cast(int, colors) <= 4
                                and 1 <= cast(int, columns) <= 10_000
                                and bits in (1, 2, 4, 8, 16)
                            )
                        ):
                            raise ValueError("Image predictor dimensions exceed bounds")
                if value.get("/Subtype") == pikepdf.Name.Image:
                    width, height = value.get("/Width"), value.get("/Height")
                    if (
                        type(width) is not int
                        or type(height) is not int
                        or not (
                            0 < width <= 10_000
                            and 0 < height <= 10_000
                            and width * height <= 25_000_000
                        )
                    ):
                        raise ValueError("Image decoded dimensions exceed bounds")
                    # Upper bound covers 16-bit CMYK plus image/mask resources.
                    self.decoded_image_bytes += width * height * 8
                    if self.decoded_image_bytes > _MAX_IMAGE_WORK_BYTES:
                        raise ValueError("Aggregate image decoding exceeds bounds")
                pairs = tuple(
                    (str(key), self.value(item, depth + 1))
                    for key, item in sorted(value.items())
                    if str(key) not in ignored
                )
                if isinstance(value, pikepdf.Stream):
                    length = value.get("/Length")
                    if type(length) is not int or not 0 <= length <= _MAX_STREAM_BYTES:
                        raise ValueError("Image stream proof exceeds bounds")
                    data = value.read_raw_bytes()
                    if len(data) > _MAX_STREAM_BYTES:
                        raise ValueError("Image stream proof exceeds bounds")
                    if transport == pikepdf.Name.DCTDecode:
                        if value.get("/Subtype") != pikepdf.Name.Image:
                            raise ValueError("Unsupported JPEG resource")
                        with Image.open(BytesIO(data)) as image:
                            if image.format != "JPEG" or image.size != (
                                value.Width,
                                value.Height,
                            ):
                                raise ValueError("Image encoded dimensions disagree")
                    # qpdf can recompress Flate streams when saving. Decode
                    # within an explicit budget; retain predictor/filter terms.
                    if value.get("/Filter") == pikepdf.Name.FlateDecode:
                        decoder = zlib.decompressobj()
                        data = decoder.decompress(data, _MAX_STREAM_BYTES + 1)
                        if (
                            len(data) > _MAX_STREAM_BYTES
                            or decoder.unconsumed_tail
                            or not decoder.eof
                            or decoder.unused_data
                        ):
                            raise ValueError("Image stream proof is unavailable")
                    self.bytes += len(data)
                    if self.bytes > _MAX_RESOURCE_BYTES:
                        raise ValueError("Image resource proof exceeds bounds")
                    result = ("stream", pairs, hashlib.sha256(data).hexdigest())
                else:
                    result = ("dict", pairs)
            else:
                raise ValueError("Unsupported image rendering dependency")
            encoded = repr(result).encode()
            self.bytes += len(encoded)
            if self.bytes > _MAX_RESOURCE_BYTES:
                raise ValueError("Image resource proof exceeds bounds")
            digest = self._digest((encoded,))
            if identity is not None:
                self.cache[identity] = digest
            return digest
        finally:
            if identity is not None:
                self.active.remove(identity)

    @staticmethod
    def _digest(parts: Sequence[bytes]) -> str:
        digest = hashlib.sha256()
        for part in parts:
            digest.update(len(part).to_bytes(8, "big") + part)
        return digest.hexdigest()


def _paint_proof(
    pdf: pikepdf.Pdf, page_index: int, proof: _ResourceProof | None = None
) -> tuple[Any, ...]:
    page = pdf.pages[page_index]
    if "/OCProperties" in pdf.Root or "/Group" in page.obj:
        raise ValueError("Optional/transparency page scope is unsupported")
    require_bounded_page_streams(page)
    proof = proof or _ResourceProof()
    resources = page.Resources
    paint = []
    markers = 0
    graphics = 0
    operations = list(pikepdf.parse_content_stream(page))
    if len(operations) > 100_000:
        raise ValueError("Image paint proof exceeds bounds")
    for instruction in operations:
        name, args = str(instruction.operator), list(instruction.operands)
        if name in {"BMC", "BDC"}:
            if len(args) != (1 if name == "BMC" else 2) or not isinstance(
                args[0], pikepdf.Name
            ):
                raise ValueError("Invalid structural marker")
            if args[0] == pikepdf.Name.OC:
                raise ValueError("Optional image visibility is unsupported")
            if name == "BDC":
                properties: Any = args[1]
                if isinstance(properties, pikepdf.Name):
                    properties = resources.get("/Properties", pikepdf.Dictionary()).get(
                        str(properties)
                    )
                if not isinstance(properties, pikepdf.Dictionary) or any(
                    key
                    not in {
                        "/MCID",
                        "/ActualText",
                        "/Alt",
                        "/Lang",
                        "/Type",
                        "/BBox",
                        "/Attached",
                    }
                    for key in properties.keys()
                ):
                    raise ValueError("Unsupported structural marker properties")
            markers += 1
            if markers > 50:
                raise ValueError("Image marker proof exceeds bounds")
            continue
        if name == "EMC":
            if args or markers <= 0:
                raise ValueError("Unbalanced structural markers")
            markers -= 1
            continue
        if name in {"BI", "INLINE IMAGE", "INLINE_IMAGE", "BX", "EX", "MP", "DP"}:
            raise ValueError("Unsupported image paint scope")
        if name == "Do":
            image = (
                resources.get("/XObject", pikepdf.Dictionary()).get(str(args[0]))
                if len(args) == 1
                else None
            )
            if (
                not isinstance(image, pikepdf.Stream)
                or image.get("/Subtype") != pikepdf.Name.Image
            ):
                raise ValueError("Only page-direct images have correspondence proofs")
            width, height = image.get("/Width"), image.get("/Height")
            if (
                type(width) is not int
                or type(height) is not int
                or not (
                    0 < width <= 10_000
                    and 0 < height <= 10_000
                    and width * height <= 25_000_000
                )
            ):
                raise ValueError("Image decoded dimensions exceed bounds")
            proof.draw_bytes += width * height * 8
            if proof.draw_bytes > _MAX_IMAGE_WORK_BYTES:
                raise ValueError("Aggregate image draw work exceeds bounds")
        if name == "q":
            graphics += 1
            if graphics > 50:
                raise ValueError("Image graphics proof exceeds bounds")
        elif name == "Q":
            graphics -= 1
            if graphics < 0:
                raise ValueError("Unbalanced image graphics")
        paint.append((name, tuple(proof.value(arg) for arg in args)))
    if markers or graphics:
        raise ValueError("Unbalanced image paint scope")
    # Properties are semantic marker metadata, already validated at each use.
    paint_resources = pikepdf.Dictionary(
        {key: value for key, value in resources.items() if key != "/Properties"}
    )
    annotations = page.obj.get("/Annots", pikepdf.Array())
    if not isinstance(annotations, pikepdf.Array) or len(annotations) > 1000:
        raise ValueError("Annotation paint scope exceeds bounds")
    annotation_paint = []
    for annotation in annotations:
        if (
            not isinstance(annotation, pikepdf.Dictionary)
            or annotation.get("/Subtype") != pikepdf.Name.Link
            or "/AP" in annotation
        ):
            raise ValueError("Annotation appearance scope is unsupported")
        parent = annotation.get("/P")
        if parent is not None and (
            not parent.is_indirect or parent.objgen != page.obj.objgen
        ):
            raise ValueError("Annotation page binding is unavailable")
        annotation_paint.append(
            proof.value(
                pikepdf.Dictionary(
                    {
                        key: value
                        for key, value in annotation.items()
                        if key
                        not in {
                            "/P",
                            "/Contents",
                            "/A",
                            "/AA",
                            "/NM",
                            "/M",
                            "/StructParent",
                        }
                    }
                )
            )
        )
    return (
        tuple(paint),
        proof.value(paint_resources),
        tuple(annotation_paint),
        proof.value(page.mediabox),
        proof.value(page.cropbox),
        proof.value(page.obj.get("/UserUnit", 1)),
    )


def preserve_image_pages(
    source_path: str,
    output_path: str,
    bindings: dict[str, ImageFindingBinding],
) -> SavedImageCorrespondence:
    """Prove a complete ordered image inventory and unchanged page presentation."""
    if not bindings:
        return SavedImageCorrespondence({}, frozenset())
    pages = sorted({binding.page_number for binding in bindings.values()})
    saved: dict[tuple[int, int], dict[str, Any]] = {}
    accessible: set[tuple[int, int]] = set()
    with (
        fitz.open(source_path) as before,
        fitz.open(output_path) as after,
        pikepdf.open(source_path, attempt_recovery=False) as source_pdf,
        pikepdf.open(output_path, attempt_recovery=False) as output_pdf,
    ):
        if len(before) != len(after) or len(before) > 500:
            raise ValueError("Image document page count changed")
        # Resource/dimension/decoded-stream limits precede image inventory and rendering.
        source_proof, output_proof = _ResourceProof(), _ResourceProof()
        draw_counts: dict[int, int] = {}
        for page_number in pages:
            if not 1 <= page_number <= len(before):
                raise ValueError("Image source page is unavailable")
            source_paint = _paint_proof(source_pdf, page_number - 1, source_proof)
            if source_paint != _paint_proof(output_pdf, page_number - 1, output_proof):
                raise ValueError("Image paint or rendering dependency changed")
            draw_counts[page_number] = sum(name == "Do" for name, _ in source_paint[0])
            if not 0 < draw_counts[page_number] <= 1000:
                raise ValueError("Image occurrence inventory exceeds bounds")
        render_bytes = 0
        for document in (before, after):
            for page_number in pages:
                for dpi in _RENDER_DPI:
                    width, height = _region_render_geometry(document, page_number, dpi)
                    render_bytes += width * height * 3
                    if render_bytes > _MAX_RENDER_BYTES:
                        raise ValueError("Image transaction render budget exceeded")
        for page_number in pages:
            source, output = before[page_number - 1], after[page_number - 1]
            old = _displayed_image_occurrences(source, page_number)
            new = _displayed_image_occurrences(output, page_number)
            if len(old) != len(new) or len(old) != draw_counts[page_number]:
                raise ValueError("Image occurrence inventory changed")
            if source.rect != output.rect or source.rotation != output.rotation:
                raise ValueError("Image page geometry changed")
            # The graph proof catches subpixel or masked changes even where
            # a raster happens to look identical. Renders add an independent
            # check of inherited page geometry and renderer interpretation.
            for dpi in _RENDER_DPI:
                if _page_render_signature(
                    before, page_number, dpi
                ) != _page_render_signature(after, page_number, dpi):
                    raise ValueError("Image page rendered appearance changed")
            for original, current in zip(old, new):
                if any(
                    original[key] != current[key] for key in ("image_index", "bbox")
                ):
                    raise ValueError("Image occurrence placement changed")
                saved[(page_number, current["image_index"])] = current
            with require_complete_pdf_scan(True):
                semantics = structured_image_semantics(
                    output_path, page_number - 1, new
                )
            if semantics is not None:
                accessible.update(
                    (page_number, current["image_index"])
                    for current in new
                    if semantics[current["occurrence_id"]][0] is True
                )
            for binding in bindings.values():
                if binding.page_number != page_number:
                    continue
                source_occurrence = (
                    old[binding.image_index] if binding.image_index < len(old) else None
                )
                if source_occurrence is None or (
                    source_occurrence["image_xref"],
                    source_occurrence["occurrence_ordinal"],
                    source_occurrence["bbox"],
                    source_occurrence["occurrence_id"],
                ) != (
                    binding.image_xref,
                    binding.occurrence_ordinal,
                    binding.bbox,
                    binding.occurrence_id,
                ):
                    raise ValueError("Source image occurrence identity changed")
    return SavedImageCorrespondence(saved, frozenset(accessible))


def output_image_key(
    issue: RemediationIssue,
    saved: SavedImageCorrespondence,
    finding_key: Callable[[RemediationIssue], FindingKey],
) -> FindingKey:
    """Bind an unresolved output image to the proven source draw position."""
    identity = _identity(issue) if _is_image_finding(issue) else None
    if identity is None or (identity[0], identity[2]) not in saved.occurrences:
        return finding_key(issue)
    current = saved.occurrences[(identity[0], identity[2])]
    if identity != (
        current["page_number"],
        current["image_xref"],
        current["image_index"],
        current["occurrence_ordinal"],
        current["bbox"],
        current["occurrence_id"],
    ) or not _is_missing_image_wrapper(issue):
        raise ValueError("Output image finding identity is unavailable")
    return (
        IssueCategory.ALT_TEXT.value,
        issue.location or "",
        "Image missing alternative text",
    )
