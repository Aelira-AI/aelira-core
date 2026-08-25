"""Explicit registry and compatibility adapters for executable queue jobs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .contracts import JobFailure, JobHandler, JobResult, JobSuccess, sanitize_json

EXECUTABLE_JOB_TYPES = frozenset(
    {
        "sync",
        "scan",
        "remediate",
        "upload",
        "webhook_refresh",
        "canvas_reconcile",
        "canvas_content",
    }
)


class JobRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        if job_type not in EXECUTABLE_JOB_TYPES:
            raise ValueError(f"Job type is not executable: {job_type}")
        if job_type in self._handlers:
            raise ValueError(f"Handler already registered: {job_type}")
        self._handlers[job_type] = handler

    def get(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)

    @property
    def job_types(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def validate(self) -> None:
        missing = EXECUTABLE_JOB_TYPES - self._handlers.keys()
        if missing:
            raise RuntimeError(f"Missing job handlers: {', '.join(sorted(missing))}")


def adapt_legacy_handler(
    handler: Callable[[Any, Any, Any], Awaitable[Any]],
) -> JobHandler:
    """Adapt old model-based handlers; only explicit success may complete."""

    async def adapted(context: Any, session: Any, token_manager: Any) -> JobResult:
        from src.db.models import CloudJobQueue

        job = session.get(CloudJobQueue, context.job_id)
        if job is None:
            # Small unit fakes may pass a context-shaped object directly.
            job = context
        assert_owned = getattr(context, "assert_owned", None)
        if assert_owned is not None:
            try:
                setattr(job, "_assert_owned", assert_owned)
            except (AttributeError, TypeError):
                pass
        begin_external_effect = getattr(context, "begin_external_effect", None)
        if begin_external_effect is not None:
            try:
                setattr(job, "_begin_external_effect", begin_external_effect)
            except (AttributeError, TypeError):
                pass
        try:
            result = await handler(job, session, token_manager)
        except Exception as exc:
            if getattr(exc, "terminal_state_committed", False) is True:
                raise
            code = getattr(exc, "code", None)
            if any(
                base.__name__ == "RetryableRemediationJobError"
                for base in type(exc).__mro__
            ):
                details: dict[str, Any] = {}
                artifact_id = getattr(exc, "artifact_id", None)
                if isinstance(artifact_id, str) and not getattr(
                    exc, "cleanup_complete", True
                ):
                    details = {
                        "artifact_id": artifact_id,
                        "publication_cleanup_pending": True,
                    }
                return JobFailure.retryable(
                    code if isinstance(code, str) else "handler_retryable", details
                )
            if type(exc).__name__ == "RemediationJobFailed" and exc.__cause__ is None:
                return JobFailure.deterministic(
                    code if isinstance(code, str) else "remediation_failed"
                )
            raise
        if isinstance(result, JobSuccess | JobFailure):
            return result
        if not isinstance(result, dict):
            return JobFailure.indeterminate("malformed_handler_result")
        safe = sanitize_json(result)
        if result.get("success") is True and isinstance(safe, dict):
            return JobSuccess(
                safe,
                handler_committed=getattr(result, "handler_committed", False) is True,
            )
        if result.get("success") is False:
            code = result.get("error_code") or result.get("error") or "handler_failed"
            failure_kind = result.get("failure_kind")
            if failure_kind == "retryable":
                return JobFailure.retryable(
                    code if isinstance(code, str) else "handler_failed"
                )
            if failure_kind == "indeterminate":
                return JobFailure.indeterminate(
                    code if isinstance(code, str) else "handler_failed"
                )
            return JobFailure.deterministic(
                code if isinstance(code, str) else "handler_failed"
            )
        return JobFailure.indeterminate("malformed_handler_result")

    return adapted


def build_default_registry() -> JobRegistry:
    from .canvas_content_job import handle_canvas_content_job
    from .cloud_scan_job import handle_scan_job
    from .cloud_sync_job import handle_sync_job
    from .remediation_job import handle_remediation_job
    from .upload_job import handle_upload_job
    from .webhook_refresh_job import handle_webhook_refresh_job
    from .reconciliation_job import handle_reconciliation_job

    registry = JobRegistry()
    registry.register("canvas_content", handle_canvas_content_job)
    registry.register("sync", adapt_legacy_handler(handle_sync_job))
    registry.register("scan", adapt_legacy_handler(handle_scan_job))
    registry.register("remediate", adapt_legacy_handler(handle_remediation_job))
    registry.register("upload", adapt_legacy_handler(handle_upload_job))
    registry.register(
        "webhook_refresh", adapt_legacy_handler(handle_webhook_refresh_job)
    )
    registry.register(
        "canvas_reconcile", adapt_legacy_handler(handle_reconciliation_job)
    )
    registry.validate()
    return registry
