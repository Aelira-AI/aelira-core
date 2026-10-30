"""Content-owned heading inventory and narrowly bounded role-only promotions.

Text is presentation evidence, never a structure identifier. A target is bound by
its complete page/MCID set and verified ParentTree ownership. Inferred title
intent always needs human review, even when the saved role is mechanically valid.
"""

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from statistics import median

import pikepdf
import pymupdf as fitz

from ..scan_completeness import record_incomplete_check
from .marked_content import MarkedContentResolver

HEADING_ROLES = {f"/H{level}" for level in range(1, 7)}
MAX_NODES = 20000
MAX_DEPTH = 50


@dataclass
class HeadingTarget:
    node: object
    path: tuple
    role: str
    refs: tuple
    text: str
    pages: tuple
    changes: list = field(default_factory=list)
    bounds: tuple | None = None
    font_size: float = 0
    title_candidate: bool = False
    reason: str | None = None

    @property
    def eligible(self):
        return bool(self.changes) and self.reason is None


@dataclass
class HeadingInventory:
    targets: list = field(default_factory=list)
    complete: bool = True
    source_sha256: str = ""
    reason: str | None = None

    @property
    def headings(self):
        return [t for t in self.targets if t.role in HEADING_ROLES or t.role == "/H"]

    @property
    def has_h1(self):
        return self.complete and any(t.role == "/H1" and t.text for t in self.headings)

    @property
    def first_is_h1(self):
        return self.complete and bool(self.headings) and self.headings[0].role == "/H1"

    @property
    def proven_first_is_h1(self):
        """The first heading is a bound H1, even if the walk stopped later.

        A target joins the list only after its binding checks and the walk
        stops at the first refusal, so the first listed heading is the
        document's first heading whether or not the inventory finished.
        """
        return bool(self.headings) and self.headings[0].role == "/H1"


def effective_role(role, root):
    roles = root.get("/RoleMap", {})
    seen = set()
    while role in roles:
        if role in seen or len(seen) >= 16:
            raise ValueError("heading_role_map_cycle_or_limit")
        seen.add(role)
        value = roles[role]
        if not isinstance(value, pikepdf.Name):
            raise ValueError("heading_invalid_role_map")
        next_role = str(value)
        if next_role == role:
            break
        role = next_role
    return role


def _is_incomplete_check(reason):
    """Work or size limits and unexpected errors leave the scan unfinished.

    Document-structure refusals (empty or unbound headings, bad parent
    relations) are findings about the document, not a failed check.
    """
    if reason == "heading_inventory_failed" or reason.endswith("_limit"):
        return True
    # Coded refusals are structure findings; any other ValueError text came
    # from an unexpected resolver error.
    return not reason.startswith(("heading_", "reading_order_"))


def inventory(pdf, source_path):
    # Shared traversal enforces parent, page, unique owner and work-limit rules.
    from ..remediation.source_order import _tree, _nodes

    result = HeadingInventory(
        source_sha256=sha256(Path(source_path).read_bytes()).hexdigest()
    )
    if "/StructTreeRoot" not in pdf.Root:
        return result
    try:
        if "/Namespaces" in pdf.Root.StructTreeRoot:
            raise ValueError("heading_unsupported_namespace")
        resolver = MarkedContentResolver(pdf, source_path)
        tree = _tree(pdf, resolver)
        paths = {}
        ancestors = {}

        def walk(node, path=(), parents=()):
            paths[id(node)] = path
            ancestors[id(node)] = parents
            for index, child in enumerate(node.children):
                walk(child, (*path, index), (*parents, node))

        walk(tree)
        nodes = list(_nodes(tree))
        refs = tree_refs = [ref for n in nodes if not n.children for ref in n.refs]
        if len(refs) > MAX_NODES:
            raise ValueError("heading_reference_limit")
        decoded = {ref: resolver.text(ref) for ref in tree_refs}
        if resolver.failed or any(value is None for value in decoded.values()):
            raise ValueError("heading_unresolved_content")
        with fitz.open(source_path) as document:
            for node in nodes:
                if not node.role:
                    continue
                if "/NS" in node.obj:
                    raise ValueError("heading_unsupported_namespace")
                role = effective_role(node.role, pdf.Root.StructTreeRoot)
                if role not in HEADING_ROLES | {"/H", "/P", "/Div"}:
                    continue
                text = "\n".join(decoded[r]["text"] for r in node.refs).strip()
                target = HeadingTarget(
                    node,
                    paths[id(node)],
                    role,
                    tuple(node.refs),
                    text,
                    tuple(sorted(node.pages)),
                )
                if role in HEADING_ROLES | {"/H"} and not text:
                    raise ValueError("heading_unbound_or_empty")
                descendants = [n for n in _nodes(node) if n.role]
                if role in HEADING_ROLES | {"/H"} and (
                    any(decoded[r]["source"] != "MCID" for r in node.refs)
                    # An ancestor replacement can replace the heading's
                    # accessible content or erase its individual boundary.
                    or any(
                        "/ActualText" in n.obj or "/Alt" in n.obj
                        for n in ancestors[id(node)]
                    )
                    or any(
                        "/Alt" in n.obj
                        or (
                            "/ActualText" in n.obj
                            and " ".join(str(n.obj.ActualText).split())
                            != " ".join(
                                " ".join(decoded[r]["text"] for r in n.refs).split()
                            )
                        )
                        for n in descendants
                    )
                ):
                    raise ValueError("heading_replacement_content_ambiguous")
                # Only a node that passed every binding check joins the list, so
                # a refused first heading never counts as a proven first heading.
                result.targets.append(target)
                if (
                    not text
                    or len(node.pages) != 1
                    or any(
                        n.replacement
                        or any(k in n.obj for k in ("/A", "/C", "/NS", "/Ref", "/ID"))
                        for n in descendants
                    )
                    or any(decoded[r]["source"] != "MCID" for r in node.refs)
                ):
                    target.reason = "heading_unsupported_content_or_attributes"
                    continue
                # Replacement semantics on ancestors also prohibit promotion.
                if not node.safe_context:
                    target.reason = "heading_unsupported_ancestor"
                    continue
                page = document[target.pages[0]]
                if page.rotation or tuple(page.cropbox) != tuple(page.mediabox):
                    target.reason = "heading_rotated_or_cropped_page"
                    continue
                boxes = [resolver.bounds(ref) for ref in target.refs]
                if any(box is None for box in boxes):
                    target.reason = "heading_missing_geometry"
                    continue
                box = fitz.Rect()
                for native in boxes:
                    box |= fitz.Rect(native) * page.transformation_matrix
                target.bounds = tuple(box)
                lines = []
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        lb = fitz.Rect(line["bbox"])
                        if lb.intersects(box):
                            if tuple(line.get("dir", (1, 0))) != (1, 0):
                                target.reason = "heading_unsupported_direction"
                            lines.append(line)
                sizes = [
                    s["size"]
                    for line in lines
                    for s in line["spans"]
                    if s["text"].strip()
                ]
                target.font_size = median(sizes) if sizes else 0
                lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
                height = (
                    median([line["bbox"][3] - line["bbox"][1] for line in lines])
                    if lines
                    else 0
                )
                target.title_candidate = bool(
                    1 <= len(lines) <= 4
                    and sizes
                    and min(sizes) >= max(sizes) * 0.90
                    and all(
                        b["bbox"][1] - a["bbox"][3] <= 0.75 * height
                        for a, b in zip(lines, lines[1:])
                    )
                    and " ".join(text.split())
                    == " ".join(
                        " ".join(
                            s["text"] for line in lines for s in line["spans"]
                        ).split()
                    )
                )
                if node.role in HEADING_ROLES | {"/P"} and all(
                    not n.role for n in node.children
                ):
                    target.changes = [(node.obj, None)]
                elif (
                    node.role in HEADING_ROLES | {"/Div"}
                    and len(descendants) >= 3
                    and (
                        node.role != "/Div"
                        or any(n.role == "/P" for n in descendants[1:])
                    )
                    and all(
                        n.role in {"/P", "/Span"}
                        and n.refs
                        and any(decoded[r]["text"].strip() for r in n.refs)
                        for n in descendants[1:]
                    )
                ):
                    # A complete short, consistently styled multiline group. Body
                    # or subtitle contamination must not become part of a title.
                    lines.sort(key=lambda line: (line["bbox"][1], line["bbox"][0]))
                    heights = [line["bbox"][3] - line["bbox"][1] for line in lines]
                    height = median(heights) if heights else 0
                    if (
                        not 2 <= len(lines) <= 4
                        or not sizes
                        or min(sizes) < max(sizes) * 0.90
                        or max(line["bbox"][0] for line in lines)
                        - min(line["bbox"][0] for line in lines)
                        > height
                        or any(
                            b["bbox"][1] - a["bbox"][3] > 0.75 * height
                            for a, b in zip(lines, lines[1:])
                        )
                        or " ".join(text.split())
                        != " ".join(s["text"] for line in lines for s in line["spans"])
                        .strip()
                        .replace("  ", " ")
                    ):
                        target.reason = "heading_group_not_complete_uniform_lines"
                        continue
                    target.changes = [(node.obj, None)] + [
                        (n.obj, "/Span") for n in descendants[1:] if n.role == "/P"
                    ]
                else:
                    target.reason = "heading_unsupported_group"
        return result
    except Exception as exc:
        result.complete = False
        result.reason = (
            str(exc) if isinstance(exc, ValueError) else "heading_inventory_failed"
        )
        if _is_incomplete_check(result.reason):
            record_incomplete_check("heading_inventory")
        # A failed inventory never clears a finding about a heading it did not
        # prove. Existing scanners report broken structure separately; keep the
        # unresolved heading observable.
        return result


def nominate_title(found):
    """Nominate the complete unique largest first-page owner, retaining review."""
    if not found.complete:
        return None
    candidates = [
        t
        for t in found.targets
        if t.eligible and t.title_candidate and t.pages == (0,) and t.font_size > 0
    ]
    if not candidates:
        return None
    largest = max(t.font_size for t in candidates)
    candidates = [t for t in candidates if t.font_size >= largest * 0.98]
    # Prefer the complete group over a title line contained by that same group.
    groups = [t for t in candidates if t.role == "/Div"]
    candidates = [
        t
        for t in candidates
        if not any(t is not g and set(t.refs) <= set(g.refs) for g in groups)
    ]
    return candidates[0] if len(candidates) == 1 else None


def promote(target, level):
    if not target.eligible or type(level) is not int or not 1 <= level <= 6:
        raise ValueError("unsupported_heading_target")
    for node, role in target.changes:
        node.S = pikepdf.Name(role or f"/H{level}")


def verify_saved_heading(source_path, saved_path, metadata):
    """Reopened source occurrences, never merely a disappearing finding."""
    refs = tuple(tuple(ref) for ref in metadata.get("heading_refs", []))
    if not refs:
        return True  # The older whole-run path has its own binding verification.
    try:
        if sha256(Path(source_path).read_bytes()).hexdigest() != metadata.get(
            "heading_source_sha256"
        ):
            return False
        with pikepdf.open(source_path) as before, pikepdf.open(saved_path) as after:
            original, saved = inventory(before, source_path), inventory(
                after, saved_path
            )
            sources = [t for t in original.targets if t.refs == refs and t.eligible]
            targets = [
                t
                for t in saved.headings
                if t.refs == refs and t.role == f"/H{metadata.get('suggested_level')}"
            ]
            if (
                not original.complete
                or not saved.complete
                or len(sources) != 1
                or len(targets) != 1
                or sources[0].text != targets[0].text
            ):
                return False
            # Role/permutation edits must leave every drawing operation intact.
            if len(before.pages) != len(after.pages):
                return False

            def drawing(page):
                return pikepdf.unparse_content_stream(
                    [
                        op
                        for op in pikepdf.parse_content_stream(page)
                        if str(op.operator) not in {"BMC", "BDC", "EMC"}
                    ]
                )

            return all(
                drawing(a) == drawing(b) for a, b in zip(before.pages, after.pages)
            )
    except Exception:
        return False
