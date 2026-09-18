# LaTeX PDF validation and delivery boundaries

Related issues: #445 (PDF validation) and #444 (broader corpus replay).

## Acceptance contract

`LaTeXConverter.convert_to_pdf()` returns a PDF path only when both the bounded
pikepdf structural check and the existing veraPDF `ua1` integration pass on the
candidate bytes. LuaLaTeX, LaTeXML/browser PDF and pdflatex share this gate.
Compiler nonzero exits cannot return a partial PDF. Each export attempt owns a
fresh directory, so a pre-existing sibling PDF cannot stand in for its output.

`convert_to_pdf_with_validation()` also returns a typed receipt. Remediation
results, child-process responses, stored job failures, job status and the
synchronous compatibility response carry its public `latex_pdf_validation` field:

- `status`: `passed`, `failed` or `unavailable`.
- `reason`: a bounded code, without filesystem paths or exception text.
- `candidate_sha256`: the exact candidate digest, or null when no readable
  candidate exists.
- `validator`: `pikepdf+veraPDF`; `profile`: `ua1`;
  `structure_profile`: `latex-pdf-content-references-v1`.
- `structure_status` and `independent_status`: outcomes of the individual checks.
- `human_review_required`: always true.

Validator/profile fields identify the configured checks, not evidence that an
unavailable check ran. A `passed` receipt requires a digest and both checks to
pass. The independent validator receives a private byte snapshot; changes to
either snapshot or candidate invalidate acceptance.

The structural check requires language, marked content, a structure tree and
references to actual marked-content IDs on known pages. A page pointer by itself,
a string-valued child or a nonexistent MCID cannot satisfy it. This is a bounded
check, not a complete implementation of PDF/UA: for example, form-XObject content
and object-reference-only structures are not supported by this profile and may
be refused conservatively.

## Operator behavior

PDF acceptance requires pikepdf and `VERAPDF_ENABLED=true` with a working
`VERAPDF_URL` configured for the existing REST integration. Disabled, unreachable,
empty or wrong-profile independent validation cannot verify a PDF. No secrets or
environment changes are required to run the deterministic regression tests.

A retained failed candidate is diagnostic material, not a downloadable verified
artifact. A PDF-only request cannot silently succeed by returning TEX. When TEX
was explicitly requested, it can still be source-verified and delivered even if
PDF validation is unavailable; the PDF receipt and human-review flag remain.

Existing publication protections remain authoritative: the managed-artifact
allowlist accepts **TEX for a LaTeX scan**, not generated PDF or HTML. A successful
PDF machine check does not provide a comparable LaTeX source/output score, enable
PDF delivery, or bypass descriptor, hash, tenant or review checks. HTML selection
and broader per-format result behavior are separate work in #446.

## Evidence and reproduction

Before running the focused tests, check collection of the full suite so direct-file
module loaders are exercised too:

```sh
python -m pytest tests/ --collect-only --no-cov
```

The focused validation tests are:

```sh
python -m pytest tests/test_latex_pdf_validation.py \
  tests/test_latex_validation_corpus.py tests/test_latex_validation_routes.py
```

The PostgreSQL/API cases use the repository's disposable `TEST_DATABASE_URL`
contract. They cover the existing direct helper, real child-process execution,
committed outcome reload, mounted status/formats/download routes and exact TEX
artifact bytes. Dispatcher completion/lease ownership is a fixture boundary;
the existing worker/race suites retain that responsibility. These are backend
integration checks, not a browser user journey.

Independent-validator success and failure responses are controlled test doubles,
including the real veraPDF HTTP adapter/parser. The structural positive PDF is a
synthetic control; it is not a certified PDF/UA example. No live veraPDF service or
assistive technology was used for these regression results.

Eleven synthetic sources from #444 are included with hashes in
[`tests/fixtures/latex_validation/corpus.json`](../../tests/fixtures/latex_validation/corpus.json).
The deterministic suite checks saved-source preservation. An explicit real
compiler replay is available when LuaLaTeX and the fixtures' packages are installed:

```sh
python -m scripts.replay_latex_pdf_validation --output /tmp/latex-validation-replay
```

The replay retains compiler output and a JSON report. The initial run with
LuaHBTeX 1.21.0 (TeX Live 2025) met 11/11 expectations: seven valid
sources compiled
but their untagged PDF candidates were refused, and four negative sources failed
compilation. Source hashes remained unchanged. Optional compiler replay is not a
new CI skip or an external-service prerequisite.

This increment does not close #444. The full 26-case application replay,
mathematical/figure/table meaning, truncation, project assets, human authoring and
screen-reader behavior still require their own evidence.
