"""Executable contract for bounded commutative-diagram semantics."""

from __future__ import annotations

import ast
from copy import deepcopy
from html.parser import HTMLParser
from itertools import permutations
from pathlib import Path

import pytest
from pydantic import ValidationError


def _triangle() -> dict[str, object]:
    return {
        "contract_kind": "commutative_diagram_v1",
        "nodes": [
            {"node_id": "a"},
            {"node_id": "b"},
            {"node_id": "c"},
        ],
        "edges": [
            {
                "edge_id": "f",
                "source_node_id": "a",
                "target_node_id": "b",
                "direction": "directed",
            },
            {
                "edge_id": "g",
                "source_node_id": "b",
                "target_node_id": "c",
                "direction": "directed",
            },
            {
                "edge_id": "h",
                "source_node_id": "a",
                "target_node_id": "c",
                "direction": "directed",
            },
        ],
        "labels": [
            {
                "label_id": "label-a",
                "text": "A",
                "target_kind": "node",
                "target_id": "a",
            },
            {
                "label_id": "label-b",
                "text": "B",
                "target_kind": "node",
                "target_id": "b",
            },
            {
                "label_id": "label-c",
                "text": "C",
                "target_kind": "node",
                "target_id": "c",
            },
            {
                "label_id": "label-f",
                "text": "f",
                "target_kind": "edge",
                "target_id": "f",
            },
            {
                "label_id": "label-g",
                "text": "g",
                "target_kind": "edge",
                "target_id": "g",
            },
            {
                "label_id": "label-h",
                "text": "h",
                "target_kind": "edge",
                "target_id": "h",
            },
        ],
        "paths": [
            {
                "path_id": "direct",
                "start_node_id": "a",
                "end_node_id": "c",
                "edge_ids": ["h"],
            },
            {
                "path_id": "composed",
                "start_node_id": "a",
                "end_node_id": "c",
                "edge_ids": ["f", "g"],
            },
        ],
        "relations": [
            {
                "relation_id": "triangle-commutes",
                "path_ids": ["direct", "composed"],
            }
        ],
        "layout": [
            {"node_id": "a", "x": 0.0, "y": 0.0},
            {"node_id": "b", "x": 1.0, "y": 0.0},
            {"node_id": "c", "x": 1.0, "y": 1.0},
        ],
        "unresolved_crossings": [],
    }


def _square() -> dict[str, object]:
    value = _triangle()
    value["nodes"] = [
        {"node_id": "a"},
        {"node_id": "b"},
        {"node_id": "c"},
        {"node_id": "d"},
    ]
    value["edges"] = [
        {
            "edge_id": "top",
            "source_node_id": "a",
            "target_node_id": "b",
            "direction": "directed",
        },
        {
            "edge_id": "right",
            "source_node_id": "b",
            "target_node_id": "d",
            "direction": "directed",
        },
        {
            "edge_id": "left",
            "source_node_id": "a",
            "target_node_id": "c",
            "direction": "directed",
        },
        {
            "edge_id": "bottom",
            "source_node_id": "c",
            "target_node_id": "d",
            "direction": "directed",
        },
    ]
    value["labels"] = [
        {
            "label_id": f"label-{node}",
            "text": node.upper(),
            "target_kind": "node",
            "target_id": node,
        }
        for node in "abcd"
    ] + [
        {
            "label_id": f"label-{edge}",
            "text": edge,
            "target_kind": "edge",
            "target_id": edge,
        }
        for edge in ("top", "right", "left", "bottom")
    ]
    value["paths"] = [
        {
            "path_id": "upper",
            "start_node_id": "a",
            "end_node_id": "d",
            "edge_ids": ["top", "right"],
        },
        {
            "path_id": "lower",
            "start_node_id": "a",
            "end_node_id": "d",
            "edge_ids": ["left", "bottom"],
        },
    ]
    value["relations"] = [
        {"relation_id": "square-commutes", "path_ids": ["upper", "lower"]}
    ]
    value["layout"] = [
        {"node_id": "a", "x": 0.0, "y": 0.0},
        {"node_id": "b", "x": 1.0, "y": 0.0},
        {"node_id": "c", "x": 0.0, "y": 1.0},
        {"node_id": "d", "x": 1.0, "y": 1.0},
    ]
    return value


def _parallel_arrows() -> dict[str, object]:
    return {
        "contract_kind": "commutative_diagram_v1",
        "nodes": [{"node_id": "a"}, {"node_id": "b"}],
        "edges": [
            {
                "edge_id": "f",
                "source_node_id": "a",
                "target_node_id": "b",
                "direction": "directed",
            },
            {
                "edge_id": "g",
                "source_node_id": "a",
                "target_node_id": "b",
                "direction": "directed",
            },
        ],
        "labels": [
            {
                "label_id": "la",
                "text": "A",
                "target_kind": "node",
                "target_id": "a",
            },
            {
                "label_id": "lb",
                "text": "B",
                "target_kind": "node",
                "target_id": "b",
            },
            {
                "label_id": "lf",
                "text": "f",
                "target_kind": "edge",
                "target_id": "f",
            },
            {
                "label_id": "lg",
                "text": "g",
                "target_kind": "edge",
                "target_id": "g",
            },
        ],
        "paths": [
            {
                "path_id": "via-f",
                "start_node_id": "a",
                "end_node_id": "b",
                "edge_ids": ["f"],
            },
            {
                "path_id": "via-g",
                "start_node_id": "a",
                "end_node_id": "b",
                "edge_ids": ["g"],
            },
        ],
        "relations": [
            {"relation_id": "parallel-equal", "path_ids": ["via-f", "via-g"]}
        ],
        "layout": [],
        "unresolved_crossings": [],
    }


def _rename_ids(value: dict[str, object]) -> dict[str, object]:
    renamed = deepcopy(value)
    node_ids = {"a": "object-9", "b": "object-3", "c": "object-7"}
    edge_ids = {"f": "arrow-z", "g": "arrow-x", "h": "arrow-y"}
    path_ids = {"direct": "path-z", "composed": "path-x"}

    for node in renamed["nodes"]:
        node["node_id"] = node_ids[node["node_id"]]
    for edge in renamed["edges"]:
        edge["edge_id"] = edge_ids[edge["edge_id"]]
        edge["source_node_id"] = node_ids[edge["source_node_id"]]
        edge["target_node_id"] = node_ids[edge["target_node_id"]]
    for label in renamed["labels"]:
        label["label_id"] = "renamed-" + label["label_id"]
        if label["target_kind"] == "node":
            label["target_id"] = node_ids[label["target_id"]]
        else:
            label["target_id"] = edge_ids[label["target_id"]]
    for path in renamed["paths"]:
        path["path_id"] = path_ids[path["path_id"]]
        path["start_node_id"] = node_ids[path["start_node_id"]]
        path["end_node_id"] = node_ids[path["end_node_id"]]
        path["edge_ids"] = [edge_ids[item] for item in path["edge_ids"]]
    for relation in renamed["relations"]:
        relation["relation_id"] = "renamed-relation"
        relation["path_ids"] = [path_ids[item] for item in relation["path_ids"]]
    for position in renamed["layout"]:
        position["node_id"] = node_ids[position["node_id"]]
    return renamed


def test_public_contract_is_exact_frozen_bounded_and_discriminated():
    from src.education.commutative_diagram import (
        CommutativeDiagramContractAdapter,
        VerifiedCommutativeDiagramV1,
        verify_commutative_diagram,
    )

    contract = verify_commutative_diagram(_triangle())

    assert isinstance(contract, VerifiedCommutativeDiagramV1)
    assert contract.model_config["extra"] == "forbid"
    assert contract.model_config["frozen"] is True
    assert CommutativeDiagramContractAdapter.json_schema()["discriminator"] == {
        "mapping": {"commutative_diagram_v1": "#/$defs/VerifiedCommutativeDiagramV1"},
        "propertyName": "contract_kind",
    }
    with pytest.raises(ValidationError):
        contract.nodes[0].node_id = "changed"
    with pytest.raises(ValidationError):
        verify_commutative_diagram(
            {**_triangle(), "provider_payload": {"active": True}}
        )


def test_directed_and_bidirectional_edges_have_exact_traversal_semantics():
    from src.education.commutative_diagram import verify_commutative_diagram

    bidirectional = _parallel_arrows()
    bidirectional["edges"][1]["direction"] = "bidirectional"
    bidirectional["paths"][1] = {
        "path_id": "reverse-g",
        "start_node_id": "b",
        "end_node_id": "a",
        "edge_ids": ["g"],
    }
    bidirectional["relations"] = []
    assert verify_commutative_diagram(bidirectional)

    directed_reverse = deepcopy(bidirectional)
    directed_reverse["edges"][1]["direction"] = "directed"
    with pytest.raises(ValidationError, match="path traversal"):
        verify_commutative_diagram(directed_reverse)

    bidirectional_loop = _parallel_arrows()
    bidirectional_loop["edges"][0].update(
        {"source_node_id": "a", "target_node_id": "a", "direction": "bidirectional"}
    )
    with pytest.raises(ValidationError, match="bidirectional self-loops"):
        verify_commutative_diagram(bidirectional_loop)

    duplicate_bidirectional = _parallel_arrows()
    duplicate_bidirectional["edges"][0]["direction"] = "bidirectional"
    duplicate_bidirectional["edges"][1].update(
        {
            "source_node_id": "b",
            "target_node_id": "a",
            "direction": "bidirectional",
        }
    )
    duplicate_bidirectional["labels"][3]["text"] = "f"
    with pytest.raises(ValidationError, match="distinct semantic labels"):
        verify_commutative_diagram(duplicate_bidirectional)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["paths"][1].update(edge_ids=["g", "f"]),
        lambda value: value["paths"][1].update(edge_ids=["f", "missing"]),
        lambda value: value["paths"][1].update(end_node_id="b"),
    ],
)
def test_composition_paths_must_be_contiguous_ordered_traversals(mutation):
    from src.education.commutative_diagram import verify_commutative_diagram

    value = _triangle()
    mutation(value)
    with pytest.raises(ValidationError):
        verify_commutative_diagram(value)


def test_relations_require_distinct_paths_with_identical_endpoints():
    from src.education.commutative_diagram import verify_commutative_diagram

    one_path = _triangle()
    one_path["relations"][0]["path_ids"] = ["direct"]
    with pytest.raises(ValidationError):
        verify_commutative_diagram(one_path)

    duplicate_path = _triangle()
    duplicate_path["paths"][1]["edge_ids"] = ["h"]
    with pytest.raises(ValidationError, match="distinct semantic paths"):
        verify_commutative_diagram(duplicate_path)

    wrong_endpoint = _triangle()
    wrong_endpoint["paths"][1]["end_node_id"] = "b"
    wrong_endpoint["paths"][1]["edge_ids"] = ["f"]
    with pytest.raises(ValidationError, match="same endpoints"):
        verify_commutative_diagram(wrong_endpoint)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["edges"][0].update(source_node_id="missing"),
        lambda value: value["paths"][0].update(edge_ids=["missing"]),
        lambda value: value["relations"][0].update(path_ids=["direct", "missing"]),
        lambda value: value["nodes"].append({"node_id": "a"}),
        lambda value: value["edges"].append(deepcopy(value["edges"][0])),
    ],
)
def test_incomplete_or_duplicate_topology_is_rejected(mutation):
    from src.education.commutative_diagram import verify_commutative_diagram

    value = _triangle()
    mutation(value)
    with pytest.raises(ValidationError):
        verify_commutative_diagram(value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["labels"][0].update(target_id="missing"),
        lambda value: value["labels"].append(
            {
                "label_id": "duplicate-node-label",
                "text": "other A",
                "target_kind": "node",
                "target_id": "a",
            }
        ),
        lambda value: value["labels"].pop(0),
        lambda value: value["labels"].append(
            {
                "label_id": "duplicate-edge-label",
                "text": "other f",
                "target_kind": "edge",
                "target_id": "f",
            }
        ),
    ],
)
def test_labels_are_attached_exactly_once_to_existing_targets(mutation):
    from src.education.commutative_diagram import verify_commutative_diagram

    value = _triangle()
    mutation(value)
    with pytest.raises(ValidationError, match="label"):
        verify_commutative_diagram(value)


def test_label_layout_crossing_and_reference_payloads_are_passive_and_bounded():
    from src.education.commutative_diagram import (
        UnresolvedDiagramCrossingV1,
        verify_commutative_diagram,
    )

    whitespace_label = _triangle()
    whitespace_label["labels"][0]["text"] = " A "
    with pytest.raises(ValidationError, match="trimmed printable"):
        verify_commutative_diagram(whitespace_label)

    non_finite_layout = _triangle()
    non_finite_layout["layout"][0]["x"] = float("nan")
    with pytest.raises(ValidationError, match="bounded finite"):
        verify_commutative_diagram(non_finite_layout)

    with pytest.raises(ValidationError, match="two different edges"):
        UnresolvedDiagramCrossingV1.model_validate(
            {"crossing_id": "cross-1", "edge_ids": ["f", "f"]}
        )

    missing_edge_label_target = _triangle()
    missing_edge_label_target["labels"][3]["target_id"] = "missing"
    with pytest.raises(ValidationError, match="edge label"):
        verify_commutative_diagram(missing_edge_label_target)

    missing_path_node = _triangle()
    missing_path_node["paths"][0].update(
        {"start_node_id": "missing", "end_node_id": "missing", "edge_ids": []}
    )
    with pytest.raises(ValidationError, match="path direct references a missing node"):
        verify_commutative_diagram(missing_path_node)

    missing_layout_node = _triangle()
    missing_layout_node["layout"][0]["node_id"] = "missing"
    with pytest.raises(ValidationError, match="layout references a missing node"):
        verify_commutative_diagram(missing_layout_node)


def test_unresolved_crossings_higher_cells_and_reserved_kinds_fail_closed():
    from src.education.commutative_diagram import verify_commutative_diagram

    crossing = _triangle()
    crossing["unresolved_crossings"] = [
        {"crossing_id": "cross-1", "edge_ids": ["f", "h"]}
    ]
    with pytest.raises(ValidationError):
        verify_commutative_diagram(crossing)

    with pytest.raises(ValidationError):
        verify_commutative_diagram({**_triangle(), "higher_cells": [{"dimension": 2}]})
    with pytest.raises(ValidationError):
        verify_commutative_diagram(
            {**_triangle(), "contract_kind": "reserved_diagram_v2"}
        )


def test_canonical_identity_ignores_collection_order():
    from src.education.commutative_diagram import verify_commutative_diagram

    reordered = deepcopy(_triangle())
    for field in ("nodes", "edges", "labels", "paths", "relations", "layout"):
        reordered[field].reverse()
    reordered["relations"][0]["path_ids"].reverse()

    assert (
        verify_commutative_diagram(_triangle()).canonical_sha256
        == verify_commutative_diagram(reordered).canonical_sha256
    )


def test_canonical_identity_is_exhaustive_over_each_collection_permutation():
    from src.education.commutative_diagram import verify_commutative_diagram

    baseline = verify_commutative_diagram(_triangle()).canonical_sha256
    for field in ("nodes", "edges", "labels", "paths", "relations", "layout"):
        original = _triangle()
        for ordering in permutations(original[field]):
            candidate = _triangle()
            candidate[field] = list(ordering)
            assert verify_commutative_diagram(candidate).canonical_sha256 == baseline


def test_canonical_identity_ignores_layout_and_incidental_ids():
    from src.education.commutative_diagram import verify_commutative_diagram

    moved = deepcopy(_triangle())
    moved["layout"] = [
        {"node_id": "a", "x": 900.0, "y": -200.0},
        {"node_id": "b", "x": -4.0, "y": 12.0},
        {"node_id": "c", "x": 0.125, "y": 999.0},
    ]

    original = verify_commutative_diagram(_triangle())
    assert (
        original.canonical_sha256 == verify_commutative_diagram(moved).canonical_sha256
    )
    assert (
        original.canonical_sha256
        == verify_commutative_diagram(_rename_ids(_triangle())).canonical_sha256
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["labels"][0].update(text="A prime"),
        lambda value: value["labels"][3].update(text="phi"),
        lambda value: value["edges"][2].update(source_node_id="b"),
        lambda value: value["edges"][0].update(direction="bidirectional"),
        lambda value: value["paths"][1].update(edge_ids=["h"]),
        lambda value: value.update(relations=[]),
    ],
)
def test_material_semantic_mutations_change_digest_or_are_rejected(mutation):
    from src.education.commutative_diagram import verify_commutative_diagram

    original = verify_commutative_diagram(_triangle()).canonical_sha256
    changed = _triangle()
    mutation(changed)
    try:
        changed_digest = verify_commutative_diagram(changed).canonical_sha256
    except ValidationError:
        return
    assert changed_digest != original


def test_canonicalization_has_a_deterministic_symmetry_work_bound():
    from src.education.commutative_diagram import verify_commutative_diagram

    value = _parallel_arrows()
    value["nodes"] = [{"node_id": f"n{index}"} for index in range(7)]
    value["edges"] = [
        {
            "edge_id": f"loop-{index}",
            "source_node_id": f"n{index}",
            "target_node_id": f"n{index}",
            "direction": "directed",
        }
        for index in range(7)
    ]
    value["labels"] = [
        {
            "label_id": f"node-label-{index}",
            "text": "X",
            "target_kind": "node",
            "target_id": f"n{index}",
        }
        for index in range(7)
    ]
    value["paths"] = [
        {
            "path_id": "identity",
            "start_node_id": "n0",
            "end_node_id": "n0",
            "edge_ids": [],
        },
        {
            "path_id": "loop",
            "start_node_id": "n0",
            "end_node_id": "n0",
            "edge_ids": ["loop-0"],
        },
    ]
    value["relations"] = [
        {"relation_id": "loop-is-identity", "path_ids": ["identity", "loop"]}
    ]
    value["layout"] = []

    with pytest.raises(ValidationError, match="canonicalization work limit"):
        verify_commutative_diagram(value)


def test_triangle_square_parallel_arrows_and_directed_loops_are_supported():
    from src.education.commutative_diagram import verify_commutative_diagram

    assert verify_commutative_diagram(_triangle())
    assert verify_commutative_diagram(_square())
    parallel = verify_commutative_diagram(_parallel_arrows())
    assert {label.text for label in parallel.labels if label.target_kind == "edge"} == {
        "f",
        "g",
    }

    loop = _parallel_arrows()
    loop["edges"] = [
        {
            "edge_id": "loop",
            "source_node_id": "a",
            "target_node_id": "a",
            "direction": "directed",
        }
    ]
    loop["labels"] = [
        label for label in loop["labels"] if label["target_kind"] == "node"
    ] + [
        {
            "label_id": "loop-label",
            "text": "u",
            "target_kind": "edge",
            "target_id": "loop",
        }
    ]
    loop["paths"] = [
        {
            "path_id": "identity",
            "start_node_id": "a",
            "end_node_id": "a",
            "edge_ids": [],
        },
        {
            "path_id": "via-loop",
            "start_node_id": "a",
            "end_node_id": "a",
            "edge_ids": ["loop"],
        },
    ]
    loop["relations"] = [
        {"relation_id": "loop-relation", "path_ids": ["identity", "via-loop"]}
    ]
    assert verify_commutative_diagram(loop)


def test_structured_description_covers_the_verified_semantic_inventory():
    from src.education.commutative_diagram import describe_commutative_diagram

    description = describe_commutative_diagram(_triangle())

    assert description.summary == (
        "Commutative diagram with 3 objects, 3 arrows, 2 paths, and "
        "1 declared commutativity relation."
    )
    assert len(description.objects) == 3
    assert len(description.arrows) == 3
    assert len(description.paths) == 2
    assert len(description.relations) == 1
    assert all(description.semantic_inventory)


class _SemanticTokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if "data-semantic-token" in attributes:
            self.tokens.append(attributes["data-semantic-token"] or "")


def test_rendered_representation_is_semantic_deterministic_and_escaped():
    from src.education.commutative_diagram import render_commutative_diagram_html

    value = _triangle()
    value["labels"][0]["text"] = '<A & "source">'
    first = render_commutative_diagram_html(value)
    second = render_commutative_diagram_html(value)

    assert first == second
    assert '<figure class="commutative-diagram" aria-labelledby=' in first
    assert "<figcaption" in first
    assert "<h3>Objects</h3>" in first
    assert "<h3>Arrows</h3>" in first
    assert "<h3>Declared commutativity</h3>" in first
    assert '<A & "source">' not in first
    assert "&lt;A &amp; &quot;source&quot;&gt;" in first

    bidirectional = _parallel_arrows()
    bidirectional["edges"][1].update(
        {
            "source_node_id": "b",
            "target_node_id": "a",
            "direction": "bidirectional",
        }
    )
    bidirectional["relations"] = []
    bidirectional["paths"] = []
    rendered_bidirectional = render_commutative_diagram_html(bidirectional)
    assert "Bidirectional arrow g: B and A." in rendered_bidirectional


def test_structured_and_rendered_outputs_share_digest_and_inventory():
    from src.education.commutative_diagram import (
        describe_commutative_diagram,
        render_commutative_diagram_html,
    )

    description = describe_commutative_diagram(_square())
    rendered = render_commutative_diagram_html(_square())
    parser = _SemanticTokenParser()
    parser.feed(rendered)

    assert f'data-graph-sha256="{description.graph_sha256}"' in rendered
    assert tuple(parser.tokens) == description.semantic_inventory


def test_public_module_has_no_prohibited_subsystem_dependencies():
    module_path = Path("src/education/commutative_diagram.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    prohibited = ("src.api", "src.ai", "src.db", "src.services", "fitz", "httpx")
    assert not {
        module
        for module in imported
        if any(
            module == prefix or module.startswith(prefix + ".") for prefix in prohibited
        )
    }


def test_issue_232_bridge_exposes_stable_verified_type_schema_and_digest():
    from src.education.commutative_diagram import (
        CommutativeDiagramContract,
        CommutativeDiagramContractAdapter,
        VerifiedCommutativeDiagramV1,
        verify_commutative_diagram,
    )

    contract: CommutativeDiagramContract = verify_commutative_diagram(_triangle())
    assert isinstance(contract, VerifiedCommutativeDiagramV1)
    assert len(contract.canonical_sha256) == 64
    schema = CommutativeDiagramContractAdapter.json_schema()
    assert schema["discriminator"]["propertyName"] == "contract_kind"
    assert verify_commutative_diagram(contract) == contract
    assert verify_commutative_diagram(contract.model_dump(mode="json")) == contract

    forged = contract.model_dump(mode="json")
    forged["canonical_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical_sha256"):
        verify_commutative_diagram(forged)
