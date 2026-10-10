"""Source-bound, reviewed Unicode maps for page-direct Identity-H text.

The caller supplies the review; this module never infers Unicode from CID values,
OCR confidence, or glyph names, and never claims that human approval occurred.
It verifies the submitted mapping against every reviewed text run, then verifies
the saved derivative. Content streams, images, font programs and page geometry
are preserved. Semantic tagging and publication are separate responsibilities.
"""

from __future__ import annotations

import hashlib
import io
import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NoReturn, cast

import pikepdf
from pdfminer.layout import LTChar
from pdfminer.pdfdevice import PDFTextDevice
from pdfminer.pdfpage import PDFPage
from pdfminer.psparser import literal_name

from ..pdf_checks.marked_content import _FontResourceManager, _PageInterpreter
from .pdf_font_text import (
    MAX_OPERATIONS,
    MAX_TEXT_OPERATIONS,
    _TEXT_OPERATORS,
    decode_page_text_runs,
    require_bounded_page_streams,
)
from .pdf_ocr_form import (
    MAX_GLYPHS,
    MAX_OBJECTS,
    MAX_PAGES,
    MAX_PDF_BYTES,
    _BoundedBuffer,
    require_bounded_font_resources,
)

FontIdentity = tuple[int, int]


class VerifiedFontRecoveryError(ValueError):
    """Content-free refusal; no derivative is returned on failure."""

    def __init__(self, code: str):
        self.code = "font_recovery_" + code
        super().__init__(self.code)


def _refuse(code: str) -> NoReturn:
    raise VerifiedFontRecoveryError(code)


@dataclass(frozen=True)
class RecoveryFont:
    objgen: FontIdentity
    fingerprint: str
    used_cids: tuple[int, ...]


@dataclass(frozen=True)
class RecoveryGlyph:
    font_objgen: FontIdentity
    cid: int
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class RecoveryRun:
    page_index: int
    start: int
    end: int
    glyphs: tuple[RecoveryGlyph, ...]


@dataclass(frozen=True)
class FontRecoveryInventory:
    source_sha256: str
    fonts: tuple[RecoveryFont, ...]
    runs: tuple[RecoveryRun, ...]
    page_count: int


@dataclass(frozen=True)
class ReviewedFontMap:
    objgen: FontIdentity
    fingerprint: str
    mappings: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class ReviewedTextRun:
    page_index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class FontRecoveryManifest:
    source_sha256: str
    reviewer: str
    review_reference: str
    fonts: tuple[ReviewedFontMap, ...]
    runs: tuple[ReviewedTextRun, ...]


@dataclass(frozen=True)
class VerifiedFontRecoveryResult:
    pdf_bytes: bytes
    source_sha256: str
    output_sha256: str
    review_sha256: str
    font_count: int
    glyph_count: int


def _identity(value: Any) -> FontIdentity:
    number, generation = value.objgen
    return number, generation


def _fingerprint(
    value: Any,
    *,
    ignore_unicode: bool = False,
    authorized_maps: frozenset[FontIdentity] = frozenset(),
) -> str:
    """Object-number-independent digest with bounded traversal and raw streams."""
    digest = hashlib.sha256()
    visits = total = 0

    def emit(data: bytes) -> None:
        nonlocal total
        total += len(data)
        if total > MAX_PDF_BYTES:
            _refuse("byte_limit")
        digest.update(len(data).to_bytes(8, "big") + data)

    def visit(item: Any, active: frozenset[FontIdentity], depth: int) -> None:
        nonlocal visits
        visits += 1
        if visits > MAX_OBJECTS or depth > 40:
            _refuse("object_limit")
        if isinstance(item, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)):
            identity = _identity(item) if item.is_indirect else None
            if identity is not None:
                if identity in active:
                    _refuse("resource_cycle")
                active = active | {identity}
            if isinstance(item, pikepdf.Array):
                emit(b"array")
                for child in cast(Iterable[Any], item):
                    visit(child, active, depth + 1)
                emit(b"end")
                return
            emit(b"stream" if isinstance(item, pikepdf.Stream) else b"dict")
            for key in sorted(item.keys()):
                if key == "/Length" and isinstance(item, pikepdf.Stream):
                    continue
                if key == "/ToUnicode" and (
                    (ignore_unicode and item.get("/Subtype") == pikepdf.Name.Type0)
                    or identity in authorized_maps
                ):
                    continue
                emit(str(key).encode())
                visit(item[key], active, depth + 1)
            if isinstance(item, pikepdf.Stream):
                emit(item.read_raw_bytes())
            emit(b"end")
        elif isinstance(item, pikepdf.Name):
            emit(b"name:" + str(item).encode())
        elif isinstance(item, pikepdf.String):
            emit(b"string:" + bytes(item))
        elif item is None or isinstance(item, bool):
            emit(str(item).encode())
        elif isinstance(item, (int, float, Decimal)):
            number = Decimal(str(item))
            if not number.is_finite():
                _refuse("numeric_bounds")
            emit(b"number:" + str(number.normalize()).encode())
        elif isinstance(item, str):
            emit(b"text:" + item.encode())
        else:
            _refuse("object_type")

    visit(value, frozenset(), 0)
    return digest.hexdigest()


def _page_fingerprints(pdf: pikepdf.Pdf) -> tuple[str, ...]:
    # Resolve inherited geometry/resources; omit only the page-tree back-link.
    return tuple(
        _fingerprint(
            pikepdf.Dictionary(
                {
                    **{
                        key: value
                        for key, value in page.obj.items()
                        if key != "/Parent"
                    },
                    "/Resources": page.Resources,
                    "/MediaBox": page.mediabox,
                    "/CropBox": page.cropbox,
                }
            ),
            ignore_unicode=True,
        )
        for page in pdf.pages
    )


def _require_text_operands(name: str, args: list[Any]) -> None:
    counts = {
        "BT": 0,
        "ET": 0,
        "q": 0,
        "Q": 0,
        "T*": 0,
        "Tf": 2,
        "Tc": 1,
        "Tw": 1,
        "Tz": 1,
        "TL": 1,
        "Tr": 1,
        "Ts": 1,
        "Tm": 6,
        "cm": 6,
        "Td": 2,
        "TD": 2,
        "Tj": 1,
        "TJ": 1,
        "'": 1,
        '"': 3,
    }
    if name not in counts:
        return
    if len(args) != counts[name]:
        _refuse("operands")
    if name == "Tf":
        if not isinstance(args[0], pikepdf.Name):
            _refuse("operands")
        args = args[1:]
    elif name in {"Tj", "'", '"'}:
        if not isinstance(args[-1], pikepdf.String):
            _refuse("operands")
        args = args[:-1]
    elif name == "TJ":
        if not isinstance(args[0], pikepdf.Array):
            _refuse("operands")
        args = [
            value
            for value in cast(Iterable[Any], args[0])
            if not isinstance(value, pikepdf.String)
        ]
    for value in args:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            _refuse("operands")
        number = float(value)
        if not math.isfinite(number) or abs(number) > 1_000_000:
            _refuse("numeric_bounds")


class _InventoryManager(_FontResourceManager):
    def __init__(self) -> None:
        super().__init__(lambda _reason: _refuse("font_scope"))
        self.font_ids: dict[int, int] = {}

    def get_font(self, objid: Any, spec: Any) -> Any:
        font = super().get_font(objid, spec)
        if literal_name(spec.get("Subtype")) == "Type0":
            self.font_ids[id(font)] = objid
        return font


class _InventoryDevice(PDFTextDevice):
    def __init__(self, manager: _InventoryManager, fonts: dict[int, FontIdentity]):
        super().__init__(manager)
        self.manager = manager
        self.fonts = fonts
        self.runs: list[tuple[RecoveryGlyph, ...]] = []
        self.current: list[RecoveryGlyph] | None = None
        self.count = 0

    def fail(self, _reason: str) -> NoReturn:
        _refuse("text_scope")

    def render_string(self, state: Any, seq: Any, ncs: Any, graphics: Any) -> None:
        if self.current is None:
            _refuse("text_balance")
        if any(isinstance(value, bytes) and len(value) % 2 for value in seq):
            _refuse("character_code")
        super().render_string(state, seq, ncs, graphics)

    def render_char(
        self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate
    ):
        self.count += 1
        if self.count > MAX_GLYPHS:
            _refuse("glyph_limit")
        objid = self.manager.font_ids.get(id(font))
        if objid not in self.fonts or self.current is None or not 0 <= cid <= 65535:
            _refuse("font_scope")
        # The placeholder is not decoded text. LTChar supplies glyph geometry
        # from the original font metrics without consulting a Unicode map.
        char = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            "?",
            font.char_width(cid),
            font.char_disp(cid),
            ncs,
            graphicstate,
        )
        if not all(
            math.isfinite(v) and abs(v) <= 1_000_000 for v in (*char.bbox, char.adv)
        ):
            _refuse("geometry")
        self.current.append(RecoveryGlyph(self.fonts[objid], cid, char.bbox))
        return char.adv


class _InventoryInterpreter(_PageInterpreter):
    def do_BT(self) -> None:
        device = cast(_InventoryDevice, self.device)
        if device.current is not None:
            _refuse("text_balance")
        device.current = []
        super().do_BT()

    def do_ET(self) -> None:
        device = cast(_InventoryDevice, self.device)
        if device.current is None:
            _refuse("text_balance")
        device.runs.append(tuple(device.current))
        device.current = None
        super().do_ET()


def _inspect(source: bytes, pdf: pikepdf.Pdf) -> FontRecoveryInventory:
    if (
        pdf.is_encrypted
        or not 1 <= len(pdf.pages) <= MAX_PAGES
        or len(pdf.objects) > MAX_OBJECTS
    ):
        _refuse("document_limit")
    if any(key in pdf.Root for key in ("/AcroForm", "/Perms", "/StructTreeRoot")):
        _refuse("document_scope")
    fonts: dict[FontIdentity, Any] = {}
    boundaries: list[list[tuple[int, int]]] = []
    resources = []
    operations = 0
    text_operations = 0
    decoded_bytes = 0
    for page in pdf.pages:
        if "/Annots" in page.obj or "/StructParents" in page.obj:
            _refuse("document_scope")
        require_bounded_page_streams(page)
        contents = page.obj.get("/Contents")
        streams = (
            cast(Iterable[Any], contents)
            if isinstance(contents, pikepdf.Array)
            else ([] if contents is None else [contents])
        )
        decoded_bytes += sum(len(stream.read_bytes()) for stream in streams)
        if decoded_bytes > MAX_PDF_BYTES:
            _refuse("decoded_limit")
        font_resources = page.Resources.get("/Font", pikepdf.Dictionary())
        if not isinstance(font_resources, pikepdf.Dictionary):
            _refuse("font_scope")
        resources.append(font_resources)
        for _, font in font_resources.items():
            if (
                not isinstance(font, pikepdf.Dictionary)
                or not font.is_indirect
                or font.get("/Subtype") != pikepdf.Name.Type0
                or font.get("/Encoding") != pikepdf.Name("/Identity-H")
                or "/ToUnicode" in font
            ):
                _refuse("font_scope")
            descendants = font.get("/DescendantFonts")
            if not isinstance(descendants, pikepdf.Array) or len(descendants) != 1:
                _refuse("font_scope")
            descendant = descendants[0]
            if descendant.get("/Subtype") not in {
                pikepdf.Name.CIDFontType0,
                pikepdf.Name.CIDFontType2,
            }:
                _refuse("font_scope")
            descriptor = descendant.get("/FontDescriptor")
            if (
                not isinstance(descriptor, pikepdf.Dictionary)
                or sum(
                    isinstance(descriptor.get(key), pikepdf.Stream)
                    for key in ("/FontFile", "/FontFile2", "/FontFile3")
                )
                != 1
            ):
                _refuse("font_program")
            fonts[_identity(font)] = font
        ext = page.Resources.get("/ExtGState", pikepdf.Dictionary())
        if not isinstance(ext, pikepdf.Dictionary) or any(
            "/Font" in state for _, state in ext.items()
        ):
            _refuse("graphics_font")
        ops = list(pikepdf.parse_content_stream(page))
        operations += len(ops)
        if operations > MAX_OPERATIONS:
            _refuse("operation_limit")
        starts = []
        start = None
        depth = 0
        for index, op in enumerate(ops):
            name = str(op.operator)
            text_operations += name in _TEXT_OPERATORS
            if text_operations > MAX_TEXT_OPERATIONS:
                _refuse("text_operation_limit")
            if name in {"BMC", "BDC", "EMC", "MP", "DP", "BI", "BX", "EX"}:
                _refuse("text_scope")
            method = "do_" + name.replace("*", "_a").replace('"', "_w").replace(
                "'", "_q"
            )
            if not hasattr(_InventoryInterpreter, method):
                _refuse("operator")
            _require_text_operands(name, list(op.operands))
            if name == "BT":
                if start is not None:
                    _refuse("text_balance")
                start = index
            elif name == "ET":
                if start is None:
                    _refuse("text_balance")
                starts.append((start, index + 1))
                start = None
            elif name in {"q", "Q"}:
                depth += 1 if name == "q" else -1
                if not 0 <= depth <= 50:
                    _refuse("graphics_balance")
        if start is not None or depth:
            _refuse("text_balance")
        boundaries.append(starts)
    require_bounded_font_resources(resources)
    identities = {number: (number, generation) for number, generation in fonts}
    if len(identities) != len(fonts):
        _refuse("font_identity")
    manager = _InventoryManager()
    device = _InventoryDevice(manager, identities)
    runs = []
    pages = list(PDFPage.get_pages(io.BytesIO(source)))
    if len(pages) != len(pdf.pages):
        _refuse("page_count")
    for page_index, decoded_page in enumerate(pages):
        device.runs = []
        _InventoryInterpreter(manager, device).process_page(decoded_page)
        if device.current is not None or len(device.runs) != len(
            boundaries[page_index]
        ):
            _refuse("text_balance")
        for (start, end), glyphs in zip(
            boundaries[page_index], device.runs, strict=True
        ):
            runs.append(RecoveryRun(page_index, start, end, glyphs))
    used: dict[FontIdentity, set[int]] = {}
    for run in runs:
        for glyph in run.glyphs:
            used.setdefault(glyph.font_objgen, set()).add(glyph.cid)
    if not used:
        _refuse("text_absent")
    return FontRecoveryInventory(
        hashlib.sha256(source).hexdigest(),
        tuple(
            RecoveryFont(key, _fingerprint(fonts[key]), tuple(sorted(cids)))
            for key, cids in sorted(used.items())
        ),
        tuple(runs),
        len(pdf.pages),
    )


def inspect_font_recovery_source(source: bytes) -> FontRecoveryInventory:
    """Return codes and geometry for review, without guessing any Unicode."""
    try:
        if not isinstance(source, bytes) or not source or len(source) > MAX_PDF_BYTES:
            _refuse("byte_limit")
        with pikepdf.open(io.BytesIO(source), attempt_recovery=False) as pdf:
            return _inspect(source, pdf)
    except VerifiedFontRecoveryError:
        raise
    except Exception:
        _refuse("unsupported_source")


def _cmap(mapping: tuple[tuple[int, str], ...]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Reviewed-UCS def /CMapType 2 def",
        "1 begincodespacerange <0000> <FFFF> endcodespacerange",
    ]
    for start in range(0, len(mapping), 100):
        chunk = mapping[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        lines.extend(
            f"<{cid:04X}> <{text.encode('utf-16-be').hex().upper()}>"
            for cid, text in chunk
        )
        lines.append("endbfchar")
    lines.append("endcmap CMapName currentdict /CMap defineresource pop end end")
    return ("\n".join(lines) + "\n").encode("ascii")


def recover_verified_font_maps(
    source: bytes, manifest: FontRecoveryManifest
) -> VerifiedFontRecoveryResult:
    """Apply caller-reviewed maps and require exact saved per-run transcripts.

    reviewer/review_reference record caller-provided provenance, not an approval
    checked by this function. The caller must establish trustworthy review.
    No file is written, and unsupported input returns no candidate bytes.
    """
    try:
        inventory = inspect_font_recovery_source(source)
        if manifest.source_sha256 != inventory.source_sha256:
            _refuse("source_changed")
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 1024
            for value in (manifest.reviewer, manifest.review_reference)
        ):
            _refuse("review_provenance")
        expected = {font.objgen: font for font in inventory.fonts}
        if len(manifest.fonts) != len(expected) or {
            font.objgen for font in manifest.fonts
        } != set(expected):
            _refuse("font_coverage")
        for font in manifest.fonts:
            source_font = expected[font.objgen]
            if font.fingerprint != source_font.fingerprint:
                _refuse("font_changed")
            if len(font.mappings) != len(source_font.used_cids):
                _refuse("code_coverage")
            codes = [cid for cid, _ in font.mappings]
            if (
                any(type(cid) is not int or not 0 <= cid <= 65535 for cid in codes)
                or len(codes) != len(set(codes))
                or set(codes) != set(source_font.used_cids)
            ):
                _refuse("code_coverage")
            for _, text in font.mappings:
                if (
                    not isinstance(text, str)
                    or not 1 <= len(text) <= 16
                    or any(
                        ord(c) < 32
                        or 0x7F <= ord(c) < 0xA0
                        or 0xD800 <= ord(c) <= 0xDFFF
                        or c == "\ufffd"
                        for c in text
                    )
                ):
                    _refuse("unicode")
        locations = [(run.page_index, run.start, run.end) for run in inventory.runs]
        if len(manifest.runs) != len(locations):
            _refuse("run_coverage")
        if [(run.page_index, run.start, run.end) for run in manifest.runs] != locations:
            _refuse("run_coverage")
        if (
            any(not isinstance(run.text, str) for run in manifest.runs)
            or sum(len(run.text.encode("utf-8")) for run in manifest.runs) > 1024 * 1024
        ):
            _refuse("transcript_limit")
        with pikepdf.open(io.BytesIO(source), attempt_recovery=False) as pdf:
            before = _page_fingerprints(pdf)
            for font in manifest.fonts:
                pdf.get_object(font.objgen).ToUnicode = pdf.make_stream(
                    _cmap(tuple(sorted(font.mappings)))
                )
            output = _BoundedBuffer()
            pdf.save(
                output,
                deterministic_id=True,
                fix_metadata_version=False,
                compress_streams=False,
                stream_decode_level=pikepdf.StreamDecodeLevel.none,
            )
        recovered = output.getvalue()
        with pikepdf.open(io.BytesIO(recovered), attempt_recovery=False) as saved:
            if before != _page_fingerprints(saved):
                _refuse("artwork_changed")
            decoded = tuple(
                ReviewedTextRun(index, run.start, run.end, run.text)
                for index in range(len(saved.pages))
                for run in decode_page_text_runs(saved, index)
            )
            if decoded != manifest.runs:
                _refuse("transcript_mismatch")
        # Unambiguous, length-prefixed review evidence; includes the exact map
        # and per-run text, without publishing these values in error messages.
        review = hashlib.sha256()
        for value in (repr(manifest),):
            encoded = value.encode("utf-8")
            review.update(len(encoded).to_bytes(8, "big") + encoded)
        return VerifiedFontRecoveryResult(
            recovered,
            inventory.source_sha256,
            hashlib.sha256(recovered).hexdigest(),
            review.hexdigest(),
            len(inventory.fonts),
            sum(len(run.glyphs) for run in inventory.runs),
        )
    except VerifiedFontRecoveryError:
        raise
    except Exception:
        _refuse("verification_failed")
