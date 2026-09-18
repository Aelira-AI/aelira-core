# LaTeX conversion diagnostics

Issue #447 adds bounded loss checks to the LaTeXML, Pandoc, LuaLaTeX and
pdfLaTeX wrappers. Exit zero and a generated file no longer establish that a
candidate can be returned. This is not a complete content-preservation test,
semantic equivalence proof, or accessibility certificate.

## Result contract

`latex_evidence.<format>.conversion_diagnostics` carries request-local stage
observations through remediation, child execution, persisted jobs and public
results. Existing evidence fields and the deprecated false `wcag_compliant`
boolean retain their meanings. No new managed delivery format is enabled.

Each attempted parser, postprocessor or compiler stage records the executable,
a numeric version when obtainable (`unknown` otherwise), input and candidate
SHA-256, exit status, classified severity, and a source line when reported.
The line belongs to that stage's input hash, which can be preprocessed TEX or
intermediate XML. The HTML renderer records its input/output hashes and result;
its version is currently `unknown`. Skipped stages are not invented.

Public diagnostics contain fixed codes, never converter text, source excerpts,
paths or reference names. Limits are 24 stages, 32 findings per stage, 128
reference observations and 64 KiB of diagnostic text considered per process.
Oversized diagnostics are explicitly refused. XML/HTML inspection is limited
to 16 MiB candidates. Process capture still uses the existing subprocess timeout;
the diagnostic limit is not an operating-system resource sandbox.

`accepted` means the wrapper returned a candidate after its bounded checks.
`refused` means it did not. PDF structural/independent validation remains a
separate gate and can refuse otherwise generated PDF bytes. The overall
accessibility status remains `not_verified`, and fidelity/reader checks remain
`not_assessed`.

## What is checked

- Explicit missing input/include/bibliography/graphic dependencies, with source
  locations. Macro-expanded paths and full project ingestion are not implemented.
- Parser errors, unsupported commands, malformed expressions, missing assets,
  unresolved references and unknown warnings, including exit-zero diagnostics.
- LaTeXML error nodes and retained semantic XML; raw-TeX math spans, empty
  equations/citations, caption-only figures when the source requests graphics,
  missing local image files, unverified external images and missing link targets.
- Known layout, font substitution, title and deprecation warnings are retained
  without automatic refusal. First-pass TeX reference warnings can resolve on
  pass two; unresolved final-pass references remain errors.

Known loss is terminal for that representation. Another converter cannot hide
it by returning a file. Separately requested TEX remains eligible for its own
source checks and existing delivery rules. Fresh attempt directories prevent
an old sibling HTML/PDF file from being mistaken for a new result.

Each fragment link records a hashed target and target existence. Target identity
and reader activation remain separate `not_assessed` observations. These checks
do not equate the number of source blocks with the number of output nodes, or
claim coverage of every source expression, table relationship or authored word.

## Private evidence

Attempt directories are created with mode 0700 under the job output directory.
Preprocessed TEX, intermediate LaTeXML XML and structured stage JSON remain for
review; XML/TEX and JSON use mode 0600. Native LaTeXML logs remain in the private
attempt directory. These files follow that job directory's retention lifecycle.
They are not added to `output_files` or public download descriptors. Operators
must treat semantic artifacts and native logs as document content, not telemetry.
The public receipt keeps hashes and safe codes only.

## Replay

The eleven hash-pinned synthetic sources from #444 are reused without changes.
With the relevant converters installed:

```sh
python -m scripts.replay_latex_conversion_diagnostics --engine pandoc --output /tmp/pandoc-loss-replay
python -m scripts.replay_latex_conversion_diagnostics --engine latexml --output /tmp/latexml-loss-replay
pytest tests/test_latex_conversion_diagnostics.py tests/test_latex_validation_routes.py --no-cov
```

The replay reports exact source preservation, returned/refused candidates and
stage receipts. Expected behavior: all four N cases are refused; LaTeXML keeps
the seven positive controls; Pandoc refuses M10's unsupported physics macros and
M14's unresolved equation targets while retaining the other five controls.
Converter versions can change these capabilities; a changed expectation requires
review of the receipts and output, not silently changing the test expectation.

Regression tests also exercise final-pass compile warnings, postprocessor-only
loss, stale files, oversized diagnostics, timeouts, request isolation, safe job
projection and persisted API reload. Compiler subprocesses and the PDF validator
are controlled in ordinary unit tests. Real replay is a separate explicit lane;
no browser, Word, PDF/UA, speech, braille or human-review result is claimed.
