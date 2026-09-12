# Synthetic LTI visual fixture

From `dashboard`, run `bun tests/fixtures/lti-preview.ts` and open
`http://127.0.0.1:4321/tests/fixtures/lti-preview.html`.

The harness renders the actual `LTICourseView`, `LTIFilePicker`, `LTILayout`,
toast provider, and dashboard CSS. Its separate Vite configuration replaces
the session hook and API clients with synthetic in-memory data. It does not
exercise LMS authentication, integrations, scans, remediation, or writeback.
No credentials are needed or stored. Remote actions reject locally.

Walkthrough:

1. Open **Course view** in the fixture banner. Inspect the course average scan
   score, Content type scores, and content rows. Their screen-reader text
   identifies automated scan scores and the need for conformance review.
2. Click the page's **Files** tab. Inspect **Scan score 90+**, individual file
   scores, the measured zero, and the unscanned file.
3. Click **File picker** in the fixture banner. Inspect the one-decimal scores
   `95.7%`, `80.3%`, and `0.0%`, alongside the unscanned file and its **Scan first**
   label. Selection is disabled at the fixture API boundary and cannot embed
   content in an LMS.

The fixture banner remains visible to distinguish this walkthrough from an
authenticated integration check. Browser validation should use the project's
approved browser workflow; this harness does not run browser automation.
