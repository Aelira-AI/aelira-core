"""Bounded, provider-neutral contracts for commutative-diagram semantics."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from html import escape
from itertools import permutations, product
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from src.education.visual_semantic_contract import canonical_json_bytes

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_CANONICAL_PERMUTATIONS = 4_096
_MAX_COORDINATE = 1_000_000.0


class _FrozenContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagramNodeV1(_FrozenContractModel):
    """One object in a commutative diagram."""

    node_id: str = Field(pattern=_IDENTIFIER_PATTERN)


class DiagramEdgeV1(_FrozenContractModel):
    """One directed or bidirectional arrow between diagram objects."""

    edge_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    source_node_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    target_node_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    direction: Literal["directed", "bidirectional"]

    @model_validator(mode="after")
    def _reject_ambiguous_bidirectional_loop(self) -> "DiagramEdgeV1":
        if (
            self.direction == "bidirectional"
            and self.source_node_id == self.target_node_id
        ):
            raise ValueError("bidirectional self-loops are ambiguous")
        return self


class DiagramLabelV1(_FrozenContractModel):
    """One semantic label attached to exactly one node or edge."""

    label_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    text: str = Field(min_length=1, max_length=1_024)
    target_kind: Literal["node", "edge"]
    target_id: str = Field(pattern=_IDENTIFIER_PATTERN)

    @field_validator("text")
    @classmethod
    def _bounded_printable_text(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("label text must be trimmed printable text")
        return value


class CompositionPathV1(_FrozenContractModel):
    """One ordered traversal whose edge order is mathematically material."""

    path_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    start_node_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    end_node_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    edge_ids: tuple[Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)], ...] = Field(
        max_length=32
    )


class CommutativityRelationV1(_FrozenContractModel):
    """A declaration that two or more verified paths are equal."""

    relation_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    path_ids: tuple[Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)], ...] = Field(
        min_length=2, max_length=8
    )


class DiagramNodePositionV1(_FrozenContractModel):
    """Non-semantic layout metadata excluded from canonical identity."""

    node_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    x: float
    y: float

    @field_validator("x", "y", mode="before")
    @classmethod
    def _bounded_finite_coordinate(cls, value: Any) -> Any:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or abs(float(value)) > _MAX_COORDINATE
        ):
            raise ValueError("layout coordinates must be bounded finite numbers")
        return value


class UnresolvedDiagramCrossingV1(_FrozenContractModel):
    """A visual crossing whose topology was not resolved by an upstream specialist."""

    crossing_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    edge_ids: tuple[Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)], ...] = Field(
        min_length=2, max_length=2
    )

    @model_validator(mode="after")
    def _different_edges(self) -> "UnresolvedDiagramCrossingV1":
        if self.edge_ids[0] == self.edge_ids[1]:
            raise ValueError("an unresolved crossing must name two different edges")
        return self


def _require_unique(values: Iterable[str], *, label: str) -> None:
    counts = Counter(values)
    duplicates = sorted(value for value, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate {label}: {', '.join(duplicates)}")


def _traverse_path(
    path: CompositionPathV1,
    edges_by_id: Mapping[str, DiagramEdgeV1],
) -> tuple[tuple[str, str, str], ...]:
    current = path.start_node_id
    steps: list[tuple[str, str, str]] = []
    for edge_id in path.edge_ids:
        edge = edges_by_id.get(edge_id)
        if edge is None:
            raise ValueError(f"path {path.path_id} references missing edge {edge_id}")
        if edge.direction == "directed":
            if current != edge.source_node_id:
                raise ValueError(f"path traversal fails at edge {edge_id}")
            following = edge.target_node_id
        elif current == edge.source_node_id:
            following = edge.target_node_id
        elif current == edge.target_node_id:
            following = edge.source_node_id
        else:
            raise ValueError(f"path traversal fails at edge {edge_id}")
        steps.append((edge_id, current, following))
        current = following
    if current != path.end_node_id:
        raise ValueError(f"path traversal does not end at {path.end_node_id}")
    return tuple(steps)


def _canonical_work(node_labels: Iterable[str]) -> int:
    work = 1
    for count in Counter(node_labels).values():
        work *= math.factorial(count)
        if work > _MAX_CANONICAL_PERMUTATIONS:
            break
    return work


class CommutativeDiagramCandidateV1(_FrozenContractModel):
    """A bounded graph candidate awaiting topology and digest verification."""

    contract_kind: Literal["commutative_diagram_v1"]
    nodes: tuple[DiagramNodeV1, ...] = Field(min_length=1, max_length=16)
    edges: tuple[DiagramEdgeV1, ...] = Field(max_length=64)
    labels: tuple[DiagramLabelV1, ...] = Field(min_length=1, max_length=80)
    paths: tuple[CompositionPathV1, ...] = Field(max_length=64)
    relations: tuple[CommutativityRelationV1, ...] = Field(max_length=32)
    layout: tuple[DiagramNodePositionV1, ...] = Field(default=(), max_length=16)
    unresolved_crossings: tuple[UnresolvedDiagramCrossingV1, ...] = Field(
        default=(), max_length=0
    )

    @model_validator(mode="after")
    def _validate_complete_topology(self) -> "CommutativeDiagramCandidateV1":
        _require_unique((node.node_id for node in self.nodes), label="node IDs")
        _require_unique((edge.edge_id for edge in self.edges), label="edge IDs")
        _require_unique((label.label_id for label in self.labels), label="label IDs")
        _require_unique((path.path_id for path in self.paths), label="path IDs")
        _require_unique(
            (relation.relation_id for relation in self.relations),
            label="relation IDs",
        )
        _require_unique(
            (position.node_id for position in self.layout),
            label="layout node IDs",
        )

        node_ids = {node.node_id for node in self.nodes}
        edges_by_id = {edge.edge_id: edge for edge in self.edges}
        edge_ids = set(edges_by_id)
        paths_by_id = {path.path_id: path for path in self.paths}

        for edge in self.edges:
            if (
                edge.source_node_id not in node_ids
                or edge.target_node_id not in node_ids
            ):
                raise ValueError(f"edge {edge.edge_id} references a missing node")

        node_labels: defaultdict[str, list[DiagramLabelV1]] = defaultdict(list)
        edge_labels: defaultdict[str, list[DiagramLabelV1]] = defaultdict(list)
        for label in self.labels:
            if label.target_kind == "node":
                if label.target_id not in node_ids:
                    raise ValueError("node label references a missing target")
                node_labels[label.target_id].append(label)
            else:
                if label.target_id not in edge_ids:
                    raise ValueError("edge label references a missing target")
                edge_labels[label.target_id].append(label)

        if any(len(node_labels[node_id]) != 1 for node_id in node_ids):
            raise ValueError("every node must have exactly one label")
        if any(len(edge_labels[edge_id]) > 1 for edge_id in edge_ids):
            raise ValueError("an edge may have at most one label")

        edge_semantics = []
        for edge in self.edges:
            source = edge.source_node_id
            target = edge.target_node_id
            if edge.direction == "bidirectional" and source > target:
                source, target = target, source
            edge_semantics.append(
                (
                    source,
                    target,
                    edge.direction,
                    (
                        edge_labels[edge.edge_id][0].text
                        if edge_labels[edge.edge_id]
                        else ""
                    ),
                )
            )
        if len(edge_semantics) != len(set(edge_semantics)):
            raise ValueError("parallel edges require distinct semantic labels")

        traversals = {
            path.path_id: _traverse_path(path, edges_by_id) for path in self.paths
        }
        for path in self.paths:
            if path.start_node_id not in node_ids or path.end_node_id not in node_ids:
                raise ValueError(f"path {path.path_id} references a missing node")

        for relation in self.relations:
            _require_unique(relation.path_ids, label="relation path IDs")
            try:
                relation_paths = [paths_by_id[path_id] for path_id in relation.path_ids]
            except KeyError as exc:
                raise ValueError(
                    f"relation {relation.relation_id} references missing path {exc.args[0]}"
                ) from exc
            endpoints = {
                (path.start_node_id, path.end_node_id) for path in relation_paths
            }
            if len(endpoints) != 1:
                raise ValueError(
                    "commutative relation paths must have the same endpoints"
                )
            semantic_paths = {
                (
                    path.start_node_id,
                    path.end_node_id,
                    tuple(step[0] for step in traversals[path.path_id]),
                )
                for path in relation_paths
            }
            if len(semantic_paths) != len(relation_paths):
                raise ValueError(
                    "commutative relations require distinct semantic paths"
                )

        for position in self.layout:
            if position.node_id not in node_ids:
                raise ValueError("layout references a missing node")

        ordered_node_labels = [node_labels[node.node_id][0].text for node in self.nodes]
        if _canonical_work(ordered_node_labels) > _MAX_CANONICAL_PERMUTATIONS:
            raise ValueError("canonicalization work limit exceeded")
        return self


def _edge_signature(
    edge: DiagramEdgeV1,
    mapping: Mapping[str, int],
    edge_labels: Mapping[str, str],
) -> tuple[int, int, str, str]:
    source = mapping[edge.source_node_id]
    target = mapping[edge.target_node_id]
    if edge.direction == "bidirectional" and source > target:
        source, target = target, source
    return (source, target, edge.direction, edge_labels.get(edge.edge_id, ""))


def _canonical_material_for_mapping(
    candidate: CommutativeDiagramCandidateV1,
    mapping: Mapping[str, int],
    node_labels: Mapping[str, str],
    edge_labels: Mapping[str, str],
) -> dict[str, Any]:
    edges_by_id = {edge.edge_id: edge for edge in candidate.edges}
    edge_signatures = {
        edge.edge_id: _edge_signature(edge, mapping, edge_labels)
        for edge in candidate.edges
    }
    canonical_edges = sorted(edge_signatures.values())

    path_material: dict[str, dict[str, Any]] = {}
    for path in candidate.paths:
        steps = _traverse_path(path, edges_by_id)
        path_material[path.path_id] = {
            "start": mapping[path.start_node_id],
            "end": mapping[path.end_node_id],
            "steps": [
                {
                    "edge": edge_signatures[edge_id],
                    "from": mapping[source],
                    "to": mapping[target],
                }
                for edge_id, source, target in steps
            ],
        }

    canonical_paths = sorted(
        path_material.values(), key=lambda value: canonical_json_bytes(value)
    )
    canonical_relations = []
    for relation in candidate.relations:
        relation_paths = sorted(
            (path_material[path_id] for path_id in relation.path_ids),
            key=lambda value: canonical_json_bytes(value),
        )
        canonical_relations.append({"paths": relation_paths})
    canonical_relations.sort(key=lambda value: canonical_json_bytes(value))

    labels_by_index = [""] * len(mapping)
    for node_id, index in mapping.items():
        labels_by_index[index] = node_labels[node_id]
    return {
        "contract_kind": candidate.contract_kind,
        "nodes": labels_by_index,
        "edges": canonical_edges,
        "paths": canonical_paths,
        "relations": canonical_relations,
    }


def _canonicalize(
    candidate: CommutativeDiagramCandidateV1,
) -> tuple[dict[str, Any], dict[str, int]]:
    node_labels = {
        label.target_id: label.text
        for label in candidate.labels
        if label.target_kind == "node"
    }
    edge_labels = {
        label.target_id: label.text
        for label in candidate.labels
        if label.target_kind == "edge"
    }
    groups: defaultdict[str, list[str]] = defaultdict(list)
    for node in candidate.nodes:
        groups[node_labels[node.node_id]].append(node.node_id)
    ordered_groups = [tuple(sorted(groups[label])) for label in sorted(groups)]

    best_bytes: bytes | None = None
    best_material: dict[str, Any] | None = None
    best_mapping: dict[str, int] | None = None
    permutation_groups = [tuple(permutations(group)) for group in ordered_groups]
    for selected_groups in product(*permutation_groups):
        ordered_node_ids = tuple(
            node_id for group in selected_groups for node_id in group
        )
        mapping = {node_id: index for index, node_id in enumerate(ordered_node_ids)}
        material = _canonical_material_for_mapping(
            candidate, mapping, node_labels, edge_labels
        )
        encoded = canonical_json_bytes(material)
        if best_bytes is None or encoded < best_bytes:
            best_bytes = encoded
            best_material = material
            best_mapping = mapping

    if best_material is None or best_mapping is None:
        raise ValueError("canonicalization produced no graph identity")
    return best_material, best_mapping


def canonical_commutative_diagram_bytes(value: Any) -> bytes:
    """Return layout- and identifier-independent canonical graph bytes."""
    candidate = CommutativeDiagramCandidateV1.model_validate(value)
    material, _ = _canonicalize(candidate)
    return canonical_json_bytes(material)


def canonical_commutative_diagram_sha256(value: Any) -> str:
    """Return the canonical graph digest for a valid candidate."""
    return hashlib.sha256(canonical_commutative_diagram_bytes(value)).hexdigest()


class VerifiedCommutativeDiagramV1(CommutativeDiagramCandidateV1):
    """A complete graph whose topology and canonical digest have been verified."""

    canonical_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_canonical_digest(self) -> "VerifiedCommutativeDiagramV1":
        candidate = CommutativeDiagramCandidateV1.model_validate(
            self.model_dump(mode="json", exclude={"canonical_sha256"})
        )
        expected = canonical_commutative_diagram_sha256(candidate)
        if self.canonical_sha256 != expected:
            raise ValueError("canonical_sha256 does not match the verified graph")
        return self


CommutativeDiagramContract: TypeAlias = Annotated[
    VerifiedCommutativeDiagramV1,
    Field(discriminator="contract_kind"),
]
CommutativeDiagramContractAdapter = TypeAdapter(CommutativeDiagramContract)


def verify_commutative_diagram(value: Any) -> VerifiedCommutativeDiagramV1:
    """Validate topology and return a digest-bound immutable graph contract."""
    if isinstance(value, VerifiedCommutativeDiagramV1):
        return VerifiedCommutativeDiagramV1.model_validate(value)
    if isinstance(value, Mapping) and "canonical_sha256" in value:
        return VerifiedCommutativeDiagramV1.model_validate(value)
    candidate = CommutativeDiagramCandidateV1.model_validate(value)
    digest = canonical_commutative_diagram_sha256(candidate)
    return VerifiedCommutativeDiagramV1.model_validate(
        {**candidate.model_dump(mode="json"), "canonical_sha256": digest}
    )


class AccessibleDiagramDescriptionV1(_FrozenContractModel):
    """Structured screen-reader content derived from one verified graph."""

    description_kind: Literal["commutative_diagram_description_v1"]
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    summary: str = Field(min_length=1, max_length=1_024)
    objects: tuple[str, ...] = Field(max_length=16)
    arrows: tuple[str, ...] = Field(max_length=64)
    paths: tuple[str, ...] = Field(max_length=64)
    relations: tuple[str, ...] = Field(max_length=32)
    semantic_inventory: tuple[str, ...] = Field(max_length=176)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else plural or singular + "s"


def _description_parts(
    contract: VerifiedCommutativeDiagramV1,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    _, mapping = _canonicalize(contract)
    node_labels = {
        label.target_id: label.text
        for label in contract.labels
        if label.target_kind == "node"
    }
    edge_labels = {
        label.target_id: label.text
        for label in contract.labels
        if label.target_kind == "edge"
    }
    nodes_by_index = sorted(contract.nodes, key=lambda node: mapping[node.node_id])
    object_text = tuple(
        f"Object {index + 1}: {node_labels[node.node_id]}."
        for index, node in enumerate(nodes_by_index)
    )
    object_tokens = tuple(
        canonical_json_bytes(
            {"kind": "object", "index": index, "label": node_labels[node.node_id]}
        ).decode("ascii")
        for index, node in enumerate(nodes_by_index)
    )

    ordered_edges = sorted(
        contract.edges,
        key=lambda edge: _edge_signature(edge, mapping, edge_labels),
    )
    arrow_text_items: list[str] = []
    arrow_tokens: list[str] = []
    for edge in ordered_edges:
        source_label = node_labels[edge.source_node_id]
        target_label = node_labels[edge.target_node_id]
        label = edge_labels.get(edge.edge_id)
        label_phrase = f" {label}" if label else ""
        if edge.direction == "bidirectional":
            text = (
                f"Bidirectional arrow{label_phrase}: {source_label} and "
                f"{target_label}."
            )
        else:
            text = f"Arrow{label_phrase}: {source_label} to {target_label}."
        arrow_text_items.append(text)
        arrow_tokens.append(
            canonical_json_bytes(
                {
                    "kind": "arrow",
                    "source": mapping[edge.source_node_id],
                    "target": mapping[edge.target_node_id],
                    "direction": edge.direction,
                    "label": label or "",
                }
            ).decode("ascii")
        )

    edges_by_id = {edge.edge_id: edge for edge in contract.edges}
    path_records: list[tuple[bytes, str, str, str]] = []
    for path in contract.paths:
        steps = _traverse_path(path, edges_by_id)
        step_labels = [
            edge_labels.get(edge_id) or "unlabeled arrow" for edge_id in path.edge_ids
        ]
        route = "identity path" if not step_labels else " then ".join(step_labels)
        text = (
            f"Path from {node_labels[path.start_node_id]} to "
            f"{node_labels[path.end_node_id]} via {route}."
        )
        token = canonical_json_bytes(
            {
                "kind": "path",
                "start": mapping[path.start_node_id],
                "end": mapping[path.end_node_id],
                "steps": [
                    {
                        "arrow": _edge_signature(
                            edges_by_id[edge_id], mapping, edge_labels
                        ),
                        "from": mapping[source],
                        "to": mapping[target],
                    }
                    for edge_id, source, target in steps
                ],
            }
        ).decode("ascii")
        path_records.append((token.encode("ascii"), path.path_id, text, token))
    path_records.sort(key=lambda record: record[0])
    path_numbers = {
        path_id: index + 1 for index, (_, path_id, _, _) in enumerate(path_records)
    }
    path_text = tuple(
        f"Path {index + 1}: {record[2]}" for index, record in enumerate(path_records)
    )
    path_tokens = tuple(record[3] for record in path_records)

    relation_records: list[tuple[bytes, str, str]] = []
    for relation in contract.relations:
        numbers = sorted(path_numbers[path_id] for path_id in relation.path_ids)
        text = (
            "Declared equal paths: "
            + ", ".join(f"Path {number}" for number in numbers)
            + "."
        )
        token = canonical_json_bytes(
            {"kind": "commutativity", "paths": numbers}
        ).decode("ascii")
        relation_records.append((token.encode("ascii"), text, token))
    relation_records.sort(key=lambda record: record[0])
    relation_text = tuple(record[1] for record in relation_records)
    relation_tokens = tuple(record[2] for record in relation_records)

    return (
        object_text,
        tuple(arrow_text_items),
        path_text,
        relation_text,
        object_tokens + tuple(arrow_tokens) + path_tokens + relation_tokens,
    )


def describe_commutative_diagram(value: Any) -> AccessibleDiagramDescriptionV1:
    """Derive a deterministic structured description from a verified graph."""
    contract = verify_commutative_diagram(value)
    objects, arrows, paths, relations, inventory = _description_parts(contract)
    summary = (
        f"Commutative diagram with {len(objects)} {_plural(len(objects), 'object')}, "
        f"{len(arrows)} {_plural(len(arrows), 'arrow')}, "
        f"{len(paths)} {_plural(len(paths), 'path')}, and "
        f"{len(relations)} declared commutativity "
        f"{_plural(len(relations), 'relation')}."
    )
    return AccessibleDiagramDescriptionV1(
        description_kind="commutative_diagram_description_v1",
        graph_sha256=contract.canonical_sha256,
        summary=summary,
        objects=objects,
        arrows=arrows,
        paths=paths,
        relations=relations,
        semantic_inventory=inventory,
    )


def _render_list(items: Sequence[str], tokens: Sequence[str]) -> str:
    return "".join(
        '<li data-semantic-token="'
        + escape(token, quote=True)
        + '">'
        + escape(item)
        + "</li>"
        for item, token in zip(items, tokens)
    )


def render_commutative_diagram_html(value: Any) -> str:
    """Render a semantic text representation from the verified graph."""
    description = describe_commutative_diagram(value)
    object_end = len(description.objects)
    arrow_end = object_end + len(description.arrows)
    path_end = arrow_end + len(description.paths)
    object_tokens = description.semantic_inventory[:object_end]
    arrow_tokens = description.semantic_inventory[object_end:arrow_end]
    path_tokens = description.semantic_inventory[arrow_end:path_end]
    relation_tokens = description.semantic_inventory[path_end:]
    caption_id = f"commutative-diagram-{description.graph_sha256[:16]}"
    return (
        '<figure class="commutative-diagram" aria-labelledby="'
        + caption_id
        + '" data-graph-sha256="'
        + description.graph_sha256
        + '"><figcaption id="'
        + caption_id
        + '">'
        + escape(description.summary)
        + "</figcaption><section><h3>Objects</h3><ol>"
        + _render_list(description.objects, object_tokens)
        + "</ol></section><section><h3>Arrows</h3><ol>"
        + _render_list(description.arrows, arrow_tokens)
        + "</ol></section><section><h3>Paths</h3><ol>"
        + _render_list(description.paths, path_tokens)
        + "</ol></section><section><h3>Declared commutativity</h3><ol>"
        + _render_list(description.relations, relation_tokens)
        + "</ol></section></figure>"
    )


__all__ = [
    "AccessibleDiagramDescriptionV1",
    "CommutativeDiagramCandidateV1",
    "CommutativeDiagramContract",
    "CommutativeDiagramContractAdapter",
    "CommutativityRelationV1",
    "CompositionPathV1",
    "DiagramEdgeV1",
    "DiagramLabelV1",
    "DiagramNodePositionV1",
    "DiagramNodeV1",
    "UnresolvedDiagramCrossingV1",
    "VerifiedCommutativeDiagramV1",
    "canonical_commutative_diagram_bytes",
    "canonical_commutative_diagram_sha256",
    "describe_commutative_diagram",
    "render_commutative_diagram_html",
    "verify_commutative_diagram",
]
