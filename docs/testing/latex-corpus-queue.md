# Saved LaTeX corpus through the document queue

The required `Real document queue and artifact acceptance` CI job exercises all
26 cases and 28 source files in `tests/fixtures/latex_research/corpus.json` through
the authenticated API, PostgreSQL, durable worker and artifact storage. It runs
each original plus an explicitly authored language variant. This completes the
queued-source slice tracked by #480; the wider research work remains in #444.
It is API integration evidence, not a browser or conformance claim.

## Predeclared outcomes

| Cases | Original | Declared-language variant |
| --- | --- | --- |
| M01–M18 | Author-review refusal; no download | TEX candidate, exact source preservation and independent rescan |
| P01 | Complete project scan and original ZIP retrieval; automatic editing refused | Same project boundary, with only the entrypoint language declaration added |
| P02, P03 | Diagram/table review required; no download | Review remains required; no invented descriptions or header associations |
| P04 | Zero source findings; durable no-op, no artifact | Same no-op with an explicit German declaration |
| N01, N02 | Author-review refusal | TEX source may be delivered, but must fail compilation for its intended undefined-macro/missing-include reason |
| N03, N04 | Refusal; no download | Refusal remains; missing image alternative or incomplete source comparison |

Single-file cases request TEX only with AI disabled. Reviewed variants add
`pdflang=en`, except P04 which uses `de` and retains its authored Babel and
`selectlanguage` declarations. These are labelled synthetic inputs created by
the harness, never inferred language repairs. The gate expects 20 TEX downloads,
28 single-file refusals and two no-ops, plus two project scan/refusal journeys.

Every delivered candidate retains the document body exactly, including whitespace,
equation source, labels, references, captions and missing inputs. Original preamble
lines remain in order; permitted additions are tested separately by compilation.
German metadata has targeted positive and conflicting-language controls, including
escaped comments and unsupported nonliteral assignments. Source preservation does
not prove rendered mathematical equivalence.

Published files require a real artifact ID, matching source/output hashes,
byte-identical repeated downloads, durable reload and an independent rescan that
matches the recorded source score/findings. Refusals require the persisted failure,
zero published fixes, no verified remediated score, no available formats and HTTP 404
from download. P04's zero findings must not manufacture a repair or artifact.

P01 keeps `main.tex`, `macros.tex` and `chapters/one.tex` in their original layout.
The uploaded ZIP, downloaded original, returned file inventory and conversion
receipt are bound to the same source hashes. Automatic source editing returns
`project_source_review_required` before remediation is queued. P01's conversion must
select an installed Pandoc and retain its measured `unsupported_command` refusal,
with no HTML candidate or download. Missing tools cannot satisfy that expectation.
Authenticated artifact URLs are restricted to the disposable API origin.

## Compilation evidence

The separate compiler gate checks 26 original contexts, 26 declared-language
contexts and all 20 exact TEX downloads. It preserves project dependencies,
uses fresh directories and hashes the actual input, output PDF, logs and installed
package files. Positive cases require two successful passes and no unresolved
references on the final pass. Negative cases require the intended diagnostic;
a missing or killed compiler or an unrelated missing package never satisfies them.
The untagged PDFs are diagnostic artifacts, not approved accessible outputs.

Run the [disposable document stack](document-review-journey.md), then:

```sh
node --experimental-strip-types --test scripts/latex_corpus_contract.test.ts scripts/latex_compilation_contract.test.ts scripts/document_stack_transport.test.ts
node --experimental-strip-types scripts/verify_document_stack.ts
node --experimental-strip-types scripts/verify_latex_corpus_compilation.ts
```

Use Node 22 or newer, `zip`, `unzip`, Pandoc and a supported `pdflatex` or `lualatex`
with the declared packages installed, including TikZ, physics, siunitx and German
Babel. CI installs the prerequisites explicitly. `STACK_TEX_COMPILER=lualatex`
selects that engine; an engine change is recorded, not treated as the same runtime.

`report.json` records revision, tracked-diff digest, harness/manifest hashes,
configuration, scan/job/artifact IDs and source/output hashes. It rejects harness
changes during execution. `compilation.json` binds that exact queue report to engine
and package identities, every source context and produced PDF. CI also retains
server Python/package versions in `runtime.json`. Local host execution has no
container image identity; container-based measurements must record their image
separately. Keep local evidence and patches together; a dirty tree is not a release.

## Remaining boundaries

The earlier eleven selected fixtures remain unchanged in `latex_validation` for
their PDF-failure and source-evidence regressions. The separate
[compatibility smoke](latex-compatibility.md) compares raw and preprocessed converter
outputs for its declared controls. This queue lane neither replaces those results
nor claims a complete HTML/DOCX/PDF replay of all 26 cases. Domain-human review and
assistive-technology testing remain unperformed; expected reader answers are
hypotheses, not certifications. #444, #454 and #455 retain the wider format,
profile and reader work; #378 retains broader saved-artifact/media acceptance.
