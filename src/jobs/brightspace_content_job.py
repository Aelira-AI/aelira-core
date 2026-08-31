"""Durable worker boundary for Brightspace content remediation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.db.models import (
    CloudFile,
    CloudJobQueue,
    CloudJobStatus,
    CloudOAuthCredentials,
    CloudProvider,
    Department,
    DepartmentAIProviderConfig,
)
from src.jobs.contracts import LostJobOwnership, public_job_result
from src.services.job_enqueue_service import JobEnqueueError, enqueue_cloud_job

BRIGHTSPACE_CONTENT_EXECUTION = "brightspace_content"


def _failure_outcome(
    error_code: str,
    *,
    payload: dict[str, Any] | None = None,
    outcome_status: str = "failed",
    manual_count: int = 0,
    usage_known: bool = False,
) -> dict[str, Any]:
    """Return the complete bounded terminal shape consumed by polling clients."""
    safe_payload = payload if isinstance(payload, dict) else {}
    options = safe_payload.get("options")
    requested = options if isinstance(options, dict) else {}
    decision = (
        "denied_at_dispatch"
        if error_code == "policy_not_permitted"
        else "allowed_not_used"
    )
    purpose_decisions = (
        {
            purpose: decision
            for purpose, option in (
                ("remediation", "use_ai"),
                ("alt_text", "generate_alt_text"),
            )
            if requested.get(option) is True
        }
        if usage_known
        else None
    )
    return {
        "success": False,
        "status": outcome_status,
        "fixed_count": 0,
        "manual_count": max(0, manual_count),
        "failed_count": 0 if outcome_status == "manual_required" else 1,
        "skipped_count": 0,
        "download_available": False,
        "ai_used": False if usage_known else None,
        "external_ai_used": False if usage_known else None,
        "providers": [] if usage_known else None,
        "purpose_decisions": purpose_decisions,
        "error_code": error_code,
    }


def _commit_terminal_outcome(
    db: Session, job: CloudJobQueue, result: dict[str, Any]
) -> None:
    """Commit domain mutations and queue truth in the same transaction."""
    owner = db.execute(
        select(CloudJobQueue)
        .where(
            CloudJobQueue.id == job.id,
            CloudJobQueue.status == CloudJobStatus.PROCESSING.value,
            CloudJobQueue.claim_token == job.claim_token,
            CloudJobQueue.worker_id == job.worker_id,
            or_(
                CloudJobQueue.last_error_code.is_(None),
                CloudJobQueue.last_error_code != "scan_cancel_requested",
            ),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if owner is None:
        db.rollback()
        raise LostJobOwnership("job ownership lost before terminal commit")
    public = public_job_result(result) or public_job_result(
        _failure_outcome("remediation_failed", payload=job.payload)
    )
    succeeded = bool(
        result.get("success") is True and result.get("status") in {"completed", "no_op"}
    )
    now = datetime.now(timezone.utc)
    owner.status = (
        CloudJobStatus.COMPLETED.value if succeeded else CloudJobStatus.FAILED.value
    )
    owner.completed_at = now
    owner.progress = 100 if succeeded else 0
    owner.progress_message = "Completed" if succeeded else "Failed"
    owner.result_data = public
    owner.error_message = None if succeeded else str(result.get("error_code"))[:128]
    owner.last_error_code = owner.error_message
    owner.last_error_retryable = False if not succeeded else None
    owner.claim_token = None
    owner.worker_id = None
    owner.claimed_at = None
    owner.heartbeat_at = None
    owner.lease_expires_at = None
    owner.updated_at = now
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _timestamp(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _source_reference(cloud_file: CloudFile) -> dict[str, Any]:
    body = cloud_file.content_body
    return {
        "scan_id": str(cloud_file.last_scan_id),
        "provider_parent_id": cloud_file.provider_parent_id,
        "provider_version": cloud_file.provider_version,
        "content_updated_at": _timestamp(cloud_file.content_updated_at),
        "content_sha256": (
            hashlib.sha256(body.encode("utf-8")).hexdigest()
            if isinstance(body, str)
            else None
        ),
        "file_size_bytes": (
            cloud_file.file_size_bytes
            if type(cloud_file.file_size_bytes) is int
            else None
        ),
    }


def _validated_options(options: dict[str, Any]) -> dict[str, bool]:
    if set(options) != {"use_ai", "generate_alt_text"} or any(
        type(options[key]) is not bool for key in options
    ):
        raise JobEnqueueError("brightspace_remediation_options_invalid")
    return dict(options)


def _provider_configuration_reference(
    db: Session, department_id: str
) -> dict[str, Any]:
    department = db.get(Department, department_id, populate_existing=True)
    if (
        department is None
        or type(department.ai_provider_config_revision) is not int
        or type(department.lms_ai_policy_revision) is not int
    ):
        raise JobEnqueueError("brightspace_remediation_scope_invalid")
    provider = department.lms_ai_provider or department.ai_primary_provider
    configuration = None
    if isinstance(provider, str):
        configuration = (
            db.query(DepartmentAIProviderConfig)
            .filter(
                DepartmentAIProviderConfig.department_id == department_id,
                DepartmentAIProviderConfig.provider == provider,
            )
            .first()
        )
    return {
        "workspace_id": department_id,
        "configuration_id": (
            str(configuration.id) if configuration is not None else None
        ),
        "provider": provider if isinstance(provider, str) else None,
        "configuration_revision": department.ai_provider_config_revision,
        "lms_policy_revision": department.lms_ai_policy_revision,
    }


def enqueue_brightspace_content_remediation(
    db: Session,
    *,
    cloud_file: CloudFile,
    actor_id: str,
    options: dict[str, Any],
) -> CloudJobQueue:
    """Bind a queued worker job to the current Brightspace source references."""
    safe_options = _validated_options(options)
    if not isinstance(actor_id, str) or not actor_id:
        raise JobEnqueueError("brightspace_remediation_scope_invalid")
    locked = db.execute(
        select(CloudFile)
        .where(
            CloudFile.id == cloud_file.id,
            CloudFile.department_id == cloud_file.department_id,
            CloudFile.provider == CloudProvider.BRIGHTSPACE.value,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        locked is None
        or not isinstance(locked.department_id, str)
        or not locked.department_id
        or not isinstance(locked.credential_id, str)
        or not locked.credential_id
        or not isinstance(locked.provider_file_id, str)
        or not locked.provider_file_id
        or not isinstance(locked.provider_parent_id, str)
        or not locked.provider_parent_id
        or not isinstance(locked.last_scan_id, str)
        or not locked.last_scan_id
    ):
        raise JobEnqueueError("brightspace_remediation_scope_invalid")
    source = _source_reference(locked)
    provider_configuration = _provider_configuration_reference(
        db, str(locked.department_id)
    )
    payload = {
        "execution": BRIGHTSPACE_CONTENT_EXECUTION,
        "scan_id": str(locked.last_scan_id),
        "actor_id": actor_id,
        "options": safe_options,
        "source": source,
        "provider_configuration": provider_configuration,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    purposes = [
        purpose
        for purpose, requested in (
            ("remediation", safe_options["use_ai"]),
            ("alt_text", safe_options["generate_alt_text"]),
        )
        if requested
    ]
    return enqueue_cloud_job(
        db,
        department_id=locked.department_id,
        job_type="remediate",
        payload=payload,
        dedupe_key=f"brightspace-content:{locked.id}:{digest}",
        provider=CloudProvider.BRIGHTSPACE.value,
        credential_id=locked.credential_id,
        cloud_file_id=str(locked.id),
        provider_file_id=locked.provider_file_id,
        max_retries=0,
        execution_context={
            "ai_requested": safe_options["use_ai"],
            "alt_text_requested": safe_options["generate_alt_text"],
            "requested_purposes": purposes,
            "originating_route": "brightspace_content_api",
        },
    )


def _job_scope_is_current(
    job: CloudJobQueue,
    cloud_file: CloudFile | None,
    credential: CloudOAuthCredentials | None,
    payload: dict[str, Any],
    provider_configuration: dict[str, Any] | None = None,
) -> bool:
    source = payload.get("source")
    return bool(
        cloud_file is not None
        and credential is not None
        and job.provider == CloudProvider.BRIGHTSPACE.value
        and job.department_id == cloud_file.department_id == credential.department_id
        and job.credential_id == cloud_file.credential_id == credential.id
        and job.cloud_file_id == cloud_file.id
        and job.provider_file_id == cloud_file.provider_file_id
        and cloud_file.provider
        == credential.provider
        == CloudProvider.BRIGHTSPACE.value
        and credential.is_active is True
        and isinstance(source, dict)
        and source == _source_reference(cloud_file)
        and payload.get("scan_id") == cloud_file.last_scan_id
        and provider_configuration == payload.get("provider_configuration")
    )


def _public_outcome(outcome: Any, *, scan_id: str) -> dict[str, Any]:
    status_value = (
        outcome.status
        if outcome.status in {"completed", "manual_required", "no_op", "failed"}
        else "failed"
    )
    providers = [
        provider
        for provider in outcome.providers
        if provider in {"anthropic", "gemini", "local", "ollama", "openai", "xai"}
    ][:2]
    purpose_decisions = {
        purpose: decision
        for purpose, decision in outcome.purpose_decisions.items()
        if purpose in {"remediation", "alt_text"}
        and decision
        in {
            "not_requested",
            "allowed_not_used",
            "manual_required",
            "denied_at_dispatch",
            "attempted_failed",
            "used",
        }
    }
    result = {
        "success": status_value in {"completed", "no_op"},
        "status": status_value,
        "scan_id": scan_id,
        "fixed_count": outcome.fixed_count,
        "manual_count": outcome.manual_count,
        "failed_count": outcome.failed_count,
        "skipped_count": outcome.skipped_count,
        "download_available": outcome.has_remediated_version,
        "ai_used": outcome.ai_used is True,
        "external_ai_used": outcome.external_ai_used is True,
        "providers": providers,
        "purpose_decisions": purpose_decisions,
    }
    if isinstance(outcome.artifact_id, str):
        result["artifact_id"] = outcome.artifact_id
    if result["success"] is not True:
        result["error_code"] = (
            "manual_required"
            if status_value == "manual_required"
            else "remediation_failed"
        )
    return result


async def _execute_brightspace_content_remediation_job(
    job: CloudJobQueue,
    db: Session,
    token_manager: Any,
) -> dict[str, Any]:
    """Execute the specialized Brightspace semantics only in a queue worker."""
    payload = job.payload if type(job.payload) is dict else {}
    if set(payload) != {
        "execution",
        "scan_id",
        "actor_id",
        "options",
        "source",
        "provider_configuration",
    }:
        return _failure_outcome(
            "invalid_job_payload", payload=payload, usage_known=True
        )
    if payload.get("execution") != BRIGHTSPACE_CONTENT_EXECUTION:
        return _failure_outcome(
            "invalid_job_payload", payload=payload, usage_known=True
        )
    try:
        options = _validated_options(payload.get("options"))
    except (JobEnqueueError, TypeError):
        return _failure_outcome(
            "invalid_job_payload", payload=payload, usage_known=True
        )
    actor_id = payload.get("actor_id")
    if not isinstance(actor_id, str) or not actor_id:
        return _failure_outcome("invalid_job_scope", payload=payload, usage_known=True)

    cloud_file = db.get(CloudFile, job.cloud_file_id, populate_existing=True)
    credential = db.get(
        CloudOAuthCredentials, job.credential_id, populate_existing=True
    )
    try:
        provider_configuration = _provider_configuration_reference(
            db, str(job.department_id)
        )
    except JobEnqueueError:
        return _failure_outcome("invalid_job_scope", payload=payload, usage_known=True)
    if not _job_scope_is_current(
        job, cloud_file, credential, payload, provider_configuration
    ):
        return _failure_outcome("invalid_job_scope", payload=payload, usage_known=True)

    checker = getattr(job, "_assert_owned", None)
    if checker is not None:
        await checker()

    from src.api.brightspace_routes import (
        BrightspaceContentRemediateRequest,
        _bind_brightspace_clients,
        _client_for_fresh_credential,
        _remediate_file,
    )

    principal = SimpleNamespace(department_id=job.department_id, user_id=actor_id)
    intent = BrightspaceContentRemediateRequest(**options)
    try:
        remediation_client, alt_text_client, decisions = _bind_brightspace_clients(
            principal=principal,
            cloud_file=cloud_file,
            intent=intent,
        )
    except Exception as exc:
        if getattr(exc, "status_code", None) == 403:
            return _failure_outcome(
                "policy_not_permitted",
                payload=payload,
                outcome_status="manual_required",
                manual_count=1,
                usage_known=True,
            )
        raise

    _, api_client = await _client_for_fresh_credential(
        db,
        credential_id=str(credential.id),
        department_id=str(job.department_id),
        token_manager=token_manager,
    )

    async def fence_commit() -> None:
        if checker is not None:
            await checker()
        current_file = db.get(CloudFile, job.cloud_file_id, populate_existing=True)
        current_credential = db.get(
            CloudOAuthCredentials, job.credential_id, populate_existing=True
        )
        try:
            current_provider_configuration = _provider_configuration_reference(
                db, str(job.department_id)
            )
        except JobEnqueueError as exc:
            raise LostJobOwnership("provider configuration authority changed") from exc
        if not _job_scope_is_current(
            job,
            current_file,
            current_credential,
            payload,
            current_provider_configuration,
        ):
            raise LostJobOwnership("Brightspace source authority changed")
        owner = db.execute(
            select(CloudJobQueue.id)
            .where(
                CloudJobQueue.id == job.id,
                CloudJobQueue.status == CloudJobStatus.PROCESSING.value,
                CloudJobQueue.claim_token == job.claim_token,
                CloudJobQueue.worker_id == job.worker_id,
                or_(
                    CloudJobQueue.last_error_code.is_(None),
                    CloudJobQueue.last_error_code != "scan_cancel_requested",
                ),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if owner is None:
            raise LostJobOwnership("job ownership lost")

    try:
        outcome = await _remediate_file(
            cloud_file,
            db,
            remediation_client=remediation_client,
            alt_text_client=alt_text_client,
            api_client=api_client,
            purpose_decisions=decisions,
            assert_owned=fence_commit,
            remediation_job_id=str(job.id),
            commit_changes=False,
        )
    finally:
        await api_client.close()
    if checker is not None:
        await checker()
    return _public_outcome(outcome, scan_id=str(payload["scan_id"]))


async def _run_brightspace_subprocess(job: CloudJobQueue) -> dict[str, Any]:
    from src.config.settings import get_settings
    from src.jobs.local_scan_subprocess import _run_process

    request = {
        "job_id": str(job.id),
        "department_id": str(job.department_id),
        "claim_token": str(job.claim_token),
        "worker_id": str(job.worker_id),
    }
    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="aelira-brightspace-job-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        response_path = Path(temp_dir) / "response.json"
        request_path.write_text(
            json.dumps(request, allow_nan=False, separators=(",", ":")),
            encoding="utf-8",
        )
        return_code = await _run_process(
            (
                sys.executable,
                "-m",
                "src.jobs.brightspace_content_job",
                "--request",
                str(request_path),
                "--response",
                str(response_path),
                "--parent-pid",
                str(os.getpid()),
            ),
            timeout_seconds=None,
            termination_grace_seconds=(settings.remediation_termination_grace_seconds),
        )
        try:
            raw = response_path.read_bytes()
            if len(raw) > 262_144:
                raise ValueError("response too large")
            envelope = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _failure_outcome("remediation_failed", payload=job.payload)
        if (
            return_code != 0
            or not isinstance(envelope, dict)
            or envelope.get("transport_success") is not True
            or not isinstance(envelope.get("result"), dict)
        ):
            return _failure_outcome("remediation_failed", payload=job.payload)
        return dict(envelope["result"])


async def handle_brightspace_content_remediation_job(
    job: CloudJobQueue,
    db: Session,
    token_manager: Any,
) -> dict[str, Any]:
    """Run specialized Brightspace work in a killable process group."""
    del token_manager
    checker = getattr(job, "_assert_owned", None)
    if checker is not None:
        await checker()
    result = await _run_brightspace_subprocess(job)
    db.expire_all()
    terminal = db.get(CloudJobQueue, job.id, populate_existing=True)
    if (
        terminal is not None
        and terminal.status
        in {CloudJobStatus.COMPLETED.value, CloudJobStatus.FAILED.value}
        and terminal.claim_token is None
        and terminal.worker_id is None
    ):
        if terminal.status == CloudJobStatus.COMPLETED.value:
            from src.jobs.remediation_job import RemediationJobHandledResult

            return RemediationJobHandledResult(
                {"success": True, **dict(terminal.result_data or {})}
            )
        from src.jobs.remediation_job import RemediationJobFailed

        raise RemediationJobFailed(
            str(terminal.last_error_code or "remediation_failed"),
            terminal_state_committed=True,
        )
    if checker is not None:
        await checker()
    return result


def _child_main(request_path: str, response_path: str, parent_pid: int) -> int:
    from src.db.database import SessionLocal
    from src.integrations.oauth_token_manager import OAuthTokenManager
    from src.jobs.local_scan_subprocess import _bind_parent_death
    from src.jobs.execution_authority import (
        acquire_child_execution_lock,
        attach_child_checker,
        install_child_commit_fence,
    )

    _bind_parent_death(parent_pid)
    envelope: dict[str, Any] = {"transport_success": False}
    return_code = 1
    try:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        if not isinstance(request, dict) or set(request) != {
            "job_id",
            "department_id",
            "claim_token",
            "worker_id",
        }:
            raise ValueError("invalid request")
        with SessionLocal() as db:
            remove_fence = None
            authority = acquire_child_execution_lock(
                db,
                job_id=str(request["job_id"]),
                claim_token=str(request["claim_token"]),
            )
            try:
                job = db.get(CloudJobQueue, request["job_id"], populate_existing=True)
                if (
                    job is None
                    or job.department_id != request["department_id"]
                    or job.status != CloudJobStatus.PROCESSING.value
                    or job.claim_token != request["claim_token"]
                    or job.worker_id != request["worker_id"]
                    or job.last_error_code == "scan_cancel_requested"
                ):
                    raise LostJobOwnership("job ownership lost")
                remove_fence = install_child_commit_fence(
                    job_id=str(job.id),
                    claim_token=str(job.claim_token),
                    worker_id=str(job.worker_id),
                )
                attach_child_checker(
                    job,
                    db,
                    job_id=str(job.id),
                    claim_token=str(job.claim_token),
                    worker_id=str(job.worker_id),
                )
                result = asyncio.run(
                    _execute_brightspace_content_remediation_job(
                        job, db, OAuthTokenManager()
                    )
                )
                # The explicit terminal commit below performs its own queue-row
                # lock/cancellation fence.  Remove the legacy Session listener
                # first so its processing predicate cannot autoflush the new
                # terminal status and reject this same atomic commit.
                remove_fence()
                remove_fence = None
                _commit_terminal_outcome(db, job, result)
            finally:
                if remove_fence is not None:
                    remove_fence()
                authority.close()
            envelope = {"transport_success": True, "job_terminal": True}
            return_code = 0
    except Exception:
        pass
    Path(response_path).write_text(
        json.dumps(envelope, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return return_code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    arguments = parser.parse_args()
    raise SystemExit(
        _child_main(arguments.request, arguments.response, arguments.parent_pid)
    )


__all__ = [
    "BRIGHTSPACE_CONTENT_EXECUTION",
    "_execute_brightspace_content_remediation_job",
    "enqueue_brightspace_content_remediation",
    "handle_brightspace_content_remediation_job",
]


if __name__ == "__main__":
    main()
