"""Offline source-bound v2 map creation, supplementation and reviewed correction.

No upload-path enablement. A review reference records supplied provenance; it
cannot authenticate an approver. The operator must establish review authority.
Saved-output consistency and appearance preservation do not certify meaning.
The deployed v1 coupled font/semantic recovery contract remains unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import re
from typing import Any, Literal, cast

import pikepdf
from pdfminer.cmapdb import FileUnicodeMap
from pdfminer.pdfpage import PDFPage
from pdfminer.pdftypes import dict_value
from ..pdf_checks.marked_content import _FontResourceManager

from ..pdf_checks.font_mapping import usable_unicode
from .pdf_font_text import decode_page_text_runs
from .pdf_ocr_form import _BoundedBuffer, _BoundedCMapParser, _Budget, MAX_PDF_BYTES
from .pdf_text_inventory import PDFTextInventory, inspect_pdf_text_inventory
from .pdf_verified_font_recovery import _fingerprint

MAX_PLAN_BYTES = 4 * 1024 * 1024
NORMALIZATION_POLICY = "exact-unicode-v1"
Operation = Literal["create_missing", "supplement_partial", "replace_reviewed"]


class MappingProposalError(ValueError):
    def __init__(self, code: str):
        super().__init__("font_proposal_" + code)


@dataclass(frozen=True)
class AssignmentChange:
    code: int
    old: str | None
    new: str
    evidence_origin: str
    evidence_sha256: str


@dataclass(frozen=True)
class FontMapPatch:
    identity: str
    fingerprint: str
    old_map_sha256: str | None
    used_codes: tuple[int, ...]
    operation: Operation
    changes: tuple[AssignmentChange, ...]


@dataclass(frozen=True)
class ExpectedRun:
    page_index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class FontMappingProposal:
    source_sha256: str
    inventory_sha256: str
    fonts: tuple[FontMapPatch, ...]
    runs: tuple[ExpectedRun, ...]
    actor_reference: str
    review_reference: str
    schema_version: int = 2
    normalization_policy: str = NORMALIZATION_POLICY


@dataclass(frozen=True)
class CompiledFontMapping:
    pdf_bytes: bytes
    source_sha256: str
    output_sha256: str
    proposal_sha256: str
    operation_counts: tuple[tuple[str, int], ...]
    saved_consistency_verified: bool = True
    independent_review_pending: bool = True
    fidelity_status: str = "unassessed"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()


def inventory_sha256(inventory: PDFTextInventory) -> str:
    return hashlib.sha256(
        b"aelira-font-inventory-v1\0" + _canonical(asdict(inventory))
    ).hexdigest()


def _hash(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise MappingProposalError(code)


def _validate(plan: FontMappingProposal) -> None:
    _require(
        type(plan) is FontMappingProposal
        and type(plan.schema_version) is int
        and plan.schema_version == 2
        and plan.normalization_policy == NORMALIZATION_POLICY,
        "schema",
    )
    _require(_hash(plan.source_sha256) and _hash(plan.inventory_sha256), "source_hash")
    _require(
        type(plan.fonts) is tuple
        and 1 <= len(plan.fonts) <= 256
        and type(plan.runs) is tuple
        and len(plan.runs) <= 20000,
        "scope",
    )
    _require(
        all(
            type(value) is str and 1 <= len(value) <= 512
            for value in (plan.actor_reference, plan.review_reference)
        ),
        "review_provenance",
    )
    identities = set()
    total = 0
    for patch in plan.fonts:
        _require(
            type(patch) is FontMapPatch
            and type(patch.identity) is str
            and 1 <= len(patch.identity) <= 128
            and patch.identity not in identities,
            "font_identity",
        )
        identities.add(patch.identity)
        _require(
            _hash(patch.fingerprint)
            and (patch.old_map_sha256 is None or _hash(patch.old_map_sha256)),
            "font_hash",
        )
        _require(
            type(patch.operation) is str
            and patch.operation
            in {"create_missing", "supplement_partial", "replace_reviewed"}
            and type(patch.used_codes) is tuple
            and bool(patch.used_codes)
            and type(patch.changes) is tuple
            and bool(patch.changes),
            "operation",
        )
        _require(
            all(type(code) is int and 0 <= code <= 65535 for code in patch.used_codes)
            and tuple(sorted(set(patch.used_codes))) == patch.used_codes,
            "used_codes",
        )
        codes = set()
        for change in patch.changes:
            _require(
                type(change) is AssignmentChange
                and type(change.code) is int
                and 0 <= change.code <= 65535
                and change.code not in codes,
                "assignment",
            )
            codes.add(change.code)
            _require(change.old is None or usable_unicode(change.old), "old_assignment")
            _require(
                usable_unicode(change.new) and change.new != change.old,
                "new_assignment",
            )
            _require(
                change.evidence_origin
                in {
                    "defined_simple_encoding",
                    "embedded_truetype_chain",
                    "trusted_authoring_text",
                    "reviewed_glyph_context",
                }
                and _hash(change.evidence_sha256),
                "evidence",
            )
        total += len(patch.changes)
        _require(total <= 200000, "assignment_limit")
    text_bytes = 0
    keys = set()
    for run in plan.runs:
        _require(
            type(run) is ExpectedRun
            and all(
                type(value) is int and value >= 0
                for value in (run.page_index, run.start, run.end)
            )
            and run.start < run.end
            and type(run.text) is str,
            "run",
        )
        key = (run.page_index, run.start, run.end)
        _require(key not in keys, "run_duplicate")
        keys.add(key)
        text_bytes += len(run.text.encode("utf-8"))
        _require(text_bytes <= 1024 * 1024, "text_limit")


def proposal_to_json(plan: FontMappingProposal) -> bytes:
    _validate(plan)
    data = _canonical(asdict(plan))
    _require(len(data) <= MAX_PLAN_BYTES, "byte_limit")
    return data


def proposal_from_json(data: bytes) -> FontMappingProposal:
    """Closed, duplicate-free schema. No inference, coercion, or downgrade."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate_key")
            result[key] = value
        return result

    def closed(value: Any, fields: set[str]) -> None:
        _require(type(value) is dict and set(value) == fields, "fields")

    _require(type(data) is bytes and 0 < len(data) <= MAX_PLAN_BYTES, "byte_limit")
    try:
        value = json.loads(
            data,
            object_pairs_hook=pairs,
            parse_float=lambda _value: (_ for _ in ()).throw(
                MappingProposalError("numeric_type")
            ),
            parse_constant=lambda _value: (_ for _ in ()).throw(
                MappingProposalError("numeric_type")
            ),
        )
        closed(value, set(FontMappingProposal.__dataclass_fields__))
        _require(
            type(value["fonts"]) is list
            and len(value["fonts"]) <= 256
            and type(value["runs"]) is list
            and len(value["runs"]) <= 20000,
            "scope",
        )
        fonts = []
        for patch in value["fonts"]:
            closed(patch, set(FontMapPatch.__dataclass_fields__))
            _require(
                type(patch["changes"]) is list
                and len(patch["changes"]) <= 200000
                and type(patch["used_codes"]) is list
                and len(patch["used_codes"]) <= 65536,
                "assignment_limit",
            )
            changes = []
            for change in patch["changes"]:
                closed(change, set(AssignmentChange.__dataclass_fields__))
                changes.append(AssignmentChange(**change))
            fonts.append(
                FontMapPatch(
                    **{
                        **patch,
                        "changes": tuple(changes),
                        "used_codes": tuple(patch["used_codes"]),
                    }
                )
            )
        runs = []
        for run in value["runs"]:
            closed(run, set(ExpectedRun.__dataclass_fields__))
            runs.append(ExpectedRun(**run))
        plan = FontMappingProposal(
            **{**value, "fonts": tuple(fonts), "runs": tuple(runs)}
        )
        _validate(plan)
        return plan
    except MappingProposalError:
        raise
    except Exception:
        raise MappingProposalError("malformed_json") from None


class _ExistingMapParser(_BoundedCMapParser):
    def __init__(self, data: bytes, width: int) -> None:
        super().__init__(data, _Budget())
        self.width = width
        self.codes: set[int] = set()
        self.ranges: list[tuple[int, int]] = []
        self.space_count: int | None = None

    def do_keyword(self, pos: int, token: Any) -> None:
        if token is self.KEYWORD_BEGINCODESPACERANGE:
            count = self.curstack[-1][1] if self.curstack else None
            _require(
                type(count) is int and 1 <= count <= 256 and self.space_count is None,
                "code_space",
            )
            self.space_count = cast(int, count)
        elif token is self.KEYWORD_ENDCODESPACERANGE:
            objects = [value for _, value in self.curstack]
            _require(
                self.space_count is not None and len(objects) == self.space_count * 2,
                "code_space",
            )
            for offset in range(0, len(objects), 2):
                first, last = objects[offset : offset + 2]
                _require(
                    type(first) is bytes
                    and type(last) is bytes
                    and len(first) == len(last) == self.width,
                    "code_space",
                )
                start, end = int.from_bytes(cast(bytes, first), "big"), int.from_bytes(
                    cast(bytes, last), "big"
                )
                _require(
                    start <= end
                    and not any(
                        start <= upper and lower <= end for lower, upper in self.ranges
                    ),
                    "code_space",
                )
                self.ranges.append((start, end))
            self.space_count = None
        if token in {self.KEYWORD_ENDBFCHAR, self.KEYWORD_ENDBFRANGE}:
            objects = [value for _, value in self.curstack]
            stride = 2 if token is self.KEYWORD_ENDBFCHAR else 3
            _require(len(objects) % stride == 0, "old_map")
            for offset in range(0, len(objects), stride):
                byte_start = objects[offset]
                byte_end = byte_start if stride == 2 else objects[offset + 1]
                _require(
                    type(byte_start) is bytes
                    and type(byte_end) is bytes
                    and len(byte_start) == len(byte_end) == self.width,
                    "code_width",
                )
                first, last = int.from_bytes(
                    cast(bytes, byte_start), "big"
                ), int.from_bytes(cast(bytes, byte_end), "big")
                _require(first <= last and last - first < 65536, "old_map")
                for code in range(first, last + 1):
                    _require(code not in self.codes, "old_map_duplicate")
                    self.codes.add(code)
        super().do_keyword(pos, token)


def _existing_map(font: Any, width: int) -> dict[int, str]:
    if "/ToUnicode" not in font:
        return {}
    stream = font.ToUnicode
    _require(isinstance(stream, pikepdf.Stream), "old_map")
    data = _Budget().stream(stream)
    parser = _ExistingMapParser(data, width)
    parser.run()
    _require(parser.saw_begin and parser.saw_end, "old_map")
    _require(
        bool(parser.ranges)
        and parser.space_count is None
        and all(
            any(start <= code <= end for start, end in parser.ranges)
            for code in parser.codes
        ),
        "code_space",
    )
    mapping = cast(FileUnicodeMap, parser.cmap).cid2unichr
    _require(
        set(mapping) == parser.codes
        and all(usable_unicode(text) for text in mapping.values()),
        "old_map",
    )
    return dict(mapping)


def _cmap(mappings: dict[int, str], width: int) -> bytes:
    lines = [
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap",
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        b"/CMapName /Aelira-Recovery-v2 def /CMapType 2 def",
        f"1 begincodespacerange <{'00' * width}> <{'FF' * width}> endcodespacerange".encode(),
    ]
    items = sorted(mappings.items())
    for offset in range(0, len(items), 100):
        batch = items[offset : offset + 100]
        lines.append(f"{len(batch)} beginbfchar".encode())
        for code, text in batch:
            _require(
                0 <= code < 1 << (width * 8) and usable_unicode(text), "assignment"
            )
            lines.append(
                f"<{code:0{width * 2}X}> <{text.encode('utf-16-be').hex().upper()}>".encode()
            )
        lines.append(b"endbfchar")
    lines.append(b"endcmap CMapName currentdict /CMap defineresource pop end end")
    result = b"\n".join(lines)
    _require(len(result) <= 2 * 1024 * 1024, "map_limit")
    return result


def _locate(pdf: pikepdf.Pdf, paths: tuple[str, ...]) -> Any:
    font = None
    for path in paths:
        match = re.fullmatch(r"page:(\d+)/font:(/[^/]+)", path)
        if match is None:
            raise MappingProposalError("resource_path")
        page, name = int(match[1]), match[2]
        value = pdf.pages[page].Resources.Font[name]
        _require(
            value.is_indirect and (font is None or value.objgen == font.objgen),
            "resource_identity",
        )
        font = value
    _require(font is not None, "font_missing")
    return font


def _preservation(pdf: pikepdf.Pdf, targets: list[Any]) -> tuple[str, tuple[str, ...]]:
    authorized = frozenset(tuple(font.objgen) for font in targets)
    root = pikepdf.Dictionary(
        {key: value for key, value in pdf.Root.items() if key != "/Pages"}
    )
    document = _fingerprint(
        pikepdf.Dictionary(Root=root, Info=pdf.trailer.get("/Info")),
        authorized_maps=authorized,
    )
    pages = tuple(
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
            authorized_maps=authorized,
        )
        for page in pdf.pages
    )
    return document, pages


def _eligible(inventory: PDFTextInventory, pdf: pikepdf.Pdf) -> None:
    _require(
        len(inventory.pages) == inventory.page_count
        and not (set(inventory.exclusions) - {"mapping_unavailable"}),
        "inventory_incomplete",
    )
    _require(
        not inventory.replacements
        and not any(page.marked_content_count for page in inventory.pages),
        "marked_content_scope",
    )
    _require(
        not any(key in pdf.Root for key in ("/AcroForm", "/Perms", "/StructTreeRoot")),
        "document_scope",
    )
    _require(
        not any(
            "/Annots" in page.obj or "/StructParents" in page.obj for page in pdf.pages
        ),
        "document_scope",
    )
    for page in pdf.pages:
        ext = page.Resources.get("/ExtGState", pikepdf.Dictionary())
        _require(
            isinstance(ext, pikepdf.Dictionary)
            and not any("/Font" in state for _, state in ext.items()),
            "graphics_font",
        )


def compile_font_mapping_proposal(
    source: bytes, plan: FontMappingProposal
) -> CompiledFontMapping:
    """Compile a private derivative; return nothing after any failed check."""
    try:
        _require(
            type(source) is bytes and 0 < len(source) <= MAX_PDF_BYTES, "source_limit"
        )
        proposal_bytes = proposal_to_json(plan)
        _require(
            hashlib.sha256(source).hexdigest() == plan.source_sha256, "source_changed"
        )
        inventory = inspect_pdf_text_inventory(source)
        _require(
            inventory_sha256(inventory) == plan.inventory_sha256, "inventory_changed"
        )
        evidence = {font.identity: font for font in inventory.fonts}
        rules = (
            _deterministic_assignments(source, inventory)
            if any(
                change.evidence_origin
                in {"defined_simple_encoding", "embedded_truetype_chain"}
                for patch in plan.fonts
                for change in patch.changes
            )
            else {}
        )
        with pikepdf.open(io.BytesIO(source), attempt_recovery=False) as pdf:
            _eligible(inventory, pdf)
            targets, assignments, paths = [], [], []
            for patch in plan.fonts:
                _require(patch.identity in evidence, "font_missing")
                info = evidence[patch.identity]
                _require(
                    info.fingerprint == patch.fingerprint
                    and info.original_map_sha256 == patch.old_map_sha256
                    and info.used_codes == patch.used_codes,
                    "font_changed",
                )
                font = _locate(pdf, info.resource_paths)
                width = 2 if font.get("/Subtype") == pikepdf.Name.Type0 else 1
                if width == 2:
                    _require(
                        font.get("/Encoding") == pikepdf.Name("/Identity-H")
                        and info.program_sha256 is not None,
                        "font_scope",
                    )
                else:
                    _require(
                        font.get("/Subtype")
                        in {pikepdf.Name.Type1, pikepdf.Name.TrueType},
                        "font_scope",
                    )
                mapping = _existing_map(font, width)
                if patch.operation == "create_missing":
                    _require(patch.old_map_sha256 is None, "existing_map")
                else:
                    _require(patch.old_map_sha256 is not None, "missing_map")
                for change in patch.changes:
                    _require(
                        mapping.get(change.code) == change.old, "old_assignment_changed"
                    )
                    if patch.operation in {"create_missing", "supplement_partial"}:
                        _require(change.old is None, "overwrite_forbidden")
                    else:
                        _require(
                            change.code in mapping
                            and change.evidence_origin
                            in {"trusted_authoring_text", "reviewed_glyph_context"},
                            "correction_review_required",
                        )
                    if change.evidence_origin in {
                        "defined_simple_encoding",
                        "embedded_truetype_chain",
                    }:
                        derived = rules.get(patch.identity)
                        _require(
                            derived is not None
                            and derived[0] == change.evidence_origin
                            and derived[1].get(change.code) == change.new
                            and derived[2] == change.evidence_sha256,
                            "rule_evidence_changed",
                        )
                    mapping[change.code] = change.new
                _require(set(info.used_codes) <= set(mapping), "coverage")
                targets.append(font)
                assignments.append(mapping)
                paths.append(info.resource_paths)
            preserved = _preservation(pdf, targets)
            for font, mapping in zip(targets, assignments, strict=True):
                font.ToUnicode = pdf.make_stream(
                    _cmap(mapping, 2 if font.Subtype == pikepdf.Name.Type0 else 1)
                )
            output = _BoundedBuffer()
            pdf.save(
                output,
                deterministic_id=True,
                fix_metadata_version=False,
                compress_streams=False,
                stream_decode_level=pikepdf.StreamDecodeLevel.none,
            )
        result = output.getvalue()
        _require(len(result) <= MAX_PDF_BYTES, "output_limit")
        with pikepdf.open(io.BytesIO(result), attempt_recovery=False) as saved:
            saved_targets = [_locate(saved, path) for path in paths]
            _require(_preservation(saved, saved_targets) == preserved, "preservation")
            for font, expected in zip(saved_targets, assignments, strict=True):
                _require(
                    _existing_map(font, 2 if font.Subtype == pikepdf.Name.Type0 else 1)
                    == expected,
                    "retained_assignments",
                )
            actual = tuple(
                ExpectedRun(index, run.start, run.end, run.text)
                for index in range(len(saved.pages))
                for run in decode_page_text_runs(saved, index)
            )
            _require(actual == plan.runs, "transcript")
        # Complete expected transcripts include healthy and repeated occurrences.
        _require(
            tuple((run.page_index, run.start, run.end) for run in inventory.runs)
            == tuple((run.page_index, run.start, run.end) for run in plan.runs),
            "run_coverage",
        )
        counts = tuple(
            (operation, sum(font.operation == operation for font in plan.fonts))
            for operation in (
                "create_missing",
                "supplement_partial",
                "replace_reviewed",
            )
        )
        return CompiledFontMapping(
            result,
            plan.source_sha256,
            hashlib.sha256(result).hexdigest(),
            hashlib.sha256(b"aelira-font-proposal-v2\0" + proposal_bytes).hexdigest(),
            counts,
        )
    except MappingProposalError:
        raise
    except Exception:
        raise MappingProposalError("compilation_failed") from None


@dataclass(frozen=True)
class DeterministicPlanningResult:
    proposal: FontMappingProposal | None
    unresolved_fonts: tuple[str, ...]


def _deterministic_assignments(
    source: bytes, inventory: PDFTextInventory
) -> dict[str, tuple[str, dict[int, str], str]]:
    """Recompute rule evidence; gathering evidence never authorizes replacement."""
    information = {font.identity: font for font in inventory.fonts}
    result = {}
    for index, page in enumerate(PDFPage.get_pages(io.BytesIO(source))):
        for name, reference in dict_value(page.resources.get("Font", {})).items():
            objid = getattr(reference, "objid", None)
            identity = str(objid) if objid is not None else f"page:{index}/font:/{name}"
            info = information.get(identity)
            if info is None or not info.used_codes or identity in result:
                continue
            spec = dict(dict_value(reference))
            spec.pop("ToUnicode", None)
            checks: set[str] = set()
            try:
                font = _FontResourceManager(checks.add).get_font(objid, spec)
                _require(not checks, "rule_unavailable")
                origin = font.aelira_evidence_origin
                if origin == "defined_simple_encoding":
                    assignments = dict(font.cid2unicode)
                elif origin == "embedded_truetype_chain":
                    assignments = {
                        code: font.aelira_unicode_resolver.resolve(code)
                        for code in info.used_codes
                    }
                else:
                    continue
                _require(set(info.used_codes) <= set(assignments), "rule_coverage")
                digest = hashlib.sha256(
                    b"aelira-font-rule-v1\0"
                    + _canonical(
                        {
                            "source": inventory.source_sha256,
                            "font": info.fingerprint,
                            "origin": origin,
                            "assignments": assignments,
                        }
                    )
                ).hexdigest()
                result[identity] = (origin, assignments, digest)
            except Exception:
                continue
    return result


def plan_deterministic_font_maps(source: bytes) -> DeterministicPlanningResult:
    """Plan complete supported missing maps only; never change an existing map."""
    inventory = inspect_pdf_text_inventory(source)
    if (
        set(inventory.exclusions) - {"mapping_unavailable"}
        or len(inventory.pages) != inventory.page_count
    ):
        raise MappingProposalError("inventory_incomplete")
    rules = _deterministic_assignments(source, inventory)
    patches, unresolved = [], []
    for font in inventory.fonts:
        if font.original_map_sha256 is not None or not font.used_codes:
            continue
        if font.objgen == (0, 0):
            unresolved.append(font.identity)
            continue
        evidence = rules.get(font.identity)
        if evidence is None:
            unresolved.append(font.identity)
            continue
        origin, assignments, digest = evidence
        patches.append(
            FontMapPatch(
                font.identity,
                font.fingerprint,
                None,
                font.used_codes,
                "create_missing",
                tuple(
                    AssignmentChange(code, None, text, origin, digest)
                    for code, text in sorted(assignments.items())
                ),
            )
        )
    if unresolved or not patches:
        return DeterministicPlanningResult(None, tuple(unresolved))
    with pikepdf.open(io.BytesIO(source)) as pdf:
        _eligible(inventory, pdf)
        runs = tuple(
            ExpectedRun(index, run.start, run.end, run.text)
            for index in range(len(pdf.pages))
            for run in decode_page_text_runs(pdf, index)
        )
    plan = FontMappingProposal(
        inventory.source_sha256,
        inventory_sha256(inventory),
        tuple(patches),
        runs,
        "deterministic-planner-v1",
        "rule-consistency-only-independent-review-pending",
    )
    _validate(plan)
    return DeterministicPlanningResult(plan, ())
