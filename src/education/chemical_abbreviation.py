"""Frozen v1 abbreviation evidence for expanded molecular graphs.

Abbreviations are recognition evidence only. The canonical molecular identity
remains the fully expanded, element-only graph verified by ``molecular_graph``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.education.molecular_graph import (
    VerifiedMolecularGraphV1,
    verify_molecular_graph,
)

ABBREVIATION_POLICY_VERSION = "chemical-abbreviation-v1"
SupportedChemicalAbbreviation = Literal["Me", "Et", "n-Pr", "i-Pr", "t-Bu", "Ph"]

_AtomTemplate = tuple[str, int, bool]
_BondTemplate = tuple[int, int, str]
_TEMPLATES: dict[
    str,
    tuple[tuple[_AtomTemplate, ...], tuple[_BondTemplate, ...]],
] = {
    "Me": ((("C", 3, False),), ()),
    "Et": (
        (("C", 2, False), ("C", 3, False)),
        ((0, 1, "single"),),
    ),
    "n-Pr": (
        (("C", 2, False), ("C", 2, False), ("C", 3, False)),
        ((0, 1, "single"), (1, 2, "single")),
    ),
    "i-Pr": (
        (("C", 1, False), ("C", 3, False), ("C", 3, False)),
        ((0, 1, "single"), (0, 2, "single")),
    ),
    "t-Bu": (
        (
            ("C", 0, False),
            ("C", 3, False),
            ("C", 3, False),
            ("C", 3, False),
        ),
        ((0, 1, "single"), (0, 2, "single"), (0, 3, "single")),
    ),
    "Ph": (
        (
            ("C", 0, True),
            ("C", 1, True),
            ("C", 1, True),
            ("C", 1, True),
            ("C", 1, True),
            ("C", 1, True),
        ),
        (
            (0, 1, "aromatic"),
            (1, 2, "aromatic"),
            (2, 3, "aromatic"),
            (3, 4, "aromatic"),
            (4, 5, "aromatic"),
            (5, 0, "aromatic"),
        ),
    ),
}


class ChemicalAbbreviationEvidenceV1(BaseModel):
    """One source token bound to its exact expanded graph substructure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_token: SupportedChemicalAbbreviation
    anchor_atom_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    atom_ids: tuple[str, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _exact_evidence_shape(self) -> "ChemicalAbbreviationEvidenceV1":
        expected_count = len(_TEMPLATES[self.source_token][0])
        if len(self.atom_ids) != expected_count:
            raise ValueError("abbreviation atom count does not match its template")
        if len(set(self.atom_ids)) != len(self.atom_ids):
            raise ValueError("abbreviation atom IDs must be unique")
        if self.anchor_atom_id != self.atom_ids[0]:
            raise ValueError("abbreviation anchor must be the first template atom")
        return self


def _atom_tuple(atom: Any) -> _AtomTemplate:
    if (
        atom.formal_charge != 0
        or atom.isotope is not None
        or atom.stereochemistry is not None
    ):
        raise ValueError("abbreviation atom does not match the neutral template")
    return atom.element, atom.implicit_hydrogens, atom.aromatic


def verify_chemical_abbreviations(
    graph: Any,
    evidence: Any,
) -> tuple[VerifiedMolecularGraphV1, tuple[ChemicalAbbreviationEvidenceV1, ...]]:
    """Verify bounded token evidence against one fully expanded graph."""

    verified_graph = verify_molecular_graph(graph)
    if not isinstance(evidence, (list, tuple)):
        raise ValueError("abbreviation evidence must be a bounded sequence")
    if len(evidence) > len(verified_graph.atoms):
        raise ValueError("too many abbreviation evidence records")
    verified_evidence = tuple(
        ChemicalAbbreviationEvidenceV1.model_validate(item) for item in evidence
    )

    all_evidence_atom_ids = [
        atom_id for item in verified_evidence for atom_id in item.atom_ids
    ]
    if any(count > 1 for count in Counter(all_evidence_atom_ids).values()):
        raise ValueError("abbreviation subgraphs overlap")

    atoms_by_id = {atom.atom_id: atom for atom in verified_graph.atoms}
    for item in verified_evidence:
        try:
            selected_atoms = tuple(atoms_by_id[atom_id] for atom_id in item.atom_ids)
        except KeyError as exc:
            raise ValueError("abbreviation references a missing graph atom") from exc

        atom_template, bond_template = _TEMPLATES[item.source_token]
        try:
            actual_atoms = tuple(_atom_tuple(atom) for atom in selected_atoms)
        except ValueError as exc:
            raise ValueError("abbreviation atom template mismatch") from exc
        if actual_atoms != atom_template:
            raise ValueError("abbreviation atom template mismatch")

        index_by_id = {atom_id: index for index, atom_id in enumerate(item.atom_ids)}
        internal: list[_BondTemplate] = []
        external: list[tuple[str, str, str]] = []
        for bond in verified_graph.bonds:
            first, second = bond.atom_ids
            first_inside = first in index_by_id
            second_inside = second in index_by_id
            if first_inside and second_inside:
                left, right = sorted((index_by_id[first], index_by_id[second]))
                internal.append((left, right, bond.order))
            elif first_inside or second_inside:
                inside = first if first_inside else second
                outside = second if first_inside else first
                external.append((inside, outside, bond.order))

        expected_internal = sorted(
            (min(left, right), max(left, right), order)
            for left, right, order in bond_template
        )
        if sorted(internal) != expected_internal:
            raise ValueError("abbreviation bond template mismatch")
        if (
            len(external) != 1
            or external[0][0] != item.anchor_atom_id
            or external[0][2] != "single"
        ):
            raise ValueError("abbreviation requires exactly one anchor external bond")

    return verified_graph, verified_evidence


__all__ = [
    "ABBREVIATION_POLICY_VERSION",
    "ChemicalAbbreviationEvidenceV1",
    "SupportedChemicalAbbreviation",
    "verify_chemical_abbreviations",
]
