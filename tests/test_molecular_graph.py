"""Tests for the bounded molecular-graph contract and topology verifier."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.education.chemical_formula import ELEMENT_SYMBOLS
from src.education.molecular_graph import (
    MAX_ATOMS,
    MAX_CANONICAL_PERMUTATIONS,
    AccessibleMolecularDescriptionV1,
    MolecularAtomV1,
    MolecularBondV1,
    MolecularGraphCandidateV1,
    VerifiedMolecularGraphV1,
    canonical_molecular_graph_bytes,
    canonical_molecular_graph_sha256,
    describe_molecular_graph,
    verify_molecular_graph,
)

FIXTURE_MANIFEST = Path(__file__).parent / "fixtures/molecular_graph/manifest.json"


def atom(
    atom_id: str,
    element: str,
    *,
    formal_charge: int = 0,
    isotope: int | None = None,
    implicit_hydrogens: int = 0,
    aromatic: bool = False,
    stereochemistry: str | None = None,
) -> dict[str, Any]:
    return {
        "atom_id": atom_id,
        "element": element,
        "formal_charge": formal_charge,
        "isotope": isotope,
        "implicit_hydrogens": implicit_hydrogens,
        "aromatic": aromatic,
        "stereochemistry": stereochemistry,
    }


def bond(
    bond_id: str,
    first: str,
    second: str,
    *,
    order: str = "single",
    stereochemistry: str | None = None,
) -> dict[str, Any]:
    return {
        "bond_id": bond_id,
        "atom_ids": [first, second],
        "order": order,
        "stereochemistry": stereochemistry,
    }


def graph(atoms: list[dict[str, Any]], bonds: list[dict[str, Any]]) -> dict[str, Any]:
    return {"contract_kind": "molecular_graph_v1", "atoms": atoms, "bonds": bonds}


def benzene() -> dict[str, Any]:
    atoms = [
        atom(f"c{index}", "C", implicit_hydrogens=1, aromatic=True)
        for index in range(6)
    ]
    bonds = [
        bond(f"b{index}", f"c{index}", f"c{(index + 1) % 6}", order="aromatic")
        for index in range(6)
    ]
    return graph(atoms, bonds)


def chiral_carbon(stereochemistry: str = "R") -> dict[str, Any]:
    return graph(
        [
            atom("centre", "C", stereochemistry=stereochemistry),
            atom("fluorine", "F"),
            atom("chlorine", "Cl"),
            atom("bromine", "Br"),
            atom("iodine", "I"),
        ],
        [
            bond("bf", "centre", "fluorine"),
            bond("bc", "centre", "chlorine"),
            bond("bb", "centre", "bromine"),
            bond("bi", "centre", "iodine"),
        ],
    )


def alkene(stereochemistry: str = "E") -> dict[str, Any]:
    return graph(
        [
            atom("left", "C", implicit_hydrogens=1),
            atom("right", "C", implicit_hydrogens=1),
            atom("chlorine", "Cl"),
            atom("bromine", "Br"),
        ],
        [
            bond(
                "alkene",
                "left",
                "right",
                order="double",
                stereochemistry=stereochemistry,
            ),
            bond("left-substituent", "left", "chlorine"),
            bond("right-substituent", "right", "bromine"),
        ],
    )


def test_atoms_reuse_the_exact_formula_element_vocabulary() -> None:
    assert len(ELEMENT_SYMBOLS) == 118
    for symbol in ELEMENT_SYMBOLS:
        model = MolecularAtomV1.model_validate(atom("a", symbol))
        assert model.element == symbol
    for invalid in ("Xx", "cl", "CL", "*", "R"):
        with pytest.raises(ValidationError):
            MolecularAtomV1.model_validate(atom("a", invalid))


@pytest.mark.parametrize("field", ["formal_charge", "isotope", "implicit_hydrogens"])
@pytest.mark.parametrize("invalid", [True, 1.5, "1"])
def test_atom_integers_are_strict(field: str, invalid: object) -> None:
    value = atom("a", "C")
    value[field] = invalid
    with pytest.raises(ValidationError):
        MolecularAtomV1.model_validate(value)


@pytest.mark.parametrize(
    "field",
    ["formal_charge", "isotope", "implicit_hydrogens", "aromatic", "stereochemistry"],
)
def test_atom_completeness_fields_cannot_be_omitted(field: str) -> None:
    value = atom("a", "C")
    del value[field]
    with pytest.raises(ValidationError):
        MolecularAtomV1.model_validate(value)


def test_bond_contract_is_exact_and_round_trips_all_orders() -> None:
    for order in ("single", "double", "triple", "aromatic"):
        value = bond("b", "a", "z", order=order)
        assert MolecularBondV1.model_validate(value).model_dump(mode="json") == value
    for invalid in ("unknown", 1, None):
        with pytest.raises(ValidationError):
            MolecularBondV1.model_validate(bond("b", "a", "z", order=invalid))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["atoms"].append(copy.deepcopy(value["atoms"][0])),
        lambda value: value["bonds"].append(copy.deepcopy(value["bonds"][0])),
        lambda value: value["bonds"].append(bond("self", "a", "a")),
        lambda value: value["bonds"].append(bond("missing", "a", "missing")),
        lambda value: value["bonds"].append(bond("parallel", "b", "a")),
    ],
)
def test_duplicate_dangling_self_and_parallel_topology_rejects(mutate: Any) -> None:
    value = graph([atom("a", "C"), atom("b", "O")], [bond("ab", "a", "b")])
    mutate(value)
    with pytest.raises(ValidationError):
        MolecularGraphCandidateV1.model_validate(value)


def test_disconnected_labels_reject() -> None:
    value = graph(
        [atom("a", "C"), atom("b", "O"), atom("c", "N")], [bond("ab", "a", "b")]
    )
    with pytest.raises(ValidationError, match="connected"):
        verify_molecular_graph(value)


def test_aromaticity_is_explicit_consistent_and_cycle_bearing() -> None:
    verified = verify_molecular_graph(benzene())
    assert verified.topology.component_count == 1
    assert verified.topology.cycle_rank == 1
    assert verified.topology.ring_atom_indices == (0, 1, 2, 3, 4, 5)
    assert verified.topology.ring_bond_indices == (0, 1, 2, 3, 4, 5)

    mismatched = benzene()
    mismatched["atoms"][0]["aromatic"] = False
    with pytest.raises(ValidationError, match="aromatic"):
        verify_molecular_graph(mismatched)

    acyclic = graph(
        [atom("a", "C", aromatic=True), atom("b", "C", aromatic=True)],
        [bond("ab", "a", "b", order="aromatic")],
    )
    with pytest.raises(ValidationError, match="aromatic"):
        verify_molecular_graph(acyclic)


def test_ring_membership_and_cycle_rank_are_derived_from_connectivity() -> None:
    fused = graph(
        [atom(str(index), "C") for index in range(4)],
        [
            bond("01", "0", "1"),
            bond("12", "1", "2"),
            bond("20", "2", "0"),
            bond("13", "1", "3"),
            bond("32", "3", "2"),
        ],
    )
    topology = verify_molecular_graph(fused).topology
    assert topology.component_count == 1
    assert topology.cycle_rank == 2
    assert topology.ring_atom_indices == (0, 1, 2, 3)
    assert topology.ring_bond_indices == (0, 1, 2, 3, 4)
    with pytest.raises(ValidationError):
        MolecularGraphCandidateV1.model_validate({**fused, "ring_atoms": ["0"]})


@pytest.mark.parametrize("value", [chiral_carbon("R"), chiral_carbon("S")])
def test_absolute_tetrahedral_stereo_accepts_supported_distinct_substituents(
    value: dict[str, Any],
) -> None:
    verified = verify_molecular_graph(value)
    assert any(
        atom_value.stereochemistry in {"R", "S"} for atom_value in verified.atoms
    )


def test_ambiguous_or_incomplete_tetrahedral_stereo_rejects() -> None:
    ambiguous = chiral_carbon()
    ambiguous["atoms"][4]["element"] = "Br"
    with pytest.raises(ValidationError, match="distinct"):
        verify_molecular_graph(ambiguous)

    incomplete = chiral_carbon()
    incomplete["bonds"].pop()
    incomplete["atoms"].pop()
    with pytest.raises(ValidationError, match="four substituent"):
        verify_molecular_graph(incomplete)


@pytest.mark.parametrize("stereochemistry", ["E", "Z"])
def test_absolute_double_bond_stereo_accepts_supported_substituents(
    stereochemistry: str,
) -> None:
    verified = verify_molecular_graph(alkene(stereochemistry))
    assert any(item.stereochemistry == stereochemistry for item in verified.bonds)


def test_invalid_or_ambiguous_double_bond_stereo_rejects() -> None:
    wrong_order = alkene()
    wrong_order["bonds"][0]["order"] = "single"
    with pytest.raises(ValidationError, match="double"):
        verify_molecular_graph(wrong_order)

    ambiguous = alkene()
    ambiguous["atoms"][2]["element"] = "H"
    with pytest.raises(ValidationError, match="distinct"):
        verify_molecular_graph(ambiguous)


def test_graph_and_canonical_work_bounds_reject_before_enumeration() -> None:
    too_many_atoms = graph(
        [atom(str(index), "C") for index in range(MAX_ATOMS + 1)], []
    )
    with pytest.raises(ValidationError):
        verify_molecular_graph(too_many_atoms)

    symmetric = graph(
        [atom("centre", "C")] + [atom(f"leaf{index}", "F") for index in range(7)],
        [bond(f"b{index}", "centre", f"leaf{index}") for index in range(7)],
    )
    assert 5040 > MAX_CANONICAL_PERMUTATIONS
    with pytest.raises(ValidationError, match="work limit"):
        verify_molecular_graph(symmetric)


def test_canonical_identity_ignores_ids_and_input_order() -> None:
    original = benzene()
    expected = canonical_molecular_graph_bytes(original)
    expected_digest = canonical_molecular_graph_sha256(original)
    for rotation in range(6):
        renamed = copy.deepcopy(original)
        mapping = {f"c{index}": f"atom-{(index + rotation) % 6}" for index in range(6)}
        for atom_value in renamed["atoms"]:
            atom_value["atom_id"] = mapping[atom_value["atom_id"]]
        for index, bond_value in enumerate(renamed["bonds"]):
            bond_value["bond_id"] = f"renamed-{5 - index}"
            bond_value["atom_ids"] = [
                mapping[value] for value in reversed(bond_value["atom_ids"])
            ]
        renamed["atoms"].reverse()
        renamed["bonds"] = renamed["bonds"][rotation:] + renamed["bonds"][:rotation]
        assert canonical_molecular_graph_bytes(renamed) == expected
        assert canonical_molecular_graph_sha256(renamed) == expected_digest


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["atoms"][0].update(element="N"),
        lambda value: value["atoms"][0].update(isotope=13),
        lambda value: value["atoms"][0].update(formal_charge=1),
        lambda value: value["atoms"][0].update(implicit_hydrogens=2),
        lambda value: value["atoms"][0].update(aromatic=False),
        lambda value: value["bonds"][0].update(order="double"),
    ],
)
def test_every_material_graph_mutation_changes_or_invalidates_identity(
    mutation: Any,
) -> None:
    original = benzene()
    expected = canonical_molecular_graph_sha256(original)
    changed = copy.deepcopy(original)
    mutation(changed)
    try:
        actual = canonical_molecular_graph_sha256(changed)
    except ValidationError:
        return
    assert actual != expected


def test_stereo_mutations_change_identity() -> None:
    assert canonical_molecular_graph_sha256(
        chiral_carbon("R")
    ) != canonical_molecular_graph_sha256(chiral_carbon("S"))
    assert canonical_molecular_graph_sha256(
        alkene("E")
    ) != canonical_molecular_graph_sha256(alkene("Z"))


def test_refinement_orders_mixed_optional_bond_stereo_without_type_comparison() -> None:
    value = graph(
        [
            atom("centre", "C", implicit_hydrogens=1),
            atom("left", "C", implicit_hydrogens=1),
            atom("right", "C", implicit_hydrogens=2),
            atom("chlorine", "Cl"),
        ],
        [
            bond("left-double", "centre", "left", order="double", stereochemistry="E"),
            bond("right-double", "centre", "right", order="double"),
            bond("left-substituent", "left", "chlorine"),
        ],
    )
    verified = verify_molecular_graph(value)
    assert verified.canonical_sha256 == canonical_molecular_graph_sha256(value)


def test_canonical_bytes_and_versioned_identifier_are_exact() -> None:
    methane = graph([atom("carbon", "C", implicit_hydrogens=4)], [])
    verified = verify_molecular_graph(methane)
    assert canonical_molecular_graph_bytes(methane) == (
        b'{"atoms":[{"aromatic":false,"element":"C","formal_charge":0,'
        b'"implicit_hydrogens":4,"isotope":null,"stereochemistry":null}],'
        b'"bonds":[],"contract_kind":"molecular_graph_v1"}'
    )
    assert (
        verified.graph_identifier
        == f"aelira-molecular-graph-v1:sha256:{verified.canonical_sha256}"
    )


@pytest.mark.parametrize(
    "field", ["canonical_sha256", "graph_identifier", "topology", "description"]
)
def test_derived_projection_tampering_rejects(field: str) -> None:
    verified = verify_molecular_graph(benzene())
    dumped = verified.model_dump(mode="json")
    if field == "topology":
        dumped[field]["cycle_rank"] = 0
    elif field == "description":
        dumped[field]["summary"] = "invented"
    else:
        dumped[field] = (
            "0" * 64
            if field == "canonical_sha256"
            else "aelira-molecular-graph-v1:sha256:" + "0" * 64
        )
    with pytest.raises(ValidationError):
        VerifiedMolecularGraphV1.model_validate(dumped)


def test_description_preserves_material_fields_without_inventing_chemistry() -> None:
    value = chiral_carbon()
    value["atoms"][1]["isotope"] = 19
    value["atoms"][2]["formal_charge"] = -1
    description = describe_molecular_graph(value)
    assert isinstance(description, AccessibleMolecularDescriptionV1)
    speech = " ".join(
        (
            description.summary,
            *description.atoms,
            *description.bonds,
            description.topology,
        )
    )
    for phrase in (
        "5 atoms",
        "4 bonds",
        "fluorine",
        "isotope 19",
        "formal charge minus 1",
        "absolute stereochemistry R",
        "acyclic",
    ):
        assert phrase in speech
    for invented in ("methane", "valence", "resonance", "tautomer", "plausible"):
        assert invented not in speech.lower()


def test_ring_and_double_bond_descriptions_preserve_derived_distinctions() -> None:
    aromatic_description = describe_molecular_graph(benzene())
    assert "one independent cycle" in aromatic_description.topology
    assert all("ring bond" in item for item in aromatic_description.bonds)
    assert all("aromatic" in item for item in aromatic_description.atoms)

    alkene_description = describe_molecular_graph(alkene("E"))
    assert any(
        "double" in item and "absolute stereochemistry E" in item
        for item in alkene_description.bonds
    )


def test_positive_models_round_trip_and_verified_contract_is_frozen() -> None:
    for value in (benzene(), chiral_carbon(), alkene()):
        verified = verify_molecular_graph(value)
        assert verify_molecular_graph(verified.model_dump(mode="json")) == verified
        assert verify_molecular_graph(verified) == verified
        with pytest.raises(ValidationError):
            verified.atoms = ()


def test_fixture_manifest_is_licensed_complete_and_executable() -> None:
    manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "molecular-graph-corpus-v1"
    assert manifest["license"] == "CC0-1.0"
    required_coverage = {
        "elements",
        "isotope",
        "formal_charge",
        "implicit_hydrogens",
        "aromaticity",
        "r_stereo",
        "s_stereo",
        "e_stereo",
        "z_stereo",
        "acyclic",
        "single_ring",
        "fused_cycles",
        "disconnected",
        "incomplete",
        "polymer",
        "wildcard",
        "ambiguous_stereo",
        "tamper",
        "symmetry_bound",
        "adversarial",
    }
    coverage = set(
        itertools.chain.from_iterable(case["covers"] for case in manifest["cases"])
    )
    assert required_coverage <= coverage
    for case in manifest["cases"]:
        assert case["provenance"] == "repository-authored"
        assert case["license"] == "CC0-1.0"
        if case["expect"] == "accept":
            verify_molecular_graph(case["graph"])
        else:
            with pytest.raises((ValidationError, ValueError, TypeError)):
                verify_molecular_graph(case["graph"])
