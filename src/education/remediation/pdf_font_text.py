"""Font-aware source bindings for generated PDF accessibility text.

PDF content-string bytes are character codes in the current font, not PDFDoc
strings. Decode a private single-page snapshot with the verifier's font policy
before using text for a structure node. ActualText is deliberately ignored:
replacement labels cannot certify the glyphs they replace. No source is saved.
"""

from __future__ import annotations

import base64
import io
import zlib
import math
from dataclasses import dataclass
from typing import Any, cast
from typing_extensions import Buffer

import pikepdf
from pdfminer.pdfpage import PDFPage

from ..pdf_checks.marked_content import (
    _FontResourceManager,
    _MarkedTextDevice,
    _PageInterpreter,
)

MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
# Artwork can contain many small vector paths while text remains modest.
# Bound all interpretation work separately from semantic text operations.
MAX_OPERATIONS = 100_000
MAX_TEXT_OPERATIONS = 20_000
_TEXT_OPERATORS = frozenset(
    {
        "BT",
        "ET",
        "Tf",
        "Tc",
        "Tw",
        "Tz",
        "TL",
        "Tr",
        "Ts",
        "Tm",
        "Td",
        "TD",
        "T*",
        "Tj",
        "TJ",
        "'",
        '"',
    }
)
MAX_GRAPHICS_DEPTH = 50
MAX_DECODED_STREAM_BYTES = 8 * 1024 * 1024


class FontTextBindingError(ValueError):
    """Content-free refusal, never parser messages or source text."""

    def __init__(self, code: str = "source_text_mapping_unavailable"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DecodedTextRun:
    start: int
    end: int
    text: str


class _BoundedBuffer(io.BytesIO):
    def write(self, data: Buffer) -> int:
        if self.tell() + memoryview(data).nbytes > MAX_SNAPSHOT_BYTES:
            raise FontTextBindingError("source_text_binding_limit")
        return super().write(data)


class _RunDevice(_MarkedTextDevice):
    def __init__(self, manager: Any, fail: Any) -> None:
        super().__init__(manager, fail)
        self.run: dict[str, Any] | None = None
        self.runs: list[str] = []
        self.marked_depth = 0

    def begin_text(self) -> None:
        if self.run is not None:
            self.fail("text_balance")
        self.run = {"text": "", "source": "font", "previous": None}

    def end_text(self) -> None:
        if self.run is None:
            self.fail("text_balance")
            return
        self.runs.append(self.run["text"])
        self.run = None

    def _entry(self) -> dict[str, Any] | None:
        return self.run

    def begin_tag(self, tag: Any, props: Any = None) -> None:
        # Keep a bounded balance check but do not substitute /ActualText.
        self.marked_depth += 1
        if self.marked_depth > 50:
            self.fail("content_depth_limit")

    def end_tag(self) -> None:
        self.marked_depth -= 1
        if self.marked_depth < 0:
            self.fail("marked_content_balance")

    def render_string(self, textstate: Any, seq: Any, ncs: Any, graphics: Any) -> None:
        if self.run is None:
            self.fail("text_balance")
        super().render_string(textstate, seq, ncs, graphics)

    def render_char(self, *args: Any) -> float:
        advance = super().render_char(*args)
        previous = self.run["previous"] if self.run is not None else None
        if previous is not None and not all(math.isfinite(v) for v in previous.bbox):
            self.fail("text_geometry")
        return advance


class _RunInterpreter(_PageInterpreter):
    def do_BT(self) -> None:
        cast(_RunDevice, self.device).begin_text()
        super().do_BT()

    def do_ET(self) -> None:
        cast(_RunDevice, self.device).end_text()
        super().do_ET()


def require_bounded_page_streams(page: pikepdf.Page) -> None:
    contents = page.obj.get("/Contents")
    streams = (
        list(cast(Any, contents)) if isinstance(contents, pikepdf.Array) else [contents]
    )
    if contents is None:
        return
    if len(streams) > 100:
        raise FontTextBindingError("source_text_binding_limit")
    total = 0
    for stream in streams:
        if not isinstance(stream, pikepdf.Stream):
            raise FontTextBindingError("source_text_binding_invalid")
        if stream.get("/DecodeParms") is not None:
            raise FontTextBindingError("source_text_scope_unsupported")
        data = stream.read_raw_bytes()
        if len(data) > MAX_DECODED_STREAM_BYTES:
            raise FontTextBindingError("source_text_binding_limit")
        filter_object = stream.get("/Filter")
        filters: list[Any] = (
            list(cast(Any, filter_object))
            if isinstance(filter_object, pikepdf.Array)
            else ([] if filter_object is None else [filter_object])
        )
        if len(filters) > 2:
            raise FontTextBindingError("source_text_scope_unsupported")
        for filter_name in filters:
            if filter_name == pikepdf.Name.ASCII85Decode:
                data = (
                    base64.a85decode(data, adobe=data.startswith(b"<~"))
                    if data.startswith(b"<~")
                    else base64.a85decode(data.removesuffix(b"~>"))
                )
            elif filter_name == pikepdf.Name.FlateDecode:
                # qpdf emits empty Flate streams for blank pages. There is no
                # compressed payload to expand; nonempty truncated streams
                # still require an EOF marker below.
                if not data:
                    continue
                decoder = zlib.decompressobj()
                data = decoder.decompress(data, MAX_DECODED_STREAM_BYTES + 1)
                if len(data) > MAX_DECODED_STREAM_BYTES or decoder.unconsumed_tail:
                    raise FontTextBindingError("source_text_binding_limit")
                if not decoder.eof or decoder.unused_data:
                    raise FontTextBindingError("source_text_binding_invalid")
            else:
                raise FontTextBindingError("source_text_scope_unsupported")
            if len(data) > MAX_DECODED_STREAM_BYTES:
                raise FontTextBindingError("source_text_binding_limit")
        total += len(data)
        if total > MAX_DECODED_STREAM_BYTES:
            raise FontTextBindingError("source_text_binding_limit")


def decode_page_text_runs(
    pdf: pikepdf.Pdf, page_index: int, operators: list[Any] | None = None
) -> tuple[DecodedTextRun, ...]:
    """Decode complete BT/ET runs; refuse unknown fonts or Form scopes.

    pikepdf copies the page into an isolated document so neither interpretation
    nor resource normalization modifies the caller's PDF. Operator positions
    refer to the supplied page, including its existing marked-content wrappers.
    """
    try:
        page = pdf.pages[page_index]
        require_bounded_page_streams(page)
        from .pdf_ocr_form import require_bounded_font_resources

        require_bounded_font_resources(
            [
                cast(
                    pikepdf.Dictionary,
                    page.Resources.get("/Font", pikepdf.Dictionary()),
                )
            ]
        )
        ops = (
            operators
            if operators is not None
            else list(pikepdf.parse_content_stream(page))
        )
        if len(ops) > MAX_OPERATIONS:
            raise FontTextBindingError("source_text_binding_limit")
        boundaries: list[tuple[int, int]] = []
        start: int | None = None
        graphics_depth = 0
        text_operations = 0
        for index, op in enumerate(ops):
            name = str(op.operator)
            text_operations += name in _TEXT_OPERATORS
            if text_operations > MAX_TEXT_OPERATIONS:
                raise FontTextBindingError("source_text_binding_limit")
            if name == "BT":
                if start is not None:
                    raise FontTextBindingError("source_text_binding_invalid")
                start = index
            elif name == "ET":
                if start is None:
                    raise FontTextBindingError("source_text_binding_invalid")
                boundaries.append((start, index + 1))
                start = None
            elif name == "q":
                graphics_depth += 1
                if graphics_depth > MAX_GRAPHICS_DEPTH:
                    raise FontTextBindingError("source_text_binding_limit")
            elif name == "Q":
                graphics_depth -= 1
                if graphics_depth < 0:
                    raise FontTextBindingError("source_text_binding_invalid")
        if start is not None or graphics_depth:
            raise FontTextBindingError("source_text_binding_invalid")

        def fail(reason: str) -> None:
            code = (
                "source_text_scope_unsupported"
                if reason == "stream_scope"
                else "source_text_mapping_unavailable"
            )
            raise FontTextBindingError(code)

        with pikepdf.new() as probe:
            probe.pages.append(page)
            # Annotations are outside this source-glyph binding. Never copy
            # their actions/appearance graphs into the decoder snapshot.
            if "/Annots" in probe.pages[0].obj:
                del probe.pages[0].obj["/Annots"]
            output = _BoundedBuffer()
            probe.save(output, deterministic_id=True, fix_metadata_version=False)
        manager = _FontResourceManager(fail)
        device = _RunDevice(manager, fail)
        pages = list(PDFPage.get_pages(io.BytesIO(output.getvalue())))
        if len(pages) != 1:
            raise FontTextBindingError("source_text_binding_invalid")
        _RunInterpreter(manager, device).process_page(pages[0])
        if (
            device.run is not None
            or device.marked_depth
            or len(device.runs) != len(boundaries)
        ):
            raise FontTextBindingError("source_text_binding_invalid")
        return tuple(
            DecodedTextRun(first, last, text)
            for (first, last), text in zip(boundaries, device.runs, strict=True)
        )
    except FontTextBindingError:
        raise
    except Exception:
        raise FontTextBindingError() from None
