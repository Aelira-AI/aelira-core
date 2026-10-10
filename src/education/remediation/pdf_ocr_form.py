"""Conservative flattening of OCRmyPDF's invisible-text Forms.

This is a building block for a reviewed OCR derivative, not text recovery or
an accessibility claim. Planning is read-only. Applying mutates the supplied
in-memory PDF only; callers must open a private copy and independently verify
the saved result before publication. No path is read, overwritten or published.

Every selected page must contain images and exactly one uniquely used,
text-only Form. Other pages remain unchanged and the whole input is bound.
The Form's matrix, bounding-box clip, and graphics-state isolation survive
flattening. Arbitrary Forms, marked content, annotations and existing document
structure are deliberately unsupported.
"""

from __future__ import annotations

import hashlib
import io
import math
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, NoReturn, cast

import pikepdf
from pdfminer.cmapdb import CMapParser, FileUnicodeMap
from pdfminer.layout import LTChar
from pdfminer.pdfpage import PDFPage
from typing_extensions import Buffer

from ..pdf_checks.marked_content import (
    _FontResourceManager,
    _MarkedTextDevice,
    _PageInterpreter,
)

MAX_PAGES = 100
MAX_OBJECTS = 20_000
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_TOTAL_DECODED_BYTES = 32 * 1024 * 1024
MAX_OPERATIONS = 20_000
MAX_GLYPHS = 200_000
MAX_UNICODE_PER_GLYPH = 16
MAX_TEXT_BYTES = 1024 * 1024
MAX_CMAP_TOKENS = 20_000
MAX_CMAP_ENTRIES = 200_000
MAX_GRAPHICS_DEPTH = 32
MAX_IMAGE_PIXELS = 30_000_000
_IDENTITY = (1, 0, 0, 1, 0, 0)


class OCRFormFlatteningError(ValueError):
    """A stable, content-free refusal code; no output claim is created."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OCRFormPagePlan:
    page_index: int
    resource_name: str
    font_renames: tuple[tuple[str, str], ...]
    content: bytes
    glyph_count: int
    text_sha256: str = ""


@dataclass(frozen=True)
class OCRFormFlatteningPlan:
    source_sha256: str
    pages: tuple[OCRFormPagePlan, ...]


@dataclass(frozen=True)
class OCRFormFlatteningResult:
    """Transformation evidence only, not verified text or accessibility."""

    source_sha256: str
    pages_flattened: tuple[int, ...]
    glyph_count: int
    page_text_sha256: tuple[str, ...]


def _refuse(code: str) -> NoReturn:
    raise OCRFormFlatteningError("ocr_form_" + code)


class _BoundedBuffer(io.BytesIO):
    def write(self, data: Buffer, /) -> int:
        if self.tell() + memoryview(data).nbytes > MAX_PDF_BYTES:
            _refuse("byte_limit")
        return super().write(data)


def _snapshot(pdf: pikepdf.Pdf) -> bytes:
    output = _BoundedBuffer()
    pdf.save(output, deterministic_id=True, fix_metadata_version=False)
    return output.getvalue()


class _Budget:
    def __init__(self) -> None:
        self.decoded = 0
        self.operations = 0
        self.glyphs = 0
        self.cmap_entries = 0
        self.cmaps: set[tuple[int, int]] = set()
        self.streams: dict[tuple[int, int], bytes] = {}

    def stream(self, stream: Any) -> bytes:
        if not isinstance(stream, pikepdf.Stream):
            _refuse("stream_invalid")
        object_id, generation = stream.objgen
        identity = (object_id, generation)
        if identity in self.streams:
            return self.streams[identity]
        raw = stream.read_raw_bytes()
        if len(raw) > MAX_STREAM_BYTES:
            _refuse("stream_limit")
        filters = stream.get("/Filter")
        if isinstance(filters, pikepdf.Array) and len(filters) == 1:
            filters = filters[0]
        if stream.get("/DecodeParms") is not None:
            _refuse("stream_filter")
        if filters is None:
            data = raw
        elif filters == pikepdf.Name.FlateDecode:
            decoder = zlib.decompressobj()
            data = decoder.decompress(raw, MAX_STREAM_BYTES + 1)
            if len(data) > MAX_STREAM_BYTES or decoder.unconsumed_tail:
                _refuse("stream_limit")
            if not decoder.eof or decoder.unused_data:
                _refuse("stream_invalid")
        else:
            _refuse("stream_filter")
        self.decoded += len(data)
        if self.decoded > MAX_TOTAL_DECODED_BYTES:
            _refuse("decoded_limit")
        self.streams[identity] = data
        return data

    def ops(self, target: Any) -> list[Any]:
        if isinstance(target, pikepdf.Page):
            contents = target.obj.get("/Contents")
            streams = (
                list(cast(Iterable[Any], contents))
                if isinstance(contents, pikepdf.Array)
                else [contents]
            )
            if not streams or len(streams) > 100:
                _refuse("stream_limit")
            if sum(len(self.stream(stream)) for stream in streams) > MAX_STREAM_BYTES:
                _refuse("stream_limit")
        else:
            self.stream(target)
        ops = list(pikepdf.parse_content_stream(target))
        self.operations += len(ops)
        if self.operations > MAX_OPERATIONS:
            _refuse("operation_limit")
        return ops

    def cmap(self, stream: Any) -> None:
        data = self.stream(stream)
        identity = tuple(stream.objgen)
        if identity in self.cmaps:
            return
        parser = _BoundedCMapParser(data, self)
        parser.run()
        if not parser.saw_begin or not parser.saw_end:
            _refuse("font_unicode")
        self.cmaps.add(identity)


class _BoundedCMapParser(CMapParser):
    """Bound range expansion before pdfminer allocates a Unicode mapping."""

    def __init__(self, data: bytes, budget: _Budget):
        super().__init__(FileUnicodeMap(), io.BytesIO(data))
        self.budget = budget
        self.tokens = 0
        self.saw_begin = False
        self.saw_end = False
        self.section: tuple[Any, int] | None = None

    def nexttoken(self):
        self.tokens += 1
        if self.tokens > MAX_CMAP_TOKENS:
            _refuse("cmap_limit")
        return super().nexttoken()

    def _warn_once(self, _message: str) -> None:
        _refuse("font_unicode")

    @staticmethod
    def _target(value: Any) -> None:
        if (
            not isinstance(value, bytes)
            or not value
            or len(value) % 2
            or len(value) > MAX_UNICODE_PER_GLYPH * 4
        ):
            _refuse("font_unicode")
        try:
            value.decode("utf-16-be")
        except UnicodeError:
            _refuse("font_unicode")

    def do_keyword(self, pos: int, token: Any) -> None:
        if token is self.KEYWORD_BEGINCMAP:
            if self.saw_begin:
                _refuse("font_unicode")
            self.saw_begin = True
        elif token is self.KEYWORD_ENDCMAP:
            if not self.saw_begin or self.saw_end or self.section is not None:
                _refuse("font_unicode")
            self.saw_end = True
        if token in {
            self.KEYWORD_USECMAP,
            self.KEYWORD_BEGINCIDRANGE,
            self.KEYWORD_BEGINCIDCHAR,
        }:
            _refuse("font_unsupported")
        if token in {self.KEYWORD_BEGINBFRANGE, self.KEYWORD_BEGINBFCHAR}:
            count = self.curstack[-1][1] if self.curstack else None
            if (
                not self.saw_begin
                or self.saw_end
                or self.section is not None
                or isinstance(count, bool)
                or not isinstance(count, int)
                or not 0 <= count <= MAX_CMAP_ENTRIES
            ):
                _refuse("font_unicode")
            end_keyword = (
                self.KEYWORD_ENDBFRANGE
                if token is self.KEYWORD_BEGINBFRANGE
                else self.KEYWORD_ENDBFCHAR
            )
            self.section = (end_keyword, count)
        if token in {self.KEYWORD_ENDBFRANGE, self.KEYWORD_ENDBFCHAR}:
            objects = [value for _, value in self.curstack]
            stride = 3 if token is self.KEYWORD_ENDBFRANGE else 2
            if len(objects) % stride or self.section != (token, len(objects) // stride):
                _refuse("font_unicode")
            self.section = None
            for offset in range(0, len(objects), stride):
                start, target = objects[offset], objects[offset + stride - 1]
                if not isinstance(start, bytes) or len(start) not in (1, 2):
                    _refuse("font_unicode")
                count = 1
                if stride == 3:
                    end_code = objects[offset + 1]
                    if not isinstance(end_code, bytes) or len(end_code) != len(start):
                        _refuse("font_unicode")
                    count = (
                        int.from_bytes(end_code, "big")
                        - int.from_bytes(start, "big")
                        + 1
                    )
                    if count <= 0:
                        _refuse("font_unicode")
                self.budget.cmap_entries += count
                if self.budget.cmap_entries > MAX_CMAP_ENTRIES:
                    _refuse("cmap_limit")
                if isinstance(target, list):
                    if stride != 3 or len(target) != count:
                        _refuse("font_unicode")
                    for value in target:
                        self._target(value)
                else:
                    self._target(target)
        super().do_keyword(pos, token)


def _numbers(values: Any, count: int) -> tuple[float, ...]:
    if len(values) != count:
        _refuse("operands")
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            _refuse("operands")
        number = float(value)
        if not math.isfinite(number) or abs(number) > 1_000_000:
            _refuse("numeric_bounds")
        result.append(number)
    return tuple(result)


def _matrix(values: Any) -> tuple[float, ...]:
    matrix = _numbers(values, 6)
    if abs(matrix[0] * matrix[3] - matrix[1] * matrix[2]) < 1e-12:
        _refuse("matrix")
    return matrix


def _box(values: Any) -> tuple[float, ...]:
    if not isinstance(values, (tuple, list, pikepdf.Array)):
        _refuse("bbox")
    box = _numbers(values, 4)
    if box[2] <= box[0] or box[3] <= box[1]:
        _refuse("bbox")
    return box


def _instruction(operator: str, operands: Any = ()) -> Any:
    args = list(operands)
    result = pikepdf.ContentStreamInstruction(args, pikepdf.Operator(operator))
    for original, encoded in zip(args, result.operands, strict=True):
        if isinstance(original, (int, float, Decimal)) and Decimal(
            str(original)
        ) != Decimal(str(encoded)):
            # pikepdf converts some small Decimal values expressed in exponent
            # notation to zero. Refuse rather than silently alter a transform.
            _refuse("numeric_precision")
    return result


def _font_streams(fonts: Any, budget: _Budget) -> None:
    """Bound font programs and CMaps before pdfminer can decompress them."""
    stack = [(fonts, 0)]
    seen: set[tuple[int, int]] = set()
    visits = 0
    while stack:
        value, depth = stack.pop()
        visits += 1
        if visits > MAX_OBJECTS or depth > 20:
            _refuse("font_limit")
        if not isinstance(value, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
            continue
        if value.is_indirect:
            object_id, generation = value.objgen
            identity = (object_id, generation)
            if identity in seen:
                continue
            seen.add(identity)
        if isinstance(value, pikepdf.Stream):
            if value.get("/UseCMap") is not None:
                _refuse("font_unsupported")
            budget.stream(value)
        elif isinstance(value, pikepdf.Dictionary) and "/ToUnicode" in value:
            budget.cmap(value.ToUnicode)
        children = (
            list(cast(Iterable[Any], value))
            if isinstance(value, pikepdf.Array)
            else [v for _, v in value.items()]
        )
        stack.extend((child, depth + 1) for child in children)


def require_bounded_font_resources(
    font_resources: Sequence[pikepdf.Dictionary],
) -> None:
    """Bound font/CMap parsing before a separate strict text decoder runs.

    Supply the /Font dictionaries from one opened PDF, with an empty dictionary
    for pages without fonts. The shared budget covers every supplied resource
    set. This read-only preflight proves neither glyph coverage nor correctness
    of extracted text. Unsupported input raises OCRFormFlatteningError.
    """
    try:
        if len(font_resources) > MAX_PAGES:
            _refuse("font_limit")
        budget = _Budget()
        for fonts in font_resources:
            if not isinstance(fonts, pikepdf.Dictionary) or len(fonts) > 32:
                _refuse("font_resources")
            _font_streams(fonts, budget)
    except OCRFormFlatteningError:
        raise
    except Exception:
        _refuse("font_unicode")


def _form_ops(form: Any, budget: _Budget) -> tuple[list[Any], int]:
    allowed_keys = {
        "/Type",
        "/Subtype",
        "/FormType",
        "/BBox",
        "/Matrix",
        "/Resources",
        "/Length",
        "/Filter",
        "/DecodeParms",
    }
    if any(str(key) not in allowed_keys for key in form.keys()):
        _refuse("semantics")
    if form.get("/FormType", 1) != 1:
        _refuse("form_type")
    resources = form.get("/Resources")
    if not isinstance(resources, pikepdf.Dictionary) or any(
        str(key) not in {"/Font", "/ProcSet"} for key in resources.keys()
    ):
        _refuse("resources")
    fonts = resources.get("/Font")
    if not isinstance(fonts, pikepdf.Dictionary) or not 1 <= len(fonts) <= 32:
        _refuse("font_resources")
    _font_streams(fonts, budget)
    ops = budget.ops(form)
    in_text = False
    depth = 0
    font_name = None
    mode = None
    block_glyphs = 0
    glyphs = 0
    numbers = {"Td": 2, "TD": 2, "Tc": 1, "Tw": 1, "Tz": 1, "TL": 1, "Ts": 1}
    graphics = {"J": 1, "w": 1, "g": 1, "G": 1, "rg": 3, "RG": 3}
    for op in ops:
        name, args = str(op.operator), list(op.operands)
        if name in {"q", "Q", "BT", "ET", "T*"}:
            if args:
                _refuse("operands")
            if name in {"q", "Q"}:
                if in_text:
                    _refuse("grammar")
                depth += 1 if name == "q" else -1
                if not 0 <= depth <= MAX_GRAPHICS_DEPTH:
                    _refuse("graphics_balance")
            elif name == "BT":
                if in_text:
                    _refuse("grammar")
                in_text, font_name, mode, block_glyphs = True, None, None, 0
            elif name == "ET":
                if not in_text or not block_glyphs:
                    _refuse("grammar")
                in_text = False
            elif not in_text:
                _refuse("grammar")
        elif name == "cm" or name in graphics:
            if in_text:
                _refuse("grammar")
            values = _matrix(args) if name == "cm" else _numbers(args, graphics[name])
            if name == "J" and values[0] not in (0, 1, 2):
                _refuse("operands")
            if name == "w" and values[0] < 0:
                _refuse("operands")
        elif not in_text:
            _refuse("grammar")
        elif name == "Tf":
            if len(args) != 2 or not isinstance(args[0], pikepdf.Name):
                _refuse("operands")
            size = _numbers(args[1:], 1)[0]
            if not 0 < size <= 10000 or str(args[0]) not in fonts:
                _refuse("font_reference")
            font_name = str(args[0])
        elif name == "Tr":
            mode = _numbers(args, 1)[0]
            if mode != 3:
                _refuse("visible_text")
        elif name == "Tm":
            _matrix(args)
        elif name in numbers:
            values = _numbers(args, numbers[name])
            if name == "Tz" and values[0] <= 0:
                _refuse("operands")
        elif name in {"Tj", "TJ", "'", '"'}:
            if font_name is None or mode != 3:
                _refuse("text_state")
            if name == '"':
                if len(args) != 3:
                    _refuse("operands")
                _numbers(args[:2], 2)
                strings = args[2:]
            elif name == "TJ":
                if len(args) != 1 or not isinstance(args[0], pikepdf.Array):
                    _refuse("operands")
                strings = []
                for value in cast(Iterable[Any], args[0]):
                    if isinstance(value, pikepdf.String):
                        strings.append(value)
                    else:
                        _numbers([value], 1)
            else:
                if len(args) != 1:
                    _refuse("operands")
                strings = args
            font = fonts[font_name]
            composite = font.get("/Subtype") == pikepdf.Name.Type0
            if composite and (
                font.get("/Encoding") != pikepdf.Name("/Identity-H")
                or not isinstance(font.get("/ToUnicode"), pikepdf.Stream)
            ):
                _refuse("font_unicode")
            for string in strings:
                if not isinstance(string, pikepdf.String):
                    _refuse("operands")
                length = len(bytes(string))
                if composite and length % 2:
                    _refuse("font_unicode")
                count = length // 2 if composite else length
                glyphs += count
                block_glyphs += count
                budget.glyphs += count
                if budget.glyphs > MAX_GLYPHS:
                    _refuse("glyph_limit")
        else:
            _refuse("grammar")
    if in_text or depth or not glyphs:
        _refuse("grammar")
    return ops, glyphs


def _page_plan(
    page: Any, index: int, budget: _Budget, forms: set[tuple[int, int]]
) -> OCRFormPagePlan:
    if any(
        key in page.obj
        for key in ("/Annots", "/StructParents", "/Group", "/AA", "/PresSteps", "/VP")
    ):
        _refuse("semantics")
    if page.obj.get("/UserUnit", 1) != 1:
        _refuse("page_geometry")
    _box(page.mediabox)
    _box(page.cropbox)
    if page.obj.get("/Rotate", 0) not in (0, 90, 180, 270):
        _refuse("page_geometry")
    resources = page.obj.get("/Resources")
    if not isinstance(resources, pikepdf.Dictionary) or any(
        str(key) not in {"/XObject", "/Font", "/ProcSet"} for key in resources.keys()
    ):
        _refuse("resources")
    xobjects = resources.get("/XObject")
    if not isinstance(xobjects, pikepdf.Dictionary) or len(xobjects) > 1000:
        _refuse("resources")
    candidates = []
    for name, value in xobjects.items():
        if not isinstance(value, pikepdf.Stream):
            _refuse("resources")
        if value.get("/Subtype") == pikepdf.Name.Form:
            candidates.append((str(name), value))
        elif value.get("/Subtype") != pikepdf.Name.Image:
            _refuse("resources")
        elif any(
            key in value for key in ("/SMask", "/Mask", "/OC", "/Alternates", "/OPI")
        ) or value.get("/ImageMask"):
            _refuse("image_semantics")
        else:
            width, height = _numbers([value.get("/Width"), value.get("/Height")], 2)
            if (
                width <= 0
                or height <= 0
                or not width.is_integer()
                or not height.is_integer()
                or width * height > MAX_IMAGE_PIXELS
            ):
                _refuse("image_limit")
    if len(candidates) != 1:
        _refuse("form_count")
    name, form = candidates[0]
    object_id, generation = form.objgen
    identity = (object_id, generation)
    if identity in forms:
        _refuse("form_reused")
    forms.add(identity)
    form_ops, glyphs = _form_ops(form, budget)
    raw_bbox = form.get("/BBox")
    _box(raw_bbox)
    # Keep source decimal precision when writing matrix/clip operands. A float
    # round trip can otherwise shift positioned text in a valid source.
    bbox = tuple(Decimal(str(value)) for value in cast(Iterable[Any], raw_bbox))
    matrix: Any = form.get("/Matrix")
    if matrix is None:
        matrix = _IDENTITY
    _matrix(matrix)
    page_fonts = resources.get("/Font", pikepdf.Dictionary())
    if not isinstance(page_fonts, pikepdf.Dictionary) or len(page_fonts) > 32:
        _refuse("font_resources")
    _font_streams(page_fonts, budget)
    used_names = {str(key) for key in page_fonts.keys()}
    renames: list[tuple[str, str]] = []
    for old in sorted(str(key) for key in form.Resources.Font.keys()):
        suffix = 0
        new = f"/AeliraOCR{index}F{len(renames)}"
        while new in used_names:
            suffix += 1
            new = f"/AeliraOCR{index}F{len(renames)}_{suffix}"
        used_names.add(new)
        renames.append((old, new))
    mapping = dict(renames)
    renamed_ops = [
        (
            _instruction(
                "Tf", [pikepdf.Name(mapping[str(op.operands[0])]), op.operands[1]]
            )
            if str(op.operator) == "Tf"
            else op
        )
        for op in form_ops
    ]
    replacement = [
        _instruction("q"),
        _instruction("cm", matrix),
        _instruction("re", (bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1])),
        _instruction("W"),
        _instruction("n"),
        *renamed_ops,
        _instruction("Q"),
    ]
    ops = budget.ops(page)
    output = []
    depth = form_draws = image_draws = 0
    for op in ops:
        operator, args = str(op.operator), list(op.operands)
        if operator in {"q", "Q"}:
            if args:
                _refuse("operands")
            depth += 1 if operator == "q" else -1
            if not 0 <= depth <= MAX_GRAPHICS_DEPTH:
                _refuse("graphics_balance")
        elif operator == "cm":
            _matrix(args)
        elif operator == "Do":
            if (
                len(args) != 1
                or not isinstance(args[0], pikepdf.Name)
                or str(args[0]) not in xobjects
            ):
                _refuse("xobject_reference")
            if str(args[0]) == name:
                form_draws += 1
                output.extend(replacement)
                continue
            image_draws += 1
        else:
            _refuse("page_grammar")
        output.append(op)
    if depth:
        _refuse("graphics_balance")
    if form_draws != 1:
        _refuse("form_reused")
    if not image_draws:
        _refuse("image_required")
    content = pikepdf.unparse_content_stream(output)
    if len(content) > MAX_STREAM_BYTES:
        _refuse("stream_limit")
    return OCRFormPagePlan(index, name, tuple(renames), content, glyphs)


def _materialize(pdf: pikepdf.Pdf, pages: tuple[OCRFormPagePlan, ...]) -> None:
    pending = []
    for plan in pages:
        page = pdf.pages[plan.page_index]
        resources = pikepdf.Dictionary(cast(Mapping[str, Any], page.Resources))
        xobjects = pikepdf.Dictionary(cast(Mapping[str, Any], resources.XObject))
        form = xobjects[plan.resource_name]
        fonts = pikepdf.Dictionary(
            cast(Mapping[str, Any], resources.get("/Font", pikepdf.Dictionary()))
        )
        for old, new in plan.font_renames:
            fonts[new] = form.Resources.Font[old]
        del xobjects[plan.resource_name]
        resources.Font, resources.XObject = fonts, xobjects
        pending.append((page, resources, pdf.make_stream(plan.content)))
    # All parsing, validation and resource construction precede page writes.
    for page, resources, content in pending:
        page.Resources, page.Contents = resources, content


def _require_unique_form_bindings(
    pdf: pikepdf.Pdf, plans: tuple[OCRFormPagePlan, ...]
) -> None:
    """Refuse aliases through any page, including pages outside the selection.

    Count reachable bindings per page/path, not unique resource dictionaries:
    two pages sharing one /Resources dictionary are two possible invocations.
    Only resource/content/appearance graphs are inspected; page/annotation
    back-references cannot be followed into the page tree a second time.
    """
    targets = {
        tuple(pdf.pages[plan.page_index].Resources.XObject[plan.resource_name].objgen)
        for plan in plans
    }
    counts = {identity: 0 for identity in targets}
    stack: list[tuple[Any, int, frozenset[tuple[int, ...]]]] = []
    for page in pdf.pages:
        for value in (
            page.Resources,
            page.obj.get("/Contents"),
            page.obj.get("/Annots"),
        ):
            stack.append((value, 0, frozenset()))
    visits = 0
    while stack:
        value, depth, active = stack.pop()
        visits += 1
        if visits > MAX_OBJECTS or depth > 50:
            _refuse("resource_limit")
        if not isinstance(value, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
            continue
        identity = tuple(value.objgen) if value.is_indirect else None
        if identity in targets:
            counts[identity] += 1
            if counts[identity] > 1:
                _refuse("form_reused")
            continue
        if identity is not None:
            if identity in active:
                continue
            active = active | {identity}
        children = (
            list(cast(Iterable[Any], value))
            if isinstance(value, pikepdf.Array)
            else [
                child
                for key, child in value.items()
                if str(key) not in {"/P", "/Parent"}
            ]
        )
        if len(stack) + len(children) > MAX_OBJECTS:
            _refuse("resource_limit")
        stack.extend((child, depth + 1, active) for child in children)
    if any(count != 1 for count in counts.values()):
        _refuse("form_reused")


class _CoverageDevice(_MarkedTextDevice):
    def __init__(self, manager: Any, fail: Any):
        super().__init__(manager, fail)
        self.digest = hashlib.sha256()
        self.text_bytes = 0

    def render_char(
        self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate
    ):
        unicode_map = getattr(font, "unicode_map", None)
        text = (
            unicode_map.get_unichr(cid)
            if unicode_map is not None
            else font.to_unichr(cid)
        )
        if (
            not isinstance(text, str)
            or not text
            or len(text) > MAX_UNICODE_PER_GLYPH
            or any(
                char == "\ufffd"
                or ord(char) < 32
                or 0x7F <= ord(char) < 0xA0
                or 0xD800 <= ord(char) <= 0xDFFF
                for char in text
            )
        ):
            _refuse("font_unicode")
        encoded = text.encode("utf-8")
        self.text_bytes += len(encoded)
        if self.text_bytes > MAX_TEXT_BYTES:
            _refuse("text_limit")
        _matrix(matrix)
        char = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text,
            font.char_width(cid),
            font.char_disp(cid),
            ncs,
            graphicstate,
        )
        if not all(
            math.isfinite(value) and abs(value) <= 1_000_000
            for value in (*char.bbox, char.adv)
        ):
            _refuse("text_geometry")
        self.digest.update(len(encoded).to_bytes(4, "big") + encoded)
        return super().render_char(
            matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate
        )


def _decoded_plans(
    snapshot: bytes, plans: tuple[OCRFormPagePlan, ...]
) -> tuple[OCRFormPagePlan, ...]:
    with pikepdf.open(io.BytesIO(snapshot)) as candidate:
        _materialize(candidate, plans)
        flattened = _snapshot(candidate)
    checked = []
    with io.BytesIO(flattened) as source:
        selected = {plan.page_index for plan in plans}
        for page, plan in zip(
            PDFPage.get_pages(source, pagenos=selected), plans, strict=True
        ):

            def fail(_reason: str) -> NoReturn:
                _refuse("font_unicode")

            manager = _FontResourceManager(fail)
            device = _CoverageDevice(manager, fail)
            try:
                _PageInterpreter(manager, device).process_page(page)
            except OCRFormFlatteningError:
                raise
            except Exception:
                _refuse("font_unicode")
            if device.character_count != plan.glyph_count or device.stack:
                _refuse("glyph_coverage")
            checked.append(replace(plan, text_sha256=device.digest.hexdigest()))
    return tuple(checked)


def plan_ocr_form_flattening(
    pdf: pikepdf.Pdf, *, page_indices: Sequence[int] | None = None
) -> OCRFormFlatteningPlan:
    """Validate selected pages and their glyphs without mutating page content.

    The plan is bound to a deterministic serialization of this exact input.
    By default all pages must satisfy the narrow grammar. For mixed OCR output,
    callers can supply the unique zero-based indices established before OCR.
    All selected pages must pass; nonselected pages are not transformed.
    """
    try:
        if (
            pdf.is_encrypted
            or not 1 <= len(pdf.pages) <= MAX_PAGES
            or len(pdf.objects) > MAX_OBJECTS
        ):
            _refuse("document_limit")
        if any(
            key in pdf.Root
            for key in (
                "/StructTreeRoot",
                "/AcroForm",
                "/OCProperties",
                "/OpenAction",
                "/AA",
                "/Names",
            )
        ):
            _refuse("semantics")
        mark_info = pdf.Root.get("/MarkInfo")
        if mark_info is not None and not isinstance(mark_info, pikepdf.Dictionary):
            _refuse("semantics")
        if mark_info is not None and mark_info.get("/Marked"):
            _refuse("semantics")
        selected = (
            tuple(range(len(pdf.pages)))
            if page_indices is None
            else tuple(page_indices)
        )
        if (
            not selected
            or len(selected) > MAX_PAGES
            or any(
                type(index) is not int or not 0 <= index < len(pdf.pages)
                for index in selected
            )
            or len(set(selected)) != len(selected)
        ):
            _refuse("page_indices")
        selected = tuple(sorted(selected))
        budget = _Budget()
        forms: set[tuple[int, int]] = set()
        plans = tuple(
            _page_plan(pdf.pages[index], index, budget, forms) for index in selected
        )
        _require_unique_form_bindings(pdf, plans)
        snapshot = _snapshot(pdf)
        plans = _decoded_plans(snapshot, plans)
        return OCRFormFlatteningPlan(hashlib.sha256(snapshot).hexdigest(), plans)
    except OCRFormFlatteningError:
        raise
    except Exception:
        _refuse("invalid_pdf")


def apply_ocr_form_flattening(
    pdf: pikepdf.Pdf, plan: OCRFormFlatteningPlan
) -> OCRFormFlatteningResult:
    """Apply a fully revalidated plan to an in-memory private derivative.

    This writes no file and supplies no publication or accessibility claim.
    Revalidation rejects stale plans, forged content and newly unsupported input
    before changing page resources or content.
    """
    current = plan_ocr_form_flattening(
        pdf, page_indices=tuple(page.page_index for page in plan.pages)
    )
    if current != plan:
        _refuse("plan_changed")
    _materialize(pdf, current.pages)
    return OCRFormFlatteningResult(
        current.source_sha256,
        tuple(page.page_index for page in current.pages),
        sum(page.glyph_count for page in current.pages),
        tuple(page.text_sha256 for page in current.pages),
    )
