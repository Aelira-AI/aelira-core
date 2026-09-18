# Coverage and skipped-test evidence

Coverage is a regression signal, not evidence of correct remediation or accessibility conformance. The full backend suite measures `src` without adding exclusions and enforces **68%**. Critical route coverage is tracked in [#35](https://github.com/Aelira-AI/aelira-core/issues/35); complete user journeys remain [#375](https://github.com/Aelira-AI/aelira-core/issues/375).

## Measured baseline

The baseline is main revision `df49d84238eac5f6f9c052174441ea0e71730112`, [CI run 35240231688](https://github.com/Aelira-AI/aelira-core/actions/runs/35240231688). Its test job used Ubuntu, Python 3.14.7, the pinned development requirements, PostgreSQL 16, Redis 7, and the system/document tools and pinned Chromium installed by `.github/workflows/ci.yml`. `RUN_E2E_TESTS` was unset. No new coverage omissions were introduced.

| Measurement | Result at that revision |
| --- | ---: |
| Main-suite passed | 6,471 |
| Main-suite skipped | 384 |
| Covered statements | 45,549 |
| Total measured statements | 66,363 |
| Missed statements | 20,814 |
| Coverage | 68.64% |
| Separate PostgreSQL worker suite | 41 passed, 0 skipped |

The 68% floor leaves approximately 0.64 percentage points below this observed baseline. Do not lower it or broaden omissions to make a change pass. Raise it when repeatable evidence supports the increase. Targeted developer runs can use `--no-cov`; they do not establish a full-suite baseline.

The separate worker results overlap tests skipped by the main profile. Do not add them to the main-suite count and claim a distinct-test total. The same run also passed the real document API/worker/download acceptance job, PDF acceptance corpus, and native AMD64/ARM64 image checks. These are their stated integration boundaries, not a complete browser/LMS user-journey claim.

## Explicit skip dispositions

[`tests/ci_skip_policy.json`](../../tests/ci_skip_policy.json) lists every permitted main-profile skip by exact pytest node ID, with classification, accountable maintainer role, reason, source files and an existing follow-up issue. Roles identify maintenance areas; they do not assign an individual contributor. No wildcard permits future tests to skip automatically.

| Classification | Baseline skips | Continuing policy |
| --- | ---: | ---: |
| Environment-gated | 145 | 145 |
| Obsolete test contract | 155 | 80 |
| Test defect | 59 | 35 |
| Intentionally unsupported API contract | 25 | 0 |
| Total | 384 | 260 |

One defective encryption-key test caught its own assertion failure and skipped. It now checks the real token manager in production and staging, including a configured-key success path; both cases are required to execute. The 22 stale LTI registration skips have also been replaced with 34 required PostgreSQL route cases. The mixed email-preference filter now queries real PostgreSQL records instead of matching stringified SQLAlchemy expressions. Its obsolete skip allowance is removed. The 17 obsolete Google journey skips have been replaced by authenticated PostgreSQL file-workflow contracts, and their skip allowance is removed. The continuing-policy count is an allowance, not a claim that the final revision has already run in CI.

Several old mocked Canvas, Blackboard and Moodle suites are obsolete rather than waiting for credentials. Local LaTeX and scanner checks also have unnecessary blanket gates. These remain explicit defects or obsolete contracts under #374 or #375; listing them never counts them as passes. The 25 unsupported generic jobs REST cases, four mock-only worker cases and superseded SQLite cancellation case are retired with explicit [queue-contract mappings](queue-contracts.md). Their useful contracts are covered at the actual provider route or worker boundary; deleting an obsolete assertion is not counted as a passing test. The [route matrix](critical-route-coverage.md) records the restored LTI contracts and secondary route follow-ups.

The six obsolete shared integration-status mocks and 28 gated legacy webhook cases are replaced with 59 executing PostgreSQL route contracts. Their 34 obsolete skip allowances are removed. The contracts explicitly retain static health, local-only disconnection and subscription deactivation, and unimplemented subscription-management boundaries; they do not claim live provider behavior.

The 19 gated Microsoft journey cases and 22 permissive file-route assertions are replaced by [required Microsoft contracts](microsoft-route-contracts.md). Their evidence uses real PostgreSQL, real provider adapters and controlled HTTP transport; it does not require a live Microsoft account.

Environment-gated cases include the browser matrix, optional model/tooling and separately provisioned database/migration tests. The worker profile requires all 41 worker cases and permits no skips. Other environment-bound cases remain separately reported limitations with owners and issues; this change does not enable external integrations, run destructive migrations on normal databases, or establish assistive-technology evidence.

## What CI enforces

The opt-in `scripts.pytest_ci_evidence` pytest plugin records collected and deselected node IDs, setup/call/teardown outcomes, collection skips/errors, exit status, profile and revision. It omits stdout, tracebacks, exception messages and environment values. It does not change outcomes or skip decisions.

The main profile requires 1,709 exact critical cases spanning auth, first-admin setup, administrator user/invitation management, local scan and remediation queues, LTI registration, shared integration status and webhook contracts, Review, managed artifact delivery/write-back, Canvas routes and the server-side journey, account lifecycle/export, analytics allowed/failure paths, alert settings/delivery and preference filtering, provider job routes and race guards, Microsoft file/subscription routes and OAuth transport, Office saved-package preservation, PDF corpus and report contracts, LaTeX PDF validation/refusal and TEX delivery, Google file routes and their OAuth/adapter/webhook regressions, plus the repaired key checks. The separate worker profile requires 41 cases, and the separate PostgreSQL race profile requires two. Each required case must be present and passed; missing, deselected, skipped or expected-failure outcomes cannot satisfy it. These lists define the protected scope, not universal endpoint coverage.

`scripts/verify_test_evidence.py` rejects unknown skips, unexpected failures, malformed/incomplete evidence, wrong revision/profile, absent required tests and coverage below the floor. Module collection skips are recorded too, so a missing import cannot silently erase a required test module. An allowed test that starts passing is reported as progress, ready for policy cleanup. New skips require source review and an explicit disposition; adding an allowance simply to hide a regression is not a repair.

CI retains `test-evidence-<revision>-<attempt>` for 14 days: main/worker/race execution reports, coverage JSON and validator summaries. Summaries include exact line counts, test counts, skip classifications and report/policy hashes. Baseline data in the policy is historical; the retained report for the candidate revision is the final measurement. Failed runs also retain available evidence and remain failed.

## Reproduce the evidence

Use the CI job's dependencies and disposable PostgreSQL/Redis services. Follow its environment and database guards; do not point tests at an application database. From a clean checkout of the revision under test:

```sh
export GITHUB_SHA="$(git rev-parse HEAD)"
pytest tests/ -v --tb=short \
  --cov-report=json:test-results/coverage.json \
  --test-evidence=test-results/main-suite.json --test-evidence-profile=main
python scripts/verify_test_evidence.py \
  --report test-results/main-suite.json --coverage test-results/coverage.json \
  --policy tests/ci_skip_policy.json --profile main --revision "$GITHUB_SHA" \
  --output test-results/main-summary.json
```

The worker command and explicit destructive-test opt-in remain in `.github/workflows/ci.yml`, using a separate `worker_isolation_test` database. The session-refresh and enqueue races execute in `queue_races_test`, with `REQUIRE_QUEUE_POSTGRES_TESTS=1` and both explicit test database variables. These two cases remain allowed skips in the ordinary main profile, but must execute in the race profile. An ordinary developer machine may have additional skips; the CI policy deliberately fails them rather than treating a reduced environment as equivalent evidence.

The gate's own tests run real isolated pytest subprocesses. They prove successful reports pass, a real below-floor pytest-cov run fails, and missing/skipped critical tests, new skips, import skips, collection errors, deselection, xfail/XPASS, bad identity and malformed evidence are rejected. Run them with `pytest tests/test_ci_test_evidence.py --no-cov`.
