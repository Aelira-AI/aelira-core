"""Task 14 slice 3C3: Brightspace synchronous remediation policy boundary."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.auth.dependencies import AuthenticatedPrincipal
from src.db.models import CloudProvider, UserRole


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        api_key=None,
        user_id="user-1",
        department_id="dept-1",
        user_role=UserRole.FACULTY,
        auth_method="session",
    )


@pytest.mark.parametrize(
    "persisted_origin",
    [
        "http://brightspace.example",
        "https://user@brightspace.example",
        "https://brightspace.example/d2l",
        "https://brightspace.example?tenant=one",
        "https://127.0.0.1",
        "https://10.0.0.4",
    ],
)
def test_persisted_brightspace_origin_rejects_unsafe_values_with_stable_error(
    persisted_origin, monkeypatch
):
    from src.utils.security import (
        PERSISTED_BRIGHTSPACE_ORIGIN_ERROR,
        require_persisted_brightspace_origin,
    )

    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", raising=False)
    with pytest.raises(ValueError, match=PERSISTED_BRIGHTSPACE_ORIGIN_ERROR):
        require_persisted_brightspace_origin(persisted_origin)


def test_persisted_brightspace_origin_is_canonical_and_operator_authorized(monkeypatch):
    from src.utils.security import require_persisted_brightspace_origin

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS",
        "https://other.example, https://BRIGHTSPACE.EXAMPLE:443/",
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    assert (
        require_persisted_brightspace_origin(
            {"brightspace_instance_url": "https://BRIGHTSPACE.EXAMPLE:443/"}
        )
        == "https://brightspace.example"
    )


def test_persisted_brightspace_origin_rejects_revoked_operator_origin(monkeypatch):
    from src.utils.security import (
        PERSISTED_BRIGHTSPACE_ORIGIN_ERROR,
        require_persisted_brightspace_origin,
    )

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://authorized.example"
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    with pytest.raises(ValueError, match=PERSISTED_BRIGHTSPACE_ORIGIN_ERROR):
        require_persisted_brightspace_origin("https://revoked.example")


@pytest.mark.asyncio
async def test_cloud_scan_rejects_invalid_origin_before_token_or_download(monkeypatch):
    from src.jobs.cloud_scan_job import CloudScanJob, ScanJobFailed

    monkeypatch.delenv("BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", raising=False)
    job = CloudScanJob(
        SimpleNamespace(
            provider=CloudProvider.BRIGHTSPACE.value,
            provider_metadata={
                "brightspace_instance_url": "http://brightspace.example"
            },
        ),
        SimpleNamespace(file_name="course.pdf"),
        MagicMock(),
    )
    job._refresh_token_if_needed = AsyncMock()
    job._download_brightspace = AsyncMock()

    with pytest.raises(ScanJobFailed) as caught:
        await job.run(MagicMock())

    assert caught.value.code == "BRIGHTSPACE_CONNECTION_ORIGIN_INVALID"
    job._refresh_token_if_needed.assert_not_awaited()
    job._download_brightspace.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_persisted_origin_stops_before_token_client_and_api(monkeypatch):
    from src.api.brightspace_routes import _client_for_fresh_credential
    from src.utils.security import PERSISTED_BRIGHTSPACE_ORIGIN_ERROR

    monkeypatch.setenv("ENV", "test")
    credential = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider=CloudProvider.BRIGHTSPACE.value,
        provider_metadata={
            "brightspace_instance_url": "https://brightspace.example/path"
        },
        is_active=True,
    )
    db = MagicMock()
    db.get.return_value = credential

    with (
        patch(
            "src.api.brightspace_routes._ensure_valid_token", new=AsyncMock()
        ) as token,
        patch("src.api.brightspace_routes.BrightspaceAPIClient") as client,
        pytest.raises(HTTPException) as caught,
    ):
        await _client_for_fresh_credential(
            db, credential_id="cred-1", department_id="dept-1"
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == PERSISTED_BRIGHTSPACE_ORIGIN_ERROR
    token.assert_not_awaited()
    client.assert_not_called()


@pytest.mark.asyncio
async def test_brightspace_api_client_revalidates_exact_bearer_origin(monkeypatch):
    from src.integrations.brightspace.brightspace_api import BrightspaceAPIClient

    monkeypatch.setenv("ENV", "test")
    client = BrightspaceAPIClient("https://BRIGHTSPACE.EXAMPLE:443/", "token")
    transport = AsyncMock()
    client._client = transport
    client.api_base = "https://foreign.example/d2l/api"

    with pytest.raises(ValueError, match="brightspace_bearer_origin_invalid"):
        await client._call_api("GET", "/lp/1.50/users/whoami")

    transport.request.assert_not_awaited()


def test_brightspace_api_client_constructor_rejects_revoked_origin(monkeypatch):
    from src.integrations.brightspace.brightspace_api import BrightspaceAPIClient

    monkeypatch.setenv("BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://trusted.example")

    with pytest.raises(ValueError, match="not authorized by the operator"):
        BrightspaceAPIClient("https://revoked.example", "token")


@pytest.mark.asyncio
async def test_brightspace_api_client_rechecks_operator_policy_before_bearer(
    monkeypatch,
):
    from src.integrations.brightspace.brightspace_api import BrightspaceAPIClient

    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://brightspace.example"
    )
    client = BrightspaceAPIClient("https://brightspace.example", "token")
    transport = AsyncMock()
    client._client = transport
    monkeypatch.setenv(
        "BRIGHTSPACE_OAUTH_ALLOWED_ORIGINS", "https://replacement.example"
    )

    with pytest.raises(ValueError, match="brightspace_bearer_origin_invalid"):
        await client._call_api("GET", "/lp/1.50/users/whoami")

    transport.request.assert_not_awaited()


def test_brightspace_remediation_requests_are_explicit_and_mechanical_by_default():
    from src.api.brightspace_routes import (
        BrightspaceBatchRemediateRequest,
        BrightspaceContentRemediateRequest,
        BrightspaceRemediateRequest,
    )

    assert BrightspaceContentRemediateRequest().model_dump() == {
        "use_ai": False,
        "generate_alt_text": False,
    }
    batch = BrightspaceBatchRemediateRequest(
        org_unit_id=42, cloud_file_ids=["one", "two"]
    )
    assert batch.use_ai is False
    assert batch.generate_alt_text is False
    assert BrightspaceBatchRemediateRequest(
        org_unit_id=42, cloud_file_ids=["one", "one"]
    ).cloud_file_ids == ["one"]
    legacy = BrightspaceRemediateRequest(
        file_url="https://brightspace.example/file",
        org_unit_id=42,
        department_id="dept-1",
    )
    assert legacy.use_ai is False
    assert legacy.generate_alt_text is False
    assert legacy.upload_back is False

    with pytest.raises(ValidationError):
        BrightspaceBatchRemediateRequest(org_unit_id=42, cloud_file_ids=[])
    with pytest.raises(ValidationError):
        BrightspaceBatchRemediateRequest(
            org_unit_id=42, cloud_file_ids=[str(i) for i in range(21)]
        )
    with pytest.raises(ValidationError):
        BrightspaceContentRemediateRequest(provider="openai")


@pytest.mark.parametrize("value", [0, 1, "true", "false", "0", "1"])
def test_brightspace_remediation_intents_reject_coerced_booleans(value):
    from src.api.brightspace_routes import (
        BrightspaceBatchRemediateRequest,
        BrightspaceContentRemediateRequest,
        BrightspaceRemediateRequest,
    )

    with pytest.raises(ValidationError):
        BrightspaceContentRemediateRequest(use_ai=value)
    with pytest.raises(ValidationError):
        BrightspaceContentRemediateRequest(generate_alt_text=value)
    with pytest.raises(ValidationError):
        BrightspaceBatchRemediateRequest(
            org_unit_id=42, cloud_file_ids=["one"], use_ai=value
        )
    with pytest.raises(ValidationError):
        BrightspaceRemediateRequest(
            file_url="https://brightspace.example/file",
            org_unit_id=42,
            department_id="dept-1",
            upload_back=value,
        )


@pytest.mark.parametrize("value", [True, 42.0, "42"])
def test_brightspace_remediation_org_ids_are_strict_positive_integers(value):
    from src.api.brightspace_routes import (
        BrightspaceBatchRemediateRequest,
        BrightspaceContentScanRequest,
        BrightspaceRemediateRequest,
    )

    with pytest.raises(ValidationError):
        BrightspaceBatchRemediateRequest(org_unit_id=value, cloud_file_ids=["one"])
    with pytest.raises(ValidationError):
        BrightspaceRemediateRequest(
            file_url="https://brightspace.example/file",
            org_unit_id=value,
            department_id="dept-1",
        )
    with pytest.raises(ValidationError):
        BrightspaceContentScanRequest(org_unit_id=value)


@pytest.mark.asyncio
async def test_legacy_queue_fails_before_database_or_policy_activity():
    from src.api.brightspace_routes import (
        BrightspaceRemediateRequest,
        remediate_brightspace_content,
    )

    db = MagicMock()
    with (
        patch(
            "src.api.brightspace_routes.LMSRemediationClient.bind_if_allowed"
        ) as bind,
        pytest.raises(HTTPException) as caught,
    ):
        await remediate_brightspace_content(
            BrightspaceRemediateRequest(
                file_url="https://brightspace.example/file",
                org_unit_id=42,
                department_id="dept-1",
            ),
            principal=_principal(),
            db=db,
        )

    assert caught.value.status_code in (409, 501)
    assert caught.value.detail == "brightspace_queued_remediation_unsupported"
    db.query.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()
    bind.assert_not_called()


@pytest.mark.asyncio
async def test_single_authorizes_inventory_before_policy_and_returns_real_outcome():
    from src.api.brightspace_routes import (
        BrightspaceContentRemediateRequest,
        RemediationOutcome,
        remediate_content,
    )

    events = []
    cf = SimpleNamespace(
        id="cloud-1",
        department_id="dept-1",
        provider=CloudProvider.BRIGHTSPACE.value,
        credential_id="cred-1",
        provider_file_id="7",
        provider_parent_id="42",
        provider_metadata={"org_unit_id": 42, "url": "/safe/file.docx"},
        content_body=None,
        file_size_bytes=1,
        file_name="safe.docx",
        last_compliance_score=50,
    )
    credential = SimpleNamespace(
        id="cred-1",
        department_id="dept-1",
        provider=CloudProvider.BRIGHTSPACE.value,
        provider_metadata={"brightspace_instance_url": "https://brightspace.example"},
        is_active=True,
    )
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = cf
    query.all.return_value = [cf]
    db.query.return_value = query
    db.get.return_value = credential
    inventory_client = AsyncMock()
    inventory_client.get_course_content_recursive.side_effect = lambda course: (
        events.append("inventory") or [SimpleNamespace(topic_id=7, org_unit_id=42)]
    )
    outcome = RemediationOutcome(
        cloud_file_id="cloud-1", status="manual_required", manual_count=2
    )

    def bind(**kwargs):
        events.append(f"policy:{kwargs['purpose']}")
        return SimpleNamespace(provider="gemini")

    with (
        patch("src.api.brightspace_routes.require_lti_course_access"),
        patch(
            "src.api.brightspace_routes._client_for_fresh_credential",
            new=AsyncMock(
                side_effect=[(credential, inventory_client), (credential, AsyncMock())]
            ),
        ),
        patch(
            "src.api.brightspace_routes.LMSRemediationClient.bind_if_allowed",
            side_effect=bind,
        ),
        patch(
            "src.api.brightspace_routes._remediate_file",
            new=AsyncMock(return_value=outcome),
        ),
    ):
        result = await remediate_content(
            "cloud-1",
            request=BrightspaceContentRemediateRequest(use_ai=True),
            principal=_principal(),
            db=db,
        )

    assert events == ["inventory", "policy:remediation"]
    assert result.fixed_count == 0
    assert result.manual_count == 2
    assert result.status == "manual_required"


class _StreamingResponse:
    def __init__(self, status_code=200, *, headers=None, chunks=()):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks)
        self.chunks_yielded = 0
        self.closed = False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            self.chunks_yielded += 1
            yield chunk

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "download failed",
                request=httpx.Request("GET", "https://brightspace.example/file"),
                response=httpx.Response(self.status_code),
            )

    async def aclose(self):
        self.closed = True


class _StreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        await self.response.aclose()


class _StreamingClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _StreamContext(self.responses.pop(0))


@pytest.mark.asyncio
async def test_topic_download_rejects_cross_origin_fallback_and_redirect():
    from src.integrations.brightspace.brightspace_api import BrightspaceAPIClient

    client = BrightspaceAPIClient("https://brightspace.example", "token")
    official = _StreamingResponse(403)
    transport = _StreamingClient(official)
    client._client = transport

    with patch.object(
        client,
        "_call_api",
        new=AsyncMock(return_value={"Url": "https://evil.example/steal"}),
    ):
        with pytest.raises(Exception, match="brightspace_download_origin_invalid"):
            await client.get_topic_file(42, 7)
    assert official.closed is True

    official = _StreamingResponse(403)
    redirect = _StreamingResponse(
        302, headers={"location": "https://evil.example/steal"}
    )
    client._client = _StreamingClient(official, redirect)
    with patch.object(
        client,
        "_call_api",
        new=AsyncMock(return_value={"Url": "/d2l/file"}),
    ):
        with pytest.raises(Exception, match="brightspace_download_redirect_rejected"):
            await client.get_topic_file(42, 7)
    assert official.closed is True
    assert redirect.closed is True


@pytest.mark.asyncio
async def test_topic_download_enforces_content_length_before_reading():
    from src.integrations.brightspace.brightspace_api import (
        MAX_TOPIC_FILE_BYTES,
        BrightspaceAPIClient,
    )

    response = _StreamingResponse(
        headers={"content-length": str(MAX_TOPIC_FILE_BYTES + 1)},
        chunks=[b"must-not-be-read"],
    )
    client = BrightspaceAPIClient("https://brightspace.example", "token")
    transport = _StreamingClient(response)
    client._client = transport

    with pytest.raises(Exception, match="brightspace_download_too_large"):
        await client.get_topic_file(42, 7)
    assert response.chunks_yielded == 0
    assert response.closed is True
    assert transport.calls[0][2]["follow_redirects"] is False


@pytest.mark.asyncio
async def test_topic_download_stops_chunked_body_immediately_after_cap():
    from src.integrations.brightspace.brightspace_api import (
        MAX_TOPIC_FILE_BYTES,
        BrightspaceAPIClient,
    )

    response = _StreamingResponse(
        chunks=[
            b"x" * (MAX_TOPIC_FILE_BYTES - 1),
            b"yz",
            b"must-not-be-read",
        ]
    )
    client = BrightspaceAPIClient("https://brightspace.example", "token")
    client._client = _StreamingClient(response)

    with pytest.raises(Exception, match="brightspace_download_too_large"):
        await client.get_topic_file(42, 7)
    assert response.chunks_yielded == 2
    assert response.closed is True


@pytest.mark.asyncio
async def test_topic_download_accepts_exact_streaming_boundary():
    from src.integrations.brightspace.brightspace_api import (
        MAX_TOPIC_FILE_BYTES,
        BrightspaceAPIClient,
    )

    response = _StreamingResponse(
        headers={
            "content-length": str(MAX_TOPIC_FILE_BYTES),
            "content-type": "application/pdf; charset=binary",
        },
        chunks=[b"x" * (MAX_TOPIC_FILE_BYTES - 1), b"y"],
    )
    client = BrightspaceAPIClient("https://brightspace.example", "token")
    client._client = _StreamingClient(response)

    content, content_type = await client.get_topic_file(42, 7)

    assert len(content) == MAX_TOPIC_FILE_BYTES
    assert content_type == "application/pdf"
    assert response.chunks_yielded == 2
    assert response.closed is True


class _UsageClient:
    def __init__(self, provider, result=None, error=None):
        self.provider = provider
        self.result = result or {
            "success": True,
            "content": "Accessible name",
            "ai_used": True,
            "external_ai_used": provider != "ollama",
            "purpose_outcome": "used",
        }
        self.error = error

    def generate_text_sync(self, **_kwargs):
        if self.error:
            raise self.error
        return self.result


def test_brightspace_usage_truth_combines_both_purpose_trackers():
    from src.api.brightspace_routes import _PurposeUsageTracker, _usage_fields

    remediation = _PurposeUsageTracker(_UsageClient("gemini"), purpose="remediation")
    alt_text = _PurposeUsageTracker(_UsageClient("ollama"), purpose="alt_text")
    remediation.generate_text_sync(prompt="bounded")
    alt_text.generate_text_sync(prompt="bounded")

    usage = _usage_fields(
        remediation,
        alt_text,
        {"remediation": "allowed_not_used", "alt_text": "allowed_not_used"},
    )

    assert usage == {
        "ai_used": True,
        "external_ai_used": True,
        "providers": ["gemini", "ollama"],
        "purpose_decisions": {"remediation": "used", "alt_text": "used"},
    }


@pytest.mark.parametrize(
    ("provider", "purpose", "expected_external"),
    [("gemini", "remediation", True), ("ollama", "alt_text", False)],
)
def test_brightspace_usage_truth_for_one_used_purpose(
    provider, purpose, expected_external
):
    from src.api.brightspace_routes import _PurposeUsageTracker, _usage_fields

    tracker = _PurposeUsageTracker(_UsageClient(provider), purpose=purpose)
    tracker.generate_text_sync(prompt="bounded")
    remediation = tracker if purpose == "remediation" else None
    alt_text = tracker if purpose == "alt_text" else None

    usage = _usage_fields(
        remediation,
        alt_text,
        {"remediation": "not_requested", "alt_text": "not_requested"},
    )

    assert usage["ai_used"] is True
    assert usage["external_ai_used"] is expected_external
    assert usage["providers"] == [provider]
    assert usage["purpose_decisions"][purpose] == "used"


def test_brightspace_usage_truth_records_failure_and_no_call_revocation():
    from src.api.brightspace_routes import _PurposeUsageTracker, _usage_fields

    failed = _PurposeUsageTracker(
        _UsageClient("gemini", error=RuntimeError("provider failed")),
        purpose="remediation",
    )
    with pytest.raises(RuntimeError, match="provider failed"):
        failed.generate_text_sync(prompt="bounded")
    revoked = _PurposeUsageTracker(
        _UsageClient(
            "ollama",
            result={
                "success": False,
                "ai_used": False,
                "external_ai_used": False,
                "purpose_outcome": "denied_at_dispatch",
            },
        ),
        purpose="alt_text",
    )
    revoked.generate_text_sync(prompt="bounded")

    usage = _usage_fields(
        failed,
        revoked,
        {"remediation": "allowed_not_used", "alt_text": "allowed_not_used"},
    )

    assert usage["ai_used"] is False
    assert usage["external_ai_used"] is True
    assert usage["providers"] == ["gemini", "ollama"]
    assert usage["purpose_decisions"] == {
        "remediation": "attempted_failed",
        "alt_text": "denied_at_dispatch",
    }


def test_brightspace_usage_truth_does_not_downgrade_prior_success():
    from src.api.brightspace_routes import _PurposeUsageTracker, _usage_fields

    client = _UsageClient("gemini")
    tracker = _PurposeUsageTracker(client, purpose="remediation")
    tracker.generate_text_sync(prompt="first")
    client.result = {"success": False, "error": "provider_call_failed"}
    tracker.generate_text_sync(prompt="second")

    usage = _usage_fields(
        tracker,
        None,
        {"remediation": "allowed_not_used", "alt_text": "not_requested"},
    )

    assert usage["ai_used"] is True
    assert usage["purpose_decisions"]["remediation"] == "used"


@pytest.mark.asyncio
async def test_brightspace_html_helper_reports_real_remediation_usage():
    from src.api.brightspace_routes import _PurposeUsageTracker, _remediate_file

    cloud_file = SimpleNamespace(
        id="cloud-ai-html",
        last_scan_id="scan-ai",
        provider_metadata={"org_unit_id": 42, "url": "/page.html"},
        file_name="page.html",
        provider_file_id="7",
        content_body='<html lang="en"><body><button></button></body></html>',
        remediated_body=None,
        has_remediated_version=False,
        remediated_issues_fixed=0,
        remediated_issues_remaining=0,
        writeback_status=None,
    )
    scan_result = SimpleNamespace(
        issues=[
            {
                "id": "aria-roles",
                "impact": "serious",
                "description": "Button is missing an accessible name",
                "nodes": [{"html": "<button></button>", "target": ["button"]}],
            }
        ]
    )
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = scan_result
    db.query.return_value = query
    tracker = _PurposeUsageTracker(_UsageClient("gemini"), purpose="remediation")

    outcome = await _remediate_file(
        cloud_file,
        db,
        remediation_client=tracker,
        api_client=AsyncMock(),
        purpose_decisions={
            "remediation": "allowed_not_used",
            "alt_text": "not_requested",
        },
    )

    assert outcome.status == "manual_required"
    assert outcome.has_remediated_version is False
    assert outcome.ai_used is True
    assert outcome.external_ai_used is True
    assert outcome.providers == ["gemini"]
    assert outcome.purpose_decisions == {
        "remediation": "used",
        "alt_text": "not_requested",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [True, False])
async def test_brightspace_html_helper_promotes_only_verified_durable_output(verified):
    from src.api.brightspace_routes import _remediate_file

    cloud_file = SimpleNamespace(
        id="cloud-html",
        last_scan_id="scan-1",
        provider_metadata={"org_unit_id": 42, "url": "/page.html"},
        file_name="page.html",
        provider_file_id="7",
        content_body="<html><body><p>Course content</p></body></html>",
        remediated_body=None,
        has_remediated_version=False,
        remediated_issues_fixed=0,
        remediated_issues_remaining=0,
        writeback_status=None,
    )
    scan_result = SimpleNamespace(
        issues=[
            {
                "id": "html-has-lang",
                "impact": "serious",
                "description": "Document language is missing",
                "nodes": [{"html": "<html>", "target": ["html"]}],
            }
        ]
    )
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = scan_result
    db.query.return_value = query
    fake_result = SimpleNamespace(
        success=True,
        output_file=__file__,
        verification_passed=verified,
        fixed_count=1,
        manual_count=0,
        failed_count=0,
    )

    with patch(
        "src.education.remediation.html_remediator.HtmlRemediator.remediate",
        return_value=fake_result,
    ):
        outcome = await _remediate_file(
            cloud_file,
            db,
            api_client=AsyncMock(),
            purpose_decisions={
                "remediation": "not_requested",
                "alt_text": "not_requested",
            },
        )

    assert outcome.has_remediated_version is verified
    assert outcome.status == ("completed" if verified else "manual_required")
    if verified:
        db.commit.assert_called_once()
    else:
        db.commit.assert_not_called()


def _sized_cloud_file(file_id, size, *, content_body=None, file_name="source.pdf"):
    return SimpleNamespace(
        id=file_id,
        file_size_bytes=size,
        content_body=content_body,
        file_name=file_name,
        provider_metadata={"url": f"/{file_name}"},
    )


def test_brightspace_batch_preflight_enforces_100_mib_aggregate_cap():
    from src.api.brightspace_routes import _preflight_brightspace_file_sizes

    files = [_sized_cloud_file(f"cf-{index}", 21 * 1024 * 1024) for index in range(5)]

    with pytest.raises(HTTPException) as caught:
        _preflight_brightspace_file_sizes(files)

    assert caught.value.status_code == 413
    assert caught.value.detail == "brightspace_batch_size_limit_exceeded"


@pytest.mark.parametrize("size", [None, 0, -1])
def test_brightspace_size_preflight_marks_unknown_downloads_manual(size):
    from src.api.brightspace_routes import _preflight_brightspace_file_sizes

    manual = _preflight_brightspace_file_sizes(
        [_sized_cloud_file("download", size, file_name="source.docx")]
    )

    assert manual == {"download": "content_size_unknown"}


def test_brightspace_size_preflight_does_not_trust_stale_html_for_download():
    from src.api.brightspace_routes import _preflight_brightspace_file_sizes

    manual = _preflight_brightspace_file_sizes(
        [
            _sized_cloud_file(
                "download",
                None,
                content_body="<p>stale preview</p>",
                file_name="source.docx",
            )
        ]
    )

    assert manual == {"download": "content_size_unknown"}


def test_brightspace_batch_cap_counts_trusted_items_over_per_item_limit():
    from src.api.brightspace_routes import _preflight_brightspace_file_sizes

    files = [_sized_cloud_file(f"cf-{index}", 26 * 1024 * 1024) for index in range(4)]

    with pytest.raises(HTTPException) as caught:
        _preflight_brightspace_file_sizes(files)

    assert caught.value.status_code == 413
    assert caught.value.detail == "brightspace_batch_size_limit_exceeded"


@pytest.mark.asyncio
async def test_sync_remediator_runs_in_thread_without_db_session(tmp_path):
    import threading

    from src.api.brightspace_routes import _remediate_file

    event_loop_thread = threading.get_ident()
    observed = {}
    output = tmp_path / "remediated.html"
    output.write_text('<html lang="en"><body>fixed</body></html>')

    class FakeRemediator:
        def __init__(self, *args, **kwargs):
            observed["constructor_thread"] = threading.get_ident()
            observed["args"] = args
            observed["kwargs"] = kwargs

        def remediate(self):
            observed["run_thread"] = threading.get_ident()
            return SimpleNamespace(
                success=True,
                output_file=str(output),
                verification_passed=True,
                fixed_count=1,
                manual_count=0,
                failed_count=0,
            )

    cloud_file = SimpleNamespace(
        id="threaded-html",
        last_scan_id="scan-thread",
        provider_metadata={"org_unit_id": 42, "url": "/page.html"},
        file_name="page.html",
        file_size_bytes=100,
        provider_file_id="7",
        content_body="<html><body>before</body></html>",
        remediated_body=None,
        has_remediated_version=False,
        remediated_issues_fixed=0,
        remediated_issues_remaining=0,
        writeback_status=None,
    )
    scan_result = SimpleNamespace(
        issues=[
            {
                "id": "html-has-lang",
                "impact": "serious",
                "description": "Document language is missing",
                "nodes": [{"html": "<html>", "target": ["html"]}],
            }
        ]
    )
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query

    def query_first():
        observed["db_thread"] = threading.get_ident()
        return scan_result

    query.first.side_effect = query_first
    db.query.return_value = query

    with patch(
        "src.education.remediation.html_remediator.HtmlRemediator", FakeRemediator
    ):
        outcome = await _remediate_file(
            cloud_file,
            db,
            api_client=AsyncMock(),
            purpose_decisions={
                "remediation": "not_requested",
                "alt_text": "not_requested",
            },
        )

    assert outcome.status == "completed"
    assert observed["db_thread"] == event_loop_thread
    assert observed["constructor_thread"] != event_loop_thread
    assert observed["run_thread"] == observed["constructor_thread"]
    assert db not in observed["args"]
    assert db not in observed["kwargs"].values()


@pytest.mark.asyncio
async def test_batch_waits_for_each_started_item_without_false_deadline():
    from src.api.brightspace_routes import (
        BrightspaceBatchRemediateRequest,
        RemediationOutcome,
        batch_remediate_content,
    )

    cloud_files = [
        SimpleNamespace(
            id=f"cf-{index}",
            credential_id="cred-1",
            file_size_bytes=1,
            content_body=None,
            file_name="source.pdf",
            provider_metadata={"url": "/source.pdf"},
        )
        for index in range(3)
    ]
    completed = RemediationOutcome(
        cloud_file_id="cf-0", status="completed", fixed_count=2
    )

    async def remediate(cloud_file, *_args, **_kwargs):
        if cloud_file.id == "cf-0":
            return completed
        await __import__("asyncio").sleep(0.05)
        return RemediationOutcome(cloud_file_id=cloud_file.id, status="completed")

    with (
        patch(
            "src.api.brightspace_routes._authorize_brightspace_files",
            new=AsyncMock(return_value=cloud_files),
        ),
        patch(
            "src.api.brightspace_routes._bind_brightspace_clients",
            return_value=(None, None, {}),
        ),
        patch(
            "src.api.brightspace_routes._client_for_fresh_credential",
            new=AsyncMock(return_value=(SimpleNamespace(), AsyncMock())),
        ),
        patch("src.api.brightspace_routes._remediate_file", side_effect=remediate),
    ):
        result = await batch_remediate_content(
            BrightspaceBatchRemediateRequest(
                org_unit_id=42, cloud_file_ids=["cf-0", "cf-1", "cf-2"]
            ),
            principal=_principal(),
            db=MagicMock(),
        )

    assert result.requested_count == 3
    assert result.completed_count == 3
    assert result.fixed_count == 2
    assert result.failed_count == 0
    assert [item.status for item in result.results] == [
        "completed",
        "completed",
        "completed",
    ]
    assert [item.error_code for item in result.results] == [None, None, None]


@pytest.mark.asyncio
async def test_slow_item_completes_before_later_batch_item_starts():
    from src.api.brightspace_routes import (
        BrightspaceBatchRemediateRequest,
        RemediationOutcome,
        batch_remediate_content,
    )

    cloud_files = [
        SimpleNamespace(
            id=f"item-{index}",
            credential_id="cred-1",
            file_size_bytes=1,
            content_body=None,
            file_name="source.pdf",
            provider_metadata={"url": "/source.pdf"},
        )
        for index in range(2)
    ]

    async def remediate(cloud_file, *_args, **_kwargs):
        if cloud_file.id == "item-0":
            await __import__("asyncio").sleep(0.05)
        return RemediationOutcome(
            cloud_file_id=cloud_file.id, status="completed", fixed_count=1
        )

    with (
        patch(
            "src.api.brightspace_routes._authorize_brightspace_files",
            new=AsyncMock(return_value=cloud_files),
        ),
        patch(
            "src.api.brightspace_routes._bind_brightspace_clients",
            return_value=(None, None, {}),
        ),
        patch(
            "src.api.brightspace_routes._client_for_fresh_credential",
            new=AsyncMock(return_value=(SimpleNamespace(), AsyncMock())),
        ),
        patch("src.api.brightspace_routes._remediate_file", side_effect=remediate),
    ):
        result = await batch_remediate_content(
            BrightspaceBatchRemediateRequest(
                org_unit_id=42, cloud_file_ids=["item-0", "item-1"]
            ),
            principal=_principal(),
            db=MagicMock(),
        )

    assert result.requested_count == 2
    assert result.completed_count == 2
    assert result.failed_count == 0
    assert result.fixed_count == 2
    assert [item.error_code for item in result.results] == [None, None]


@pytest.mark.asyncio
async def test_local_downloaded_image_bytes_enable_the_supported_vision_path(tmp_path):
    from src.api.brightspace_routes import _PurposeUsageTracker, _remediate_file

    cloud_file = SimpleNamespace(
        id="local-image",
        last_scan_id="scan-image",
        provider_metadata={"org_unit_id": 42, "url": "/images/chart.png"},
        file_name="chart.png",
        file_size_bytes=8,
        provider_file_id="7",
        content_body=None,
        remediated_body=None,
        has_remediated_version=False,
        remediated_issues_fixed=0,
        remediated_issues_remaining=0,
        writeback_status=None,
    )
    scan_result = SimpleNamespace(
        issues=[
            {
                "id": "image-alt",
                "category": "alt_text",
                "severity": "high",
                "description": "Image is missing alt text",
            }
        ]
    )
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = scan_result
    db.query.return_value = query
    api_client = AsyncMock()
    api_client.get_topic_file.return_value = (b"PNG-BYTES", "image/png")
    tracker = _PurposeUsageTracker(_UsageClient("gemini"), purpose="alt_text")

    async def inspect_local_bytes(*, image_path, **_kwargs):
        assert __import__("pathlib").Path(image_path).read_bytes() == b"PNG-BYTES"
        tracker.generate_text_sync(prompt="vision inspection")
        return {"description": {"alt_text": "Chart of verified course data"}}

    with patch(
        "src.education.image_alt_text.ImageAltTextGenerator.analyze_image_comprehensive",
        new=AsyncMock(side_effect=inspect_local_bytes),
    ) as analyze:
        outcome = await _remediate_file(
            cloud_file,
            db,
            alt_text_client=tracker,
            api_client=api_client,
            purpose_decisions={
                "remediation": "not_requested",
                "alt_text": "allowed_not_used",
            },
        )

    analyze.assert_awaited_once()
    assert outcome.status == "completed"
    assert outcome.ai_used is True
    assert outcome.purpose_decisions["alt_text"] == "used"
