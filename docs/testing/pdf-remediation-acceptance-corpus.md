# PDF remediation acceptance corpus

Aelira Core ships eight required, compact PDF cases generated from neutral repository-authored content. They cover an accessible structural baseline and focused metadata, heading/reading-order, link, table, form, image/chart, and math/STEM failures.

Run the same required gate used by CI:

```bash
python scripts/pdf_acceptance_corpus.py --manifest tests/fixtures/pdf_acceptance/manifest.json
```

The manifest is the contract. Each required case names its generated fixture, supported journey stages, and machine-observable assertions. Removing a case or its last assertion fails before execution. Two clean generations must be byte-identical.

The metadata case runs scan, rule-based remediation, isolated artifact publication, structural validation, and rescan. Other cases stop where automated evidence stops. Image/chart, reading-order, and math/STEM semantic quality remains human review work. The live-provider visual-description quality case is explicitly quarantined outside the required core run.

Results are machine observations only, not proof of WCAG, PDF/UA, or legal conformance. A score or validator result must not be presented as a conformance decision.

Generated source PDFs live only in the runner's temporary source directory. Remediated artifacts live in a distinct output directory, and the runner verifies that original source hashes remain unchanged and output hashes differ.
