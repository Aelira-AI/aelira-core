"""Private, read-only page-direct glyph inventory with explicit exclusions.

Decoding failures are localized to occurrences/pages; later pages are visited.
Forms are counted but not followed in this version. This is diagnostic evidence,
not visual fidelity, semantic tagging approval, or a mutation eligibility claim.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from typing import Any, cast
from collections.abc import Iterable

import pikepdf
from pdfminer.layout import LTChar
from pdfminer.pdfdevice import PDFTextDevice
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import resolve1, stream_value
from pdfminer.psparser import literal_name
from pdfminer.utils import decode_text

from ..pdf_checks.marked_content import _FontResourceManager, _PageInterpreter
from .pdf_font_text import (
    MAX_OPERATIONS,
    MAX_TEXT_OPERATIONS,
    _TEXT_OPERATORS,
    require_bounded_page_streams,
)
from .pdf_ocr_form import (
    MAX_GLYPHS,
    MAX_OBJECTS,
    MAX_PAGES,
    MAX_PDF_BYTES,
    _Budget,
    _font_streams,
)
from .pdf_verified_font_recovery import _fingerprint, _require_text_operands

SCHEMA_VERSION = 1
MAX_REPLACEMENT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FontEvidence:
    identity: str
    objgen: tuple[int, int]
    resource_paths: tuple[str, ...]
    subtype: str
    fingerprint: str
    program_sha256: str | None
    encoding_sha256: str
    addressing_sha256: str | None
    original_map_sha256: str | None
    used_codes: tuple[int, ...]


@dataclass(frozen=True)
class GlyphEvidence:
    font_identity: str
    cid: int
    code_hex: str
    gid: int | None
    unicode: str | None
    origin: str
    bbox: tuple[float, float, float, float]
    rendering_mode: int


@dataclass(frozen=True)
class TextRunEvidence:
    page_index: int
    start: int
    end: int
    glyphs: tuple[GlyphEvidence, ...]


@dataclass(frozen=True)
class ReplacementEvidence:
    page_index: int
    first_glyph: int
    end_glyph: int
    text: str | None
    raw_sha256: str | None


@dataclass(frozen=True)
class PageInventory:
    page_index: int
    complete: bool
    glyph_count: int
    form_invocations: int
    exclusions: tuple[str, ...]
    marked_content_count: int = 0


@dataclass(frozen=True)
class PDFTextInventory:
    source_sha256: str
    page_count: int
    pages: tuple[PageInventory, ...]
    fonts: tuple[FontEvidence, ...]
    runs: tuple[TextRunEvidence, ...]
    replacements: tuple[ReplacementEvidence, ...]
    complete: bool
    exclusions: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION
    fidelity_status: str = "unassessed"

    def summary(self) -> dict[str, Any]:
        """Content-free projection suitable for logs, never includes text."""
        glyphs = [glyph for run in self.runs for glyph in run.glyphs]
        return {
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "page_count": self.page_count,
            "pages_visited": len(self.pages),
            "complete": self.complete,
            "font_count": len(self.fonts),
            "unused_font_count": sum(not font.used_codes for font in self.fonts),
            "glyph_count": len(glyphs),
            "unresolved_glyph_count": sum(glyph.unicode is None for glyph in glyphs),
            "form_invocations": sum(page.form_invocations for page in self.pages),
            "exclusions": self.exclusions,
            "fidelity_status": self.fidelity_status,
        }


class _InventoryLimit(ValueError):
    pass


class _Manager(_FontResourceManager):
    def __init__(self) -> None:
        super().__init__(lambda _reason: None)
        self.identities: dict[int, str] = {}
        self.errors: dict[int, tuple[str, ...]] = {}

    def get_font(self, objid: Any, spec: Any) -> Any:
        checks: set[str] = set()
        self.fail = checks.add
        try:
            font = super().get_font(objid, spec)
        except Exception:
            # Keep unused unsupported resources from hiding an independent
            # healthy occurrence. A used placeholder is explicitly unresolved,
            # has no asserted bbox, and makes the page incomplete.
            from pdfminer.pdffont import PDFType1Font
            from pdfminer.psparser import LIT

            font = PDFType1Font(
                self,
                {"BaseFont": LIT("Helvetica"), "Encoding": LIT("StandardEncoding")},
            )
            checks.add("font_resource_unavailable")
        self.errors[id(font)] = tuple(sorted(checks))
        if objid is not None:
            self.identities[id(font)] = str(objid)
        return font


class _Device(PDFTextDevice):
    def __init__(
        self,
        manager: _Manager,
        page_index: int,
        boundaries: list[tuple[int, int]],
        remaining: int,
        remaining_replacement_bytes: int,
    ) -> None:
        super().__init__(manager)
        self.manager = manager
        self.page_index = page_index
        self.boundaries = boundaries
        self.remaining = remaining
        self.remaining_replacement_bytes = remaining_replacement_bytes
        self.replacement_bytes = 0
        self.current: list[GlyphEvidence] | None = None
        self.runs: list[TextRunEvidence] = []
        self.replacements: list[ReplacementEvidence] = []
        self.stack: list[tuple[int, str | None, str | None]] = []
        self.count = 0
        self.mode = 0
        self.forms = 0
        self.marked_count = 0
        self.checks: set[str] = set()
        self.fail = self.checks.add

    def begin_tag(self, tag: Any, props: Any = None) -> None:
        self.marked_count += 1
        props = resolve1(props)
        if (
            isinstance(props, dict)
            and "ActualText" in props
            and not isinstance(resolve1(props["ActualText"]), bytes)
        ):
            self.checks.add("actualtext_unavailable")
        value = props.get("ActualText") if isinstance(props, dict) else None
        raw = resolve1(value)
        if isinstance(raw, bytes):
            self.replacement_bytes += len(raw)
            if self.replacement_bytes > self.remaining_replacement_bytes:
                raise _InventoryLimit()
        text = decode_text(raw) if isinstance(raw, bytes) else None
        if text is not None and len(text) > 65536:
            raise _InventoryLimit()
        self.stack.append(
            (
                self.count,
                text,
                hashlib.sha256(raw).hexdigest() if isinstance(raw, bytes) else None,
            )
        )
        if len(self.stack) > 50:
            raise _InventoryLimit()

    def end_tag(self) -> None:
        if not self.stack:
            self.checks.add("marked_content_balance")
            return
        first, text, raw_hash = self.stack.pop()
        if raw_hash is not None:
            self.replacements.append(
                ReplacementEvidence(self.page_index, first, self.count, text, raw_hash)
            )

    def render_string(
        self, textstate: Any, seq: Any, ncs: Any, graphicstate: Any
    ) -> None:
        self.mode = textstate.render
        if self.manager.errors.get(id(textstate.font)):
            self.checks.add("font_resource_unavailable")
        if self.current is None:
            self.checks.add("text_balance")
            raise ValueError("text_balance")
        for item in seq:
            if (
                isinstance(item, bytes)
                and textstate.font.is_multibyte()
                and len(item) % 2
            ):
                self.checks.add("font_code_width")
                raise ValueError("font_code_width")
        super().render_string(textstate, seq, ncs, graphicstate)

    def render_char(
        self,
        matrix: Any,
        font: Any,
        fontsize: Any,
        scaling: Any,
        rise: Any,
        cid: int,
        ncs: Any,
        graphicstate: Any,
    ) -> float:
        self.count += 1
        if self.count > self.remaining:
            raise _InventoryLimit()
        identity = self.manager.identities.get(id(font), "unbound")
        errors = self.manager.errors.get(id(font), ())
        text, gid = None, None
        origin = getattr(font, "aelira_evidence_origin", "unsupported")
        resolver = getattr(font, "aelira_unicode_resolver", None)
        try:
            if errors:
                raise ValueError("font_resource_unavailable")
            if resolver is not None:
                text = resolver.resolve(cid)
                _inverse, _count, addressing = resolver.evidence
                gid = (
                    cid
                    if addressing is None
                    else int.from_bytes(addressing[cid * 2 : cid * 2 + 2], "big")
                )
            else:
                unicode_map = getattr(font, "unicode_map", None)
                text = (
                    unicode_map.get_unichr(cid)
                    if unicode_map is not None
                    else font.to_unichr(cid)
                )
            if (
                not isinstance(text, str)
                or not text
                or len(text) > 16
                or "\ufffd" in text
                or any(0xD800 <= ord(char) <= 0xDFFF for char in text)
            ):
                raise ValueError("font_unicode")
        except Exception:
            text = None
            self.checks.add("mapping_unavailable")
        char = LTChar(
            matrix,
            font,
            fontsize,
            scaling,
            rise,
            text or "?",
            font.char_width(cid),
            font.char_disp(cid),
            ncs,
            graphicstate,
        )
        if not all(math.isfinite(value) for value in char.bbox):
            self.checks.add("geometry_unavailable")
            raise ValueError("geometry_unavailable")
        if errors:
            self.checks.add("font_resource_unavailable")
        width = 2 if font.is_multibyte() else 1
        if not 0 <= cid < 1 << (width * 8) or font.is_vertical():
            self.checks.add("font_code_scope")
            raise ValueError("font_code_scope")
        assert self.current is not None
        self.current.append(
            GlyphEvidence(
                identity,
                cid,
                cid.to_bytes(width, "big").hex(),
                gid,
                text,
                origin,
                (
                    (char.bbox[0], char.bbox[1], char.bbox[2], char.bbox[3])
                    if not errors
                    else (0.0, 0.0, 0.0, 0.0)
                ),
                self.mode,
            )
        )
        return char.adv


class _Interpreter(_PageInterpreter):
    device: _Device

    def do_gs(self, name: Any) -> None:
        state = resolve1(self.resources.get("ExtGState", {}))
        state = (
            resolve1(state.get(literal_name(name))) if isinstance(state, dict) else None
        )
        if isinstance(state, dict) and "Font" in state:
            self.device.checks.add("graphics_font_scope_unsupported")
            raise ValueError("graphics_font_scope_unsupported")
        super().do_gs(name)

    def init_resources(self, resources: Any) -> None:
        # Used occurrences, rather than unused resource existence, determine
        # decoding findings. Unknown scopes are still explicit on invocation.
        PDFPageInterpreter.init_resources(self, resources)
        device = self.device
        for name, font in self.fontmap.items():
            if id(font) not in device.manager.identities:
                device.manager.identities[id(font)] = (
                    f"page:{device.page_index}/font:/{name}"
                )

    def do_BT(self) -> None:
        device = self.device
        if device.current is not None:
            raise ValueError("text_balance")
        device.current = []
        super().do_BT()

    def do_ET(self) -> None:
        device = self.device
        if device.current is None or len(device.runs) >= len(device.boundaries):
            raise ValueError("text_balance")
        start, end = device.boundaries[len(device.runs)]
        device.runs.append(
            TextRunEvidence(device.page_index, start, end, tuple(device.current))
        )
        device.current = None
        super().do_ET()

    def do_Do(self, xobjid_arg: Any) -> None:
        reference = self.xobjmap.get(literal_name(xobjid_arg))
        if reference is None:
            self.device.checks.add("xobject_unavailable")
        elif literal_name(stream_value(reference).get("Subtype")) != "Image":
            self.device.forms += 1
            self.device.checks.add("form_scope_unsupported")


def _font_evidence(
    font: Any, identity: str, paths: tuple[str, ...], used: tuple[int, ...]
) -> FontEvidence:
    descendant = font
    if font.get("/Subtype") == pikepdf.Name.Type0:
        children = font.get("/DescendantFonts")
        if isinstance(children, pikepdf.Array) and len(children) == 1:
            descendant = children[0]
    descriptor = descendant.get("/FontDescriptor", pikepdf.Dictionary())
    programs = [
        descriptor.get(key) for key in ("/FontFile", "/FontFile2", "/FontFile3")
    ]
    program = next(
        (value for value in programs if isinstance(value, pikepdf.Stream)), None
    )
    original_map = font.get("/ToUnicode")
    addressing = descendant.get("/CIDToGIDMap")
    return FontEvidence(
        identity,
        tuple(font.objgen),
        paths,
        str(font.get("/Subtype", "")),
        _fingerprint(font),
        (
            hashlib.sha256(program.read_bytes()).hexdigest()
            if program is not None
            else None
        ),
        _fingerprint(font.get("/Encoding")),
        _fingerprint(addressing) if addressing is not None else None,
        _fingerprint(original_map) if original_map is not None else None,
        used,
    )


def inspect_pdf_text_inventory(source: bytes) -> PDFTextInventory:
    """Complete direct-page inventory, or explicit bounded incompleteness.

    Private glyph text/geometry is returned only here. Ordinary logs should use
    summary(). Input bytes and the caller's files are never modified or saved.
    """
    source_hash = hashlib.sha256(source).hexdigest()
    pages: list[PageInventory] = []
    fonts: dict[str, Any] = {}
    paths: dict[str, list[str]] = {}
    runs: list[TextRunEvidence] = []
    replacements: list[ReplacementEvidence] = []
    exclusions: set[str] = set()
    evidence: list[FontEvidence] = []
    page_count = 0
    if not source or len(source) > MAX_PDF_BYTES:
        return PDFTextInventory(
            source_hash, 0, (), (), (), (), False, ("source_limit",)
        )
    try:
        with pikepdf.open(io.BytesIO(source), attempt_recovery=False) as pdf:
            page_count = len(pdf.pages)
            if (
                pdf.is_encrypted
                or not 1 <= page_count <= MAX_PAGES
                or len(pdf.objects) > MAX_OBJECTS
            ):
                raise _InventoryLimit()
            parsed_pages = iter(PDFPage.get_pages(io.BytesIO(source)))
            budget = _Budget()
            operations = text_operations = glyph_count = decoded_bytes = (
                replacement_bytes
            ) = 0
            for index, page in enumerate(pdf.pages):
                parsed_page = next(parsed_pages)
                device = None
                try:
                    require_bounded_page_streams(page)
                    contents = page.obj.get("/Contents")
                    streams = (
                        list(cast(Iterable[Any], contents))
                        if isinstance(contents, pikepdf.Array)
                        else ([] if contents is None else [contents])
                    )
                    decoded_bytes += sum(len(stream.read_bytes()) for stream in streams)
                    if decoded_bytes > MAX_PDF_BYTES:
                        raise _InventoryLimit()
                    resources = page.Resources.get("/Font", pikepdf.Dictionary())
                    if (
                        not isinstance(resources, pikepdf.Dictionary)
                        or len(resources) > 32
                    ):
                        raise ValueError("font_resources")
                    _font_streams(resources, budget)
                    for key, font in resources.items():
                        if len(fonts) >= 256:
                            raise _InventoryLimit()
                        identity = (
                            str(font.objgen[0])
                            if font.is_indirect
                            else f"page:{index}/font:{key}"
                        )
                        fonts[identity] = font
                        paths.setdefault(identity, []).append(
                            f"page:{index}/font:{key}"
                        )
                    ops = list(pikepdf.parse_content_stream(page))
                    operations += len(ops)
                    text_operations += sum(
                        str(op.operator) in _TEXT_OPERATORS for op in ops
                    )
                    if (
                        operations > MAX_OPERATIONS
                        or text_operations > MAX_TEXT_OPERATIONS
                    ):
                        raise _InventoryLimit()
                    boundaries = []
                    start = None
                    graphics_depth = 0
                    for offset, op in enumerate(ops):
                        name = str(op.operator)
                        _require_text_operands(name, list(op.operands))
                        if name == "BT":
                            if start is not None:
                                raise ValueError("text_balance")
                            start = offset
                        elif name == "ET":
                            if start is None:
                                raise ValueError("text_balance")
                            boundaries.append((start, offset + 1))
                            start = None
                        elif name in {"q", "Q"}:
                            graphics_depth += 1 if name == "q" else -1
                            if not 0 <= graphics_depth <= 50:
                                raise ValueError("graphics_balance")
                    if start is not None or graphics_depth:
                        raise ValueError("text_balance")
                    manager = _Manager()
                    device = _Device(
                        manager,
                        index,
                        boundaries,
                        MAX_GLYPHS - glyph_count,
                        MAX_REPLACEMENT_BYTES - replacement_bytes,
                    )
                    _Interpreter(manager, device).process_page(parsed_page)
                    if (
                        device.stack
                        or device.current is not None
                        or len(device.runs) != len(boundaries)
                    ):
                        device.checks.add("text_balance")
                    runs.extend(device.runs)
                    replacements.extend(device.replacements)
                    glyph_count += device.count
                    replacement_bytes += device.replacement_bytes
                    checks = tuple(sorted(device.checks))
                    pages.append(
                        PageInventory(
                            index,
                            not checks,
                            device.count,
                            device.forms,
                            checks,
                            device.marked_count,
                        )
                    )
                    exclusions.update(checks)
                except _InventoryLimit:
                    exclusions.add("document_budget_exhausted")
                    break
                except Exception:
                    checks = tuple(
                        sorted(
                            (device.checks if device else set())
                            | {"page_inspection_incomplete"}
                        )
                    )
                    if device is not None:
                        runs.extend(device.runs)
                        replacements.extend(device.replacements)
                        glyph_count += device.count
                        replacement_bytes += device.replacement_bytes
                    pages.append(
                        PageInventory(
                            index,
                            False,
                            device.count if device else 0,
                            device.forms if device else 0,
                            checks,
                            device.marked_count if device else 0,
                        )
                    )
                    exclusions.update(checks)
            used_codes: dict[str, set[int]] = {}
            for run in runs:
                for glyph in run.glyphs:
                    used_codes.setdefault(glyph.font_identity, set()).add(glyph.cid)
            for identity, font in fonts.items():
                used = tuple(sorted(used_codes.get(identity, set())))
                try:
                    evidence.append(
                        _font_evidence(font, identity, tuple(paths[identity]), used)
                    )
                except Exception:
                    exclusions.add("font_evidence_incomplete")
    except Exception:
        exclusions.add("document_inspection_incomplete")
    complete = (
        len(pages) == page_count
        and all(page.complete for page in pages)
        and not exclusions
    )
    return PDFTextInventory(
        source_hash,
        page_count,
        tuple(pages),
        tuple(evidence),
        tuple(runs),
        tuple(replacements),
        complete,
        tuple(sorted(exclusions)),
    )
