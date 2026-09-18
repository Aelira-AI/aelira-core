# LaTeX conversion and accessibility evidence

Related issues: #446 and the broader corpus work in #444.

LaTeX results expose `latex_evidence`, keyed by representation (`tex`, `mathml`,
`html`, `pdf`, or `docx`). Each receipt records a schema version, source SHA-256,
candidate SHA-256 when available, and six separate observations:

| Observation | Meaning |
| --- | --- |
| `conversion` | `completed` means a converter produced candidate bytes; `failed` means conversion failed or the known equation conversion set had failures. It does not establish completeness or fidelity. |
| `structural_validation` | The PDF receipt's combined pikepdf/veraPDF outcome. Other representations and unattempted PDF validation are `not_assessed`; unavailable PDF validation stays `unavailable`. |
| `source_check` | `completed` records that the named TEX source scanner ran, with `findings_count`. It does not mean zero findings or output conformance. |
| `fidelity` | `not_assessed`: no mathematical or content-equivalence certification has run. |
| `human_review` | `not_assessed`: no human review is implied by generation, a scanner pass, or download availability. |
| `assistive_technology` | `not_assessed`: no reader test has run. |

`accessibility_status` remains `not_verified` and `human_review_required` remains
true, including when PDF machine checks pass. Methods name the bounded check or
generation path; they do not claim execution when the status is unavailable.
`unknown` is reserved for observations whose outcome cannot be established.
Unsupported positive fidelity/human/AT claims, invalid hashes, and extra fields
are rejected at the public projection boundary.

## Representation and accounting boundaries

Equation receipts bind the complete equation source and generated MathML, not its
truncated display preview. Document receipts bind the document source and output.
The asynchronous raw-content processor returns source and per-equation evidence;
it does not claim that its separate HTML-export helper has validated an export.
The source hash for remediation identifies the original input, while the candidate
hash identifies each generated representation, including the saved TEX file.

Saved TEX source-check evidence is issued only when the paired source measurement
matches the current source and saved output. Fixed/remaining/manual accounting
continues to use that saved-source verification. A source-check result cannot
validate HTML or PDF, and a PDF machine-check pass cannot become a source rescan.
A refused PDF can retain its digest and failed/unavailable observation without
becoming a downloadable artifact.

The existing managed LaTeX artifact allowlist remains TEX-only. Evidence receipts
do not enable PDF/HTML/DOCX publication or weaken artifact ownership, hash,
verification, or review gates. Converter diagnostics and content-loss detection
remain separate work in #447.

## Compatibility and consumers

- `MathMLConversionResult.wcag_compliant` remains a boolean but is deprecated and
  always false, meaning **not verified**, not a confirmed WCAG violation. It must
  not be used to invent a missing-label or missing-MathML finding.
- Both `process_latex_background` and `ScanService.store_latex_scan` store source
  findings separately from conversion/review diagnostics. The latter now stores
  the actual source findings rather than substituting equation-output findings.
- `compliance_score` and the raw processor's `compliance.score` remain source-check
  metrics. The raw response labels this scope explicitly. `conversion_success_rate`
  remains the yield among detected equations, including its legacy 100 value when
  none were detected. `conversion_scope=detected_equations_only` and the unverified
  evidence prevent that empty-set value from representing mathematical coverage.
- Direct remediation responses, child results and failures, persisted job results,
  job-status reloads, and synchronous compatibility responses carry the bounded
  receipts. Non-LaTeX responses retain their existing shape.
- The scan-details API projects historical LaTeX equation booleans to false and
  marks accessibility unverified. Missing historical receipts stay empty: no
  evidence is reconstructed from old scores. Stored records are not rewritten.
- Repository CLI and dashboard code have no consumers of `wcag_compliant` or
  `conversion_success_rate`. Existing score displays retain the source score;
  those scores are not conformance certificates. No new UI or reader behavior is
  claimed by this backend change.
- Generated HTML and the raw HTML-export helper retain their output behavior.
  Conversion evidence does not certify exported HTML. The existing
  `latex_pdf_validation` contract remains available alongside `latex_evidence`.

## Verification

```sh
python -m pytest tests/ --collect-only --no-cov
python -m pytest tests/test_latex_evidence_outcomes.py \
  tests/test_latex_scan_scoring_boundary.py tests/test_latex_validation_routes.py \
  tests/test_source_scoring_verification.py
```

The corpus tests run the real equation processor, with AI disabled, over the eleven
hash-pinned sources already imported for #445. Their oracle is conservative claim
separation and source preservation, not mathematical correctness. They include
script attachment, matrices, physics macros, alignment/references, a long final
term and malformed/missing-input sources. Separate controls cover unsupported
macros, missed equation environments, no detected equations, and CRLF provenance.

PostgreSQL/API tests cover TEX-only success, explicit TEX plus refused PDF, PDF-only
failure, actual child execution, persisted job reload, available formats, exact
downloaded TEX bytes, and historical scan projections. Worker outcome completion
is a fixture boundary; queue ownership/races have their own required suites.
The positive PDF machine-check receipt is a controlled test double. No live
veraPDF, semantic certification, human review, or assistive-technology result is
claimed. The full #444 corpus and reader journey remain open.

Converter-stage loss diagnostics are documented in
[conversion diagnostics](latex-conversion-diagnostics.md).
