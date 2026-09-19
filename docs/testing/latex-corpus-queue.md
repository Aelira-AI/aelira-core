# Saved LaTeX corpus through the document queue

The required `Real document queue and artifact acceptance` CI job replays all
eleven hash-pinned sources in `tests/fixtures/latex_validation/corpus.json` through
the real API, PostgreSQL, durable scan/remediation worker and artifact storage.
This is a bounded increment of #444 and #375, supporting the dependency checks
in #378. It is API integration coverage, not a browser or conformance claim.

## Predeclared expectations

Every case requests **TEX only**, with AI disabled. The eleven unchanged originals
omit language and must require author review, with no fabricated English repair
or downloadable artifact. The runner also creates eleven labelled `-reviewed`
variants with an explicit synthetic `pdflang=en` declaration. This declaration
is authored by the test fixture builder, never by remediation. Both inputs and
their hashes are retained; original fixture hashes remain unchanged.

Ten reviewed variants must exercise source
findings and at least one verified source repair, publish a TeX candidate, retain
its artifact identity after reload, and deliver identical bytes on repeat download.
Uploading those downloaded bytes as a new scan must reproduce the job's source
score and remaining findings. Only TEX may appear in the available-format list.
N04's original and reviewed variant have an unbalanced expression that prevents
complete source comparison: the gate requires
a persisted `manual_required` refusal, zero published fixes, no verified score,
no available formats and HTTP 404 from the download route, including after reload.

Each published variant's saved document body must match its input byte for byte after UTF-8 decoding,
including whitespace, equation source, labels, references, captions and missing
inputs. All original preamble lines must remain in their original order. Preamble
additions are allowed; this check does **not** validate the meaning or rendering of
added metadata and packages. Authored-language/metadata work remains under #451.

| Cases | Content that the comparison protects |
| --- | --- |
| M01 | Fraction numerator and denominator |
| M03, M04 | Different nested/same-base script structures |
| M06 | Matrix values and cell order |
| M10 | Physics macro source and derivative order |
| M14 | Aligned derivation, labels and cross-references |
| M16 | Complete long expression, including the final sentinel |
| N01–N04 | Undefined macro, missing include, missing figure and malformed math |

N01–N04 are **compiler-negative** fixtures. N01–N03 can deliver their TeX source;
it must never be reported as successful compilation or accessible output. Every
receipt must retain `not_verified`, human review required, and unassessed fidelity,
structural validation and assistive-technology checks. The application receipts
record no compilation claim. A separate acceptance step compiles all eleven
originals and the ten downloaded candidates, using their recorded hashes. Seven
positive sources and outputs must compile; the four original negative controls
and three delivered negative candidates must fail. A missing or killed compiler
cannot satisfy a negative expectation. N04 has no output to compile.

This gate caught #473: language remediation added `\hypersetup` without loading
`hyperref`. M01 compiled before remediation and failed after download. The fix
loads the dependency before adding the command, preserving existing package options.
The separate [PDF validation replay](latex-pdf-validation.md) tests PDF refusal;
the PDFs compiled here are diagnostic artifacts, never approved accessible outputs.

## Running and interpreting evidence

Use the [disposable document stack](document-review-journey.md). Its existing
runner now includes the corpus automatically. The preservation checker has
positive and deliberate corruption controls, executable without the services:

```sh
node --experimental-strip-types --test scripts/latex_corpus_contract.test.ts
node --experimental-strip-types scripts/verify_document_stack.ts
node --experimental-strip-types scripts/verify_latex_corpus_compilation.ts
```

`test-results/document-stack/report.json` records the source revision, tracked
diff digest, harness/manifest hashes, Node version, options, scan/job/artifact IDs,
source/output digests, exact public receipts and independent rescan IDs. Saved
`.tex` files sit beside it. CI also retains `runtime.json` with the actual Python
and relevant package versions. For a manual run, record those versions from the
API/worker environment, plus the resolved image digest when using a container;
the runner's host environment does not establish the server's installed versions.
Retain any local patch with its digest; a dirty tree is not a published revision.
The compiler step requires `pdflatex` (CI installs `texlive-latex-recommended` and
`texlive-science`), or `STACK_TEX_COMPILER=lualatex` with the fixture packages
installed. It writes `compilation.json` bound to the queue report digest, compiler
version, per-input hashes and exit codes, plus isolated logs under `compiler/`.
Compilation covers all eleven originals, eleven declared-language inputs and ten
exact queue downloads. Withheld outputs are recorded explicitly rather than compiled.
These processes use `-no-shell-escape` and a timeout and only accept the repository's
synthetic fixture paths and hash-bound downloads, not arbitrary uploaded content.

The oracle tests reject changed scripts, matrix cells, derivative order,
cross-references, deleted final terms and erased compiler-negative content.
They also reject false certification receipts. These controls test the gate's
sensitivity; they do not establish mathematical meaning or reader usability.

The remaining research fixtures, project dependencies, HTML/DOCX/PDF conversions,
domain-expert fidelity review and human/assistive-technology acceptance remain
open under #444 and its related issues. Real media/model acceptance is also still
outstanding. This increment cannot close the full corpus or dependency tracker.
