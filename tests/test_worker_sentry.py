from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, call


def test_worker_sentry_is_optional_and_uses_bounded_configuration(
    monkeypatch,
) -> None:
    from src.monitoring.worker_sentry import init_worker_sentry

    initialize = MagicMock()
    set_tag = MagicMock()
    monkeypatch.setattr("sentry_sdk.init", initialize)
    monkeypatch.setattr("sentry_sdk.set_tag", set_tag)
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    assert init_worker_sentry(SimpleNamespace(env="test", api_version="0.9.7")) is False
    initialize.assert_not_called()

    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "production")
    monkeypatch.setenv("SENTRY_RELEASE", "aelira-backend@test-release")

    assert init_worker_sentry(SimpleNamespace(env="test", api_version="0.9.7")) is True
    initialize.assert_called_once()
    assert initialize.call_args.kwargs["dsn"] == "https://public@example.invalid/1"
    assert initialize.call_args.kwargs["environment"] == "production"
    assert initialize.call_args.kwargs["release"] == "aelira-backend@test-release"
    assert initialize.call_args.kwargs["send_default_pii"] is False
    set_tag.assert_called_once_with("service", "worker")


def test_terminal_job_failure_event_is_bounded_and_tenant_safe(monkeypatch) -> None:
    from src.monitoring.worker_sentry import capture_terminal_job_failure

    scope = MagicMock()
    capture = MagicMock(return_value="event-id")
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr("sentry_sdk.new_scope", lambda: nullcontext(scope))
    monkeypatch.setattr("sentry_sdk.capture_message", capture)

    event_id = capture_terminal_job_failure(
        job_id="job-a",
        job_type="scan",
        error_code="local_scan_input_unavailable",
        failure_kind="deterministic",
        attempt_count=1,
        max_retries=3,
    )

    assert event_id == "event-id"
    assert scope.set_tag.call_args_list == [
        call("job_type", "scan"),
        call("error_code", "local_scan_input_unavailable"),
        call("failure_kind", "deterministic"),
    ]
    scope.set_context.assert_called_once_with(
        "durable_job",
        {
            "job_id": "job-a",
            "attempt_count": 1,
            "max_retries": 3,
        },
    )
    assert "payload" not in repr(scope.mock_calls)
    assert "department" not in repr(scope.mock_calls)
    capture.assert_called_once_with(
        "Durable job failed: scan/local_scan_input_unavailable",
        level="error",
    )


def test_expected_cancellation_does_not_emit_sentry_event(monkeypatch) -> None:
    from src.monitoring.worker_sentry import capture_terminal_job_failure

    capture = MagicMock()
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.invalid/1")
    monkeypatch.setattr("sentry_sdk.capture_message", capture)

    assert (
        capture_terminal_job_failure(
            job_id="job-a",
            job_type="scan",
            error_code="scan_cancelled",
            failure_kind="deterministic",
            attempt_count=1,
            max_retries=3,
        )
        is None
    )
    capture.assert_not_called()


def test_worker_entrypoint_initializes_sentry_before_event_loop(monkeypatch) -> None:
    from src.jobs import worker

    events: list[str] = []
    settings = SimpleNamespace(env="test", api_version="0.9.7")

    def run(coroutine) -> None:
        coroutine.close()
        events.append("event_loop")

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker,
        "init_worker_sentry",
        lambda actual: events.append("sentry") if actual is settings else None,
    )
    monkeypatch.setattr(worker.asyncio, "run", run)

    worker.main()

    assert events == ["sentry", "event_loop"]
