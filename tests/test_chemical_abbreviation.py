"""Contract tests for the bounded chemical-structure abbreviation vocabulary."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError


def _atom(
    atom_id: str,
    *,
    hydrogens: int,
    aromatic: bool = False,
) -> dict[str, object]:
    return {
        "atom_id": atom_id,
        "element": "C",
        "formal_charge": 0,
        "isotope": None,
        "implicit_hydrogens": hydrogens,
        "aromatic": aromatic,
        "stereochemistry": None,
    }


def _graph_for(token: str) -> tuple[dict[str, object], dict[str, object]]:
    templates = {
        "Me": ([3], []),
        "Et": ([2, 3], [(0, 1, "single")]),
        "n-Pr": ([2, 2, 3], [(0, 1, "single"), (1, 2, "single")]),
        "i-Pr": ([1, 3, 3], [(0, 1, "single"), (0, 2, "single")]),
        "t-Bu": (
            [0, 3, 3, 3],
            [(0, 1, "single"), (0, 2, "single"), (0, 3, "single")],
        ),
        "Ph": (
            [0, 1, 1, 1, 1, 1],
            [
                (0, 1, "aromatic"),
                (1, 2, "aromatic"),
                (2, 3, "aromatic"),
                (3, 4, "aromatic"),
                (4, 5, "aromatic"),
                (5, 0, "aromatic"),
            ],
        ),
    }
    hydrogens, internal_bonds = templates[token]
    atom_ids = [f"abbr-{index}" for index in range(len(hydrogens))]
    aromatic = token == "Ph"
    atoms = [_atom("outside", hydrogens=3)] + [
        _atom(atom_id, hydrogens=count, aromatic=aromatic)
        for atom_id, count in zip(atom_ids, hydrogens)
    ]
    bonds = [
        {
            "bond_id": "external",
            "atom_ids": ["outside", atom_ids[0]],
            "order": "single",
            "stereochemistry": None,
        }
    ]
    bonds.extend(
        {
            "bond_id": f"internal-{index}",
            "atom_ids": [atom_ids[left], atom_ids[right]],
            "order": order,
            "stereochemistry": None,
        }
        for index, (left, right, order) in enumerate(internal_bonds)
    )
    return (
        {"contract_kind": "molecular_graph_v1", "atoms": atoms, "bonds": bonds},
        {
            "source_token": token,
            "anchor_atom_id": atom_ids[0],
            "atom_ids": atom_ids,
        },
    )


@pytest.mark.parametrize("token", ["Me", "Et", "n-Pr", "i-Pr", "t-Bu", "Ph"])
def test_exact_v1_abbreviation_vocabulary_matches_expanded_graph(token):
    from src.education.chemical_abbreviation import (
        ABBREVIATION_POLICY_VERSION,
        ChemicalAbbreviationEvidenceV1,
        verify_chemical_abbreviations,
    )

    graph, evidence = _graph_for(token)
    verified_graph, verified_evidence = verify_chemical_abbreviations(graph, [evidence])

    assert verified_graph.contract_kind == "molecular_graph_v1"
    assert verified_evidence == (ChemicalAbbreviationEvidenceV1(**evidence),)
    assert ABBREVIATION_POLICY_VERSION == "chemical-abbreviation-v1"


@pytest.mark.parametrize("token", ["Ac", "Ts", "Bn", "Bz", "R", "*"])
def test_ambiguous_or_wildcard_tokens_are_not_in_the_wire_schema(token):
    from src.education.chemical_abbreviation import ChemicalAbbreviationEvidenceV1

    _, evidence = _graph_for("Me")
    evidence["source_token"] = token
    with pytest.raises(ValidationError):
        ChemicalAbbreviationEvidenceV1.model_validate(evidence)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda graph, evidence: evidence.update(anchor_atom_id="outside"), "anchor"),
        (lambda graph, evidence: evidence["atom_ids"].append("outside"), "atom count"),
        (
            lambda graph, evidence: graph["atoms"][1].update(implicit_hydrogens=1),
            "template",
        ),
        (
            lambda graph, evidence: graph["bonds"].append(
                {
                    "bond_id": "second-external",
                    "atom_ids": ["outside", evidence["atom_ids"][-1]],
                    "order": "single",
                    "stereochemistry": None,
                }
            ),
            "external bond",
        ),
        (
            lambda graph, evidence: graph["bonds"][0].update(order="double"),
            "external bond",
        ),
    ],
)
def test_abbreviation_evidence_rejects_wrong_template_or_attachment(mutation, message):
    from src.education.chemical_abbreviation import verify_chemical_abbreviations

    graph, evidence = _graph_for("Et")
    mutation(graph, evidence)
    with pytest.raises(ValueError, match=message):
        verify_chemical_abbreviations(graph, [evidence])


def test_abbreviation_subgraphs_cannot_overlap():
    from src.education.chemical_abbreviation import verify_chemical_abbreviations

    graph, evidence = _graph_for("Et")
    with pytest.raises(ValueError, match="overlap"):
        verify_chemical_abbreviations(graph, [evidence, deepcopy(evidence)])


def test_zero_abbreviations_preserves_the_verified_graph_identity():
    from src.education.chemical_abbreviation import verify_chemical_abbreviations
    from src.education.molecular_graph import verify_molecular_graph

    graph, _ = _graph_for("Me")
    expected = verify_molecular_graph(graph)
    verified, evidence = verify_chemical_abbreviations(graph, [])

    assert evidence == ()
    assert verified.canonical_sha256 == expected.canonical_sha256
