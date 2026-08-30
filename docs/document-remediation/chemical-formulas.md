# Chemical formula semantics

Aelira Core provides a small, deterministic chemistry contract for formulas and reactions. It is a library boundary for later document-recognition work; it does not discover chemistry in files or change a document.

Use [`verify_chemical_notation`](../../src/education/chemical_formula.py) with source notation. A successful result contains the immutable typed notation, exact source notation, canonical notation, deterministic speech, passive MathML, and SHA-256 identities for each projection. Rejected input raises `ChemicalFormulaRejected` and returns no partial chemistry.

```python
from src.education.chemical_formula import verify_chemical_notation

verified = verify_chemical_notation("2H2 + O2 -> 2H2O")
print(verified.canonical_notation)
print(verified.speech)
```

## Supported notation

The parser accepts a bounded printable-ASCII subset:

- all 118 case-correct element symbols, with counts from 1 through 999;
- isotope prefixes such as `^14C`;
- parenthesized groups such as `Ca(OH)2`, nested to at most eight levels;
- whole-species coefficients such as `2H2O`;
- caret-qualified charges such as `Na^+`, `Fe^3+`, and `SO4^2-`;
- states `(s)`, `(l)`, `(g)`, and `(aq)`;
- ordered reaction sides joined by `->`, `<=>`, or `<->`;
- up to four semicolon-separated arrow conditions, such as `<=>[heat;Fe]`.

Spaces between supported tokens are presentation-only. Canonical serialization removes those differences while the source digest continues to identify the exact submitted text.

## Accessible projections

Speech expands element symbols and explicitly announces subscripts, isotopes, groups, coefficients, charges, states, arrows, and conditions. It does not infer common compound names, balance reactions, repair malformed notation, or expand condition abbreviations.

MathML is generated only from an exact validated contract. The generator emits a fixed passive element and attribute set; it does not accept raw strings or preserve caller-supplied markup.

## Deliberate refusals

The contract rejects unknown or mis-cased elements, bare charge signs, incomplete or multiple arrows, empty sides or groups, unsafe or over-limit input, and unsupported notations including `mhchem`, ChemFig, SMILES, InChI, hydrate dots, and Unicode arrows. Molecular structure, bonds, stereochemistry, names, balancing, mass calculation, visual recognition, PDF association, and document mutation remain outside this boundary.

The reviewed [fixture manifest](../../tests/fixtures/chemical_formula/manifest.json) records exact canonical and speech results for supported cases and named refusal classes for unsupported cases.
