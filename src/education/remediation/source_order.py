"""Bounded, source-bound permutations of existing PDF structure siblings.

No structure is flattened, renumbered, reparented or marked as Artifact. The
caller owns the pikepdf handle and publication. A saved candidate must pass the
unchanged reading-order checker before a permutation is retained.
"""

import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf
import pymupdf as fitz

from ..pdf_checks.marked_content import MarkedContentResolver
from ..pdf_checks.reading_order import ReadingOrderVerifier

MAX_NODES = 20000
MAX_PAGES = 100
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_MATCH_WORK = 1000000
MAX_PAGE_REFS = 2000
MAX_WORDS = 20000
SAFE_TEXT_ROLES = {
    "/Document",
    "/Part",
    "/Sect",
    "/Div",
    "/P",
    "/Span",
    "/H",
    "/H1",
    "/H2",
    "/H3",
    "/H4",
    "/H5",
    "/H6",
}


class OrderRefusal(ValueError):
    """A specific reason the source does not support an automatic permutation."""


@dataclass
class Node:
    obj: Any
    children: list["Node"] = field(default_factory=list)
    refs: list[tuple[int, int]] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)
    role: str = ""
    replacement: bool = False
    safe_context: bool = True


def _tree(pdf, resolver):
    root = pdf.Root.get("/StructTreeRoot")
    if not isinstance(root, pikepdf.Dictionary) or "/K" not in root:
        raise OrderRefusal("reading_order_missing_structure")
    pages = {page.obj.objgen: index for index, page in enumerate(pdf.pages)}
    visited = set()
    visits = 0

    def collect(value, parent, inherited=-1, depth=0, safe_context=True):
        nonlocal visits
        visits += 1
        if visits > MAX_NODES or depth > 50:
            raise OrderRefusal("reading_order_structure_limit")
        if isinstance(value, bool):
            raise OrderRefusal("reading_order_invalid_reference")
        if isinstance(value, int):
            key = resolver.resolve(parent, value, inherited)
            if key is None or resolver.failed:
                raise OrderRefusal("reading_order_ambiguous_mcid_ownership")
            return Node(value, refs=[key], pages={key[0]})
        if not isinstance(value, pikepdf.Dictionary):
            raise OrderRefusal("reading_order_invalid_structure")
        page_index = inherited
        if "/Pg" in value:
            pg = value.Pg
            page_index = (
                pages.get(pg.objgen, -2)
                if isinstance(pg, pikepdf.Dictionary) and pg.is_indirect
                else -2
            )
            if page_index == -2:
                raise OrderRefusal("reading_order_invalid_page_reference")
        if value.get("/Type") == pikepdf.Name.MCR or "/MCID" in value:
            if "/Stm" in value or "/StmOwn" in value:
                raise OrderRefusal("reading_order_form_stream_scope")
            key = resolver.resolve(parent, value.get("/MCID"), page_index)
            if key is None or resolver.failed:
                raise OrderRefusal("reading_order_ambiguous_mcid_ownership")
            return Node(value, refs=[key], pages={key[0]})
        if value.get("/Type") == pikepdf.Name.OBJR:
            raise OrderRefusal("reading_order_object_reference")
        if not value.is_indirect or value.objgen in visited:
            raise OrderRefusal("reading_order_cyclic_or_shared_structure")
        visited.add(value.objgen)
        if value.get("/P") != parent:
            raise OrderRefusal("reading_order_invalid_parent_relation")
        role = value.get("/S")
        if value.get("/Type", pikepdf.Name.StructElem) != pikepdf.Name.StructElem:
            raise OrderRefusal("reading_order_invalid_structure_type")
        if not isinstance(role, pikepdf.Name):
            raise OrderRefusal("reading_order_invalid_role")
        # Custom role chains and replacement text need a separate semantic review.
        node = Node(
            value,
            role=str(role),
            replacement="/ActualText" in value
            or "/Alt" in value
            or str(role) in root.get("/RoleMap", {}),
        )
        kids = value.get("/K", pikepdf.Array())
        kids = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
        if len(kids) > MAX_NODES:
            raise OrderRefusal("reading_order_structure_limit")
        node.safe_context = (
            safe_context and node.role in SAFE_TEXT_ROLES and not node.replacement
        )
        node.children = [
            collect(k, value, page_index, depth + 1, node.safe_context) for k in kids
        ]
        node.refs = [ref for child in node.children for ref in child.refs]
        node.pages = {number for child in node.children for number in child.pages}
        if page_index >= 0:
            node.pages.add(page_index)
        return node

    kids = root.K
    children = list(kids) if isinstance(kids, pikepdf.Array) else [kids]
    if len(children) > MAX_NODES:
        raise OrderRefusal("reading_order_structure_limit")
    return Node(root, children=[collect(k, root) for k in children])


def _nodes(node):
    yield node
    for child in node.children:
        yield from _nodes(child)


def _tokens(text):
    return tuple(text.split())


def _page_number_box(page, number):
    """A unique printed page ordinal in the bottom margin, with no overlaps."""
    if page.rotation or tuple(page.cropbox) != tuple(page.mediabox):
        return None
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    if len(blocks) > 2000:
        return None
    candidates = []
    all_boxes = []
    for block in blocks:
        box = fitz.Rect(block["bbox"])
        all_boxes.append(box)
        lines = block.get("lines", [])
        text = " ".join(
            span["text"] for line in lines for span in line.get("spans", [])
        ).strip()
        if (
            len(text) <= 4
            and text.isascii()
            and text.isdigit()
            and int(text) == number
            and box.y0 >= page.rect.height * 0.90
            and box.y1 <= page.rect.height
            and all(tuple(line.get("dir", (1, 0))) == (1, 0) for line in lines)
        ):
            candidates.append((text, box))
    if len(candidates) != 1:
        return None
    text, box = candidates[0]
    words = page.get_text("words")
    matching_words = [word for word in words if word[4] == text]
    if (
        len(words) > MAX_WORDS
        or len(matching_words) != 1
        or not box.contains(fitz.Rect(matching_words[0][:4]))
    ):
        return None
    if any(
        other != box and (other.intersects(box) or other.y1 > box.y1 + 1)
        for other in all_boxes
    ):
        return None
    return text, box


def repair_page(pdf, page_number):
    """Return (changed sibling count, repair class); refuse without side effects."""
    if (
        not isinstance(page_number, int)
        or isinstance(page_number, bool)
        or not 1 <= page_number <= len(pdf.pages)
    ):
        raise OrderRefusal("reading_order_invalid_page")
    if len(pdf.pages) > MAX_PAGES:
        raise OrderRefusal("reading_order_page_limit")
    target = page_number - 1
    changes = []
    with tempfile.TemporaryDirectory(prefix="aelira-order-") as directory:
        source = str(Path(directory) / "source.pdf")
        candidate = str(Path(directory) / "candidate.pdf")
        pdf.save(source)
        if Path(source).stat().st_size > MAX_SNAPSHOT_BYTES:
            raise OrderRefusal("reading_order_snapshot_limit")
        resolver = MarkedContentResolver(pdf, source)
        tree = _tree(pdf, resolver)
        nodes = list(_nodes(tree))
        refs = [
            ref
            for node in nodes
            if not node.children
            for ref in node.refs
            if ref[0] == target
        ]
        if len(refs) > MAX_PAGE_REFS:
            raise OrderRefusal("reading_order_page_reference_limit")
        decoded = {ref: resolver.text(ref) for ref in refs}
        if resolver.failed or any(value is None for value in decoded.values()):
            raise OrderRefusal("reading_order_unresolved_source_content")
        text = {key: value["text"] for key, value in decoded.items()}
        if sum(len(value) for value in text.values()) > 200000:
            raise OrderRefusal("reading_order_text_limit")

        def node_text(node):
            return " ".join(text.get(ref, "") for ref in node.refs).strip()

        def stage(parent, ordered):
            original = parent.obj.K
            changes.append((parent.obj, original))
            parent.obj.K = pikepdf.Array([node.obj for node in ordered])

        try:
            with fitz.open(source) as document:
                page = document[target]
                if page.rotation or tuple(page.cropbox) != tuple(page.mediabox):
                    raise OrderRefusal("reading_order_rotated_or_cropped_page")

                def bound_to_box(ref, box):
                    native = resolver.bounds(ref)
                    if native is None:
                        return False
                    glyph_box = fitz.Rect(native) * page.transformation_matrix
                    expanded = fitz.Rect(box) + (-2, -2, 2, 2)
                    return expanded.contains(glyph_box)

                # Moving a corroborated ordinal preserves all other semantics,
                # including table cells, images, columns and nested groups.
                margin = _page_number_box(page, page_number)
                corroborated = (
                    margin
                    and sum(
                        _page_number_box(document[index], index + 1) is not None
                        for index in range(len(document))
                    )
                    >= 3
                )
                method = "single_column_siblings"
                if corroborated:
                    printed = margin[0]
                    matches = []
                    for parent in nodes:
                        if not parent.safe_context:
                            continue
                        indices = [
                            index
                            for index, child in enumerate(parent.children)
                            if child.pages == {target}
                        ]
                        if len(indices) < 2 or indices != list(
                            range(indices[0], indices[-1] + 1)
                        ):
                            continue
                        for index in indices:
                            child = parent.children[index]
                            if (
                                child.role in SAFE_TEXT_ROLES
                                and node_text(child) == printed
                                and child.refs
                                and all(
                                    decoded[ref]["source"] == "MCID"
                                    and bound_to_box(ref, margin[1])
                                    for ref in child.refs
                                )
                                and all(
                                    n.role in SAFE_TEXT_ROLES or not n.role
                                    for n in _nodes(child)
                                )
                                and not any(n.replacement for n in _nodes(child))
                            ):
                                matches.append((parent, indices, index))
                    if (
                        len(matches) == 1
                        and sum(
                            _tokens(value).count(printed) for value in text.values()
                        )
                        == 1
                    ):
                        parent, indices, index = matches[0]
                        if index != indices[-1]:
                            ordered = list(parent.children)
                            item = ordered.pop(index)
                            ordered.insert(indices[-1], item)
                            stage(parent, ordered)
                            method = "running_page_number"
                if not changes:
                    relevant = [node for node in nodes if target in node.pages]
                    if any(
                        node.replacement
                        or (node.role and node.role not in SAFE_TEXT_ROLES)
                        for node in relevant
                    ):
                        raise OrderRefusal(
                            "reading_order_semantic_group_requires_review"
                        )
                    if any(value["source"] != "MCID" for value in decoded.values()):
                        raise OrderRefusal(
                            "reading_order_replacement_text_requires_review"
                        )
                    layout = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
                    if any(
                        tuple(line.get("dir", (1, 0))) != (1, 0)
                        for block in layout["blocks"]
                        for line in block.get("lines", [])
                    ):
                        raise OrderRefusal("reading_order_unsupported_text_direction")
                    blocks = ReadingOrderVerifier()._get_visual_text_order(page)
                    blocks.sort(key=lambda block: block["bbox"][1])
                    if any(
                        a["bbox"][3] > b["bbox"][1] for a, b in zip(blocks, blocks[1:])
                    ):
                        raise OrderRefusal(
                            "reading_order_columns_or_overlapping_content"
                        )
                    visual = tuple(
                        token for block in blocks for token in _tokens(block["text"])
                    )
                    if len(visual) > MAX_WORDS:
                        raise OrderRefusal("reading_order_word_limit")
                    if Counter(visual) != Counter(
                        token for value in text.values() for token in _tokens(value)
                    ):
                        raise OrderRefusal("reading_order_source_text_mismatch")
                    positions = {}
                    occupied = set()
                    match_work = 0
                    starts_by_token = {}
                    for index, token in enumerate(visual):
                        starts_by_token.setdefault(token, []).append(index)
                    for ref, value in text.items():
                        tokens = _tokens(value)
                        if not tokens:
                            raise OrderRefusal("reading_order_empty_content_reference")
                        possible = starts_by_token.get(tokens[0], [])
                        match_work += len(possible) * len(tokens)
                        if match_work > MAX_MATCH_WORK:
                            raise OrderRefusal("reading_order_text_match_limit")
                        starts = [
                            index
                            for index in possible
                            if visual[index : index + len(tokens)] == tokens
                        ]
                        if len(starts) != 1:
                            raise OrderRefusal(
                                "reading_order_repeated_or_ambiguous_text"
                            )
                        indices = list(range(starts[0], starts[0] + len(tokens)))
                        if occupied.intersection(indices):
                            raise OrderRefusal("reading_order_ambiguous_text_ownership")
                        occupied.update(indices)
                        positions[ref] = indices
                        matching_blocks = []
                        offset = 0
                        for block in blocks:
                            count = len(_tokens(block["text"]))
                            if offset < indices[-1] + 1 and offset + count > indices[0]:
                                matching_blocks.append(fitz.Rect(block["bbox"]))
                            offset += count
                        region = fitz.Rect(matching_blocks[0])
                        for box in matching_blocks[1:]:
                            region |= box
                        if not bound_to_box(ref, region):
                            raise OrderRefusal("reading_order_source_geometry_mismatch")
                    # Bottom-up sibling permutations, never cross parent boundaries.
                    for parent in reversed(nodes):
                        indices = [
                            index
                            for index, child in enumerate(parent.children)
                            if child.pages == {target}
                        ]
                        if len(indices) < 2:
                            continue
                        if indices != list(range(indices[0], indices[-1] + 1)) or any(
                            not parent.children[index].role for index in indices
                        ):
                            raise OrderRefusal(
                                "reading_order_mixed_or_discontiguous_group"
                            )
                        ranked = []
                        for index in indices:
                            child = parent.children[index]
                            covered = sorted(
                                i for ref in child.refs for i in positions.get(ref, [])
                            )
                            if not covered or covered != list(
                                range(covered[0], covered[-1] + 1)
                            ):
                                raise OrderRefusal(
                                    "reading_order_interleaved_semantic_groups"
                                )
                            ranked.append((covered[0], index))
                        wanted = [index for _, index in sorted(ranked)]
                        if wanted != indices:
                            ordered = list(parent.children)
                            for index, old in zip(indices, wanted):
                                ordered[index] = parent.children[old]
                            stage(parent, ordered)
                if not changes:
                    raise OrderRefusal("reading_order_no_supported_permutation")
            pdf.save(candidate)
            verifier = ReadingOrderVerifier()
            with fitz.open(candidate) as saved:
                page = saved[target]
                visual = verifier._get_visual_text_order(
                    page,
                    verifier._verified_artifact_duplicates(candidate, target, page),
                )
                structure = verifier._get_structure_tree_order(page, candidate, target)
                if any("table_id" in block for block in structure):
                    issue = verifier._compare_table_reading_orders(
                        page_number, page, visual, structure
                    )
                else:
                    issue = verifier._compare_reading_orders(
                        page_number,
                        visual,
                        structure,
                        multi_column=verifier._detect_multi_column(visual),
                    )
                if issue is not None and not issue.review_only:
                    raise OrderRefusal("reading_order_candidate_still_unverified")
            return len(changes), method
        except Exception:
            for obj, original in reversed(changes):
                obj.K = original
            raise
