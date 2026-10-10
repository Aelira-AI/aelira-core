"""Bounded font evidence, separate from visual fidelity or review approval.

PDF character codes, CIDs and GIDs are different domains. In particular,
pdfminer's implicit TrueType reverse cmap cannot be queried with a CID unless
the actual glyph addressing and inverse mapping have been established.
"""

from __future__ import annotations

import io
import struct
import zlib
from functools import cached_property
from typing import Any

from fontTools.ttLib import TTFont
from pdfminer.encodingdb import EncodingDB, name2unicode
from pdfminer.pdftypes import dict_value, resolve1, stream_value
from pdfminer.psparser import PSLiteral, literal_name

MAX_FONT_BYTES = 2 * 1024 * 1024
MAX_CMAP_BYTES = 1024 * 1024
MAX_CMAP_ASSIGNMENTS = 100_000
STANDARD_ROMAN = frozenset(
    f"{family}{suffix}"
    for family, suffixes in (
        ("Courier", ("", "-Bold", "-Oblique", "-BoldOblique")),
        ("Helvetica", ("", "-Bold", "-Oblique", "-BoldOblique")),
        ("Times", ("-Roman", "-Bold", "-Italic", "-BoldItalic")),
    )
    for suffix in suffixes
)


class FontMappingUnavailable(ValueError):
    """No source text or parser detail in the failure message."""

    def __init__(self) -> None:
        super().__init__("font_mapping_unavailable")


def _bounded_stream_data(value: Any, limit: int) -> bytes:
    """Decode font evidence without pdfminer's unbounded stream expansion.

    Every caller, including reading-order checks without staging preflight,
    receives the same raw and decoded limits. Unsupported filters/predictors
    remain unresolved; source streams and their cached bytes are not changed.
    """
    stream = stream_value(value)
    if stream.decipher is not None:
        raise FontMappingUnavailable()
    if stream.data is not None:
        cached = stream.data
        if not isinstance(cached, bytes) or len(cached) > limit:
            raise FontMappingUnavailable()
        return cached
    raw = stream.rawdata
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise FontMappingUnavailable()
    data = raw
    filter_object = resolve1(stream.get_any(("F", "Filter"), []))
    filters = (
        filter_object
        if isinstance(filter_object, list)
        else ([] if filter_object is None else [filter_object])
    )
    parameters = resolve1(stream.get_any(("DP", "DecodeParms", "FDecodeParms")))
    parameter_list = (
        parameters if isinstance(parameters, list) else [parameters] * len(filters)
    )
    if len(filters) > 2 or len(parameter_list) != len(filters):
        raise FontMappingUnavailable()
    for filter_name, parameters in zip(filters, parameter_list, strict=True):
        if resolve1(parameters) not in (None, {}) or literal_name(
            resolve1(filter_name)
        ) not in (
            "FlateDecode",
            "Fl",
        ):
            raise FontMappingUnavailable()
        try:
            decoder = zlib.decompressobj()
            data = decoder.decompress(data, limit + 1)
        except zlib.error:
            raise FontMappingUnavailable() from None
        if (
            len(data) > limit
            or decoder.unconsumed_tail
            or not decoder.eof
            or decoder.unused_data
        ):
            raise FontMappingUnavailable()
    return data


def usable_unicode(text: Any) -> bool:
    return (
        isinstance(text, str)
        and 1 <= len(text) <= 16
        and not any(
            ord(char) < 32
            or 0x7F <= ord(char) < 0xA0
            or 0xD800 <= ord(char) <= 0xDFFF
            or ord(char) in {0xFFFD, 0xFFFE, 0xFFFF, 0xFEFF}
            for char in text
        )
    )


def simple_encoding_map(spec: dict[str, Any]) -> dict[int, str]:
    """Resolve the complete defined base encoding and ordered Differences.

    Unknown names remove their assignment, instead of retaining the base's
    unrelated character. The caller must check its actual used-code coverage.
    """
    encoding = resolve1(spec.get("Encoding"))
    if encoding is None:
        if (
            literal_name(spec.get("Subtype")) != "Type1"
            or literal_name(spec.get("BaseFont")) not in STANDARD_ROMAN
        ):
            raise FontMappingUnavailable()
        base, differences = "StandardEncoding", []
    elif isinstance(encoding, dict):
        if "BaseEncoding" not in encoding and not (
            literal_name(spec.get("Subtype")) == "Type1"
            and literal_name(spec.get("BaseFont")) in STANDARD_ROMAN
        ):
            raise FontMappingUnavailable()
        base = literal_name(encoding.get("BaseEncoding", PSLiteral("StandardEncoding")))
        differences = resolve1(encoding.get("Differences", []))
    else:
        base, differences = literal_name(encoding), []
    if base not in EncodingDB.encodings or not isinstance(differences, list):
        raise FontMappingUnavailable()
    result = dict(EncodingDB.encodings[base])
    if base == "WinAnsiEncoding":
        for code in (127, 129, 141, 143, 144, 157):
            result.setdefault(code, "\u2022")
    current: int | None = None
    if len(differences) > 768:
        raise FontMappingUnavailable()
    for item in differences:
        if type(item) is int and 0 <= item <= 255:
            current = item
        elif isinstance(item, PSLiteral) and current is not None and current <= 255:
            try:
                text = name2unicode(literal_name(item))
            except (KeyError, ValueError):
                result.pop(current, None)
            else:
                if usable_unicode(text):
                    result[current] = text
                else:
                    result.pop(current, None)
            current += 1
        else:
            raise FontMappingUnavailable()
    return {code: text for code, text in result.items() if usable_unicode(text)}


def _bound_sfnt(data: bytes) -> None:
    """Reject font containers/invalid directories before fontTools expansion."""
    if len(data) < 12 or data[:4] not in {b"\x00\x01\x00\x00", b"true"}:
        raise FontMappingUnavailable()
    count = struct.unpack_from(">H", data, 4)[0]
    if not 1 <= count <= 100 or 12 + count * 16 > len(data):
        raise FontMappingUnavailable()
    tags = set()
    ranges = []
    for index in range(count):
        tag, _checksum, offset, length = struct.unpack_from(
            ">4sIII", data, 12 + index * 16
        )
        if tag in tags or offset < 12 + count * 16 or offset + length > len(data):
            raise FontMappingUnavailable()
        tags.add(tag)
        if length:
            ranges.append((offset, offset + length))
    ranges.sort()
    if any(last > following for (_, last), (following, _) in zip(ranges, ranges[1:])):
        raise FontMappingUnavailable()
    if not {b"cmap", b"maxp", b"glyf", b"loca", b"head"} <= tags:
        raise FontMappingUnavailable()


def _bound_unicode_cmap(data: bytes) -> None:
    """Bound Unicode format 4/12 expansion before fontTools builds mappings."""
    if not 4 <= len(data) <= MAX_CMAP_BYTES:
        raise FontMappingUnavailable()
    count = struct.unpack_from(">H", data, 2)[0]
    if not 1 <= count <= 100 or 4 + count * 8 > len(data):
        raise FontMappingUnavailable()
    work = 0
    relevant = False
    for index in range(count):
        platform, encoding, offset = struct.unpack_from(">HHI", data, 4 + index * 8)
        unicode_record = platform == 0 or (platform == 3 and encoding in (1, 10))
        relevant |= unicode_record
        if offset + 2 > len(data):
            raise FontMappingUnavailable()
        form = struct.unpack_from(">H", data, offset)[0]
        if unicode_record and form not in {4, 12}:
            raise FontMappingUnavailable()
        if form == 4:
            if offset + 14 > len(data):
                raise FontMappingUnavailable()
            length = struct.unpack_from(">H", data, offset + 2)[0]
            segments_x2 = struct.unpack_from(">H", data, offset + 6)[0]
            segments = segments_x2 // 2
            if segments_x2 % 2 or not segments or offset + length > len(data):
                raise FontMappingUnavailable()
            if 16 + segments * 8 > length:
                raise FontMappingUnavailable()
            previous = -1
            for segment in range(segments):
                end = struct.unpack_from(">H", data, offset + 14 + segment * 2)[0]
                start = struct.unpack_from(
                    ">H", data, offset + 16 + segments * 2 + segment * 2
                )[0]
                if start > end or start <= previous:
                    raise FontMappingUnavailable()
                previous = end
                range_position = offset + 16 + segments * 6 + segment * 2
                range_offset = struct.unpack_from(">H", data, range_position)[0]
                if range_offset and (
                    range_offset % 2
                    or range_position + range_offset < offset + 16 + segments * 8
                    or range_position + range_offset + (end - start + 1) * 2
                    > offset + length
                ):
                    raise FontMappingUnavailable()
                work += end - start + 1
            if start != 65535 or end != 65535:
                raise FontMappingUnavailable()
        elif form == 12:
            if offset + 16 > len(data):
                raise FontMappingUnavailable()
            length = struct.unpack_from(">I", data, offset + 4)[0]
            groups = struct.unpack_from(">I", data, offset + 12)[0]
            if offset + length > len(data) or 16 + groups * 12 > length:
                raise FontMappingUnavailable()
            previous = -1
            for group in range(groups):
                start, end, _gid = struct.unpack_from(
                    ">III", data, offset + 16 + group * 12
                )
                if (
                    start > end
                    or start <= previous
                    or end > 0x10FFFF
                    or _gid + end - start > 65535
                ):
                    raise FontMappingUnavailable()
                previous = end
                work += end - start + 1
                if work > MAX_CMAP_ASSIGNMENTS:
                    raise FontMappingUnavailable()
        elif form == 0 and not unicode_record:
            if (
                offset + 6 > len(data)
                or struct.unpack_from(">H", data, offset + 2)[0] != 262
                or offset + 262 > len(data)
            ):
                raise FontMappingUnavailable()
            work += 256
        elif form == 6 and not unicode_record:
            if offset + 10 > len(data):
                raise FontMappingUnavailable()
            length, _language, first, entries = struct.unpack_from(
                ">HHHH", data, offset + 2
            )
            if (
                first + entries > 65536
                or length < 10 + entries * 2
                or offset + length > len(data)
            ):
                raise FontMappingUnavailable()
            work += entries
        else:
            # Variation, many-to-one, and symbolic-only recovery are separate
            # supported classes; ignoring them would manufacture uniqueness.
            raise FontMappingUnavailable()
        if work > MAX_CMAP_ASSIGNMENTS:
            raise FontMappingUnavailable()
    if not relevant:
        raise FontMappingUnavailable()


class TrueTypeUnicodeResolver:
    """Unique semantic candidates through the actual CID-to-GID chain."""

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self._evidence_failed = False

    @cached_property
    def evidence(self) -> tuple[dict[int, frozenset[str]], int, bytes | None]:
        descendants = resolve1(self.spec.get("DescendantFonts"))
        if not isinstance(descendants, list) or len(descendants) != 1:
            raise FontMappingUnavailable()
        descendant = dict_value(descendants[0])
        if literal_name(descendant.get("Subtype")) != "CIDFontType2":
            raise FontMappingUnavailable()
        descriptor = dict_value(descendant.get("FontDescriptor"))
        program = _bounded_stream_data(descriptor.get("FontFile2"), MAX_FONT_BYTES)
        if not 1 <= len(program) <= MAX_FONT_BYTES:
            raise FontMappingUnavailable()
        _bound_sfnt(program)
        addressing = resolve1(descendant.get("CIDToGIDMap"))
        if addressing is None or literal_name(addressing) == "Identity":
            gid_map = None
        else:
            gid_map = _bounded_stream_data(addressing, 131072)
            if not gid_map or len(gid_map) % 2 or len(gid_map) > 131072:
                raise FontMappingUnavailable()
        try:
            with TTFont(io.BytesIO(program), lazy=True) as font:
                _bound_unicode_cmap(font.reader["cmap"])
                count = font["maxp"].numGlyphs
                if not 1 <= count <= 65535:
                    raise FontMappingUnavailable()
                # The cmap addresses numeric glyph IDs. Do not invoke post/CFF
                # glyph-name parsing just to convert those IDs back to numbers.
                font.setGlyphOrder(
                    [".notdef"] + [f"gid{gid}" for gid in range(1, count)]
                )
                forward: dict[int, int] = {}
                inverse: dict[int, set[str]] = {}
                for table in font["cmap"].tables:
                    if not table.isUnicode():
                        continue
                    for codepoint, name in table.cmap.items():
                        gid = font.getGlyphID(name)
                        if not 0 <= gid < count:
                            raise FontMappingUnavailable()
                        if codepoint in forward and forward[codepoint] != gid:
                            raise FontMappingUnavailable()
                        forward[codepoint] = gid
                        inverse.setdefault(gid, set()).add(chr(codepoint))
                        if len(forward) > MAX_CMAP_ASSIGNMENTS:
                            raise FontMappingUnavailable()
                return (
                    {gid: frozenset(values) for gid, values in inverse.items()},
                    count,
                    gid_map,
                )
        except FontMappingUnavailable:
            raise
        except Exception:
            raise FontMappingUnavailable() from None

    def resolve(self, cid: int) -> str:
        if self._evidence_failed:
            raise FontMappingUnavailable()
        try:
            inverse, count, gid_map = self.evidence
        except Exception:
            # Cache invalid-font evidence, not an individual ambiguous CID.
            # Re-parsing a bad font for every glyph defeats the work budget.
            self._evidence_failed = True
            raise FontMappingUnavailable() from None
        try:
            if type(cid) is not int or not 0 <= cid <= 65535:
                raise FontMappingUnavailable()
            if gid_map is None:
                gid = cid
            elif cid * 2 + 2 <= len(gid_map):
                gid = int.from_bytes(gid_map[cid * 2 : cid * 2 + 2], "big")
            else:
                raise FontMappingUnavailable()
            if not 0 < gid < count:
                raise FontMappingUnavailable()
            values = inverse.get(gid, frozenset())
            if len(values) != 1:
                raise FontMappingUnavailable()
            text = next(iter(values))
            if not usable_unicode(text):
                raise FontMappingUnavailable()
            return text
        except FontMappingUnavailable:
            raise
        except Exception:
            raise FontMappingUnavailable() from None
