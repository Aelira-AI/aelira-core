# Browser user-journey suite

This suite drives the real dashboard against the real stack: API, durable worker, PostgreSQL, Redis and a mail catcher, with no AI provider configured. Nothing is mocked or routed. It is the evidence the release journey inventory calls browser evidence on a real stack; the inventory still reports every journey as not evaluated, and a green run is one revision's result, not a conformance claim.

Run it from `dashboard/`:

```sh
npx playwright test -c playwright.journeys.config.ts --project=setup --project=journeys
```

Start the stack first. CI does it with `.github/actions/journey-stack`, which installs the document runtime, starts PostgreSQL, Redis and Mailpit, serves the fixture site on its own address, applies the migrations and starts the API and worker. For a local run, `tests/journeys/stack.compose.yml` builds the same topology around an API image (`JOURNEY_API_IMAGE`) and needs `JOURNEY_JWT_SECRET`, `JOURNEY_ENCRYPTION_KEY` and `JOURNEY_REPLAY_KEY` set to throwaway values. The speech model is read from a local cache and the stack runs with `HF_HUB_OFFLINE=1`.

## Sign-in

`auth.setup.ts` is the first-run bootstrap a self-hosting administrator performs: request a magic link for the first address, follow it from the delivered message and confirm. On an empty database that person becomes the administrator. The sequence is keyboard-only and axe checks each page. The magic-link limit is five requests per address per hour, so every journey reuses the stored session instead of signing in again.

## Journeys

| Journey | What it does | Independent check |
|---|---|---|
| Documents: PDF | Upload, scan, issues, remediation and download by keyboard alone | Poppler reads the saved title; the saved bytes are uploaded again and must score as the dashboard displayed; the API receipt names the downloaded SHA-256 |
| Documents: DOCX | Same path with a pointer, then review, approve, reload and approve the file | `unzip` shows the added title and Heading 1 with the author's text unchanged; approval leaves the saved bytes and receipt unchanged |
| Documents: image left for review | A DOCX is delivered with its title fixed while its picture has no description; an image-heavy PPTX is withheld entirely | The saved package carries no description or title on the picture; no fix is counted for it; the withheld deck offers no file and no suggestion |
| Web | Scan a two-page static site served on its own address | Every finding names markup that exists in the fixture; the second page is crawled and only its own picture is reported against it; findings are stored as pending review rows; website remediation reports that it cannot start |
| Media | Upload a synthetic spoken clip | The transcript equals the spoken words; WebVTT and SRT are well formed and fit the clip; audio description is reported as unavailable; remediation reports that it cannot caption media and offers no file |
| Canvas | Register the tool, connect Canvas, launch from the course | See below |

The fixture site is on `11.99.0.10` because the product refuses to scan private and loopback addresses. The address is only ever bound to the runner's loopback device, or to the stack's private network for a local run.

## Accessibility

Every dashboard page a journey visits is checked with axe against WCAG 2.1 A and AA, waiting for entrance transitions to finish so a half-faded toast is not measured. Serious and critical violations fail the test; lesser ones are recorded in the attached axe report and in the job summary. Canvas pages are not ours and are not scanned. Only the document PDF journey and sign-in are keyboard-only; the other journeys use a pointer after the same axe checks.

## Canvas

The Canvas journey is environment-bound. It needs a Canvas instance, a course copy, a Teacher enrolled only in that copy and two developer keys, one for the LTI launch and one for API access. Canvas cannot reach the runner, so the LTI key carries a static public key and localhost redirect URIs. Tool-side configuration is the self-hoster's: environment variables on the API plus one call to `POST /integrations/lti/registrations` by the first administrator. The nine credentials are repository secrets named `AELIRA_JOURNEY_*`.

Fork and Dependabot pull requests receive no secrets. There the job is skipped, the aggregate job accepts that skip only for those events and says the journey was not run, and the Playwright project reports each test as skipped with the reason. Anywhere secrets should exist, a missing credential fails the job.

## What this does not establish

No domain-human or assistive-technology review, no semantic proof of saved documents beyond the checks above, no rendered-result validation for websites, no caption or transcript download in the dashboard (the product does not offer one), and no accessibility conformance claim. The inventory in `tests/fixtures/release_journeys.json` lists each entry's limits.

## Controls

A passing journey is only evidence if it fails when the behaviour breaks. For each journey the suite was run against a deliberate break and then against the unmodified code: a download whose bytes differ from the receipt, an alt-text description written without seeing the picture, a transcript that is not what was said, a page with an unnamed control, and the pre-fix web scan persistence. None of the breaks is committed.
