"""Bounded molecular-graph semantics, topology, identity, and speech."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from itertools import permutations, product
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from src.education.canonical_json import canonical_json_bytes
from src.education.chemical_formula import ELEMENT_NAMES

MAX_ATOMS = 32
MAX_BONDS = 64
MAX_CANONICAL_PERMUTATIONS = 4_096
MAX_IMPLICIT_HYDROGENS = 8
MAX_FORMAL_CHARGE = 16
MAX_ISOTOPE = 400

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GRAPH_IDENTIFIER_PATTERN = r"^aelira-molecular-graph-v1:sha256:[0-9a-f]{64}$"
_ELEMENT_SYMBOL_SET = frozenset(ELEMENT_NAMES)

Identifier = Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)]
FormalCharge = Annotated[
    int,
    Field(strict=True, ge=-MAX_FORMAL_CHARGE, le=MAX_FORMAL_CHARGE),
]
Isotope = Annotated[int, Field(strict=True, ge=1, le=MAX_ISOTOPE)]
ImplicitHydrogens = Annotated[
    int,
    Field(strict=True, ge=0, le=MAX_IMPLICIT_HYDROGENS),
]
BoundedCount = Annotated[int, Field(strict=True, ge=0, le=MAX_BONDS)]
AtomIndex = Annotated[int, Field(strict=True, ge=0, lt=MAX_ATOMS)]
BondIndex = Annotated[int, Field(strict=True, ge=0, lt=MAX_BONDS)]


class MolecularGraphRejected(ValueError):
    """A bounded refusal that carries no partial molecular semantics."""


class _FrozenContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class MolecularAtomV1(_FrozenContractModel):
    """One complete atom record in a bounded molecular graph."""

    atom_id: Identifier
    element: str = Field(min_length=1, max_length=2)
    formal_charge: FormalCharge
    isotope: Isotope | None
    implicit_hydrogens: ImplicitHydrogens
    aromatic: StrictBool
    stereochemistry: Literal["R", "S"] | None

    @field_validator("element")
    @classmethod
    def _known_element_symbol(cls, value: str) -> str:
        if value not in _ELEMENT_SYMBOL_SET:
            raise ValueError("unknown or case-invalid element symbol")
        return value


class MolecularBondV1(_FrozenContractModel):
    """One unordered molecular bond between two atom presentation IDs."""

    bond_id: Identifier
    atom_ids: tuple[Identifier, Identifier]
    order: Literal["single", "double", "triple", "aromatic"]
    stereochemistry: Literal["E", "Z"] | None

    @model_validator(mode="after")
    def _exact_bond_shape(self) -> MolecularBondV1:
        if self.atom_ids[0] == self.atom_ids[1]:
            raise ValueError("self-bonds are unsupported")
        if self.stereochemistry is not None and self.order != "double":
            raise ValueError("E/Z stereochemistry requires a double bond")
        return self


def _require_unique(values: Iterable[str], *, label: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate {label}: {', '.join(duplicates)}")


def _adjacency(
    atoms: Iterable[MolecularAtomV1],
    bonds: Iterable[MolecularBondV1],
) -> dict[str, list[tuple[str, str]]]:
    result: dict[str, list[tuple[str, str]]] = {atom.atom_id: [] for atom in atoms}
    for bond in bonds:
        first, second = bond.atom_ids
        result[first].append((second, bond.bond_id))
        result[second].append((first, bond.bond_id))
    return result


def _connected_atom_ids(
    start: str,
    adjacency: Mapping[str, list[tuple[str, str]]],
) -> set[str]:
    visited: set[str] = set()
    pending = [start]
    while pending:
        atom_id = pending.pop()
        if atom_id in visited:
            continue
        visited.add(atom_id)
        pending.extend(
            neighbor for neighbor, _ in adjacency[atom_id] if neighbor not in visited
        )
    return visited


def _ring_bond_ids(candidate: MolecularGraphCandidateV1) -> frozenset[str]:
    """Return non-bridge edges, which are exactly the edges in cycles."""
    adjacency = _adjacency(candidate.atoms, candidate.bonds)
    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    bridges: set[str] = set()
    counter = 0

    def visit(atom_id: str, incoming_bond_id: str | None) -> None:
        nonlocal counter
        discovery[atom_id] = counter
        low[atom_id] = counter
        counter += 1
        for neighbor, bond_id in adjacency[atom_id]:
            if bond_id == incoming_bond_id:
                continue
            if neighbor not in discovery:
                visit(neighbor, bond_id)
                low[atom_id] = min(low[atom_id], low[neighbor])
                if low[neighbor] > discovery[atom_id]:
                    bridges.add(bond_id)
            else:
                low[atom_id] = min(low[atom_id], discovery[neighbor])

    visit(candidate.atoms[0].atom_id, None)
    return frozenset(bond.bond_id for bond in candidate.bonds) - bridges


def _atom_material(atom: MolecularAtomV1) -> dict[str, Any]:
    return {
        "element": atom.element,
        "formal_charge": atom.formal_charge,
        "isotope": atom.isotope,
        "implicit_hydrogens": atom.implicit_hydrogens,
        "aromatic": atom.aromatic,
        "stereochemistry": atom.stereochemistry,
    }


def _neighbor_signature(
    atom: MolecularAtomV1,
    bond: MolecularBondV1 | None,
) -> bytes:
    if (
        atom.element == "H"
        and atom.formal_charge == 0
        and atom.isotope is None
        and atom.implicit_hydrogens == 0
        and not atom.aromatic
        and atom.stereochemistry is None
        and (bond is None or bond.order == "single")
    ):
        return canonical_json_bytes({"substituent": "hydrogen"})
    return canonical_json_bytes(
        {
            "bond_order": "single" if bond is None else bond.order,
            "atom": _atom_material(atom),
        }
    )


def _substituent_signatures(
    atom: MolecularAtomV1,
    *,
    excluded_atom_id: str | None,
    atoms_by_id: Mapping[str, MolecularAtomV1],
    bonds: Iterable[MolecularBondV1],
) -> list[bytes]:
    signatures: list[bytes] = []
    for bond in bonds:
        if atom.atom_id not in bond.atom_ids:
            continue
        neighbor_id = (
            bond.atom_ids[1] if bond.atom_ids[0] == atom.atom_id else bond.atom_ids[0]
        )
        if neighbor_id == excluded_atom_id:
            continue
        signatures.append(_neighbor_signature(atoms_by_id[neighbor_id], bond))
    signatures.extend(
        _neighbor_signature(
            MolecularAtomV1(
                atom_id="implicit-hydrogen",
                element="H",
                formal_charge=0,
                isotope=None,
                implicit_hydrogens=0,
                aromatic=False,
                stereochemistry=None,
            ),
            None,
        )
        for _ in range(atom.implicit_hydrogens)
    )
    return signatures


def _validate_stereochemistry(candidate: MolecularGraphCandidateV1) -> None:
    atoms_by_id = {atom.atom_id: atom for atom in candidate.atoms}
    for atom in candidate.atoms:
        if atom.stereochemistry is None:
            continue
        signatures = _substituent_signatures(
            atom,
            excluded_atom_id=None,
            atoms_by_id=atoms_by_id,
            bonds=candidate.bonds,
        )
        if atom.aromatic or atom.implicit_hydrogens > 1 or len(signatures) != 4:
            raise ValueError(
                "R/S stereochemistry requires four substituent slots at a "
                "non-aromatic atom"
            )
        if len(set(signatures)) != 4:
            raise ValueError("R/S stereochemistry requires distinct substituents")

    for bond in candidate.bonds:
        if bond.stereochemistry is None:
            continue
        for atom_id, excluded_atom_id in (
            (bond.atom_ids[0], bond.atom_ids[1]),
            (bond.atom_ids[1], bond.atom_ids[0]),
        ):
            endpoint = atoms_by_id[atom_id]
            signatures = _substituent_signatures(
                endpoint,
                excluded_atom_id=excluded_atom_id,
                atoms_by_id=atoms_by_id,
                bonds=candidate.bonds,
            )
            if endpoint.implicit_hydrogens > 1 or len(signatures) != 2:
                raise ValueError(
                    "E/Z stereochemistry requires two substituent slots at each "
                    "double-bond endpoint"
                )
            if len(set(signatures)) != 2:
                raise ValueError("E/Z stereochemistry requires distinct substituents")


class MolecularGraphCandidateV1(_FrozenContractModel):
    """A complete bounded molecular graph awaiting derived verification."""

    contract_kind: Literal["molecular_graph_v1"]
    atoms: tuple[MolecularAtomV1, ...] = Field(min_length=1, max_length=MAX_ATOMS)
    bonds: tuple[MolecularBondV1, ...] = Field(max_length=MAX_BONDS)

    @model_validator(mode="after")
    def _complete_connected_graph(self) -> MolecularGraphCandidateV1:
        _require_unique((atom.atom_id for atom in self.atoms), label="atom IDs")
        _require_unique((bond.bond_id for bond in self.bonds), label="bond IDs")
        atom_ids = {atom.atom_id for atom in self.atoms}
        endpoint_pairs: set[tuple[str, str]] = set()
        for bond in self.bonds:
            if any(atom_id not in atom_ids for atom_id in bond.atom_ids):
                raise ValueError(f"bond {bond.bond_id} references a missing atom")
            endpoint_pair = tuple(sorted(bond.atom_ids))
            if endpoint_pair in endpoint_pairs:
                raise ValueError("parallel bonds are unsupported")
            endpoint_pairs.add(endpoint_pair)

        adjacency = _adjacency(self.atoms, self.bonds)
        if _connected_atom_ids(self.atoms[0].atom_id, adjacency) != atom_ids:
            raise ValueError("molecular graphs must be connected")

        ring_bond_ids = _ring_bond_ids(self)
        atoms_by_id = {atom.atom_id: atom for atom in self.atoms}
        aromatic_incidence: Counter[str] = Counter()
        for bond in self.bonds:
            first, second = (atoms_by_id[atom_id] for atom_id in bond.atom_ids)
            if bond.order == "aromatic":
                if not first.aromatic or not second.aromatic:
                    raise ValueError("aromatic bonds require aromatic endpoints")
                if bond.bond_id not in ring_bond_ids:
                    raise ValueError("aromatic bonds must be cycle-bearing")
                aromatic_incidence.update(bond.atom_ids)
        for atom in self.atoms:
            if atom.aromatic and aromatic_incidence[atom.atom_id] < 2:
                raise ValueError(
                    "aromatic atoms require two cycle-bearing aromatic bonds"
                )

        _validate_stereochemistry(self)
        if _canonical_work(self) > MAX_CANONICAL_PERMUTATIONS:
            raise ValueError("canonicalization work limit exceeded")
        return self


class MolecularTopologyV1(_FrozenContractModel):
    """Topology derived exclusively from the canonical graph."""

    component_count: Annotated[int, Field(strict=True, ge=1, le=1)]
    cycle_rank: BoundedCount
    ring_atom_indices: tuple[AtomIndex, ...] = Field(max_length=MAX_ATOMS)
    ring_bond_indices: tuple[BondIndex, ...] = Field(max_length=MAX_BONDS)

    @field_validator("ring_atom_indices", "ring_bond_indices")
    @classmethod
    def _ordered_unique_indices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("derived topology indices must be sorted and unique")
        return value


class AccessibleMolecularDescriptionV1(_FrozenContractModel):
    """Structured screen-reader content derived from one canonical graph."""

    description_kind: Literal["molecular_graph_description_v1"]
    graph_sha256: str = Field(pattern=_SHA256_PATTERN)
    summary: str = Field(min_length=1, max_length=1_024)
    atoms: tuple[str, ...] = Field(min_length=1, max_length=MAX_ATOMS)
    bonds: tuple[str, ...] = Field(max_length=MAX_BONDS)
    topology: str = Field(min_length=1, max_length=1_024)
    semantic_inventory: tuple[str, ...] = Field(max_length=MAX_ATOMS + MAX_BONDS + 1)

    @field_validator("summary", "topology")
    @classmethod
    def _printable_text(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError("description text must be trimmed printable text")
        return value

    @field_validator("atoms", "bonds", "semantic_inventory")
    @classmethod
    def _printable_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value or value != value.strip() or not value.isprintable()
            for value in values
        ):
            raise ValueError("description items must be trimmed printable text")
        return values


def _as_candidate(value: Any) -> MolecularGraphCandidateV1:
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="json")
        value = {
            key: dumped[key]
            for key in ("contract_kind", "atoms", "bonds")
            if key in dumped
        }
    return MolecularGraphCandidateV1.model_validate(value)


def _refined_colors(candidate: MolecularGraphCandidateV1) -> dict[str, int]:
    atoms_by_id = {atom.atom_id: atom for atom in candidate.atoms}
    adjacency = _adjacency(candidate.atoms, candidate.bonds)
    bonds_by_id = {bond.bond_id: bond for bond in candidate.bonds}
    initial = {
        atom.atom_id: canonical_json_bytes(_atom_material(atom))
        for atom in candidate.atoms
    }
    palette = {
        token: index for index, token in enumerate(sorted(set(initial.values())))
    }
    colors = {atom_id: palette[token] for atom_id, token in initial.items()}
    for _ in range(len(candidate.atoms)):
        signatures: dict[str, bytes] = {}
        for atom_id in atoms_by_id:
            neighbors = sorted(
                canonical_json_bytes(
                    {
                        "order": bonds_by_id[bond_id].order,
                        "stereochemistry": bonds_by_id[bond_id].stereochemistry,
                        "neighbor_color": colors[neighbor_id],
                    }
                ).decode("ascii")
                for neighbor_id, bond_id in adjacency[atom_id]
            )
            signatures[atom_id] = canonical_json_bytes(
                {
                    "atom": _atom_material(atoms_by_id[atom_id]),
                    "color": colors[atom_id],
                    "neighbors": neighbors,
                }
            )
        palette = {
            token: index for index, token in enumerate(sorted(set(signatures.values())))
        }
        updated = {atom_id: palette[token] for atom_id, token in signatures.items()}
        if updated == colors:
            break
        colors = updated
    return colors


def _canonical_groups(candidate: MolecularGraphCandidateV1) -> list[tuple[str, ...]]:
    groups: defaultdict[int, list[str]] = defaultdict(list)
    for atom_id, color in _refined_colors(candidate).items():
        groups[color].append(atom_id)
    return [tuple(sorted(groups[color])) for color in sorted(groups)]


def _canonical_work(candidate: MolecularGraphCandidateV1) -> int:
    work = 1
    for group in _canonical_groups(candidate):
        work *= math.factorial(len(group))
        if work > MAX_CANONICAL_PERMUTATIONS:
            break
    return work


def _bond_material(
    bond: MolecularBondV1,
    mapping: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "atoms": sorted(mapping[atom_id] for atom_id in bond.atom_ids),
        "order": bond.order,
        "stereochemistry": bond.stereochemistry,
    }


def _material_for_mapping(
    candidate: MolecularGraphCandidateV1,
    mapping: Mapping[str, int],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    atoms_by_index: list[dict[str, Any] | None] = [None] * len(candidate.atoms)
    for atom in candidate.atoms:
        atoms_by_index[mapping[atom.atom_id]] = _atom_material(atom)
    bond_records = sorted(
        (
            canonical_json_bytes(_bond_material(bond, mapping)),
            bond.bond_id,
            _bond_material(bond, mapping),
        )
        for bond in candidate.bonds
    )
    return (
        {
            "contract_kind": candidate.contract_kind,
            "atoms": atoms_by_index,
            "bonds": [record[2] for record in bond_records],
        },
        tuple(record[1] for record in bond_records),
    )


def _canonicalize(
    candidate: MolecularGraphCandidateV1,
) -> tuple[dict[str, Any], dict[str, int], tuple[str, ...]]:
    groups = _canonical_groups(candidate)
    work = math.prod(math.factorial(len(group)) for group in groups)
    if work > MAX_CANONICAL_PERMUTATIONS:
        raise ValueError("canonicalization work limit exceeded")

    best_bytes: bytes | None = None
    best_material: dict[str, Any] | None = None
    best_mapping: dict[str, int] | None = None
    best_bond_order: tuple[str, ...] | None = None
    for selected_groups in product(*(permutations(group) for group in groups)):
        ordered_atom_ids = tuple(
            atom_id for group in selected_groups for atom_id in group
        )
        mapping = {atom_id: index for index, atom_id in enumerate(ordered_atom_ids)}
        material, bond_order = _material_for_mapping(candidate, mapping)
        encoded = canonical_json_bytes(material)
        if best_bytes is None or encoded < best_bytes:
            best_bytes = encoded
            best_material = material
            best_mapping = mapping
            best_bond_order = bond_order
    if best_material is None or best_mapping is None or best_bond_order is None:
        raise ValueError("canonicalization produced no molecular identity")
    return best_material, best_mapping, best_bond_order


def canonical_molecular_graph_bytes(value: Any) -> bytes:
    """Return identifier- and order-independent canonical graph bytes."""
    candidate = _as_candidate(value)
    material, _, _ = _canonicalize(candidate)
    return canonical_json_bytes(material)


def canonical_molecular_graph_sha256(value: Any) -> str:
    """Return the canonical SHA-256 for a complete supported graph."""
    return hashlib.sha256(canonical_molecular_graph_bytes(value)).hexdigest()


def _derive_topology(candidate: MolecularGraphCandidateV1) -> MolecularTopologyV1:
    _, mapping, canonical_bond_ids = _canonicalize(candidate)
    ring_bond_ids = _ring_bond_ids(candidate)
    bonds_by_id = {bond.bond_id: bond for bond in candidate.bonds}
    ring_atom_indices = sorted(
        {
            mapping[atom_id]
            for bond_id in ring_bond_ids
            for atom_id in bonds_by_id[bond_id].atom_ids
        }
    )
    ring_bond_indices = tuple(
        index
        for index, bond_id in enumerate(canonical_bond_ids)
        if bond_id in ring_bond_ids
    )
    return MolecularTopologyV1(
        component_count=1,
        cycle_rank=len(candidate.bonds) - len(candidate.atoms) + 1,
        ring_atom_indices=tuple(ring_atom_indices),
        ring_bond_indices=ring_bond_indices,
    )


def _plural(count: int, singular: str) -> str:
    return singular if count == 1 else singular + "s"


def _charge_text(charge: int) -> str:
    if charge > 0:
        return f"plus {charge}"
    if charge < 0:
        return f"minus {abs(charge)}"
    return "0"


def _derive_description(
    candidate: MolecularGraphCandidateV1,
    digest: str,
    topology: MolecularTopologyV1,
) -> AccessibleMolecularDescriptionV1:
    _, mapping, canonical_bond_ids = _canonicalize(candidate)
    atoms_by_index: list[MolecularAtomV1 | None] = [None] * len(candidate.atoms)
    for atom in candidate.atoms:
        atoms_by_index[mapping[atom.atom_id]] = atom

    atom_text: list[str] = []
    atom_tokens: list[str] = []
    for index, possible_atom in enumerate(atoms_by_index):
        if possible_atom is None:
            raise ValueError("canonical atom mapping is incomplete")
        atom = possible_atom
        parts = [f"Atom {index + 1}: {ELEMENT_NAMES[atom.element]}"]
        if atom.isotope is not None:
            parts.append(f"isotope {atom.isotope}")
        parts.append(f"formal charge {_charge_text(atom.formal_charge)}")
        parts.append(
            f"{atom.implicit_hydrogens} implicit "
            f"{_plural(atom.implicit_hydrogens, 'hydrogen')}"
        )
        parts.append("aromatic" if atom.aromatic else "non-aromatic")
        if atom.stereochemistry is not None:
            parts.append(f"absolute stereochemistry {atom.stereochemistry}")
        atom_text.append("; ".join(parts) + ".")
        atom_tokens.append(
            canonical_json_bytes(
                {"kind": "atom", "index": index, **_atom_material(atom)}
            ).decode("ascii")
        )

    bonds_by_id = {bond.bond_id: bond for bond in candidate.bonds}
    ring_bond_indices = set(topology.ring_bond_indices)
    bond_text: list[str] = []
    bond_tokens: list[str] = []
    for index, bond_id in enumerate(canonical_bond_ids):
        bond = bonds_by_id[bond_id]
        endpoints = sorted(mapping[atom_id] + 1 for atom_id in bond.atom_ids)
        parts = [
            f"Bond {index + 1}: Atom {endpoints[0]} to Atom {endpoints[1]}",
            bond.order,
            "ring bond" if index in ring_bond_indices else "non-ring bond",
        ]
        if bond.stereochemistry is not None:
            parts.append(f"absolute stereochemistry {bond.stereochemistry}")
        bond_text.append("; ".join(parts) + ".")
        bond_tokens.append(
            canonical_json_bytes(
                {
                    "kind": "bond",
                    "index": index,
                    **_bond_material(bond, mapping),
                    "ring": index in ring_bond_indices,
                }
            ).decode("ascii")
        )

    if topology.cycle_rank == 0:
        topology_text = "Topology: acyclic."
    elif topology.cycle_rank == 1:
        topology_text = (
            "Topology: one independent cycle with "
            f"{len(topology.ring_atom_indices)} ring atoms and "
            f"{len(topology.ring_bond_indices)} ring bonds."
        )
    else:
        topology_text = (
            f"Topology: {topology.cycle_rank} independent cycles with "
            f"{len(topology.ring_atom_indices)} ring atoms and "
            f"{len(topology.ring_bond_indices)} ring bonds."
        )
    topology_token = canonical_json_bytes(
        {"kind": "topology", **topology.model_dump(mode="json")}
    ).decode("ascii")
    summary = (
        f"Molecular graph with {len(candidate.atoms)} "
        f"{_plural(len(candidate.atoms), 'atom')}, {len(candidate.bonds)} "
        f"{_plural(len(candidate.bonds), 'bond')}, 1 connected component, and "
        f"{topology.cycle_rank} independent "
        f"{_plural(topology.cycle_rank, 'cycle')}."
    )
    return AccessibleMolecularDescriptionV1(
        description_kind="molecular_graph_description_v1",
        graph_sha256=digest,
        summary=summary,
        atoms=tuple(atom_text),
        bonds=tuple(bond_text),
        topology=topology_text,
        semantic_inventory=tuple(atom_tokens + bond_tokens + [topology_token]),
    )


class VerifiedMolecularGraphV1(MolecularGraphCandidateV1):
    """A molecular graph bound to all of its deterministic projections."""

    canonical_sha256: str = Field(pattern=_SHA256_PATTERN)
    graph_identifier: str = Field(pattern=_GRAPH_IDENTIFIER_PATTERN)
    topology: MolecularTopologyV1
    description: AccessibleMolecularDescriptionV1

    @model_validator(mode="after")
    def _derived_fields_match_graph(self) -> VerifiedMolecularGraphV1:
        candidate = _as_candidate(self)
        digest = canonical_molecular_graph_sha256(candidate)
        expected_identifier = f"aelira-molecular-graph-v1:sha256:{digest}"
        topology = _derive_topology(candidate)
        description = _derive_description(candidate, digest, topology)
        if self.canonical_sha256 != digest:
            raise ValueError("canonical_sha256 does not match the molecular graph")
        if self.graph_identifier != expected_identifier:
            raise ValueError("graph_identifier does not match the canonical digest")
        if self.topology != topology:
            raise ValueError("topology does not match the molecular graph")
        if self.description != description:
            raise ValueError("description does not match the molecular graph")
        return self


def verify_molecular_graph(value: Any) -> VerifiedMolecularGraphV1:
    """Validate a graph and return its digest-bound immutable projections."""
    if isinstance(value, VerifiedMolecularGraphV1):
        return VerifiedMolecularGraphV1.model_validate(value)
    if isinstance(value, Mapping) and any(
        field in value
        for field in ("canonical_sha256", "graph_identifier", "topology", "description")
    ):
        return VerifiedMolecularGraphV1.model_validate(value)
    candidate = _as_candidate(value)
    digest = canonical_molecular_graph_sha256(candidate)
    topology = _derive_topology(candidate)
    description = _derive_description(candidate, digest, topology)
    return VerifiedMolecularGraphV1.model_validate(
        {
            **candidate.model_dump(mode="json"),
            "canonical_sha256": digest,
            "graph_identifier": f"aelira-molecular-graph-v1:sha256:{digest}",
            "topology": topology.model_dump(mode="json"),
            "description": description.model_dump(mode="json"),
        }
    )


def describe_molecular_graph(value: Any) -> AccessibleMolecularDescriptionV1:
    """Return the deterministic accessible description of a verified graph."""
    return verify_molecular_graph(value).description


__all__ = [
    "MAX_ATOMS",
    "MAX_BONDS",
    "MAX_CANONICAL_PERMUTATIONS",
    "AccessibleMolecularDescriptionV1",
    "MolecularAtomV1",
    "MolecularBondV1",
    "MolecularGraphCandidateV1",
    "MolecularGraphRejected",
    "MolecularTopologyV1",
    "VerifiedMolecularGraphV1",
    "canonical_molecular_graph_bytes",
    "canonical_molecular_graph_sha256",
    "describe_molecular_graph",
    "verify_molecular_graph",
]
