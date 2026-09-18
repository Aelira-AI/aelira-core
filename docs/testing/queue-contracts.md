# Queue route, worker and PostgreSQL race evidence

The durable queue does not expose a generic job mutation REST API. Job submission belongs to the relevant integration, scan or remediation route. The queue worker owns progress, completion, retry and cancellation acknowledgement. [#430](https://github.com/Aelira-AI/aelira-core/issues/430) replaces tests of the old hypothetical `/api/jobs` CRUD surface with contracts for the implemented boundaries.

## Current HTTP contracts

[`tests/test_job_queue.py`](../../tests/test_job_queue.py) exercises production Google/Microsoft job status and list routers with real disposable PostgreSQL records and supplied session identity. It checks pending/processing/completed/failed response fields, provider and department filtering for lists, status filtering, ordering and limits, missing/other-department records, authentication, input validation and query failure. It also checks department-scoped `/integrations/metrics` counts.

The fixture seeds job states. A response reporting `completed` therefore proves serialization of a completed record, not execution of its worker. `test_enqueue_priority_and_claim_query_follow_durable_contract` uses the actual enqueue service and PostgreSQL claim-selection query: default priority is 5, and lower numbers run first. It does not claim ownership or run a worker. Invalid priority values are refused before storage.

## Disposition of the 25 generic REST cases

These are the former cases in `test_job_queue.py`, grouped by their old class names. Retiring an unsupported request assertion does not count it as a passing test. The replacement references below state which behavior is still tested and which imagined API is absent.

| Former class and cases | Implemented boundary and replacement |
| --- | --- |
| `TestJobEnqueueing`: `test_enqueue_sync_job`, `test_enqueue_scan_job`, `test_enqueue_remediate_job`, `test_enqueue_upload_job` | Sync: POST `/integrations/sync`, covered by `test_task17b_routes.py::test_integration_sync_http_route_replaces_legacy_501`. Scan/remediation: the provider routes and `/education` queue contracts in `test_local_scan_routes.py`, `test_canvas_remediate_endpoint.py`, and `test_remediation_queue_routes.py`. Upload: approved-artifact publication through Canvas routes, covered by `test_canvas_direct_route_scope.py::test_upload_delegates_approved_artifact_to_managed_file_writer` and the upload worker cases below. No generic enqueue endpoint is introduced. |
| `TestJobEnqueueing`: `test_enqueue_invalid_job_type`, `test_enqueue_with_priority` | The central enqueue service validates registered types and bounded priority: `test_task17b_enqueue.py::test_enqueue_requires_exact_dict_payload_and_registered_type` and the priority cases in `test_job_queue.py`. Job type/priority are service inputs, not new public REST parameters. |
| `TestJobStatus`: `test_get_job_status`, `test_get_job_status_with_progress`, `test_list_department_jobs`, `test_list_jobs_filtered_by_status` | GET `/google/jobs/{job_id}`, `/microsoft/jobs/{job_id}`, `/google/jobs` and `/microsoft/jobs`, exercised by the new persisted status/list cases. Detail lookup follows the implementation's department scope; provider filtering is asserted on lists. |
| `TestJobStatus`: `test_list_jobs_filtered_by_type` | Retired unsupported REST filter. Current provider lists expose status and limit, not a job-type filter. The real claim query limits executable types; `test_durable_job_processor.py::test_claim_query_is_skip_locked_and_dependency_gated` covers that separate worker boundary. |
| `TestJobProgress`: `test_update_job_progress`, `test_job_progress_validation`, `test_mark_job_complete`, `test_mark_job_failed` | Public job status is read-only. Worker-owned updates are covered by `test_durable_job_processor_postgres.py::test_to_thread_blocking_work_keeps_lease_heartbeat_alive`, `test_false_success_never_completes_and_deterministic_failure_is_terminal`, and `test_upload_success_confirms_effect_and_completes_once`. The former arbitrary REST progress write/validation assertion is retired, not implemented. |
| `TestJobCancellation`: `test_cancel_pending_job`, `test_cancel_in_progress_job`, `test_cannot_cancel_completed_job` | DELETE `/education/scans/{scan_id}` requests scan cancellation. `test_worker_isolation.py` covers the handler's pending/processing behavior, while `test_durable_job_processor_postgres.py::test_cancellation_terminalizes_only_after_child_group_is_reaped` verifies actual worker acknowledgement. `test_finish_cannot_overwrite_committed_cancellation` protects terminal ownership. Generic DELETE-job semantics, including the old permissive completed-job assertion, are retired. |
| `TestJobRetry`: `test_retry_failed_job`, `test_retry_with_max_attempts` | Retries are worker policy, not a public retry endpoint. `test_retry_exhaustion_and_worker_heartbeat_state`, `test_upload_pre_request_failure_keeps_bounded_retry` and `test_upload_timeout_after_checkpoint_is_terminal_without_retry` in the PostgreSQL worker suite distinguish bounded retry from unsafe replay. |
| `TestJobPrioritization`: `test_high_priority_job_processed_first`, `test_default_priority` | `test_job_queue.py::test_enqueue_priority_and_claim_query_follow_durable_contract` verifies stored default/explicit values and real query ordering. The old test incorrectly described 10 as higher priority than 1; current ordering is ascending. |
| `TestJobQueueStats`: `test_get_queue_stats`, `test_get_queue_stats_by_type`, `test_get_queue_depth` | Department counts are exposed by `/integrations/metrics` and covered by the new persisted metrics case. Global operator queue depth is `/api/jobs/worker-status`, covered by `test_job_worker_status_auth.py` with its super-admin boundary and closed aggregate response. Generic stats/depth and group-by-type REST endpoints remain absent. |

## Retired mock-only worker and SQLite cases

The four former `TestJobWorkers` cases patched a worker function and awaited that same mock. They never exercised a production worker and are removed:

| Former case | Actual replacement evidence |
| --- | --- |
| `test_cloud_sync_worker` | `test_task17b_handlers.py::test_cloud_sync_partial_folder_failure_is_typed_retryable` executes the sync handler with a controlled provider edge; `test_cloud_provider_live_contracts.py` tests adapter DTOs and sync persistence arguments. Neither is a live cloud journey. |
| `test_scan_worker` | `test_local_scan_job.py::test_file_scan_executes_disposable_copy_and_finishes_queue_normally` and PostgreSQL `test_local_scan_commit_then_response_loss_reconciles_on_exhausted_attempt` exercise actual handlers and persistence boundaries. |
| `test_remediate_worker` | `test_remediation_execution_hardening.py::test_local_job_uses_scan_authority_without_cloud_credentials` executes the remediation handler; its claimed-timeout and revoked-review cases cover terminal failure. |
| `test_upload_worker` | Required PostgreSQL upload success, pre-request retry, checkpoint crash, timeout and heartbeat-ownership cases exercise the real worker with controlled external effects. |

The skipped SQLite `test_real_cancellation_waits_for_child_reap_before_terminal_state` is also removed. Its required PostgreSQL replacement, `test_cancellation_terminalizes_only_after_child_group_is_reaped`, checks a real child process, persisted cancellation, absence of a late side effect, and terminal state only after the child is reaped. The replacement was executed before retirement. Its neighboring Linux-only worker cases remain required in Linux CI; a macOS run cannot satisfy them by skipping.

## Required CI profiles

| Profile | Database and scope | Required evidence |
| --- | --- | --- |
| `main` | Ordinary disposable `aelira_test`; provider HTTP and service contracts | Exact required node IDs and the unchanged 68% full-suite coverage floor. The two optional race skips remain visible here because this profile does not configure their separate database. |
| `worker-postgres` | `worker_isolation_test` | The existing exact 41 worker cases, unchanged, with no allowed skips. |
| `queue-races-postgres` | Separate `queue_races_test` | Exactly the concurrent enqueue unique-winner case and concurrent refresh identical-pair case, with no allowed skips. |

The race step in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) explicitly sets `TEST_DATABASE_URL`, `TEST_MIGRATION_DATABASE_URL`, `ALLOW_DESTRUCTIVE_MIGRATION_TESTS=1` and `REQUIRE_QUEUE_POSTGRES_TESTS=1`. The shared test helper validates the existing disposable PostgreSQL name/host/opt-in rules before connecting. Missing or unavailable PostgreSQL fails the required lane; an ordinary optional run without a configured race database reports a skip. The guards have positive, missing-database, unsafe-target and connection-failure tests in `test_queue_race_guards.py`.

The two concurrency tests retain their actual assertions: simultaneous enqueues produce one stored winner; simultaneous refreshes serialize on the session row and return the identical token pair, with a later replay refused. No live provider or application database is involved.

All profiles emit revision-bound JSON, validated by `scripts/verify_test_evidence.py` and retained in the existing CI artifact. The worker and race profiles overlap cases skipped by the main profile; do not add their counts as distinct additional tests. Local subset results do not establish full Linux CI, full-suite coverage, or a live cloud user journey.
