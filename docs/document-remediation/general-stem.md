# General STEM visual remediation

Aelira Core v0.9.7 adds bounded remediation contracts for mathematical and scientific visuals in PDFs. The contracts cover printed equations, ordered multi-equation screenshots, vector equations, handwritten mathematics, chemical formulas, molecular structures, commutative diagrams, and mixed visuals. It does not turn recognition into proof that the source was interpreted correctly. In this release the PDF remediation pipeline runs the printed equation and handwritten mathematics paths. The multi-equation, vector equation and mixed visual paths are not used by the pipeline.

## Trust pipeline

Every supported visual follows the same fail-closed boundary:

1. A detector identifies an exact embedded-image occurrence, page-raster crop, vector-object cluster, or native text region and records source identity, page, geometry, and digests.
2. A purpose-bound specialist may propose one typed semantic result. Provider output cannot bypass that specialist's schema or write directly to the PDF.
3. A separate verification pass checks the proposed semantics. Printed equations (embedded images and scanned regions) and handwritten equations each use a second, context-isolated reading that never receives the first, and acceptance requires exact canonical MathML v2 agreement. Printed equations must also pass the visual round trip. The multi-equation and vector paths compare renders only and are not used by the remediation pipeline in this release.
4. The result is associated with the original source object or crop. The saved PDF is reopened and checked for source identity, structure ownership, attachments, metadata, and applicable render or OCR evidence.
5. Visual fixes remain pending human review. Approval is bound to the exact current evidence and output digests and becomes invalid when that candidate changes.

“Independent” describes context-isolated verification calls, not necessarily different providers. Agreement is a strict rejection gate, not evidence that two matching readings are mathematically or scientifically true.

## Specialist boundaries

| Visual class | Supported boundary | Deliberate limit |
|---|---|---|
| Printed equation | One bounded raster equation, two context-isolated readings with equal canonical MathML, a visual round-trip filter, and exact PDF source association | Matching readings can both be wrong, and human approval is required before download or write-back. Unsupported or ambiguous visual content remains manual |
| Multi-equation screenshot | Ordered child regions are either verified separately or as one exact system, with complete source ownership | Visual comparison only, and not used by the remediation pipeline in this release. Partial child coverage and ambiguous grouping are refused |
| Vector equation | One exact cluster of PDF drawing operators and resources is associated with a `/Formula` result | Visual comparison only, and not used by the remediation pipeline in this release. It does not infer across unrelated vector objects or incomplete clusters |
| Handwritten mathematics | A frozen suitability policy admits a bounded crop to two context-isolated readings and exact canonical MathML agreement | Every accepted result still requires human review; the corpus is not a general handwriting-quality claim |
| Chemical formula | A bounded visual transcription is parsed into deterministic notation, speech, and passive MathML | It does not infer names, balance reactions, or accept unsupported chemistry notation |
| Molecular structure | A bounded drawing becomes a complete verified molecular graph with an exact abbreviation vocabulary | It does not accept polymers, wildcard atoms, disconnected fragments, or ambiguous stereochemistry |
| Commutative diagram | A bounded drawing becomes a typed object-arrow-path graph and deterministic accessible description | It does not prove commutativity or infer unsupported higher-dimensional structure |
| Mixed visual | Typed regions are routed to their matching specialists and composed only when every visual route is resolved | Not used by the remediation pipeline in this release: the router and composer have no pipeline caller. One open or mismatched route rejects the atomic composition |

See the focused guides for [handwritten mathematics](handwritten-math.md), [chemical formulas](chemical-formulas.md), [molecular graphs](molecular-graphs.md), and [commutative diagrams](commutative-diagrams.md).

## What the printed equation checks establish

A printed equation is staged for review only after three checks pass, and none of them proves the equation was read correctly.

1. **Visual round trip.** The reading is converted to MathML, rendered offline and compared with the source crop (binary ink overlap of at least 0.90 and pixel similarity of at least 0.98, threshold version `printed-equation-v1`). This is a similarity filter, not an equivalence proof. It can pass a reading that differs from the image by one symbol, such as `x^{3} + 3x - 7 = 0` for `x^{2} + 3x - 7 = 0`.
2. **Second reading.** Only if the visual check passes, the same pixels are read again in a separate call with its own pinned prompt. The second reader has no parameter that could carry the first reading. Both readings are converted to MathML and reduced to canonical form version 2, and they must be exactly equal. A difference in braces alone agrees (`x^{2}` and `x^2`); any other difference stays manual as `printed_equation_semantic_disagreement`. The second reading can also fail with `printed_equation_verifier_unavailable`, `printed_equation_verifier_failed` or `printed_equation_verifier_declined`.
3. **Saved-file proof.** The reopened PDF is checked as for every visual (step 4 of the trust pipeline).

The two readings come from context-isolated calls, not necessarily different models, so a mistake the model makes on both calls produces agreement. Matching readings can both be wrong. The agreement is recorded in the review contract (digests of both readings, their provider and model identities, and policy `printed-equation-consensus-v1`), and approval stays bound to that evidence. The reviewer still compares the candidate with the original equation.

The repository measures this on a corpus of single-symbol edits of multi-term equations (`tests/fixtures/pdfs/image_equations/single_symbol_edits.json`). The test run records how many wrong readings the visual check passes on its own, and CI keeps the figure in `test-results/printed-equation-single-symbol.json`. The full path, with a truthful second reading, accepts none of them. That shows the second reading refuses a reading that is wrong in one symbol when the other reading is right. It does not show that two readings that share an error are caught.

A printed-equation contract stored before the second reading existed carries no agreement evidence. It now reads as an invalid contract: approval is refused and the artifact stays gated. Run remediation again on the affected documents to regenerate the finding. No database migration is involved.

## Equation alt text

The alt text written for a printed or handwritten equation is built by deterministic substitution from the recognized LaTeX. It is not generated from the verified MathML, so agreement on MathML does not by itself show that the words are right. Aelira therefore writes alt text only for closed spoken forms, where nothing in the words can be read as a different equation. For example `x^{2} + 3x - 7 = 0` is announced as "x squared + 3x - 7 = 0".

Anything the substitution could garble or read two ways is refused and left for review as `image_equation_alt_text_ambiguous`. That covers a fraction, root or script (other than squared and cubed) followed by more terms, an argument that is not one number, letter or Greek letter, a script on an integral or sum limit, and a command with no spoken form. The check runs on both readings. Generating the spoken text from the verified MathML is planned in [#698](https://github.com/Aelira-AI/aelira-core/issues/698); until then, review the alt text of every equation.

## Mixed composition

The mixed-STEM router and composer are not used by the remediation pipeline in this release. They are library code with their own tests, and this section describes that code, not a path a PDF remediation job runs.

The mixed-STEM router reopens and revalidates each source before invoking its matching specialist. It preserves one typed region graph in canonical reading order and rejects missing, duplicate, stale, incompatible, or over-budget routes.

Composition accepts native text plus fully verified specialist contracts. It preserves the original rendering, derives structure roles and accessible descriptions deterministically, records attachment and contract digests, serializes one candidate, and verifies that exact saved output. The artifact is available only while a current, unexpired human approval matches the output, result, plan, contract set, and review digest.

## Human review and semantic limits

The pipeline verifies identity, schema, deterministic projections, source association, saved-file structure, and bounded render evidence. It cannot establish the author's intended meaning, mathematical correctness, chemical plausibility, or pedagogical quality. Review the exact candidate against the original visual and use assistive technology before accepting it.

An unsupported region, uncertain source association, failed provider call, verifier disagreement, saved-file mismatch, or stale approval fails closed and leaves the work open. Aelira does not substitute placeholder or plausible-looking semantics to obtain a successful artifact state.

## Source and test evidence

| Area | Source | Tests |
|---|---|---|
| Printed equations | [source contract](../../src/education/equation_region_contract.py), [detector](../../src/education/pdf_checks/equation_region_detector.py), [recognizer](../../src/education/remediation/equation_recognizer.py), [verifier](../../src/education/remediation/equation_verifier.py) | [source regions](../../tests/test_pdf_scanned_equation_regions.py), [source identity](../../tests/test_scanned_equation_region_source.py), [saved association](../../tests/test_scanned_equation_region_association.py) |
| Multi-equation screenshots (not used by the pipeline in this release) | [region graph](../../src/education/multi_equation_region.py), [semantic contract](../../src/education/multi_equation_semantics.py), [PDF association](../../src/education/remediation/multi_equation_semantics.py) | [region detector](../../tests/test_multi_equation_region_detector.py), [semantic association](../../tests/test_multi_equation_semantic_association.py) |
| Vector equations (not used by the pipeline in this release) | [cluster contract](../../src/education/vector_equation_cluster.py), [semantic contract](../../src/education/vector_equation_semantics.py), [PDF association](../../src/education/remediation/vector_equation_semantics.py) | [cluster detector](../../tests/test_vector_equation_cluster_detector.py), [semantic association](../../tests/test_vector_equation_semantic_association.py) |
| Printed-equation agreement and equation alt text | [second reader and agreement](../../src/education/remediation/printed_equation_verifier.py), [consensus policy](../../src/education/printed_equation_policy.py), [canonical form version](../../src/education/canonical_mathml_version.py), [reading parser](../../src/education/remediation/equation_reading_parser.py), [spoken forms and ambiguity guard](../../src/education/math_speech.py), [review contract](../../src/education/visual_semantic_contract.py) | [second reading](../../tests/test_printed_equation_second_reading.py), [single-symbol edits](../../tests/test_printed_equation_single_symbol.py), [edit corpus](../../tests/fixtures/pdfs/image_equations/single_symbol_edits.json), [canonical form](../../tests/test_equation_canonical_form.py), [reading parser](../../tests/test_equation_reading_parser.py), [spoken forms](../../tests/test_math_speech.py), [contract persistence](../../tests/test_printed_equation_contract_persistence.py) |
| Handwritten mathematics | [suitability policy](../../src/education/handwritten_math_suitability.py), [recognizer](../../src/education/remediation/handwritten_equation_recognizer.py), [verifier](../../src/education/remediation/handwritten_equation_verifier.py) | [corpus policy](../../tests/test_handwritten_math_suitability.py), [recognition](../../tests/test_handwritten_math_recognition.py), [PDF fixer](../../tests/test_handwritten_math_fixer.py) |
| Chemical formulas | [semantic contract](../../src/education/chemical_formula.py), [PDF specialist](../../src/education/chemical_formula_pdf.py) | [semantic contract](../../tests/test_chemical_formula.py), [PDF contract](../../tests/test_chemical_formula_pdf_contract.py), [saved association](../../tests/test_chemical_formula_pdf_association.py) |
| Molecular structures | [graph contract](../../src/education/molecular_graph.py), [PDF specialist](../../src/education/chemical_structure_pdf.py) | [graph contract](../../tests/test_molecular_graph.py), [PDF contract](../../tests/test_chemical_structure_pdf_contract.py), [saved association](../../tests/test_chemical_structure_pdf_association.py) |
| Commutative diagrams | [graph contract](../../src/education/commutative_diagram.py), [PDF specialist](../../src/education/commutative_diagram_pdf.py) | [graph contract](../../tests/test_commutative_diagram_contract.py), [PDF contract](../../tests/test_commutative_diagram_pdf_contract.py), [saved association](../../tests/test_commutative_diagram_pdf_association.py) |
| Mixed visuals (router and composer not used by the pipeline in this release) | [region graph](../../src/education/mixed_stem_regions.py), [router](../../src/education/remediation/mixed_stem_region_router.py), [composition contract](../../src/education/mixed_stem_composition.py), [composer](../../src/education/remediation/mixed_stem_composer.py) | [routing](../../tests/test_mixed_stem_region_routing.py), [composition](../../tests/test_mixed_stem_composition.py) |
