"""Bounded horizontal-text regional order, independent of extraction sequence.

Version 1: persistent whitespace corridors precede row cuts. A cut ends a
column region when its occupied columns change; this retains narrow trailing
content after the body. Aligned single-line matrices remain semantic review.
The model orders occurrence IDs and never discards or duplicates an atom.
"""

from dataclasses import dataclass
from math import isfinite
from statistics import median

RULE_VERSION = "regional-order-v1"
MAX_ATOMS = 2000
MAX_DEPTH = 8
MAX_WORK = 1000000


class LayoutAmbiguous(ValueError):
    pass


@dataclass(frozen=True)
class Atom:
    identity: object
    bbox: tuple
    line_height: float = 0
    lines: int = 1
    trailing: bool = False


@dataclass(frozen=True)
class LayoutPlan:
    order: tuple
    regions: tuple
    version: str = RULE_VERSION


def plan_order(atoms, page_width):
    atoms = list(atoms)
    if len(atoms) > MAX_ATOMS or len({a.identity for a in atoms}) != len(atoms):
        raise LayoutAmbiguous("reading_order_atom_limit_or_duplicate")
    if not isfinite(page_width) or page_width <= 0:
        raise LayoutAmbiguous("reading_order_invalid_page_geometry")
    for atom in atoms:
        if (
            len(atom.bbox) != 4
            or not all(isfinite(v) for v in atom.bbox)
            or atom.bbox[2] <= atom.bbox[0]
            or atom.bbox[3] <= atom.bbox[1]
        ):
            raise LayoutAmbiguous("reading_order_invalid_atom_geometry")
    work = 0
    regions = []

    def visit(items, depth):
        nonlocal work
        if depth > MAX_DEPTH:
            raise LayoutAmbiguous("reading_order_region_depth_limit")
        if not items:
            return []
        if len(items) == 1:
            regions.append(tuple(a.identity for a in items))
            return items
        work += len(items) ** 2
        if work > MAX_WORK:
            raise LayoutAmbiguous("reading_order_region_work_limit")
        lh = median(
            a.line_height or (a.bbox[3] - a.bbox[1]) / max(a.lines, 1) for a in items
        )
        corridor = max(0.75 * lh, 0.01 * page_width)
        extent = max(a.bbox[2] for a in items) - min(a.bbox[0] for a in items)
        # Wide blocks can span columns; they must not erase persistent corridors.
        narrow = [a for a in items if a.bbox[2] - a.bbox[0] < 0.60 * extent]
        intervals = []
        for atom in sorted(narrow, key=lambda a: a.bbox[0]):
            x0, _, x1, _ = atom.bbox
            if intervals and x0 - intervals[-1][1] < corridor:
                intervals[-1][1] = max(intervals[-1][1], x1)
            else:
                intervals.append([x0, x1])
        cuts = [(a[1] + b[0]) / 2 for a, b in zip(intervals, intervals[1:])]
        if len(intervals) > 8:
            raise LayoutAmbiguous("reading_order_column_limit")
        if cuts:
            spanning = [
                a for a in items if any(a.bbox[0] < x < a.bbox[2] for x in cuts)
            ]
            rest = [a for a in items if a not in spanning]
            if spanning:
                spans = sorted(spanning, key=lambda a: (a.bbox[1], a.bbox[0]))
                if any(a.bbox[3] > b.bbox[1] for a, b in zip(spans, spans[1:])):
                    raise LayoutAmbiguous("reading_order_overlapping_spanning_regions")
                bands = [[] for _ in range(len(spans) + 1)]
                for atom in rest:
                    index = sum(s.bbox[3] <= atom.bbox[1] for s in spans)
                    if index < len(spans) and atom.bbox[3] > spans[index].bbox[1]:
                        raise LayoutAmbiguous("reading_order_crossing_regions")
                    bands[index].append(atom)
                ordered = []
                for index, band in enumerate(bands):
                    ordered.extend(visit(band, depth + 1))
                    if index < len(spans):
                        ordered.extend(visit([spans[index]], depth + 1))
                return ordered

            def column(atom):
                return sum(atom.bbox[0] >= x for x in cuts)

            # A clear horizontal gap with changed column occupancy defines a new
            # region. Persistent columns otherwise win over apparent table rows.
            rows = sorted(items, key=lambda a: a.bbox[1])
            bottom = rows[0].bbox[3]
            for index, atom in enumerate(rows[1:], 1):
                if atom.bbox[1] - bottom > 1.25 * lh:
                    before, after = rows[:index], rows[index:]
                    before_cols = {column(a) for a in before}
                    after_cols = {column(a) for a in after}
                    if before_cols != after_cols:
                        return visit(before, depth + 1) + visit(after, depth + 1)
                bottom = max(bottom, atom.bbox[3])
            columns = [
                [a for a in items if column(a) == i] for i in range(len(cuts) + 1)
            ]
            columns = [
                sorted(c, key=lambda a: (a.bbox[1], a.bbox[0])) for c in columns if c
            ]
            if (
                len(columns) > 1
                and all(len(c) >= 2 for c in columns)
                and all(a.lines == 1 for a in items)
            ):
                rows = [
                    tuple(round(a.bbox[1] / max(lh * 0.25, 1)) for a in c)
                    for c in columns
                ]
                if all(row == rows[0] for row in rows[1:]):
                    raise LayoutAmbiguous("reading_order_ambiguous_matrix")
            ordered = []
            for col in columns:
                ordered.extend(visit(col, depth + 1))
            return ordered
        ordered = sorted(items, key=lambda a: (a.bbox[1], a.bbox[0]))
        if any(a.bbox[3] > b.bbox[1] + 0.1 for a, b in zip(ordered, ordered[1:])):
            raise LayoutAmbiguous("reading_order_overlapping_content")
        regions.append(tuple(a.identity for a in ordered))
        return ordered

    trailing = [atom for atom in atoms if atom.trailing]
    if len(trailing) > 1:
        raise LayoutAmbiguous("reading_order_ambiguous_trailing_atom")
    ordered = visit([atom for atom in atoms if not atom.trailing], 0) + trailing
    if trailing:
        regions.append(tuple(atom.identity for atom in trailing))
    if len(ordered) != len(atoms) or {a.identity for a in ordered} != {
        a.identity for a in atoms
    }:
        raise LayoutAmbiguous("reading_order_incomplete_plan")
    return LayoutPlan(tuple(a.identity for a in ordered), tuple(regions))


def block_plan(blocks, page_width=None):
    atoms = [
        Atom(
            i,
            tuple(b["bbox"]),
            b.get("line_height", 0),
            b.get("line_count", 1),
            b.get("verified_page_ordinal", False),
        )
        for i, b in enumerate(blocks)
    ]
    width = page_width or max((a.bbox[2] for a in atoms), default=1)
    plan = plan_order(atoms, width)
    return [blocks[i] for i in plan.order]
