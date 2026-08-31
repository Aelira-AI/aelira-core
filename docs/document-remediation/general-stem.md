# General STEM visual remediation

Aelira Core v0.9.7 adds bounded remediation contracts for mathematical and scientific visuals in PDFs. The pipeline covers printed equations, ordered multi-equation screenshots, vector equations, handwritten mathematics, chemical formulas, molecular structures, commutative diagrams, and mixed visuals. It does not turn recognition into proof that the source was interpreted correctly.

## Trust pipeline

Every supported visual follows the same fail-closed boundary:

1. A detector identifies an exact embedded-image occurrence, page-raster crop, vector-object cluster, or native text region and records source identity, page, geometry, and digests.
2. A purpose-bound specialist may propose one typed semantic result. Provider output cannot bypass that specialist's schema or write directly to the PDF.
3. A separate verification pass checks the proposed semantics. For equation recognition, the verifier does not receive the primary reading; exact canonical MathML agreement is required.
4. The result is associated with the original source object or crop. The saved PDF is reopened and checked for source identity, structure ownership, attachments, metadata, and applicable render or OCR evidence.
5. Visual fixes remain pending human review. Approval is bound to the exact current evidence and output digests and becomes invalid when that candidate changes.

“Independent” describes context-isolated verification calls, not necessarily different providers. Agreement is a strict rejection gate, not evidence that two matching readings are mathematically or scientifically true.

## Specialist boundaries

| Visual class | Supported boundary | Deliberate limit |
|---|---|---|
| Printed equation | One bounded raster equation, verified LaTeX-to-MathML round trip, and exact PDF source association | Unsupported or ambiguous visual content remains manual |
| Multi-equation screenshot | Ordered child regions are either verified separately or as one exact system, with complete source ownership | Partial child coverage and ambiguous grouping are refused |
| Vector equation | One exact cluster of PDF drawing operators and resources is associated with a `/Formula` result | It does not infer across unrelated vector objects or incomplete clusters |
| Handwritten mathematics | A frozen suitability policy admits a bounded crop to two context-isolated readings and exact MathML agreement | Every accepted result still requires human review; the corpus is not a general handwriting-quality claim |
| Chemical formula | A bounded visual transcription is parsed into deterministic notation, speech, and passive MathML | It does not infer names, balance reactions, or accept unsupported chemistry notation |
| Molecular structure | A bounded drawing becomes a complete verified molecular graph with an exact abbreviation vocabulary | It does not accept polymers, wildcard atoms, disconnected fragments, or ambiguous stereochemistry |
| Commutative diagram | A bounded drawing becomes a typed object-arrow-path graph and deterministic accessible description | It does not prove commutativity or infer unsupported higher-dimensional structure |
| Mixed visual | Typed regions are routed to their matching specialists and composed only when every visual route is resolved | One open or mismatched route rejects the atomic composition |

See the focused guides for [handwritten mathematics](handwritten-math.md), [chemical formulas](chemical-formulas.md), [molecular graphs](molecular-graphs.md), and [commutative diagrams](commutative-diagrams.md).

## Mixed composition

The mixed-STEM router reopens and revalidates each source before invoking its matching specialist. It preserves one typed region graph in canonical reading order and rejects missing, duplicate, stale, incompatible, or over-budget routes.

Composition accepts native text plus fully verified specialist contracts. It preserves the original rendering, derives structure roles and accessible descriptions deterministically, records attachment and contract digests, serializes one candidate, and verifies that exact saved output. The artifact is available only while a current, unexpired human approval matches the output, result, plan, contract set, and review digest.

## Human review and semantic limits

The pipeline verifies identity, schema, deterministic projections, source association, saved-file structure, and bounded render evidence. It cannot establish the author's intended meaning, mathematical correctness, chemical plausibility, or pedagogical quality. Review the exact candidate against the original visual and use assistive technology before accepting it.

An unsupported region, uncertain source association, failed provider call, verifier disagreement, saved-file mismatch, or stale approval fails closed and leaves the work open. Aelira does not substitute placeholder or plausible-looking semantics to obtain a successful artifact state.

## Source and test evidence

| Area | Source | Tests |
|---|---|---|
| Printed equations | [source contract](../../src/education/equation_region_contract.py), [detector](../../src/education/pdf_checks/equation_region_detector.py), [recognizer](../../src/education/remediation/equation_recognizer.py), [verifier](../../src/education/remediation/equation_verifier.py) | [source regions](../../tests/test_pdf_scanned_equation_regions.py), [source identity](../../tests/test_scanned_equation_region_source.py), [saved association](../../tests/test_scanned_equation_region_association.py) |
| Multi-equation screenshots | [region graph](../../src/education/multi_equation_region.py), [semantic contract](../../src/education/multi_equation_semantics.py), [PDF association](../../src/education/remediation/multi_equation_semantics.py) | [region detector](../../tests/test_multi_equation_region_detector.py), [semantic association](../../tests/test_multi_equation_semantic_association.py) |
| Vector equations | [cluster contract](../../src/education/vector_equation_cluster.py), [semantic contract](../../src/education/vector_equation_semantics.py), [PDF association](../../src/education/remediation/vector_equation_semantics.py) | [cluster detector](../../tests/test_vector_equation_cluster_detector.py), [semantic association](../../tests/test_vector_equation_semantic_association.py) |
| Handwritten mathematics | [suitability policy](../../src/education/handwritten_math_suitability.py), [recognizer](../../src/education/remediation/handwritten_equation_recognizer.py), [verifier](../../src/education/remediation/handwritten_equation_verifier.py) | [corpus policy](../../tests/test_handwritten_math_suitability.py), [recognition](../../tests/test_handwritten_math_recognition.py), [PDF fixer](../../tests/test_handwritten_math_fixer.py) |
| Chemical formulas | [semantic contract](../../src/education/chemical_formula.py), [PDF specialist](../../src/education/chemical_formula_pdf.py) | [semantic contract](../../tests/test_chemical_formula.py), [PDF contract](../../tests/test_chemical_formula_pdf_contract.py), [saved association](../../tests/test_chemical_formula_pdf_association.py) |
| Molecular structures | [graph contract](../../src/education/molecular_graph.py), [PDF specialist](../../src/education/chemical_structure_pdf.py) | [graph contract](../../tests/test_molecular_graph.py), [PDF contract](../../tests/test_chemical_structure_pdf_contract.py), [saved association](../../tests/test_chemical_structure_pdf_association.py) |
| Commutative diagrams | [graph contract](../../src/education/commutative_diagram.py), [PDF specialist](../../src/education/commutative_diagram_pdf.py) | [graph contract](../../tests/test_commutative_diagram_contract.py), [PDF contract](../../tests/test_commutative_diagram_pdf_contract.py), [saved association](../../tests/test_commutative_diagram_pdf_association.py) |
| Mixed visuals | [region graph](../../src/education/mixed_stem_regions.py), [router](../../src/education/remediation/mixed_stem_region_router.py), [composition contract](../../src/education/mixed_stem_composition.py), [composer](../../src/education/remediation/mixed_stem_composer.py) | [routing](../../tests/test_mixed_stem_region_routing.py), [composition](../../tests/test_mixed_stem_composition.py) |
