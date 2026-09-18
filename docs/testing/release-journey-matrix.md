# Release journey inventory

This is the inventory prerequisite for [#375](https://github.com/Aelira-AI/aelira-core/issues/375), which remains open. The machine-readable matrix is [release_journeys.json](../../tests/fixtures/release_journeys.json). It records existing evidence boundaries before additional journey coverage is added.

| Pillar | Existing evidence | Missing full-journey evidence |
|---|---|---|
| Document | Real API/PostgreSQL/Redis/worker scan, remediation, persisted approval and exact saved download; route contracts for pending, expired and superseded artifacts; injected validator failures | Dashboard interaction against those services, supported editing, reload, failure states and revision-bound browser receipts |
| Canvas | Real server/database journey with stubbed provider/scanner edges; dashboard browser smoke against a simulated service | A scoped course file through real services and approved LMS publish-back; provider-version changes, denied access, interruption/retry and duplicate prevention |
| Web | Environment-gated scanner component cases using generated local file URLs | Controlled local HTTP site, sourced findings, proposed changes, review and rendered-result validation through the application |
| Media | Processor and caption-format tests with mocked transcription and ffmpeg/ffprobe | Synthetic clip through real transcription/captions, supported review and actual downloadable output; explicit unsupported audio-description results |

Test filenames containing `e2e` are not evidence classifications. A simulated browser service and a separately tested backend cannot be combined into an inferred full user journey. Unit, component integration, API integration, simulated browser and manual procedure entries retain their own boundaries.

## Executable inventory check

```sh
python scripts/verify_release_journeys.py \
  --revision FULL_COMMIT_SHA \
  --output test-results/journey-matrix.json
```

The supplied revision must identify the checkout being inspected. Use a fresh output path; previous reports are not overwritten. The validator requires all four pillars, nonempty limitations and missing-evidence lists, repository-contained source files, unique entries, supported evidence classes, and valid CI job/step/command references. CI bindings that ignore failures are rejected.

The report records the supplied revision, SHA-256 of canonicalized manifest content (sorted JSON keys without spacing), the workflow bytes and every referenced source file. The main CI test job validates this inventory and retains the report with its existing JSON evidence artifact.

**Exit zero means the inventory references are valid. It does not mean a journey passed.** The tool executes no journey commands and reads no test-run results. Its report always says `journey_verification: not_evaluated` and each entry says `execution: not_run_by_inventory`. A CI binding describes configured wiring, not execution, collection or skip status. Consult the corresponding run's actual results separately. Manual procedures are instructions, not observed browser evidence. Changes that add true full-journey evidence require an explicit schema/validator review; version 1 cannot certify it.

## Next acceptance work

Start with the [document browser walkthrough](document-review-journey.md#browser-walkthrough) against the real local stack. Bind screenshots and observations to the revision, scan/job/artifact identities and saved-file hashes. Cover an authorized success as well as unavailable AI, failed verification, pending review and expired/superseded outputs. Keep injected failures distinct from observed service failures, and preserve tenant/course isolation tests.

Canvas environment-bound LMS evidence must be reported separately from the simulated release smoke. Web acceptance needs only an owned local test site. Media acceptance must expose unsupported behavior and retain real output, not count mocked transcription as a completed clip.

Before a release tag, every changed page/flow needs a manual browser walkthrough with revision-bound evidence and explicit blockers. This inventory adds no page or flow and supplies no manual execution receipt, accessibility conformance claim or release-readiness decision. The remaining work is tracked by #375 and release tracker #379.
