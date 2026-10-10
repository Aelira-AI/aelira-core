"""Apply a complete, source-bound semantic review to page-direct PDF content.

Each text-show and image occurrence is explicitly assigned to structure or an
Artifact. Original tokens are copied byte-for-byte; only marked-content tokens
and a structure tree are added. Unicode comes from strict font decoding, never
ActualText. Caller provenance records a supplied review, not human approval.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from typing import Any, NoReturn, cast

import pikepdf
from pdfminer.pdfpage import PDFPage
from pdfminer.utils import apply_matrix_pt

from ..pdf_checks.marked_content import (
    _FontResourceManager,
    _MarkedTextDevice,
    _PageInterpreter,
)
from .pdf_font_text import (
    MAX_OPERATIONS,
    MAX_TEXT_OPERATIONS,
    _TEXT_OPERATORS,
    decode_page_text_runs,
)
from .pdf_ocr_form import (
    MAX_GLYPHS,
    MAX_OBJECTS,
    MAX_PAGES,
    MAX_PDF_BYTES,
    _BoundedBuffer,
)
from .pdf_verified_font_recovery import _fingerprint, _require_text_operands

_SHOWS = frozenset({"Tj", "TJ", "'", '"'})
_SAFE_TEXT_STATE = frozenset(
    {"Tf", "Tc", "Tw", "Tz", "TL", "Tr", "Ts", "Tm", "Td", "TD", "T*"}
)
_PAINTS = frozenset({"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "sh"})
_ROLES = frozenset(
    {"H1", "H2", "H3", "H4", "H5", "H6", "P", "L", "LI", "Lbl", "LBody", "Figure"}
)


class ReviewedSemanticsError(ValueError):
    def __init__(self, code: str):
        self.code = "reviewed_semantics_" + code
        super().__init__(self.code)


def _refuse(code: str) -> NoReturn:
    raise ReviewedSemanticsError(code)


@dataclass(frozen=True)
class SemanticGlyph:
    text: str
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]


@dataclass(frozen=True)
class SemanticOccurrence:
    page_index: int
    operator_index: int
    kind: str
    text: str | None
    image_sha256: str | None
    bbox: tuple[float, float, float, float] | None
    artifact: bool = False
    glyphs: tuple[SemanticGlyph, ...] = ()


@dataclass(frozen=True)
class SemanticInventory:
    source_sha256: str
    occurrences: tuple[SemanticOccurrence, ...]
    page_count: int
    vector_paint_count: int = 0


@dataclass(frozen=True)
class ReviewedOccurrence:
    page_index: int
    operator_index: int
    text: str | None = None
    image_sha256: str | None = None


@dataclass(frozen=True)
class ReviewedSemanticNode:
    node_id: str
    role: str
    children: tuple[str, ...] = ()
    occurrences: tuple[ReviewedOccurrence, ...] = ()
    alt: str | None = None
    outline_title: str | None = None


@dataclass(frozen=True)
class ReviewedSemanticManifest:
    source_sha256: str
    reviewer: str
    review_reference: str
    title: str
    language: str
    nodes: tuple[ReviewedSemanticNode, ...]
    root_ids: tuple[str, ...]
    artifacts: tuple[ReviewedOccurrence, ...] = ()
    vector_artifacts_reviewed: bool = False


@dataclass(frozen=True)
class ReviewedSemanticResult:
    pdf_bytes: bytes
    source_sha256: str
    output_sha256: str
    review_sha256: str
    semantic_occurrences: int
    artifact_occurrences: int


@dataclass(frozen=True)
class _TokenSpan:
    start: int
    end: int
    name: str


class _Tokens(pikepdf.TokenFilter):
    """Keep original lexemes, including numeric precision and string escapes."""

    def __init__(self) -> None:
        super().__init__()
        self.raw = bytearray()
        self.spans: list[_TokenSpan] = []
        self.depth = 0
        self.start = 0
        self.count = 0

    def handle_token(self, token: pikepdf.Token | None = None) -> pikepdf.Token | None:
        if token is None:
            return None
        self.count += 1
        if self.count > 1_000_000:
            _refuse("token_limit")
        self.raw.extend(token.raw_value)
        if len(self.raw) > 8 * 1024 * 1024:
            _refuse("stream_limit")
        kind = token.type_
        if kind in {
            pikepdf.TokenType.bad,
            pikepdf.TokenType.inline_image,
            pikepdf.TokenType.brace_open,
            pikepdf.TokenType.brace_close,
        }:
            _refuse("token_scope")
        if kind in {pikepdf.TokenType.array_open, pikepdf.TokenType.dict_open}:
            self.depth += 1
            if self.depth > 50:
                _refuse("token_depth")
        elif kind in {pikepdf.TokenType.array_close, pikepdf.TokenType.dict_close}:
            self.depth -= 1
            if self.depth < 0:
                _refuse("token_balance")
        elif kind == pikepdf.TokenType.word and not self.depth:
            self.spans.append(_TokenSpan(self.start, len(self.raw), token.value))
            self.start = len(self.raw)
        elif kind == pikepdf.TokenType.eof and self.depth:
            _refuse("token_balance")
        return token


class _ShowDevice(_MarkedTextDevice):
    def __init__(self, manager: Any):
        super().__init__(manager, lambda _reason: _refuse("font_decode"))
        self.current: dict[str, Any] | None = None
        self.shows: list[tuple[str, Any, tuple[SemanticGlyph, ...]]] = []
        self.bounds: list[Any] = []
        self.glyphs: list[SemanticGlyph] = []
        self.marked_depth = 0

    def _entry(self):
        return self.current

    def begin_tag(self, tag, props=None):
        self.marked_depth += 1
        if self.marked_depth > 50:
            _refuse("marked_depth")

    def end_tag(self):
        self.marked_depth -= 1
        if self.marked_depth < 0:
            _refuse("marked_balance")

    def render_string(self, state, seq, ncs, graphics):
        self.current = {"text": "", "previous": None, "source": "font"}
        self.bounds = []
        self.glyphs = []
        super().render_string(state, seq, ncs, graphics)
        bbox = None
        if self.bounds:
            bbox = (
                min(b[0] for b in self.bounds),
                min(b[1] for b in self.bounds),
                max(b[2] for b in self.bounds),
                max(b[3] for b in self.bounds),
            )
        self.shows.append((self.current["text"], bbox, tuple(self.glyphs)))
        self.current = None

    def render_char(self, *args):
        advance = super().render_char(*args)
        if self.current is None or self.current["previous"] is None:
            _refuse("font_decode")
        box = self.current["previous"].bbox
        if not all(math.isfinite(v) and abs(v) <= 1_000_000 for v in box):
            _refuse("geometry")
        self.bounds.append(box)
        self.glyphs.append(
            SemanticGlyph(
                self.current["previous"].get_text(), box, (args[0][4], args[0][5])
            )
        )
        return advance


class _ShowInterpreter(_PageInterpreter):
    def __init__(self, manager: Any, device: _ShowDevice):
        super().__init__(manager, device)
        self.images: list[tuple[float, float, float, float]] = []

    def do_Do(self, name):
        super().do_Do(name)
        corners = [
            apply_matrix_pt(self.ctm, pt) for pt in ((0, 0), (0, 1), (1, 0), (1, 1))
        ]
        box = (
            min(pt[0] for pt in corners),
            min(pt[1] for pt in corners),
            max(pt[0] for pt in corners),
            max(pt[1] for pt in corners),
        )
        if not all(math.isfinite(v) and abs(v) <= 1_000_000 for v in box):
            _refuse("geometry")
        self.images.append(box)


def _inspect(
    source: bytes, pdf: pikepdf.Pdf, *, tagged: bool = False
) -> tuple[SemanticInventory, list[Any], list[_Tokens]]:
    if (
        pdf.is_encrypted
        or not 1 <= len(pdf.pages) <= MAX_PAGES
        or len(pdf.objects) > MAX_OBJECTS
    ):
        _refuse("document_limit")
    if any(key in pdf.Root for key in ("/AcroForm", "/Perms")) or (
        not tagged and "/StructTreeRoot" in pdf.Root
    ):
        _refuse("document_scope")
    if tagged:
        _tree_evidence(pdf)
    occurrences = []
    operations = []
    token_pages = []
    decoded_pages = list(PDFPage.get_pages(io.BytesIO(source)))
    if len(decoded_pages) != len(pdf.pages):
        _refuse("page_count")
    total_ops = total_bytes = vector_paints = text_operations = total_glyphs = 0
    for page_index, page in enumerate(pdf.pages):
        if "/Annots" in page.obj or (not tagged and "/StructParents" in page.obj):
            _refuse("document_scope")
        # This bounds streams/fonts/glyphs and validates all BT/ET source text.
        decode_page_text_runs(pdf, page_index)
        ops = list(pikepdf.parse_content_stream(page))
        total_ops += len(ops)
        if total_ops > MAX_OPERATIONS:
            _refuse("operation_limit")
        tokens = _Tokens()
        page.get_filtered_contents(tokens)
        total_bytes += len(tokens.raw)
        if total_bytes > MAX_PDF_BYTES or [span.name for span in tokens.spans] != [
            str(op.operator) for op in ops
        ]:
            _refuse("token_binding")
        for op in ops:
            name = str(op.operator)
            vector_paints += name in _PAINTS
            text_operations += name in _TEXT_OPERATORS
            if text_operations > MAX_TEXT_OPERATIONS:
                _refuse("text_operation_limit")
            if not tagged and name in {"BMC", "BDC", "EMC", "MP", "DP", "BX", "EX"}:
                _refuse("marked_source")
            method = "do_" + name.replace("*", "_a").replace('"', "_w").replace(
                "'", "_q"
            )
            if not hasattr(_PageInterpreter, method):
                _refuse("operator")
            _require_text_operands(name, list(op.operands))
        ext = page.Resources.get("/ExtGState", pikepdf.Dictionary())
        if not isinstance(ext, pikepdf.Dictionary) or any(
            "/Font" in state for _, state in ext.items()
        ):
            _refuse("graphics_font")
        manager = _FontResourceManager(lambda _reason: _refuse("font_decode"))
        device = _ShowDevice(manager)
        device.character_count = total_glyphs
        interpreter = _ShowInterpreter(manager, device)
        interpreter.process_page(decoded_pages[page_index])
        total_glyphs = device.character_count
        if total_glyphs > MAX_GLYPHS:
            _refuse("glyph_limit")
        if device.marked_depth:
            _refuse("marked_balance")
        artifact_states = (
            _marked_occurrences(
                ops, len(pdf.Root.StructTreeRoot.ParentTree.Nums[page_index * 2 + 1])
            )
            if tagged
            else {}
        )
        show_ops = [
            (index, op) for index, op in enumerate(ops) if str(op.operator) in _SHOWS
        ]
        image_ops = [
            (index, op) for index, op in enumerate(ops) if str(op.operator) == "Do"
        ]
        if len(show_ops) != len(device.shows) or len(image_ops) != len(
            interpreter.images
        ):
            _refuse("occurrence_binding")
        for (index, _), (text, bbox, glyphs) in zip(
            show_ops, device.shows, strict=True
        ):
            occurrences.append(
                SemanticOccurrence(
                    page_index,
                    index,
                    "text",
                    text,
                    None,
                    bbox,
                    artifact_states.get(index, False),
                    glyphs,
                )
            )
        for (index, op), bbox in zip(image_ops, interpreter.images, strict=True):
            if len(op.operands) != 1:
                _refuse("image_binding")
            image = page.Resources.XObject[str(op.operands[0])]
            if image.get("/Subtype") != pikepdf.Name.Image:
                _refuse("image_scope")
            occurrences.append(
                SemanticOccurrence(
                    page_index,
                    index,
                    "image",
                    None,
                    _fingerprint(image),
                    bbox,
                    artifact_states.get(index, False),
                )
            )
        operations.append(ops)
        token_pages.append(tokens)
    return (
        SemanticInventory(
            hashlib.sha256(source).hexdigest(),
            tuple(sorted(occurrences, key=lambda v: (v.page_index, v.operator_index))),
            len(pdf.pages),
            vector_paints,
        ),
        operations,
        token_pages,
    )


def inspect_reviewed_semantic_source(source: bytes) -> SemanticInventory:
    try:
        if not isinstance(source, bytes) or not source or len(source) > MAX_PDF_BYTES:
            _refuse("byte_limit")
        with pikepdf.open(io.BytesIO(source), attempt_recovery=False) as pdf:
            return _inspect(source, pdf)[0]
    except ReviewedSemanticsError:
        raise
    except Exception:
        _refuse("unsupported_source")


def inspect_tagged_semantic_source(source: bytes) -> SemanticInventory:
    """Inspect strict source glyphs and artifact scope without ActualText.

    This narrow reader requires the explicit page-MCR/ParentTree ownership
    supported by this compiler. It does not decide that any Artifact is
    decorative or safe to omit; callers must independently establish that.
    Glyph boxes/origins use pdfminer's normalized PDF page coordinate system.
    """
    try:
        if not isinstance(source, bytes) or not source or len(source) > MAX_PDF_BYTES:
            _refuse("byte_limit")
        with pikepdf.open(io.BytesIO(source), attempt_recovery=False) as pdf:
            return _inspect(source, pdf, tagged=True)[0]
    except ReviewedSemanticsError:
        raise
    except Exception:
        _refuse("unsupported_source")


def _marked_occurrences(ops: list[Any], owner_count: int) -> dict[int, bool]:
    frames: list[tuple[bool, int | None]] = []
    claimed: set[int] = set()
    artifacts = {}
    for index, op in enumerate(ops):
        name, args = str(op.operator), list(op.operands)
        if name in {"BMC", "BDC"}:
            if len(args) != (1 if name == "BMC" else 2) or not isinstance(
                args[0], pikepdf.Name
            ):
                _refuse("marked_scope")
            artifact = args[0] == pikepdf.Name.Artifact
            mcid: int | None = None
            if name == "BDC":
                if not isinstance(args[1], pikepdf.Dictionary):
                    _refuse("marked_scope")
                raw_mcid = args[1].get("/MCID")
                if raw_mcid is not None:
                    if (
                        type(raw_mcid) is not int
                        or not 0 <= raw_mcid < owner_count
                        or raw_mcid in claimed
                    ):
                        _refuse("marked_ownership")
                    mcid = int(raw_mcid)
                    claimed.add(mcid)
            frames.append((artifact, mcid))
            if len(frames) > 50:
                _refuse("marked_depth")
        elif name == "EMC":
            if args or not frames:
                _refuse("marked_balance")
            frames.pop()
        elif name in _SHOWS or name == "Do":
            is_artifact = any(frame[0] for frame in frames)
            mcids = [frame[1] for frame in frames if frame[1] is not None]
            if (
                len(mcids) > 1
                or (is_artifact and mcids)
                or (not is_artifact and not mcids)
            ):
                _refuse("marked_ownership")
            artifacts[index] = is_artifact
    if frames or claimed != set(range(owner_count)):
        _refuse("marked_ownership")
    return artifacts


def _validate(
    manifest: ReviewedSemanticManifest, inventory: SemanticInventory
) -> tuple[dict[str, ReviewedSemanticNode], dict[tuple[int, int], str | None]]:
    if manifest.source_sha256 != inventory.source_sha256:
        _refuse("source_changed")
    if inventory.vector_paint_count and manifest.vector_artifacts_reviewed is not True:
        _refuse("vector_review_required")
    if any(
        not isinstance(v, str) or not v.strip() or len(v) > 1024
        for v in (
            manifest.reviewer,
            manifest.review_reference,
            manifest.title,
            manifest.language,
        )
    ):
        _refuse("metadata")
    if not manifest.nodes or len(manifest.nodes) > 20_000:
        _refuse("node_limit")
    nodes = {node.node_id: node for node in manifest.nodes}
    if len(nodes) != len(manifest.nodes) or any(
        not isinstance(key, str) or not key or len(key) > 100 for key in nodes
    ):
        _refuse("node_identity")
    visited: set[str] = set()

    def walk(key: str, depth: int) -> None:
        if depth > 30 or key in visited or key not in nodes:
            _refuse("hierarchy")
        visited.add(key)
        node = nodes[key]
        if node.role not in _ROLES or bool(node.children) == bool(node.occurrences):
            _refuse("node_role")
        if node.role == "L" and (
            not node.children
            or any(
                nodes.get(child) is None or nodes[child].role != "LI"
                for child in node.children
            )
        ):
            _refuse("list_hierarchy")
        if node.role == "LI":
            roles = [
                nodes[child].role if child in nodes else None for child in node.children
            ]
            if roles not in [["LBody"], ["Lbl", "LBody"]]:
                _refuse("list_hierarchy")
        if node.children and node.role not in {"L", "LI", "LBody"}:
            _refuse("node_role")
        if node.role == "Figure":
            if (
                not isinstance(node.alt, str)
                or not node.alt.strip()
                or len(node.alt) > 16_000
            ):
                _refuse("figure_alt")
        elif node.alt is not None:
            _refuse("node_alt")
        if (
            node.role != "Figure"
            and node.occurrences
            and not any(o.text and o.text.strip() for o in node.occurrences)
        ):
            _refuse("empty_text_node")
        if node.outline_title is not None:
            if (
                not node.role.startswith("H")
                or not isinstance(node.outline_title, str)
                or not node.outline_title.strip()
                or len(node.outline_title) > 1024
                or "".join(node.outline_title.split())
                != "".join("".join(o.text or "" for o in node.occurrences).split())
            ):
                _refuse("outline_title")
        for child in node.children:
            walk(child, depth + 1)

    for key in manifest.root_ids:
        if key in nodes and nodes[key].role in {"LI", "Lbl", "LBody"}:
            _refuse("hierarchy")
        walk(key, 0)
    if visited != set(nodes):
        _refuse("hierarchy")
    expected = {(o.page_index, o.operator_index): o for o in inventory.occurrences}
    bindings: dict[tuple[int, int], str | None] = {}
    for node_id, reviewed in [
        (node.node_id, node.occurrences) for node in manifest.nodes
    ] + [(None, manifest.artifacts)]:
        if len(reviewed) > 20_000:
            _refuse("occurrence_limit")
        for occurrence in reviewed:
            location = (occurrence.page_index, occurrence.operator_index)
            if (
                any(type(value) is not int for value in location)
                or location in bindings
                or location not in expected
            ):
                _refuse("occurrence_coverage")
            source = expected[location]
            if (
                occurrence.text != source.text
                or occurrence.image_sha256 != source.image_sha256
            ):
                _refuse("occurrence_changed")
            if node_id is not None and (nodes[node_id].role == "Figure") != (
                source.kind == "image"
            ):
                _refuse("occurrence_role")
            bindings[location] = node_id
    if set(bindings) != set(expected):
        _refuse("occurrence_coverage")
    return nodes, bindings


def _text_regions(
    page_index: int,
    ops: list[Any],
    bindings: dict[tuple[int, int], str | None],
    nodes: dict[str, ReviewedSemanticNode],
) -> dict[int, tuple[int, tuple[tuple[int, int], ...]]]:
    """Coalesce source-adjacent shows without crossing semantic/state barriers."""
    ranks = {
        (o.page_index, o.operator_index): rank
        for node in nodes.values()
        for rank, o in enumerate(node.occurrences)
    }
    regions = {}
    consumed = -1
    for index, op in enumerate(ops):
        key = page_index, index
        node_id = bindings.get(key)
        if index <= consumed or node_id is None or str(op.operator) not in _SHOWS:
            continue
        last = index
        keys = [key]
        cursor = index + 1
        while cursor < len(ops):
            name = str(ops[cursor].operator)
            if name in _SAFE_TEXT_STATE:
                cursor += 1
                continue
            next_key = page_index, cursor
            if (
                name not in _SHOWS
                or bindings.get(next_key) != node_id
                or ranks[next_key] != ranks[keys[-1]] + 1
            ):
                break
            last = cursor
            keys.append(next_key)
            cursor += 1
        regions[index] = last, tuple(keys)
        consumed = last
    return regions


def apply_reviewed_semantics(
    source: bytes, manifest: ReviewedSemanticManifest
) -> ReviewedSemanticResult:
    """Return a completely classified derivative; never save or publish a file."""
    try:
        if not isinstance(source, bytes) or not source or len(source) > MAX_PDF_BYTES:
            _refuse("byte_limit")
        with pikepdf.open(io.BytesIO(source), attempt_recovery=False) as pdf:
            inventory, operations, token_pages = _inspect(source, pdf)
            nodes, bindings = _validate(manifest, inventory)
            resource_hashes = [_fingerprint(page.Resources) for page in pdf.pages]
            page_geometry: list[tuple[Any, ...]] = [
                (
                    tuple(cast(Any, page.mediabox)),
                    tuple(cast(Any, page.cropbox)),
                    page.obj.get("/Rotate"),
                )
                for page in pdf.pages
            ]
            root = pdf.make_indirect(
                pikepdf.Dictionary(Type=pikepdf.Name.StructTreeRoot)
            )
            document = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.StructElem, S=pikepdf.Name.Document, P=root
                )
            )
            elements = {
                key: pdf.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.StructElem, S=pikepdf.Name("/" + node.role)
                    )
                )
                for key, node in nodes.items()
            }
            owners: list[list[Any]] = [[] for _ in pdf.pages]
            references = {}
            planned_contents = []
            for page_index, (page, ops, tokens) in enumerate(
                zip(pdf.pages, operations, token_pages, strict=True)
            ):
                output = bytearray()
                cursor = 0
                regions = _text_regions(page_index, ops, bindings, nodes)
                consumed = -1
                for operator_index, (op, span) in enumerate(
                    zip(ops, tokens.spans, strict=True)
                ):
                    key = (page_index, operator_index)
                    if operator_index <= consumed:
                        continue
                    if key not in bindings and str(op.operator) not in _PAINTS:
                        continue
                    node_id = bindings.get(key)
                    last, region_keys = regions.get(
                        operator_index, (operator_index, (key,))
                    )
                    final_span = tokens.spans[last]
                    if node_id is None:
                        prefix = b"\n/Artifact BMC\n"
                    else:
                        mcid = len(owners[page_index])
                        if mcid >= 20_000:
                            _refuse("mcid_limit")
                        owners[page_index].append(elements[node_id])
                        reference = pikepdf.Dictionary(
                            Type=pikepdf.Name.MCR, Pg=page.obj, MCID=mcid
                        )
                        for region_key in region_keys:
                            references[region_key] = reference
                        prefix = (
                            f"\n/{nodes[node_id].role} << /MCID {mcid} >> BDC\n".encode(
                                "ascii"
                            )
                        )
                    output.extend(tokens.raw[cursor : span.start])
                    output.extend(prefix)
                    output.extend(tokens.raw[span.start : final_span.end])
                    output.extend(b"\nEMC\n")
                    cursor = final_span.end
                    consumed = last
                output.extend(tokens.raw[cursor:])
                if len(output) > 8 * 1024 * 1024:
                    _refuse("stream_limit")
                planned_contents.append(bytes(output))
                page.Contents = pdf.make_stream(bytes(output))
                page.obj.StructParents = page_index
                page.obj.Tabs = pikepdf.Name.S
            for node_key, node in nodes.items():
                element = elements[node_key]
                if node.alt is not None:
                    element.Alt = pikepdf.String(node.alt)
                if node.children:
                    element.K = pikepdf.Array(
                        [elements[child] for child in node.children]
                    )
                    for child in node.children:
                        elements[child].P = element
                else:
                    kids = []
                    previous = None
                    for occurrence in node.occurrences:
                        reference = references[
                            (occurrence.page_index, occurrence.operator_index)
                        ]
                        reference_key = occurrence.page_index, int(reference.MCID)
                        if reference_key != previous:
                            kids.append(reference)
                        previous = reference_key
                    element.K = pikepdf.Array(kids)
            document.K = pikepdf.Array([elements[key] for key in manifest.root_ids])
            for node_key in manifest.root_ids:
                elements[node_key].P = document
            root.K = pikepdf.Array([document])
            root.ParentTree = pdf.make_indirect(
                pikepdf.Dictionary(
                    Nums=pikepdf.Array(
                        [
                            value
                            for index, array in enumerate(owners)
                            for value in (index, pikepdf.Array(array))
                        ]
                    )
                )
            )
            root.ParentTreeNextKey = len(pdf.pages)
            pdf.Root.StructTreeRoot = root
            pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
            pdf.Root.Lang = pikepdf.String(manifest.language)
            preferences = pdf.Root.get("/ViewerPreferences", pikepdf.Dictionary())
            if not isinstance(preferences, pikepdf.Dictionary):
                _refuse("metadata")
            preferences.DisplayDocTitle = True
            pdf.Root.ViewerPreferences = preferences
            pdf.docinfo.Title = pikepdf.String(manifest.title)
            _write_outlines(pdf, manifest, nodes)
            outline_evidence = _outline_evidence(pdf)
            # Expected tree shape is independent of object numbers after save.
            tree_hash = _tree_evidence(pdf)
            serialized = _BoundedBuffer()
            pdf.save(
                serialized,
                deterministic_id=True,
                fix_metadata_version=False,
                compress_streams=False,
                stream_decode_level=pikepdf.StreamDecodeLevel.none,
            )
        saved_bytes = serialized.getvalue()
        with pikepdf.open(io.BytesIO(saved_bytes), attempt_recovery=False) as saved:
            if (
                len(saved.pages) != inventory.page_count
                or _tree_evidence(saved) != tree_hash
                or _outline_evidence(saved) != outline_evidence
            ):
                _refuse("structure_changed")
            if (
                str(saved.docinfo.Title) != manifest.title
                or str(saved.Root.Lang) != manifest.language
                or saved.Root.ViewerPreferences.DisplayDocTitle is not True
            ):
                _refuse("metadata_changed")
            for index, page in enumerate(saved.pages):
                if (
                    page.Contents.read_bytes() != planned_contents[index]
                    or _fingerprint(page.Resources) != resource_hashes[index]
                    or (
                        tuple(cast(Any, page.mediabox)),
                        tuple(cast(Any, page.cropbox)),
                        page.obj.get("/Rotate"),
                    )
                    != page_geometry[index]
                ):
                    _refuse("artwork_changed")
            verified, _, _ = _inspect(saved_bytes, saved, tagged=True)
            original_content = [
                (o.page_index, o.kind, o.text, o.image_sha256, o.bbox, o.glyphs)
                for o in inventory.occurrences
            ]
            saved_content = [
                (o.page_index, o.kind, o.text, o.image_sha256, o.bbox, o.glyphs)
                for o in verified.occurrences
            ]
            if original_content != saved_content:
                _refuse("source_changed")
        review_hash = hashlib.sha256(repr(manifest).encode("utf-8")).hexdigest()
        return ReviewedSemanticResult(
            saved_bytes,
            inventory.source_sha256,
            hashlib.sha256(saved_bytes).hexdigest(),
            review_hash,
            sum(v is not None for v in bindings.values()),
            sum(v is None for v in bindings.values()),
        )
    except ReviewedSemanticsError:
        raise
    except Exception:
        _refuse("verification_failed")


def _tree_evidence(pdf: pikepdf.Pdf) -> tuple[Any, ...]:
    """Check exact hierarchy, ParentTree ownership, and unique MCR references."""
    pages = {page.obj.objgen: index for index, page in enumerate(pdf.pages)}
    root = pdf.Root.StructTreeRoot
    nums = root.ParentTree.Nums
    if len(nums) != 2 * len(pages):
        _refuse("parent_tree")
    owners = {}
    for index, page in enumerate(pdf.pages):
        if nums[index * 2] != index or page.obj.StructParents != index:
            _refuse("parent_tree")
        owners[index] = nums[index * 2 + 1]
    seen_nodes: set[tuple[int, ...]] = set()
    seen_mcr = set()

    def walk(node, parent, depth):
        if (
            depth > 32
            or len(seen_nodes) > 20_001
            or node.objgen in seen_nodes
            or node.P.objgen != parent.objgen
            or "/ActualText" in node
        ):
            _refuse("structure_ownership")
        seen_nodes.add(node.objgen)
        kids = []
        for kid in node.K:
            if kid.get("/Type") == pikepdf.Name.MCR:
                page_index = pages.get(kid.Pg.objgen)
                mcid = int(kid.MCID)
                key = page_index, mcid
                if (
                    page_index is None
                    or key in seen_mcr
                    or not 0 <= mcid < len(owners[page_index])
                    or owners[page_index][mcid].objgen != node.objgen
                ):
                    _refuse("parent_tree")
                seen_mcr.add(key)
                kids.append(("MCR", page_index, mcid))
            else:
                kids.append(walk(kid, node, depth + 1))
        return str(node.S), str(node.get("/Alt", "")), tuple(kids)

    evidence = tuple(walk(node, root, 0) for node in cast(Any, root.K))
    if len(seen_mcr) != sum(len(value) for value in owners.values()):
        _refuse("parent_tree")
    return evidence


def _write_outlines(
    pdf: pikepdf.Pdf,
    manifest: ReviewedSemanticManifest,
    nodes: dict[str, ReviewedSemanticNode],
) -> None:
    ordered = []

    def collect(key):
        node = nodes[key]
        if node.outline_title is not None:
            ordered.append(node)
        for child in node.children:
            collect(child)

    for key in manifest.root_ids:
        collect(key)
    if not ordered:
        return
    if "/Outlines" in pdf.Root:
        _refuse("existing_outline")
    root = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Outlines))
    entry: dict[str, Any] = {"obj": root, "level": 0, "kids": []}
    stack = [entry]
    for node in ordered:
        level = int(node.role[1:])
        while stack[-1]["level"] >= level:
            stack.pop()
        parent = stack[-1]
        item = pdf.make_indirect(
            pikepdf.Dictionary(
                Title=pikepdf.String(node.outline_title),
                Parent=parent["obj"],
                Dest=pikepdf.Array(
                    [pdf.pages[node.occurrences[0].page_index].obj, pikepdf.Name.Fit]
                ),
            )
        )
        child = {"obj": item, "level": level, "kids": []}
        parent["kids"].append(child)
        stack.append(child)

    def link(item):
        kids = item["kids"]
        if not kids:
            return 0
        count = 0
        for index, child in enumerate(kids):
            if index:
                child["obj"].Prev = kids[index - 1]["obj"]
            if index + 1 < len(kids):
                child["obj"].Next = kids[index + 1]["obj"]
            count += 1 + link(child)
        item["obj"].First, item["obj"].Last, item["obj"].Count = (
            kids[0]["obj"],
            kids[-1]["obj"],
            count,
        )
        return count

    link(entry)
    pdf.Root.Outlines = root


def _outline_evidence(pdf: pikepdf.Pdf) -> tuple[Any, ...]:
    root = pdf.Root.get("/Outlines")
    if root is None:
        return ()
    pages = {page.obj.objgen: index for index, page in enumerate(pdf.pages)}
    seen: set[tuple[int, ...]] = set()

    def walk(parent, depth):
        if depth > 30:
            _refuse("outline")
        current = parent.get("/First")
        previous = None
        result = []
        while current is not None:
            if (
                len(seen) >= 20_000
                or current.objgen in seen
                or current.Parent.objgen != parent.objgen
            ):
                _refuse("outline")
            seen.add(current.objgen)
            if (current.get("/Prev") is None) != (previous is None) or (
                previous is not None and current.Prev.objgen != previous.objgen
            ):
                _refuse("outline")
            dest = current.Dest
            if (
                len(dest) != 2
                or dest[1] != pikepdf.Name.Fit
                or dest[0].objgen not in pages
            ):
                _refuse("outline")
            result.append(
                (str(current.Title), pages[dest[0].objgen], walk(current, depth + 1))
            )
            previous, current = current, current.get("/Next")
        if previous is not None and parent.Last.objgen != previous.objgen:
            _refuse("outline")
        return tuple(result)

    return walk(root, 0)
