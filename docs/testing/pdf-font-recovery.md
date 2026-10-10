# PDF font recovery diagnostics

The repository-authored corpus in `tests/fixtures/pdf_font_recovery/manifest.json`
covers nine synthetic page-direct font cases. It checks exact decoded text,
explicit abstention when mappings are ambiguous, and unchanged 72 DPI RGB
pixels after a compiled mapping. These checks do not establish universal text
fidelity or accessibility conformance.

Run the unit assertions with
`python -m pytest -q tests/test_pdf_font_recovery_corpus.py`. For a JSON runtime
receipt, run `python scripts/pdf_font_recovery_corpus.py --output receipt.json`.
Use `--require-pinned` when the installed package versions must match the
repository's pinned diagnostic environment. The receipt includes synthetic
expected text and is created only at a new path with owner-only permissions.

For a local PDF, `python scripts/inspect_pdf_font_recovery.py input.pdf` prints
a content-free summary. `--detailed-output glyphs.json` writes glyph text and
geometry to a new owner-only file; treat that file as sensitive. The inspector
is read-only and reports exclusions. It does not authorize a map change.

`python scripts/prepare_pdf_font_maps.py input.pdf proposal.json` creates a
deterministic, source-bound proposal when possible. After an authorized human
review, `python scripts/prepare_pdf_font_maps.py input.pdf output.pdf
--reviewed-plan proposal.json` compiles the supplied v2 proposal. Both modes
refuse to overwrite an existing output. A review reference records provenance;
it does not authenticate an approver. The compiler checks saved-output
consistency and appearance, while meaning and final approval remain separate
review decisions. Inputs are bounded to 32 MiB and plans to 4 MiB by the
library.
