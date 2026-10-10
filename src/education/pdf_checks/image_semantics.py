"""Resolve direct image draws to reachable Figure owners or true Artifacts.

Resource-level Alt strings and nearby bounding boxes cannot establish ownership
of a particular draw. Unsupported scopes remain missing and mark strict scans
incomplete, rather than silently excluding images from review.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

import pikepdf

from .completeness import record_incomplete_check
from .marked_content import MarkedContentResolver
from ..remediation.pdf_font_text import require_bounded_page_streams


@dataclass(frozen=True)
class ImageOwnership:
    semantics: dict[str, tuple[bool, str | None]]
    exclusive_figures: dict[str, Any]
    figures: dict[tuple[int, int], Any]


@dataclass(frozen=True)
class FigureFailureEvidence:
    missing: frozenset[tuple[int, int]]
    described: frozenset[tuple[int, int]]
    figure_count: int


def resolve_image_ownership(
    pdf: pikepdf.Pdf, path: str, page_index: int, occurrences: list[dict[str, Any]]
) -> ImageOwnership:
    """Resolve ownership on the caller's live PDF, without guessing a new tag.

    Scanning allows grouped Figures. Mutation requires a Figure whose entire
    marked-content scope contains exactly this image and no other painted content.
    """
    return _resolve_image_ownership(pdf, path, page_index, occurrences)


def structured_image_semantics(
    path: str, page_index: int, occurrences: list[dict[str, Any]]
) -> dict[str, tuple[bool, str | None]] | None:
    """Return None for untagged sources, otherwise exact occurrence results."""
    missing: dict[str, tuple[bool, str | None]] = {
        item["occurrence_id"]: (False, None) for item in occurrences
    }
    if not occurrences:
        return missing
    try:
        with pikepdf.open(path, attempt_recovery=False) as pdf:
            if "/StructTreeRoot" not in pdf.Root:
                return None
            return resolve_image_ownership(pdf, path, page_index, occurrences).semantics
    except Exception:
        record_incomplete_check("image_checker.image_semantic_ownership")
        return missing


def _resolve_image_ownership(
    pdf: pikepdf.Pdf, path: str, page_index: int, occurrences: list[dict[str, Any]]
) -> ImageOwnership:
    if "/StructTreeRoot" not in pdf.Root:
        raise ValueError("untagged")
    if len(pdf.pages) > 500 or len(pdf.objects) > 20_000:
        raise ValueError("bounds")
    page = pdf.pages[page_index]
    require_bounded_page_streams(page)
    resolver = MarkedContentResolver(pdf, path)
    page_ids = {item.obj.objgen: index for index, item in enumerate(pdf.pages)}
    owners: dict[tuple[int, int], tuple[Any, Any, str | None]] = {}
    figures: dict[tuple[int, int], Any] = {}
    figure_keys: dict[tuple[int, int], set[tuple[int, int]]] = {}
    blocked_figures: set[tuple[int, int]] = set()
    seen: set[tuple[int, int]] = set()
    visits = 0

    def walk(
        node: Any,
        parent: Any,
        inherited: int,
        figure: Any,
        depth: int,
        replacement_ancestor: bool = False,
    ) -> None:
        nonlocal visits
        visits += 1
        if visits > 20_000 or depth > 50:
            raise ValueError("bounds")
        if not isinstance(node, pikepdf.Dictionary) or not node.is_indirect:
            raise ValueError("structure")
        parent_reference = node.get("/P")
        if (
            node.objgen in seen
            or not isinstance(parent_reference, pikepdf.Dictionary)
            or not parent_reference.is_indirect
            or parent_reference.objgen != parent.objgen
        ):
            raise ValueError("structure")
        seen.add(node.objgen)
        current = inherited
        if "/Pg" in node:
            current = page_ids.get(node.Pg.objgen, -2)
            if current < 0:
                raise ValueError("page")
        role = node.get("/S")
        if not isinstance(role, pikepdf.Name):
            raise ValueError("role")
        role_map = pdf.Root.StructTreeRoot.get("/RoleMap", pikepdf.Dictionary())
        unresolved_role = (
            not isinstance(role_map, pikepdf.Dictionary)
            or str(role) in role_map
            or "/NS" in node
        )
        if role == pikepdf.Name.Figure:
            if figure is not None:
                blocked_figures.add(figure.objgen)
            figure = node
            figures[node.objgen] = node
            figure_keys[node.objgen] = set()
            if replacement_ancestor or unresolved_role or "/ActualText" in node:
                blocked_figures.add(node.objgen)
        elif figure is not None and ("/Alt" in node or "/ActualText" in node):
            blocked_figures.add(figure.objgen)
        if figure is not None and unresolved_role:
            blocked_figures.add(figure.objgen)
        replacement_scope = (
            replacement_ancestor
            or unresolved_role
            or "/ActualText" in node
            or (role != pikepdf.Name.Figure and "/Alt" in node)
        )
        # /K is optional: a reachable, correctly parented structure
        # element may have no children (ISO 32000-1, Table 323).
        # It owns no draws. Keep validating its parent/role above and
        # require actual image MCIDs to resolve through ParentTree.
        if "/K" not in node:
            return
        kids = node.get("/K")
        items = (
            list(cast(Iterable[Any], kids))
            if isinstance(kids, pikepdf.Array)
            else [kids]
        )
        for child in items:
            if isinstance(child, int) and not isinstance(child, bool):
                key = resolver.resolve(node, child, current)
            elif isinstance(child, pikepdf.Dictionary) and "/MCID" in child:
                if "/Stm" in child or "/StmOwn" in child:
                    raise ValueError("scope")
                child_page = current
                if "/Pg" in child:
                    child_page = page_ids.get(child.Pg.objgen, -2)
                key = resolver.resolve(
                    node, child.MCID, child_page, invalid_page=child_page == -2
                )
            elif isinstance(child, pikepdf.Dictionary) and "/S" in child:
                walk(child, node, current, figure, depth + 1, replacement_scope)
                continue
            else:
                # Object references/custom stream ownership need a
                # separate verifier; they cannot suppress an image.
                raise ValueError("scope")
            if key is None or key in owners or resolver.failed:
                raise ValueError("owner")
            alt = figure.get("/Alt") if figure is not None else None
            alt_text = str(alt).strip() if isinstance(alt, pikepdf.String) else None
            owners[key] = (node, figure, alt_text or None)
            if figure is not None:
                figure_keys[figure.objgen].add(key)

    root = pdf.Root.StructTreeRoot
    kids = root.get("/K")
    for child in (
        list(cast(Iterable[Any], kids)) if isinstance(kids, pikepdf.Array) else [kids]
    ):
        walk(child, root, -1, None, 0)
    ops = list(pikepdf.parse_content_stream(page))
    if len(ops) > 100_000:
        raise ValueError("bounds")
    stack: list[tuple[bool, int | None, bool]] = []
    draws: list[tuple[int, bool, int | None]] = []
    used_mcids: set[int] = set()
    non_image_mcids: set[int] = set()
    resources: Any = page.Resources
    for op in ops:
        name, args = str(op.operator), list(op.operands)
        if name in {"BMC", "BDC"}:
            if len(args) != (1 if name == "BMC" else 2) or not isinstance(
                args[0], pikepdf.Name
            ):
                raise ValueError("markers")
            artifact = args[0] == pikepdf.Name.Artifact
            marker_mcid: int | None = None
            replacement = False
            if name == "BDC":
                props: Any = args[1]
                if isinstance(props, pikepdf.Name):
                    props = resources.get("/Properties", pikepdf.Dictionary()).get(
                        str(props)
                    )
                if not isinstance(props, pikepdf.Dictionary):
                    raise ValueError("markers")
                replacement = "/Alt" in props or "/ActualText" in props
                if "/MCID" in props:
                    raw_mcid: Any = props.MCID
                    if (
                        type(raw_mcid) is not int
                        or raw_mcid < 0
                        or raw_mcid in used_mcids
                    ):
                        raise ValueError("markers")
                    marker_mcid = int(raw_mcid)
                    used_mcids.add(marker_mcid)
            if (
                (artifact and marker_mcid is not None)
                or len(stack) >= 50
                or (
                    marker_mcid is not None
                    and any(value[1] is not None or value[0] for value in stack)
                )
            ):
                raise ValueError("markers")
            if artifact and any(value[1] is not None for value in stack):
                raise ValueError("markers")
            if replacement:
                non_image_mcids.update(
                    value[1] for value in stack if value[1] is not None
                )
                if marker_mcid is not None:
                    non_image_mcids.add(marker_mcid)
            stack.append((artifact, marker_mcid, replacement))
        elif name == "EMC":
            if args or not stack:
                raise ValueError("markers")
            stack.pop()
        elif name == "Do":
            if len(args) != 1:
                raise ValueError("draw")
            image = resources.get("/XObject", pikepdf.Dictionary()).get(str(args[0]))
            if (
                not isinstance(image, pikepdf.Stream)
                or image.get("/Subtype") != pikepdf.Name.Image
            ):
                raise ValueError("scope")
            artifact = any(value[0] for value in stack)
            mcids = [value[1] for value in stack if value[1] is not None]
            if any(value[2] for value in stack):
                non_image_mcids.update(mcids)
            draws.append((image.objgen[0], artifact, mcids[0] if mcids else None))
        elif name in {"BI", "BX", "EX"}:
            raise ValueError("scope")
        elif name in {
            "Tj",
            "TJ",
            "'",
            '"',
            "S",
            "s",
            "f",
            "F",
            "f*",
            "B",
            "B*",
            "b",
            "b*",
            "sh",
        }:
            non_image_mcids.update(value[1] for value in stack if value[1] is not None)
    if stack or len(draws) != len(occurrences):
        raise ValueError("draws")
    result: dict[str, tuple[bool, str | None]] = {
        item["occurrence_id"]: (False, None) for item in occurrences
    }
    exclusive_figures: dict[str, Any] = {}
    for position, (xref, artifact, mcid) in enumerate(draws):
        occurrence = occurrences[position]
        if occurrence["image_index"] != position or occurrence["image_xref"] != xref:
            raise ValueError("draws")
        if artifact:
            result[occurrence["occurrence_id"]] = (True, None)
        elif mcid is not None:
            binding = owners.get((page_index, mcid))
            if binding is None:
                raise ValueError("owner")
            if binding[2]:
                result[occurrence["occurrence_id"]] = (True, binding[2])
            figure = binding[1]
            if (
                figure is not None
                and figure.objgen not in blocked_figures
                and figure_keys[figure.objgen] == {(page_index, mcid)}
                and mcid not in non_image_mcids
                and sum(draw[2] == mcid for draw in draws) == 1
            ):
                exclusive_figures[occurrence["occurrence_id"]] = figure
    return ImageOwnership(result, exclusive_figures, figures)


def missing_figure_occurrences(
    path: str,
) -> FigureFailureEvidence | None:
    """Return complete, exclusive image evidence for Matterhorn 13-004 failures.

    Orphan, grouped, mixed-content and unsupported Figures have no such proof.
    Object numbers never serve as cross-save identities. The caller must also
    prove unchanged image paint and resources for every returned occurrence.
    """
    import pymupdf as fitz
    from .image_checker import _displayed_image_occurrences

    try:
        with pikepdf.open(path, attempt_recovery=False) as pdf, fitz.open(path) as doc:
            if not 0 < len(pdf.pages) <= 128 or len(pdf.objects) > 20_000:
                return None
            missing: dict[tuple[int, int], tuple[int, int]] = {}
            described: set[tuple[int, int]] = set()
            figures: dict[tuple[int, int], Any] = {}
            for page_index, page in enumerate(doc):
                occurrences = _displayed_image_occurrences(page, page_index + 1)
                ownership = resolve_image_ownership(pdf, path, page_index, occurrences)
                figures = ownership.figures
                by_id = {item["occurrence_id"]: item for item in occurrences}
                for occurrence_id, figure in ownership.exclusive_figures.items():
                    occurrence = by_id[occurrence_id]
                    position = (occurrence["page_number"], occurrence["image_index"])
                    if "/Alt" in figure or "/ActualText" in figure:
                        alt = figure.get("/Alt")
                        if (
                            isinstance(alt, pikepdf.String)
                            and str(alt).strip()
                            and "/ActualText" not in figure
                        ):
                            described.add(position)
                        continue
                    if figure.objgen in missing:
                        return None
                    missing[figure.objgen] = position
            all_missing = {
                key
                for key, figure in figures.items()
                if "/Alt" not in figure and "/ActualText" not in figure
            }
            if set(missing) != all_missing:
                return None
            return FigureFailureEvidence(
                frozenset(missing.values()), frozenset(described), len(figures)
            )
    except Exception:
        return None
