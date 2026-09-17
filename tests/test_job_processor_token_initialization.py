"""Worker token configuration is required only when a handler uses OAuth."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs.contracts import JobSuccess
from src.jobs.job_processor import ClaimedJob, JobProcessor


@pytest.fixture(scope="module", autouse=True)
def setup_test_database():
    """These dispatch tests use fake sessions and never initialize a database."""
    yield


@pytest.fixture
def dispatch():
    """Exercise both public worker dispatch seams with database work isolated."""
    db = MagicMock()
    factory = MagicMock()
    factory.return_value.__enter__.return_value = db
    worker = JobProcessor(session_factory=factory)
    worker._owns_claim = MagicMock(return_value=True)
    worker._assert_owned = AsyncMock()
    worker._claim_heartbeat = AsyncMock()
    worker._finish = MagicMock(return_value=True)
    worker._record_outcome = MagicMock()

    async def run(path, handler, *, job_type="scan", payload=None):
        job = SimpleNamespace(
            id="job-1",
            job_type=job_type,
            payload=payload or {},
            retry_count=0,
            max_retries=1,
        )
        if job_type not in worker.registry.job_types:
            worker.register_handler(job_type, handler)
        if path == "legacy":
            await worker._process_job(job, db)
            return job
        db.get.return_value = job
        claim = ClaimedJob(
            job.id, job_type, job.payload, "claim-1", worker.worker_id, 1, 1
        )
        assert await worker.process_claim(claim) is True
        return worker._finish.call_args.args[1]

    return worker, db, run


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["legacy", "claimed"])
@pytest.mark.parametrize("environment", ["development", "production"])
async def test_local_scan_dispatch_does_not_require_token_key(
    monkeypatch, dispatch, path, environment
):
    from src.jobs import job_processor, local_scan_job
    from src.jobs.cloud_scan_job import handle_scan_job

    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ENV", environment)
    constructor = MagicMock(side_effect=AssertionError("OAuth is not needed"))
    monkeypatch.setattr(job_processor, "OAuthTokenManager", constructor)
    local_scan = AsyncMock(return_value={"success": True, "files_scanned": 1})
    monkeypatch.setattr(local_scan_job, "handle_local_scan_job", local_scan)
    worker, db, run = dispatch

    result = await run(
        path,
        handle_scan_job,
        payload={"scan_kind": sorted(local_scan_job.LOCAL_SCAN_KINDS)[0]},
    )

    local_scan.assert_awaited_once()
    assert local_scan.await_args.args[1] is db
    if path == "legacy":
        assert result.status == "completed"
        assert result.result_data == {"success": True, "files_scanned": 1}
    else:
        assert result == JobSuccess({"success": True, "files_scanned": 1})
    assert worker._token_manager is None
    constructor.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["legacy", "claimed"])
@pytest.mark.parametrize("environment", ["development", "production"])
@pytest.mark.parametrize("key", [None, "invalid-key"])
async def test_oauth_use_still_requires_valid_explicit_key(
    monkeypatch, dispatch, path, environment, key
):
    from src.integrations.oauth_token_manager import TokenEncryptionError
    from src.jobs.contracts import JobFailure

    monkeypatch.setenv("ENV", environment)
    if key is None:
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    else:
        monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    worker, _, run = dispatch
    errors = []

    async def oauth_handler(_job, db, token_manager):
        assert token_manager  # Existing handlers use `manager or fallback`.
        assert worker._token_manager is None
        try:
            await token_manager.refresh_if_expired(object(), db)
        except Exception as exc:
            errors.append(exc)
            raise
        return {"success": True}

    result = await run(path, oauth_handler, job_type="sync")

    assert len(errors) == 1  # Initialization fails at token use, inside the handler.
    if key is None:
        assert isinstance(errors[0], ValueError)
        assert str(errors[0]) == "TOKEN_ENCRYPTION_KEY not configured"
    else:
        assert isinstance(errors[0], TokenEncryptionError)
    assert worker._token_manager is None
    if path == "legacy":
        assert result.status == "failed"
        assert result.error_message == "job_processing_failed"
    else:
        assert result == JobFailure.indeterminate("job_handler_exception")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["legacy", "claimed"])
async def test_configured_token_manager_is_cached_and_forwards_refresh(
    monkeypatch, dispatch, path
):
    from cryptography.fernet import Fernet

    from src.jobs import job_processor

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    monkeypatch.setenv("ENV", "production")
    constructor = MagicMock(wraps=job_processor.OAuthTokenManager)
    monkeypatch.setattr(job_processor, "OAuthTokenManager", constructor)
    refresh = AsyncMock(return_value="access-token")
    monkeypatch.setattr(constructor._mock_wraps, "refresh_if_expired", refresh)
    worker, db, run = dispatch
    credential = object()
    managers = []

    async def oauth_handler(_job, session, token_manager):
        assert token_manager
        assert (
            await token_manager.refresh_if_expired(credential, session, lock_timeout=17)
            == "access-token"
        )
        managers.append(worker._token_manager)
        return {"success": True}

    await run(path, oauth_handler, job_type="sync")
    await run(path, oauth_handler, job_type="sync")

    constructor.assert_called_once_with(key)
    assert len(managers) == 2
    assert managers[0] is managers[1] is worker._token_manager
    assert refresh.await_count == 2
    refresh.assert_awaited_with(credential, db, lock_timeout=17)
