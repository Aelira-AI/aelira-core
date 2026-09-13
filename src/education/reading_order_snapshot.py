"""Read-only, bounded snapshots of a PDF's existing semantic reading order.

The raster and semantic text come from the same input bytes. Positions are
optional: only unique, complete painted lines matching MCID text are located.
Neither visual extraction order nor remediation predictions define the sequence.
"""

import base64
from collections import Counter
import hashlib
import io
import math
import tempfile
import zlib

import fitz
import pikepdf
from pikepdf import Name
from pdfminer.pdfpage import PDFPage

from .pdf_checks.completeness import require_complete_pdf_scan
from .pdf_checks.marked_content import (
    MarkedContentResolver,
    _FontResourceManager,
    _MarkedTextDevice,
    _PageInterpreter,
)
from .pdf_checks.reading_order import _element_page

MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
MAX_OBJECTS = 40000
MAX_STRUCTURE_NODES = 20000
MAX_BLOCKS = 2000
MAX_TEXT_CHARACTERS = 200000
MAX_DECODED_STREAM_BYTES = 8 * 1024 * 1024
MAX_TOTAL_DECODED_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_PAGE_AREA = 20_000_000
MAX_PAGE_DIMENSION = 14400
MAX_PREVIEW_DIMENSION = 1200
MAX_PREVIEW_BYTES = 6 * 1024 * 1024
MAX_PAGE_CONTENT_BYTES = 1024 * 1024
MAX_EXECUTED_CONTENT_BYTES = 4 * 1024 * 1024
MAX_CONTENT_OPERATIONS = 20000


class _Unavailable(Exception):
    def __init__(self, reason):
        self.reason = reason


def _limit(condition):
    if condition:
        raise _Unavailable("limit_exceeded")


class _SnapshotResolver(MarkedContentResolver):
    def text(self, key):
        page_index, _ = key
        if page_index not in self.decoded:
            self.decoded[page_index] = {}
            try:
                manager = _FontResourceManager(self.fail)
                device = _MarkedTextDevice(manager, self.fail)
                with open(self.file_path, "rb") as source:
                    page = next(PDFPage.get_pages(source, pagenos={page_index}))
                    # Rotation describes viewing, not text-showing sequence.
                    # Keep it out of the scanner device's line-gap inference.
                    page.rotate = 0
                    _PageInterpreter(manager, device).process_page(page)
                if device.stack:
                    self.fail("marked_content_balance")
                self.decoded[page_index] = device.entries
            except Exception:
                self.fail("content_decode")
        return super().text(key)


def _execution_preflight(pdf, stream_sizes):
    """Bound invocation work, not just unique stored objects, before rendering.

    Executable resources outside page streams are unsupported in this initial
    subset. Walking direct dictionaries as well as indirect objects prevents a
    direct resource dictionary from bypassing the resource-type checks.
    """
    stack = [(pdf.Root, 0)]
    seen = set()
    visits = 0
    while stack:
        obj, depth = stack.pop()
        visits += 1
        _limit(visits > MAX_OBJECTS or depth > 50)
        if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
            continue
        if obj.is_indirect:
            if obj.objgen in seen:
                continue
            seen.add(obj.objgen)
        if isinstance(obj, pikepdf.Array):
            children = list(obj)
        else:
            if (
                obj.get("/Subtype") in (Name.Form, Name.Type3)
                or "/PatternType" in obj
                or "/ShadingType" in obj
                or ("/SMask" in obj and obj.SMask != Name("/None"))
            ):
                raise _Unavailable("unsupported_pdf")
            children = list(obj.values())
        _limit(len(children) + len(stack) > MAX_OBJECTS)
        stack.extend((child, depth + 1) for child in children)

    executed_bytes = operations = image_pixels = 0
    for page in pdf.pages:
        contents = page.obj.get("/Contents", pikepdf.Array())
        streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
        _limit(len(streams) > 1000)
        page_bytes = 0
        for stream in streams:
            if (
                not isinstance(stream, pikepdf.Stream)
                or stream.objgen not in stream_sizes
            ):
                raise _Unavailable("unsupported_pdf")
            page_bytes += stream_sizes[stream.objgen]
            _limit(page_bytes > MAX_PAGE_CONTENT_BYTES)
        executed_bytes += page_bytes
        _limit(executed_bytes > MAX_EXECUTED_CONTENT_BYTES)
        resources = page.Resources
        xobjects = resources.get("/XObject", pikepdf.Dictionary())
        for instruction in pikepdf.parse_content_stream(page):
            operations += 1
            _limit(operations > MAX_CONTENT_OPERATIONS)
            operator = str(instruction.operator)
            if operator == "INLINE IMAGE":
                raise _Unavailable("unsupported_pdf")
            if operator == "Do":
                operands = instruction.operands
                if len(operands) != 1 or operands[0] not in xobjects:
                    raise _Unavailable("unsupported_pdf")
                image = xobjects[operands[0]]
                if (
                    not isinstance(image, pikepdf.Stream)
                    or image.get("/Subtype") != Name.Image
                ):
                    raise _Unavailable("unsupported_pdf")
                image_pixels += int(image.Width) * int(image.Height)
                _limit(image_pixels > MAX_IMAGE_PIXELS)


def _preflight(pdf):
    """Bound stream expansion before either PDF engine decodes or renders it.

    Unfiltered and single-Flate streams cover normal page/font/vector content.
    JPEG images have their encoded dimensions checked independently. Other filter
    chains are deliberately unavailable until they have bounded decoding support.
    """
    _limit(len(pdf.objects) > MAX_OBJECTS)
    total_decoded = 0
    image_pixels = 0
    stream_sizes = {}
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream):
            continue
        raw = obj.read_raw_bytes()
        _limit(len(raw) > MAX_SOURCE_BYTES)
        filters = obj.get("/Filter")
        if isinstance(filters, pikepdf.Array):
            if len(filters) != 1:
                raise _Unavailable("unsupported_pdf")
            filters = filters[0]
        if filters is None:
            size = len(raw)
        elif filters == Name.FlateDecode:
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(raw, MAX_DECODED_STREAM_BYTES + 1)
            _limit(
                len(decoded) > MAX_DECODED_STREAM_BYTES or bool(decoder.unconsumed_tail)
            )
            if not decoder.eof:
                raise _Unavailable("invalid_pdf")
            size = len(decoded)
        elif filters == Name.DCTDecode and obj.get("/Subtype") == Name.Image:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as image:
                width, height = image.size
                _limit(width * height > MAX_IMAGE_PIXELS)
                if width != obj.get("/Width") or height != obj.get("/Height"):
                    raise _Unavailable("unsupported_pdf")
            size = len(raw)
        else:
            raise _Unavailable("unsupported_pdf")
        _limit(size > MAX_DECODED_STREAM_BYTES)
        stream_sizes[obj.objgen] = size
        total_decoded += size
        _limit(total_decoded > MAX_TOTAL_DECODED_BYTES)
        if obj.get("/Subtype") == Name.Image:
            width, height = obj.get("/Width"), obj.get("/Height")
            if (
                not isinstance(width, int)
                or not isinstance(height, int)
                or min(width, height) <= 0
            ):
                raise _Unavailable("invalid_pdf")
            image_pixels += width * height
            _limit(image_pixels > MAX_IMAGE_PIXELS)
    _execution_preflight(pdf, stream_sizes)


def _semantic_blocks(pdf, path, target_page):
    """Traverse /K, sharing the scanner's verified MCID/ParentTree resolver.

    This presentation reader retains replacement strings verbatim and fails closed
    on malformed alternatives or content omitted from the structure traversal.
    It does not alter the scanner or any document object.
    """
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        raise _Unavailable("untagged_pdf")
    if not isinstance(root, pikepdf.Dictionary):
        raise _Unavailable("unresolved_structure")
    if "/K" not in root:
        raise _Unavailable("no_tagged_content")
    page_indices = {page.obj.objgen: i for i, page in enumerate(pdf.pages)}
    resolver = _SnapshotResolver(pdf, path)
    visits = 0
    text_count = 0

    def text_block(text, source):
        nonlocal text_count
        if not isinstance(text, str):
            raise _Unavailable("unresolved_structure")
        text_count += len(text)
        _limit(text_count > MAX_TEXT_CHARACTERS)
        return {"text": text, "source": source}

    def visit(kid, owner=None, inherited=-1, depth=0):
        nonlocal visits
        visits += 1
        _limit(depth > 50 or visits > MAX_STRUCTURE_NODES)
        if isinstance(kid, pikepdf.Array):
            _limit(len(kid) > MAX_STRUCTURE_NODES)
            result = []
            for child in kid:
                result.extend(visit(child, owner, inherited, depth + 1))
                _limit(len(result) > MAX_BLOCKS)
            return result
        is_mcr = isinstance(kid, pikepdf.Dictionary) and (
            kid.get("/Type") == Name.MCR or "/MCID" in kid
        )
        if isinstance(kid, int) or is_mcr:
            page_index = (
                _element_page(kid, inherited, page_indices) if is_mcr else inherited
            )
            if is_mcr and ("/Stm" in kid or "/StmOwn" in kid):
                raise _Unavailable("unresolved_structure")
            key = resolver.resolve(
                owner,
                kid.get("/MCID") if is_mcr else kid,
                page_index,
                invalid_page=page_index == -2,
            )
            if key is None:
                raise _Unavailable("unresolved_structure")
            block = None
            if key[0] == target_page:
                decoded = resolver.text(key)
                if decoded is not None:
                    # The scanner normalizes edge whitespace for comparison;
                    # snapshots preserve the underlying decoded string.
                    raw = resolver.decoded[key[0]][key[1]]
                    block = text_block(raw["text"], raw["source"])
            return [(key[0], block)]
        if not isinstance(kid, pikepdf.Dictionary) or kid.get("/Type") == Name.OBJR:
            raise _Unavailable("unresolved_structure")
        page_index = _element_page(kid, inherited, page_indices)
        if page_index == -2:
            raise _Unavailable("unresolved_structure")
        children = visit(kid.K, kid, page_index, depth + 1) if "/K" in kid else []
        for name in ("/ActualText", "/Alt"):
            if name in kid and not isinstance(kid[name], pikepdf.String):
                raise _Unavailable("unresolved_structure")
        if "/ActualText" in kid:
            pages = {number for number, _ in children}
            if page_index >= 0:
                pages.add(page_index)
            if len(pages) != 1:
                raise _Unavailable("unresolved_structure")
            return [(pages.pop(), text_block(str(kid.ActualText), "ActualText"))]
        if "/Alt" in kid:
            pages = {number for number, _ in children}
            if page_index >= 0:
                pages.add(page_index)
            if len(pages) != 1:
                raise _Unavailable("unresolved_structure")
            alt_page = pages.pop()
            if alt_page == target_page:
                children.insert(0, (alt_page, text_block(str(kid.Alt), "Alt")))
        return children

    ordered = visit(root.K)
    if resolver.failed:
        raise _Unavailable("unresolved_structure")
    # A complete sequence cannot omit a decoded marked-content reference.
    decoded = resolver.decoded.get(target_page, {})
    if any((target_page, mcid) not in resolver.used for mcid in decoded):
        raise _Unavailable("unresolved_structure")
    blocks = [
        block
        for page, block in ordered
        if page == target_page and block and block["text"].strip()
    ]
    _limit(len(blocks) > MAX_BLOCKS)
    if not blocks:
        raise _Unavailable("no_tagged_content")
    return blocks


def _positions(page, blocks):
    """Only locate an entire unique line; never search substrings for boxes."""
    extracted = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    lines = []
    for block in extracted.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            lines.append((text, line.get("bbox")))
    _limit(len(lines) > MAX_STRUCTURE_NODES)
    _limit(sum(len(text) for text, _ in lines) > MAX_TEXT_CHARACTERS)
    counts = Counter(block["text"] for block in blocks)
    for index, block in enumerate(blocks, 1):
        block.update(index=index, bbox=None)
        text = block["text"]
        if (
            block["source"] != "MCID"
            or counts[text] != 1
            or "\n" in text
            or "\r" in text
        ):
            continue
        # A second occurrence inside another line is also ambiguous.
        candidates = [line for line in lines if text in line[0]]
        if len(candidates) != 1 or candidates[0][0] != text or candidates[0][1] is None:
            continue
        rect = fitz.Rect(candidates[0][1]) * page.rotation_matrix
        if (
            not rect.is_empty
            and not rect.is_infinite
            and all(math.isfinite(value) for value in rect)
            and page.rect.contains(rect)
        ):
            block["bbox"] = list(rect)


def inspect_pdf_reading_order(content: bytes, page_number: int) -> dict:
    """Return semantic order or an explicit safe reason, with no partial order.

    ``width``, ``height`` and optional boxes use top-left coordinates in the
    rotated, cropped page frame (PDF points); the preview is scaled to that frame.
    Unknown/unsupported documents never receive a visual-order fallback.
    """
    result = {
        "status": "unavailable",
        "reason": None,
        "sha256": None,
        "page_count": 0,
        "page_number": page_number,
        "width": None,
        "height": None,
        "preview_png_base64": None,
        "blocks": [],
        "unpositioned_count": 0,
    }
    try:
        if not isinstance(content, bytes) or not content:
            raise _Unavailable("invalid_pdf")
        _limit(len(content) > MAX_SOURCE_BYTES)
        result["sha256"] = hashlib.sha256(content).hexdigest()
        if (
            not isinstance(page_number, int)
            or isinstance(page_number, bool)
            or page_number < 1
        ):
            raise _Unavailable("invalid_page")
        with pikepdf.open(io.BytesIO(content), attempt_recovery=False) as pdf:
            if pdf.is_encrypted:
                raise _Unavailable("encrypted_pdf")
            result["page_count"] = len(pdf.pages)
            _limit(result["page_count"] > MAX_PAGES)
            if page_number > result["page_count"]:
                raise _Unavailable("invalid_page")
            _preflight(pdf)
            with fitz.open(stream=content, filetype="pdf") as rendered:
                if len(rendered) != result["page_count"] or rendered.is_repaired:
                    raise _Unavailable("invalid_pdf")
                page = rendered[page_number - 1]
                width, height = page.rect.width, page.rect.height
                if not all(
                    math.isfinite(value) and value > 0 for value in (width, height)
                ):
                    raise _Unavailable("invalid_pdf")
                _limit(
                    width * height > MAX_PAGE_AREA
                    or max(width, height) > MAX_PAGE_DIMENSION
                )
                result.update(width=width, height=height)
                scale = min(2.0, MAX_PREVIEW_DIMENSION / max(width, height))
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(scale, scale), alpha=False, annots=False
                )
                preview = pixmap.tobytes("png")
                _limit(len(preview) > MAX_PREVIEW_BYTES)
                result["preview_png_base64"] = base64.b64encode(preview).decode("ascii")
                # The resolver accepts a filename. This private temporary copy
                # retains the exact uploaded bytes; no PDF is saved or repaired.
                with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
                    source.write(content)
                    source.flush()
                    with require_complete_pdf_scan(False):
                        blocks = _semantic_blocks(pdf, source.name, page_number - 1)
                _positions(page, blocks)
                result.update(
                    status="available",
                    blocks=blocks,
                    unpositioned_count=sum(block["bbox"] is None for block in blocks),
                )
    except _Unavailable as error:
        result["reason"] = error.reason
    except pikepdf.PasswordError:
        result["reason"] = "encrypted_pdf"
    except Exception:
        # Parser messages can contain PDF text and paths; do not expose them.
        result["reason"] = "invalid_pdf"
    return result
