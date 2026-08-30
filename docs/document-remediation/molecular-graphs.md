# Molecular graph semantics

Aelira Core provides a bounded, provider-neutral contract for molecular graphs. It is the semantic boundary that later structure-recognition work can produce; it does not read drawings, infer missing chemistry, associate a graph with a source object, or change a document.

Use [`verify_molecular_graph`](../../src/education/molecular_graph.py) with one complete connected graph. A successful result contains the immutable graph, connectivity-derived topology, a canonical SHA-256, a versioned graph identifier, and a deterministic accessible description. Rejected input returns no verified structure.

```python
from src.education.molecular_graph import verify_molecular_graph

verified = verify_molecular_graph(
    {
        "contract_kind": "molecular_graph_v1",
        "atoms": [
            {
                "atom_id": "carbon",
                "element": "C",
                "formal_charge": 0,
                "isotope": None,
                "implicit_hydrogens": 4,
                "aromatic": False,
                "stereochemistry": None,
            }
        ],
        "bonds": [],
    }
)
print(verified.graph_identifier)
print(verified.description.summary)
```

## Supported graph

The contract accepts one connected graph with 1–32 atoms and up to 64 bonds:

- atoms use the same 118 case-correct element symbols as the formula contract;
- every atom explicitly supplies formal charge, optional isotope, implicit-hydrogen count, aromaticity, and optional absolute `R` or `S` stereochemistry;
- bonds identify two atoms and carry exactly one of `single`, `double`, `triple`, or `aromatic` order;
- a double bond may carry absolute `E` or `Z` stereochemistry;
- ring atoms and ring bonds are derived from graph connectivity, and cycle rank is derived as edges minus vertices plus connected components.

Atom IDs, bond IDs, endpoint order, and collection order are presentation references. They do not enter canonical identity. The verifier refines graph labels, searches any remaining symmetry under a fixed 4,096-permutation bound, and selects one exact passive JSON representation. Every accepted material field enters that identity.

## Stereo and aromaticity boundary

`R` and `S` are accepted only for a non-aromatic atom with four substituent slots whose bounded first-shell signatures are distinct. `E` and `Z` are accepted only for a double bond whose two endpoints each have two distinct substituent slots. These checks establish an unambiguous supported graph shape; they do not infer stereochemistry from coordinates or implement full chemical priority rules.

Aromaticity is explicit input, not a perceived property. An aromatic bond must join aromatic atoms and belong to a cycle, and each aromatic atom must have two cycle-bearing aromatic bonds. The verifier does not choose a resonance form or claim chemical plausibility.

## Accessible description

The accessible projection lists atoms and bonds in canonical order. It announces element names, isotope and formal-charge values, implicit hydrogens, aromaticity, absolute stereo, bond order, canonical endpoints, derived ring membership, and cycle counts. It does not invent compound names, valence, resonance, tautomers, or missing chemistry.

## Deliberate refusals

The contract rejects duplicate or missing IDs, self-bonds, parallel bonds, disconnected labels or fragments, incomplete atom records, unknown or wildcard atoms, polymers and repeating units, query structures, inconsistent or acyclic aromaticity, ambiguous or incomplete stereo, oversized graphs, and symmetry work above the public bound.

SMILES, SMARTS, InChI, molfiles, CML, coordinates, wedge/dash marks, reactions, salts and mixtures, systematic naming, substructure search, visual recognition, PDF association, and document mutation remain outside this boundary. The repository-authored [CC0 fixture manifest](../../tests/fixtures/molecular_graph/manifest.json) records supported topology and stereo cases plus named refusals.
