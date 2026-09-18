# Document review and saved-output acceptance

See the [four-pillar release journey inventory](release-journey-matrix.md) for evidence classes and remaining application-journey gaps.

The `Real document queue and artifact acceptance` CI job runs
`scripts/verify_document_stack.ts` against a real API, PostgreSQL, Redis,
durable worker and Mailpit. It uses synthetic documents and an administrator
created through the public magic-link flow. Transport, findings and artifact
storage are not mocked.

This is API integration coverage. The separate browser walkthrough below is
required to verify dashboard interaction; this gate alone does not establish
a full browser journey or accessibility conformance.

## Executable coverage

| Scenario | Required result |
| --- | --- |
| PPTX, XLSX, PDF metadata, PDF forms/links | Scan, queue remediation, download, independent upload/rescan and preserved source text/values |
| Successful outputs awaiting review | Single-fix approval, batch approval when multiple fixes are pending, fresh review read and recorded audit decisions |
| Output after approval | Same artifact ID, scanner receipt and byte-identical download; approval cannot manufacture a new score |
| DOCX and syllabus PDF requiring manual work | No downloadable artifact, no verified remediated score, zero published fixes and reconciled unresolved counts |
| Image-heavy PPTX with missing alt text, AI requested and no provider configured | Missing descriptions remain unresolved; terminal refusal with no artifact or success-shaped result |
| Incomplete academic-paper scan | Failed scan with actionable explanation and no score after reload |

Successful fixtures must contain at least one pending fix, so the review checks
cannot pass without exercising approval. Approval is bound to each fix's
recorded content digest. Evidence exports must explicitly remain
non-conformance determinations.

Failed-validator and invalid score-receipt branches also have backend regression
coverage in `tests/test_queued_pdf_output_claim.py` and
`tests/test_remediation_score_provenance.py`. Those tests inject controlled
failures; they are not evidence of a live validator outage. No-provider behavior
is covered both by the real-stack case and `tests/test_provider_neutral_routing.py`.

## Running the disposable stack

Build the current API image using the repository's production Dockerfile, then
start the acceptance stack from the repository root:

```sh
docker build -t aelira-document-test .
export STACK_API_IMAGE=aelira-document-test
export STACK_JWT_SECRET="$(openssl rand -hex 48)"
export STACK_ENCRYPTION_KEY="$(openssl rand -base64 32)"
export STACK_REPLAY_KEY="$(openssl rand -base64 32)"
docker compose -p aelira-document-test -f tests/document-stack.compose.yml up -d --wait
node --experimental-strip-types scripts/verify_document_stack.ts
docker compose -p aelira-document-test -f tests/document-stack.compose.yml stop
```

Use Node 22 or newer, `pdftotext` and `unzip`. The Compose project has isolated
application data and loopback API/mail ports. It mounts the current source and
migrations read-only. It uses production security settings, real session auth
and disposable encryption keys, with local HTTP cookie settings for this test.
Do not reuse these settings or identities in production.

The runner only accepts loopback HTTP endpoints and an `@example.org` identity.
AI primary and fallback providers must both be disabled. It writes `report.json`
and downloaded fixture outputs under `test-results/document-stack/`; CI retains
them as `document-journey-evidence`. Cookies and magic-link tokens are excluded.
Set `STACK_EVIDENCE_DIR` to preserve separate local runs. Record the source
revision and any working-tree diff alongside manual evidence.

## Browser walkthrough

Serve the dashboard with `VITE_API_URL=http://localhost:18300` on port 15373,
matching the test stack's dashboard URL. Use a separate browser profile and the
synthetic inbox at `http://localhost:18325`.

1. Sign in as `document.acceptance@example.org` through the dashboard magic-link
   request and explicit verification button.
2. Choose **New Scan → Excel Spreadsheets**, upload
   `tests/fixtures/document_stack/course.xlsx`, and start the scan. Record the
   scan ID, the two source findings and original score of 49.
3. Open **Remediation Review**, start remediation, and wait for the durable job.
   Download **Output for Review**. The expected scanner score is 100; the page
   must retain its limitation that automated scores do not establish conformance.
4. Inspect the saved workbook: the sheet is named `Course`, its table marks a
   header row, and `Course`, `Credits`, `History`, and `3` are preserved in order.
5. Open **Review**, select that scan, inspect both recorded changes and approve
   them. Refresh: both decisions must remain approved. Export the review evidence.
6. Return through **History** to the same remediation job and download again.
   Compare SHA-256 hashes before and after approval. Upload the downloaded file
   as a separate scan; expect zero scanner findings and score 100.
7. Inspect the withheld DOCX/syllabus and incomplete PDF cases from the gate.
   Confirm the dashboard exposes the unresolved/failed state without an output
   download or certified score.

Capture screenshots and exact scan/job/artifact identities. Keep these evidence
classes separate: browser observation, API integration, injected backend failure,
and semantic document inspection. PDF structural editing, PPTX reading-order
editing, LMS publish-back, web and media journeys have separate acceptance work.
