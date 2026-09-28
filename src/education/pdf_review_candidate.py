"""Source-bound edits of existing PDF tags, without publication or approval.

Targets belong to one exact source checksum. This deliberately small primitive
returns new bytes only after reopening them and checking the complete reachable
object graph against the explicitly edited graph. It does not infer reading
order or header intent, create missing tags, or certify accessibility.
"""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import io
import json
import tempfile

import pikepdf
from pikepdf import Array, Dictionary, Name

from .pdf_checks.completeness import require_complete_pdf_scan
from .reading_order_snapshot import (
    MAX_PAGES,
    MAX_SOURCE_BYTES,
    _preflight,
    _semantic_blocks,
)

MAX_NODES = 2000
MAX_DEPTH = 40
MAX_GRAPH_VISITS = 100000
MAX_CONTEXT_PAGES = 16
MAX_CONTEXT_SEGMENTS = 4
MAX_CONTEXT_TEXT_CHARACTERS = 240
MAX_CONTEXT_TOTAL_BYTES = 256 * 1024
HEADING_ROLES = {"/P", *(f"/H{level}" for level in range(1, 7))}
ORDER_ROLES = {"/StructTreeRoot", "/Document", "/Part", "/Sect", "/Div"}


class PDFEditRefused(ValueError):
    """Safe public reason; parser messages and document text are never included."""


@dataclass(frozen=True)
class PDFEditCandidate:
    content: bytes
    source_sha256: str
    sha256: str
    operation: str
    needs_review: bool = True


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _check_source(content, expected_sha256):
    if not isinstance(content, bytes) or not content or len(content) > MAX_SOURCE_BYTES:
        raise PDFEditRefused("invalid_or_oversized_source")
    actual = _sha(content)
    if expected_sha256 != actual:
        raise PDFEditRefused("source_checksum_mismatch")
    return actual


def _graph_digest(pdf):
    """Compare semantic object content despite qpdf renumbering/compression.

    Indirect references get traversal-local IDs, preserving alias/cycle identity.
    Only stream transport keys and trailer transport IDs are excluded. Decoded
    streams are bounded by the shared preflight before this function is called.
    """
    references = {}
    visits = 0

    def visit(obj, depth=0):
        nonlocal visits
        visits += 1
        if visits > MAX_GRAPH_VISITS or depth > 80:
            raise PDFEditRefused("graph_limit_exceeded")
        if obj is None or isinstance(obj, (bool, int)):
            return obj
        if isinstance(obj, (float, Decimal)):
            return ["number", str(obj)]
        identity = None
        if isinstance(obj, pikepdf.Object) and obj.is_indirect:
            if obj.objgen in references:
                return ["ref", references[obj.objgen]]
            identity = len(references)
            references[obj.objgen] = identity
        if isinstance(obj, pikepdf.Stream):
            filters = obj.get("/Filter")
            if isinstance(filters, Array) and len(filters) == 1:
                filters = filters[0]
            jpeg = filters == Name.DCTDecode
            ignored = {"/Length"} if jpeg else {"/Length", "/Filter", "/DecodeParms"}
            value = [
                "jpeg" if jpeg else "stream",
                _sha(obj.read_raw_bytes() if jpeg else obj.read_bytes()),
                [
                    [str(key), visit(obj[key], depth + 1)]
                    for key in sorted(obj.keys())
                    if str(key) not in ignored
                ],
            ]
        elif isinstance(obj, Dictionary):
            value = [
                "dict",
                [[str(key), visit(obj[key], depth + 1)] for key in sorted(obj.keys())],
            ]
        elif isinstance(obj, Array):
            value = ["array", [visit(child, depth + 1) for child in obj]]
        elif isinstance(obj, pikepdf.String):
            value = ["string", bytes(obj).hex()]
        elif isinstance(obj, pikepdf.Name):
            value = ["name", str(obj)]
        else:
            raise PDFEditRefused("unsupported_object")
        return ["object", identity, value] if identity is not None else value

    content = [
        [str(key), visit(pdf.trailer[key])]
        for key in sorted(pdf.trailer.keys())
        if str(key) not in {"/ID", "/Size", "/Prev", "/XRefStm"}
    ]
    return _sha(json.dumps(content, separators=(",", ":")).encode())


def _structure(pdf):
    root = pdf.Root.get("/StructTreeRoot")
    if (
        not isinstance(root, Dictionary)
        or not root.is_indirect
        or root.get("/Type") != Name.StructTreeRoot
    ):
        raise PDFEditRefused("unsupported_structure")
    if any(key in root for key in ("/RoleMap", "/Namespaces")):
        raise PDFEditRefused("custom_structure_roles")
    nodes, seen = {}, set()

    def walk(node, parent, path, depth):
        if depth > MAX_DEPTH or len(nodes) >= MAX_NODES:
            raise PDFEditRefused("structure_limit_exceeded")
        if (
            not isinstance(node, Dictionary)
            or not node.is_indirect
            or node.objgen in seen
            or "/NS" in node
        ):
            raise PDFEditRefused("ambiguous_structure")
        if parent is not None and (
            node.get("/Type") != Name.StructElem
            or not isinstance(node.get("/S"), pikepdf.Name)
            or node.get("/P") is None
            or node.P.objgen != parent.objgen
        ):
            raise PDFEditRefused("invalid_structure_parent")
        seen.add(node.objgen)
        nodes[path] = node
        kids = node.get("/K")
        children = list(kids) if isinstance(kids, Array) else [kids]
        structural = [
            child
            for child in children
            if isinstance(child, Dictionary) and child.get("/Type") == Name.StructElem
        ]
        if structural and len(structural) != len(children):
            raise PDFEditRefused("mixed_structure_children")
        if structural and any(key in node for key in ("/ActualText", "/Alt")):
            raise PDFEditRefused("parent_text_alternative")
        if not structural:
            if parent is None or not children:
                raise PDFEditRefused("unbound_structure")
            for child in children:
                mcid = (
                    child.get("/MCID")
                    if isinstance(child, Dictionary) and child.get("/Type") == Name.MCR
                    else child
                )
                if type(mcid) is not int or mcid < 0:
                    raise PDFEditRefused("unbound_structure")
        for index, child in enumerate(structural):
            walk(child, node, (*path, index), depth + 1)

    walk(root, None, (), 0)
    return nodes


def _validate_document(pdf, content, owner_context=None):
    if pdf.is_encrypted or any(key in pdf.Root for key in ("/AcroForm", "/Perms")):
        raise PDFEditRefused("encrypted_signed_or_form_pdf")
    if not 1 <= len(pdf.pages) <= MAX_PAGES:
        raise PDFEditRefused("page_limit_exceeded")
    for obj in pdf.objects:
        if isinstance(obj, Dictionary) and (
            obj.get("/Type") == Name.Sig or "/ByteRange" in obj
        ):
            raise PDFEditRefused("signed_pdf")
    _preflight(pdf)
    nodes = _structure(pdf)
    # Existing resolver verifies MCID ownership and ParentTree bindings. No
    # geometric or text-matching fallback can create an editable target.
    with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
        source.write(content)
        source.flush()
        with require_complete_pdf_scan(False):
            for index in range(len(pdf.pages)):
                _semantic_blocks(pdf, source.name, index, owner_context=owner_context)
    return nodes


class _TargetContextCollector:
    """Keep bounded semantic excerpts by verified indirect structure owner."""

    def __init__(self):
        self.entries = {}

    def __call__(self, owner, target_page, resolved):
        entry = self.entries.setdefault(
            owner, {"page_numbers": [], "segments": [], "truncated": False}
        )
        page_number = target_page + 1
        for page, block in resolved:
            if page != target_page:
                continue
            if page_number not in entry["page_numbers"]:
                if len(entry["page_numbers"]) < MAX_CONTEXT_PAGES:
                    entry["page_numbers"].append(page_number)
                else:
                    entry["truncated"] = True
                    continue
            if block is None or not block["text"].strip():
                continue
            if len(entry["segments"]) >= MAX_CONTEXT_SEGMENTS:
                entry["truncated"] = True
                continue
            used = sum(len(segment["text"]) for segment in entry["segments"])
            remaining = MAX_CONTEXT_TEXT_CHARACTERS - used
            if remaining <= 0:
                entry["truncated"] = True
                continue
            excerpt = block["text"][:remaining]
            entry["segments"].append(
                {"page_number": page_number, "text": excerpt, "source": block["source"]}
            )
            if len(excerpt) < len(block["text"]):
                entry["truncated"] = True

    def context(self, owner):
        entry = self.entries.get(owner)
        if entry is None:
            return {
                "status": "unavailable",
                "page_numbers": [],
                "segments": [],
                "truncated": False,
            }
        return {
            "status": "available" if entry["segments"] else "empty",
            "page_numbers": entry["page_numbers"],
            "segments": entry["segments"],
            "truncated": entry["truncated"],
        }


def _attach_context(descriptors, nodes, collector):
    """Reserve a fallback for every target before spending the response budget."""
    fallback = {
        "status": "unavailable",
        "page_numbers": [],
        "segments": [],
        "truncated": True,
    }

    def size(value):
        return len(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        )

    fallback_size = size(fallback)
    remaining = MAX_CONTEXT_TOTAL_BYTES - fallback_size * len(descriptors)
    if remaining < 0:
        raise PDFEditRefused("context_limit_exceeded")
    for descriptor, node in zip(descriptors, nodes.values()):
        context = collector.context(node.objgen)
        extra = size(context) - fallback_size
        if extra <= remaining:
            descriptor["context"] = context
            remaining -= max(extra, 0)
        else:
            descriptor["context"] = dict(fallback, page_numbers=[], segments=[])
    return descriptors


def _target_id(sha256, path):
    return sha256 + ":" + ("/".join(map(str, path)) if path else "root")


def _table_header_cells(pdf, nodes, path):
    """Return first-row cells only for a simple, page-bound table grid."""
    table = nodes[path]
    if table.get("/S") != Name.Table or any(key in table for key in ("/A", "/C")):
        return None
    rows = [
        nodes[key] for key in nodes if len(key) == len(path) + 1 and key[:-1] == path
    ]
    if len(rows) < 2 or any(
        row.get("/S") != Name.TR or any(key in row for key in ("/A", "/C"))
        for row in rows
    ):
        return None
    pages = {page.obj.objgen for page in pdf.pages}
    root_id = nodes[()].objgen
    width, page_id = None, None
    for row_index, row in enumerate(rows):
        cells = [
            nodes[key]
            for key in nodes
            if len(key) == len(path) + 2 and key[:-2] == path and key[-2] == row_index
        ]
        if width is None:
            width = len(cells)
        if width < 2 or len(cells) != width:
            return None
        for cell in cells:
            if cell.get("/S") not in (Name.TD, Name.TH) or any(
                key in cell
                for key in (
                    "/C",
                    "/ID",
                    "/Headers",
                    "/Scope",
                    "/RowSpan",
                    "/ColSpan",
                    "/ActualText",
                    "/Alt",
                )
            ):
                return None
            attr = cell.get("/A")
            if attr is not None and (
                not isinstance(attr, Dictionary)
                or attr.get("/O") != Name.Table
                or any(key in attr for key in ("/Headers", "/RowSpan", "/ColSpan"))
                or (
                    "/Scope" in attr
                    and (
                        row_index != 0
                        or cell.get("/S") != Name.TH
                        or attr.Scope != Name.Column
                    )
                )
            ):
                return None
            if row_index and cell.get("/S") != Name.TD:
                return None
            if (
                row_index == 0
                and cell.get("/S") == Name.TH
                and (attr is None or attr.get("/Scope") != Name.Column)
            ):
                return None
            # The scanner has already resolved each MCID and ParentTree owner.
            # Here every cell must additionally inherit or name one PDF page.
            current = cell
            found = None
            seen = set()
            while current is not None:
                if (
                    not isinstance(current, Dictionary)
                    or not current.is_indirect
                    or current.objgen in seen
                    or len(seen) > MAX_DEPTH
                    or current.objgen == root_id
                ):
                    return None
                seen.add(current.objgen)
                if "/Pg" in current:
                    pg = current.Pg
                    if not isinstance(pg, Dictionary) or not pg.is_indirect:
                        return None
                    found = pg.objgen
                    break
                current = current.get("/P")
            if found not in pages or (page_id is not None and found != page_id):
                return None
            page_id = found
            kids = cell.get("/K")
            for kid in (list(kids) if isinstance(kids, Array) else [kids]):
                if type(kid) is int:
                    continue
                if isinstance(kid, Dictionary) and kid.get("/Type") == Name.MCR:
                    if (
                        "/Stm" in kid
                        or "/StmOwn" in kid
                        or ("/Pg" in kid and kid.Pg.objgen != found)
                    ):
                        return None
                else:
                    return None
    return [nodes[(*path, 0, index)] for index in range(width)]


def _descriptors(pdf, nodes, sha256):
    result = []
    for path, node in nodes.items():
        role = str(node.get("/S", Name.StructTreeRoot))
        children = [
            key for key in nodes if len(key) == len(path) + 1 and key[:-1] == path
        ]
        result.append(
            {
                "target_id": _target_id(sha256, path),
                "role": role.lstrip("/"),
                "children": [_target_id(sha256, child) for child in children],
                "can_set_heading": role in HEADING_ROLES and not children,
                "can_reorder": role in ORDER_ROLES and len(children) > 1,
                "can_set_column_headers": (
                    _table_header_cells(pdf, nodes, path) is not None
                    if role == "/Table"
                    else False
                ),
            }
        )
    return result


def inspect_pdf_edit_targets(content: bytes, expected_sha256: str) -> list[dict]:
    """List targets for one exact source; unsupported input raises a safe refusal."""
    sha256 = _check_source(content, expected_sha256)
    try:
        with pikepdf.open(io.BytesIO(content), attempt_recovery=False) as pdf:
            collector = _TargetContextCollector()
            nodes = _validate_document(pdf, content, owner_context=collector)
            _graph_digest(pdf)
            return _attach_context(_descriptors(pdf, nodes, sha256), nodes, collector)
    except PDFEditRefused:
        raise
    except Exception:
        raise PDFEditRefused("unsupported_or_invalid_pdf") from None


def _serialize(pdf):
    output = io.BytesIO()
    pdf.save(output, fix_metadata_version=False)
    return output.getvalue()


def create_pdf_edit_candidate(
    content: bytes, expected_sha256: str, operation: dict
) -> PDFEditCandidate:
    """Apply one explicit bounded edit; callers own authorization/publication.

    Supported operations: ``heading`` with target_id and integer level 1–6;
    ``order`` with target_id and a complete ordered list of child target IDs;
    ``table_column_headers`` with the target_id of a simple Table whose first
    row the caller explicitly identifies as column headers.
    IDs and checksums are not authorization credentials.
    """
    sha256 = _check_source(content, expected_sha256)
    try:
        with pikepdf.open(io.BytesIO(content), attempt_recovery=False) as pdf:
            nodes = _validate_document(pdf, content)
            descriptors = _descriptors(pdf, nodes, sha256)
            by_id = {item["target_id"]: item for item in descriptors}
            objects = {_target_id(sha256, path): node for path, node in nodes.items()}
            if not isinstance(operation, dict):
                raise PDFEditRefused("invalid_operation")
            target = operation.get("target_id")
            if not isinstance(target, str) or target not in by_id:
                raise PDFEditRefused("stale_or_unknown_target")
            descriptor, node = by_id[target], objects[target]
            kind = operation.get("kind")
            if kind == "heading":
                level = operation.get("level")
                if (
                    set(operation) != {"kind", "target_id", "level"}
                    or not descriptor["can_set_heading"]
                    or type(level) is not int
                    or not 1 <= level <= 6
                ):
                    raise PDFEditRefused("unsupported_heading_edit")
                role = Name(f"/H{level}")
                if node.S == role:
                    raise PDFEditRefused("no_change")
                node.S = role
            elif kind == "order":
                order = operation.get("children")
                if (
                    set(operation) != {"kind", "target_id", "children"}
                    or not descriptor["can_reorder"]
                    or not isinstance(order, list)
                    or any(not isinstance(item, str) for item in order)
                    or len(order) != len(descriptor["children"])
                    or set(order) != set(descriptor["children"])
                ):
                    raise PDFEditRefused("incomplete_or_unsupported_order")
                if order == descriptor["children"]:
                    raise PDFEditRefused("no_change")
                node.K = Array([objects[item] for item in order])
            elif kind == "table_column_headers":
                if set(operation) != {"kind", "target_id"}:
                    raise PDFEditRefused("unsupported_table_header_edit")
                cells = _table_header_cells(
                    pdf,
                    nodes,
                    next(
                        path
                        for path, item in nodes.items()
                        if item.objgen == node.objgen
                    ),
                )
                if cells is None:
                    raise PDFEditRefused("unsupported_table_header_edit")
                if all(cell.get("/S") == Name.TH for cell in cells):
                    raise PDFEditRefused("no_change")
                for cell in cells:
                    if cell.get("/S") == Name.TD:
                        cell.S = Name.TH
                        attr = cell.get("/A")
                        # An existing attribute dictionary may be shared with
                        # a body cell. Copy it before adding this cell's scope.
                        attr = (
                            Dictionary(attr)
                            if attr is not None
                            else Dictionary(O=Name.Table)
                        )
                        attr.Scope = Name.Column
                        cell.A = attr
            else:
                raise PDFEditRefused("unsupported_operation")
            expected_graph = _graph_digest(pdf)
            candidate = _serialize(pdf)
        if len(candidate) > MAX_SOURCE_BYTES:
            raise PDFEditRefused("candidate_too_large")
        with pikepdf.open(io.BytesIO(candidate), attempt_recovery=False) as saved:
            _validate_document(saved, candidate)
            if _graph_digest(saved) != expected_graph:
                raise PDFEditRefused("saved_content_changed")
        return PDFEditCandidate(candidate, sha256, _sha(candidate), kind)
    except PDFEditRefused:
        raise
    except Exception:
        raise PDFEditRefused("unsupported_or_invalid_pdf") from None
