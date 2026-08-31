# Chemical formula semantics

Aelira Core provides a deterministic chemistry contract for formulas and reactions, plus a purpose-bound PDF specialist that can transcribe one explicitly bounded visual candidate. The specialist accepts only an `alt_text` provider, records provider and model identity, and passes the returned source notation through the same deterministic verifier before any PDF mutation.

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

## PDF recognition and saved-file proof

`ChemicalFormulaRecognizer` accepts one exact embedded-image occurrence or page-raster region as a bounded JPEG. The provider may return only a positive source transcription or a negative classification. It cannot author speech, MathML, typed chemistry, confidence, or extra prose. Invalid notation is refused without a retry; only provider or transport failure receives one retry.

An accepted result is associated with the exact source as a PDF `/Formula`. The verified speech is stored in `/Alt`, and the verifier-generated MathML is stored as the sole `/AF` supplementary attachment. `/AeliraChemicalFormula` binds the notation kind and the source, semantic, speech, MathML, and aggregate metadata digests. The saved file is reopened to verify source identity, marked-content ownership, ParentTree linkage, attachment bytes, metadata, and render parity.

Embedded images and clipped scanned regions use different saved-evidence variants. Scanned-region verification additionally proves clip geometry and OCR reading-order ownership. Both variants produce a durable `ChemicalFormulaPdfContract`; incomplete or inconsistent evidence cannot be persisted as a successful visual fix.

Every chemical-formula fix is forced into human review. This gate is shared by direct, queued, Canvas, and Brightspace artifact publication, and approval is invalidated if the reviewed content or its provenance changes.

## Deliberate refusals

The contract rejects unknown or mis-cased elements, bare charge signs, incomplete or multiple arrows, empty sides or groups, unsafe or over-limit input, and unsupported notations including `mhchem`, ChemFig, SMILES, InChI, hydrate dots, and Unicode arrows. Molecular structure, bonds, stereochemistry, names, balancing, and mass calculation remain outside this boundary. Recognition is limited to a caller-supplied bounded PDF visual; this feature does not search arbitrary document content for chemistry.

The reviewed [fixture manifest](../../tests/fixtures/chemical_formula/manifest.json) records exact canonical and speech results for supported cases and named refusal classes for unsupported cases.
