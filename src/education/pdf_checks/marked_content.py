"""Bounded decoding of page-stream MCIDs with explicit ParentTree ownership.

Only page content streams are supported. Form/other stream scopes, nested MCIDs,
unresolved glyphs and ambiguous ownership report incomplete verification. Text
comes from PDF font decoding or a PDF ActualText string, never an MCID label.
"""

from collections import defaultdict

import pikepdf
from pdfminer.layout import LTChar
from pdfminer.pdfdevice import PDFTextDevice
from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import dict_value, resolve1, stream_value
from pdfminer.psparser import LIT, PSLiteral, literal_name
from pdfminer.utils import decode_text

from .completeness import record_incomplete_check
from .font_mapping import (
    FontMappingUnavailable,
    TrueTypeUnicodeResolver,
    simple_encoding_map,
)


def incomplete(reason):
    record_incomplete_check("reading_order." + reason)


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class _LimitExceeded(ValueError):
    pass


class _FontResourceManager(PDFResourceManager):
    def __init__(self, fail):
        super().__init__()
        self.fail = fail

    def get_font(self, objid, spec):
        original_spec = spec
        subtype = literal_name(spec.get("Subtype"))
        if (
            subtype == "Type0"
            and literal_name(resolve1(spec.get("Encoding"))) != "Identity-H"
        ):
            # Bound composite decoding to two-byte horizontal identity codes.
            # Other CMaps can silently discard undecodable bytes in pdfminer.
            self.fail("font_encoding")
        simple_map = None
        if subtype in ("Type1", "TrueType", "MMType1") and "ToUnicode" not in spec:
            try:
                simple_map = simple_encoding_map(spec)
            except FontMappingUnavailable:
                self.fail("font_encoding")
                raise ValueError("Unsupported font encoding") from None
            # Do not let pdfminer choose a different implicit base encoding.
            if resolve1(spec.get("Encoding")) is None and simple_map is not None:
                spec = dict(spec, Encoding=LIT("StandardEncoding"))
        if subtype == "Type0" and "ToUnicode" not in spec:
            # pdfminer expands its implicit reverse cmap in the constructor,
            # before our bounded resolver can run. Omit only that program from
            # the private metrics spec; retain the original for lazy evidence.
            descendants = resolve1(spec.get("DescendantFonts"))
            if not isinstance(descendants, list) or len(descendants) != 1:
                self.fail("font_encoding")
                raise ValueError("Unsupported font encoding")
            descendant = dict(dict_value(descendants[0]))
            descriptor = dict(dict_value(descendant.get("FontDescriptor", {})))
            descriptor.pop("FontFile2", None)
            descendant["FontDescriptor"] = descriptor
            spec = dict(spec, DescendantFonts=[descendant])
        font = super().get_font(objid, spec)
        font.aelira_evidence_origin = "declared_tounicode"
        if "ToUnicode" not in spec:
            if subtype == "Type0":
                # Lazy resolution is per used glyph. The nonsemantic recovery
                # inventory must still be able to collect undecodable fonts.
                if (
                    getattr(font, "cidcoding", "")
                    not in ("Adobe-Identity", "Adobe-UCS")
                    and getattr(font, "unicode_map", None) is not None
                ):
                    font.aelira_evidence_origin = "defined_character_collection"
                else:
                    font.aelira_unicode_resolver = TrueTypeUnicodeResolver(
                        original_spec
                    )
                    font.aelira_evidence_origin = "embedded_truetype_chain"
            elif simple_map is not None:
                font.cid2unicode = simple_map
                font.aelira_evidence_origin = "defined_simple_encoding"
        return font


class _MarkedTextDevice(PDFTextDevice):
    def __init__(self, manager, fail):
        super().__init__(manager)
        self.fail = fail
        self.stack = []
        self.entries = {}
        self.character_count = 0
        self.evidence_origins = set()

    def begin_tag(self, tag, props=None):
        incomplete = self.fail
        props = resolve1(props)
        if props is None:
            props = {}
        if not isinstance(props, dict):
            incomplete("marked_content_properties")
            props = {}
        mcid = resolve1(props.get("MCID"))
        if "MCID" in props and not _integer(mcid):
            incomplete("mcid_reference")
            mcid = None
        if mcid is not None:
            if any(frame["mcid"] is not None for frame in self.stack):
                incomplete("nested_mcid")
            if mcid in self.entries:
                incomplete("duplicate_mcid")
            if len(self.entries) >= 20000:
                incomplete("content_limit")
                raise _LimitExceeded()
            entry = {"text": "", "source": "MCID", "previous": None, "bbox": None}
            self.entries.setdefault(mcid, entry)
        replacement = None
        if "ActualText" in props:
            raw = resolve1(props["ActualText"])
            if isinstance(raw, bytes):
                replacement = decode_text(raw)
            else:
                incomplete("actual_text")
        self.stack.append(
            {"mcid": mcid, "replacement": replacement, "first": None, "last": None}
        )
        if len(self.stack) > 50:
            incomplete("content_depth_limit")
            raise _LimitExceeded()

    def _entry(self):
        for frame in reversed(self.stack):
            if frame["mcid"] is not None:
                return self.entries[frame["mcid"]]
        return None

    def end_tag(self):
        incomplete = self.fail
        if not self.stack:
            incomplete("marked_content_balance")
            return
        frame = self.stack[-1]
        entry = self._entry()
        if frame["replacement"] is not None and not any(
            f["replacement"] is not None for f in self.stack[:-1]
        ):
            if entry is None:
                incomplete("replacement_scope")
            else:
                self._append(entry, frame["replacement"], frame["first"], frame["last"])
                entry["source"] = "ActualText"
        self.stack.pop()

    @staticmethod
    def _append(entry, text, first, last):
        previous = entry["previous"]
        if previous is not None and first is not None:
            if abs(first.y0 - previous.y0) > max(first.height, previous.height) * 0.5:
                entry["text"] += "\n"
            elif first.x0 - previous.x1 > max(first.width, first.height) * 0.1:
                entry["text"] += " "
        entry["text"] += text
        entry["previous"] = last

    def render_string(self, textstate, seq, ncs, graphicstate):
        for value in seq:
            if isinstance(value, bytes) and value:
                if (textstate.font.is_multibyte() and len(value) % 2) or not list(
                    textstate.font.decode(value)
                ):
                    self.fail("font_encoding")
                    raise ValueError("Undecodable text-showing bytes")
        super().render_string(textstate, seq, ncs, graphicstate)

    def render_char(
        self, matrix, font, fontsize, scaling, rise, cid, ncs, graphicstate
    ):
        incomplete = self.fail
        self.character_count += 1
        if self.character_count > 200000:
            incomplete("content_limit")
            raise _LimitExceeded()
        # Undefined mappings must raise, never become '(cid:N)'.
        unicode_map = getattr(font, "unicode_map", None)
        resolver = getattr(font, "aelira_unicode_resolver", None)
        try:
            text = (
                resolver.resolve(cid)
                if resolver is not None
                else (
                    unicode_map.get_unichr(cid)
                    if unicode_map is not None
                    else font.to_unichr(cid)
                )
            )
        except FontMappingUnavailable:
            incomplete("font_unicode")
            raise ValueError("Unresolved Unicode glyph") from None
        self.evidence_origins.add(
            getattr(font, "aelira_evidence_origin", "unsupported")
        )
        if (
            not isinstance(text, str)
            or not text
            or len(text) > 16
            or "\ufffd" in text
            or any(0xD800 <= ord(char) <= 0xDFFF for char in text)
        ):
            incomplete("font_unicode")
            raise ValueError("Unresolved Unicode glyph")
        if font.is_vertical():
            incomplete("font_direction")
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
        entry = self._entry()
        if entry is not None and "bbox" in entry:
            previous_box = entry["bbox"]
            entry["bbox"] = (
                (
                    min(previous_box[0], char.x0),
                    min(previous_box[1], char.y0),
                    max(previous_box[2], char.x1),
                    max(previous_box[3], char.y1),
                )
                if previous_box is not None
                else char.bbox
            )
        replacements = [
            frame for frame in self.stack if frame["replacement"] is not None
        ]
        for frame in replacements:
            if frame["first"] is None:
                frame["first"] = char
            frame["last"] = char
        if entry is not None and not replacements:
            self._append(entry, text, char, char)
        return char.adv


class _PageInterpreter(PDFPageInterpreter):
    """Do not inherit pdfminer's permissive font and XObject fallbacks."""

    def init_resources(self, resources):
        incomplete = self.device.fail
        for spec in dict_value(resources.get("Font", {})).values():
            if literal_name(dict_value(spec).get("Subtype")) not in (
                "Type0",
                "Type1",
                "TrueType",
                "MMType1",
            ):
                incomplete("font_type")
        super().init_resources(resources)

    def do_Tf(self, fontid, fontsize):
        incomplete = self.device.fail
        if literal_name(fontid) not in self.fontmap:
            incomplete("font_reference")
            raise ValueError("Unresolved text font")
        super().do_Tf(fontid, fontsize)

    def do_TJ(self, seq):
        incomplete = self.device.fail
        if self.textstate.font is None:
            incomplete("font_reference")
            raise ValueError("Missing text font")
        super().do_TJ(seq)

    def do_BDC(self, tag, props):
        incomplete = self.device.fail
        if isinstance(props, PSLiteral):
            props = dict_value(self.resources.get("Properties", {})).get(
                literal_name(props)
            )
            if props is None:
                incomplete("marked_content_properties")
        if not isinstance(tag, PSLiteral):
            incomplete("marked_content_properties")
        super().do_BDC(tag, resolve1(props))

    def do_Do(self, xobjid_arg):
        incomplete = self.device.fail
        reference = self.xobjmap.get(literal_name(xobjid_arg))
        if (
            reference is None
            or literal_name(stream_value(reference).get("Subtype")) != "Image"
        ):
            incomplete("stream_scope")
        # Images have no text; Forms require their own ownership namespace.


class MarkedContentResolver:
    """Resolve (page, MCID, owning structure element) before retrieving text."""

    def __init__(self, pdf, file_path):
        self.pdf = pdf
        self.file_path = file_path
        self.owners = {}
        self.reverse = defaultdict(list)
        self.decoded = {}
        self.used = set()
        self.loaded = False
        self.failed = False

    def fail(self, reason):
        self.failed = True
        incomplete(reason)

    def _load_owners(self):
        incomplete = self.fail
        self.loaded = True
        numbers = {}
        visits = 0

        def walk(node, depth=0):
            nonlocal visits
            visits += 1
            if depth > 50 or visits > 20000:
                incomplete("parent_tree_limit")
                return
            if not isinstance(node, pikepdf.Dictionary):
                incomplete("parent_tree")
                return
            nums = node.get("/Nums", pikepdf.Array())
            if not isinstance(nums, pikepdf.Array) or len(nums) % 2:
                incomplete("parent_tree")
                return
            if len(nums) > 40000:
                incomplete("parent_tree_limit")
                return
            for offset in range(0, len(nums), 2):
                key = nums[offset]
                if not _integer(key) or key in numbers:
                    incomplete("parent_tree")
                    continue
                numbers[key] = nums[offset + 1]
            kids = node.get("/Kids", pikepdf.Array())
            if not isinstance(kids, pikepdf.Array):
                incomplete("parent_tree")
                return
            if len(kids) > 20000:
                incomplete("parent_tree_limit")
                return
            for kid in kids:
                walk(kid, depth + 1)

        walk(self.pdf.Root.StructTreeRoot.get("/ParentTree"))
        page_keys = set()
        for page_index, page in enumerate(self.pdf.pages):
            key = page.obj.get("/StructParents")
            if key is None:
                continue
            if not _integer(key) or key in page_keys:
                incomplete("parent_tree_page")
                continue
            page_keys.add(key)
            owners = numbers.get(key)
            if not isinstance(owners, pikepdf.Array):
                incomplete("parent_tree_page")
                continue
            if len(owners) > 20000:
                incomplete("parent_tree_limit")
                continue
            for mcid, owner in enumerate(owners):
                if owner is None:
                    continue
                if not isinstance(owner, pikepdf.Dictionary) or not owner.is_indirect:
                    incomplete("parent_tree_owner")
                    continue
                self.owners[page_index, mcid] = owner.objgen
                self.reverse[owner.objgen, mcid].append(page_index)

    def resolve(self, owner, mcid, page_index, *, invalid_page=False):
        incomplete = self.fail
        if not self.loaded:
            self._load_owners()
        if (
            not _integer(mcid)
            or not isinstance(owner, pikepdf.Dictionary)
            or not owner.is_indirect
        ):
            incomplete("mcid_reference")
            return None
        if invalid_page:
            incomplete("page_reference")
            return None
        if page_index < 0:
            candidates = self.reverse.get((owner.objgen, mcid), [])
            if len(candidates) != 1:
                incomplete("mcid_page")
                return None
            page_index = candidates[0]
        key = (page_index, mcid)
        if self.owners.get(key) != owner.objgen:
            incomplete("parent_tree_owner")
            return None
        if key in self.used:
            incomplete("duplicate_structure_reference")
        self.used.add(key)
        return key

    def text(self, key):
        incomplete = self.fail
        page_index, mcid = key
        if page_index not in self.decoded:
            self.decoded[page_index] = {}
            try:
                manager = _FontResourceManager(self.fail)
                device = _MarkedTextDevice(manager, self.fail)
                with open(self.file_path, "rb") as source:
                    page = next(PDFPage.get_pages(source, pagenos={page_index}))
                    if (
                        sum(len(stream_value(s).get_data()) for s in page.contents)
                        > 8 * 1024 * 1024
                    ):
                        incomplete("content_limit")
                        return None
                    _PageInterpreter(manager, device).process_page(page)
                if device.stack:
                    incomplete("marked_content_balance")
                self.decoded[page_index] = device.entries
            except Exception:
                incomplete("content_decode")
        entry = self.decoded[page_index].get(mcid)
        if entry is None:
            incomplete("missing_mcid")
            return None
        return {"text": entry["text"].strip(), "source": entry["source"]}

    def bounds(self, key):
        """Decoded source-glyph bounds in pdfminer's page coordinates.

        Repair evidence only: this does not change scanner text or scoring.
        Empty/image marked content has no glyph bounds and returns None.
        """
        self.text(key)
        return self.decoded.get(key[0], {}).get(key[1], {}).get("bbox")
