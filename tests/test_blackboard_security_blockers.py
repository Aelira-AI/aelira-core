"""Task16B1 Blackboard origin, bearer, and worker security contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.db.models import CloudFile, CloudProvider

PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]
PRIVATE_DNS = [(2, 1, 6, "", ("10.0.0.8", 443))]


def _credential(**overrides):
    values = {
        "id": "cred-1",
        "department_id": "dept-1",
        "provider": CloudProvider.BLACKBOARD.value,
        "provider_metadata": {"blackboard_instance_url": "https://blackboard.example"},
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _cloud_file():
    return SimpleNamespace(
        id="cloud-1",
        department_id="dept-1",
        credential_id="cred-1",
        provider=CloudProvider.BLACKBOARD.value,
        provider_file_id="content-1",
        file_name="file.docx",
        metadata={"course_id": "course-1"},
    )


class _DownloadDB:
    def __init__(self, credential):
        self.credential = credential
        self.cloud_file = _cloud_file()

    def query(self, model):
        assert model is CloudFile
        query = MagicMock()
        query.filter.return_value = query
        query.first.return_value = self.cloud_file
        return query

    def get(self, _model, _identity, **_kwargs):
        return self.credential


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
                request=httpx.Request("GET", "https://blackboard.example/file"),
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
    def __init__(self, response):
        self.response = response
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _StreamContext(self.response)


@pytest.mark.parametrize(
    "origin",
    [
        "not-a-url",
        "http://blackboard.example",
        "https://user@blackboard.example",
        "https://blackboard.example/path",
        "https://blackboard.example?next=evil",
        "https://blackboard.example#fragment",
    ],
)
def test_persisted_blackboard_origin_rejects_noncanonical_values(monkeypatch, origin):
    from src.utils.security import (
        PERSISTED_BLACKBOARD_ORIGIN_ERROR,
        require_persisted_blackboard_origin,
    )

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", "https://blackboard.example")
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )

    with pytest.raises(ValueError) as caught:
        require_persisted_blackboard_origin({"blackboard_instance_url": origin})

    assert str(caught.value) == PERSISTED_BLACKBOARD_ORIGIN_ERROR
    assert len(str(caught.value)) < 160


@pytest.mark.parametrize(
    ("allowed", "origin", "dns"),
    [
        ("https://blackboard.example", "https://private.example", PRIVATE_DNS),
        ("https://other.example", "https://blackboard.example", PUBLIC_DNS),
        ("", "https://blackboard.example", PUBLIC_DNS),
    ],
)
def test_persisted_blackboard_origin_rejects_private_foreign_or_revoked(
    monkeypatch, allowed, origin, dns
):
    from src.utils.security import require_persisted_blackboard_origin

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", allowed)
    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", lambda *_a, **_k: dns)

    with pytest.raises(ValueError, match="Blackboard connection origin"):
        require_persisted_blackboard_origin({"blackboard_instance_url": origin})


def test_blackboard_test_localhost_exception_is_exact(monkeypatch):
    from src.utils.security import require_blackboard_oauth_allowed_origin

    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", raising=False)

    assert (
        require_blackboard_oauth_allowed_origin("http://localhost:8000")
        == "http://localhost:8000"
    )
    with pytest.raises(ValueError):
        require_blackboard_oauth_allowed_origin("http://127.0.0.1:8000")


@pytest.mark.asyncio
async def test_blackboard_safe_transport_pins_public_dns_and_client_is_hardened(
    monkeypatch,
):
    from src.integrations.blackboard.blackboard_api import BlackboardAPIClient
    from src.integrations.blackboard.safe_http import BlackboardSafeAsyncHTTPTransport

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", "https://blackboard.example")
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    wrapped = MagicMock()
    wrapped.connect_tcp = AsyncMock(return_value=MagicMock())
    transport = BlackboardSafeAsyncHTTPTransport(network_backend=wrapped)
    with patch(
        "src.integrations.blackboard.safe_http.socket.getaddrinfo",
        return_value=PUBLIC_DNS,
    ):
        await transport._pool._network_backend.connect_tcp("blackboard.example", 443)
    assert wrapped.connect_tcp.await_args.args[:2] == ("93.184.216.34", 443)

    with patch("src.integrations.blackboard.blackboard_api.httpx.AsyncClient") as ctor:
        client = BlackboardAPIClient("https://BLACKBOARD.EXAMPLE:443/", "bearer")
        await client._get_client()

    kwargs = ctor.call_args.kwargs
    assert kwargs["trust_env"] is False
    assert kwargs["follow_redirects"] is False
    assert 0 < kwargs["timeout"] <= 60
    assert isinstance(kwargs["transport"], BlackboardSafeAsyncHTTPTransport)
    assert client.api_base == "https://blackboard.example/learn/api/public/v1"


@pytest.mark.asyncio
async def test_blackboard_valid_request_uses_exact_origin_bearer(monkeypatch):
    from src.integrations.blackboard.blackboard_api import BlackboardAPIClient

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", "https://blackboard.example")
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    request = httpx.Request(
        "GET", "https://blackboard.example/learn/api/public/v1/users/me"
    )
    response = httpx.Response(
        200,
        request=request,
        json={"id": "u1", "userName": "teacher"},
    )
    transport = AsyncMock()
    transport.request.return_value = response
    client = BlackboardAPIClient("https://blackboard.example", "secret-bearer")
    client._client = transport

    user = await client.get_current_user()

    assert user.id == "u1"
    call = transport.request.await_args
    assert call.args[:2] == (
        "GET",
        "https://blackboard.example/learn/api/public/v1/users/me",
    )
    assert call.kwargs["headers"]["Authorization"] == "Bearer secret-bearer"


@pytest.mark.asyncio
async def test_blackboard_redirect_is_not_followed_or_forwarded(monkeypatch):
    from src.integrations.blackboard.blackboard_api import BlackboardAPIClient

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", "https://blackboard.example")
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    request = httpx.Request(
        "GET", "https://blackboard.example/learn/api/public/v1/users/me"
    )
    response = httpx.Response(
        302,
        request=request,
        headers={"location": "https://evil.example/steal"},
    )
    transport = AsyncMock()
    transport.request.return_value = response
    client = BlackboardAPIClient("https://blackboard.example", "secret-bearer")
    client._client = transport

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_current_user()

    transport.request.assert_awaited_once()
    assert transport.request.await_args.kwargs["follow_redirects"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credential", "allowed", "dns"),
    [
        (_credential(provider_metadata={}), "https://blackboard.example", PUBLIC_DNS),
        (
            _credential(provider_metadata={"blackboard_instance_url": "not-a-url"}),
            "https://blackboard.example",
            PUBLIC_DNS,
        ),
        (
            _credential(
                provider_metadata={"blackboard_instance_url": "https://private.example"}
            ),
            "https://private.example",
            PRIVATE_DNS,
        ),
        (_credential(), "https://other.example", PUBLIC_DNS),
        (_credential(is_active=False), "https://blackboard.example", PUBLIC_DNS),
    ],
)
async def test_blackboard_worker_rejects_invalid_origin_or_credential_before_token_http(
    monkeypatch, credential, allowed, dns
):
    from src.jobs.remediation_job import _download_cloud_file

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", allowed)
    monkeypatch.setattr("src.utils.security.socket.getaddrinfo", lambda *_a, **_k: dns)
    token_manager = MagicMock()
    token_manager.refresh_if_expired = AsyncMock()

    with patch("src.integrations.blackboard.BlackboardAPIClient") as constructor:
        result = await _download_cloud_file(
            "cloud-1",
            "dept-1",
            _DownloadDB(credential),
            credential=_credential(),
            token_manager=token_manager,
            require_exact_credential=True,
        )

    assert result["success"] is False
    token_manager.refresh_if_expired.assert_not_awaited()
    constructor.assert_not_called()


def test_blackboard_client_revalidates_current_allowlist_before_http(monkeypatch):
    from src.integrations.blackboard.blackboard_api import BlackboardAPIClient

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", "https://other.example")
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )

    with (
        patch("src.integrations.blackboard.blackboard_api.httpx.AsyncClient") as ctor,
        pytest.raises(ValueError, match="authorized"),
    ):
        BlackboardAPIClient("https://blackboard.example", "secret-bearer")

    ctor.assert_not_called()


def test_blackboard_rejects_foreign_bearer_destination_before_http(monkeypatch):
    from src.integrations.blackboard.blackboard_api import BlackboardAPIClient

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv(
        "BLACKBOARD_OAUTH_ALLOWED_ORIGINS",
        "https://blackboard.example,https://foreign.example",
    )
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    client = BlackboardAPIClient("https://blackboard.example", "secret-bearer")
    client._client = AsyncMock()

    with pytest.raises(ValueError, match="blackboard_bearer_origin_invalid"):
        client._bearer_url("https://foreign.example/download")

    client._client.request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_chunks"),
    [
        (_StreamingResponse(headers={"content-length": "5"}, chunks=[b"xxxxx"]), 0),
        (_StreamingResponse(chunks=[b"xxx", b"yy", b"unread"]), 2),
        (
            _StreamingResponse(
                status_code=302, headers={"location": "https://evil.example/steal"}
            ),
            0,
        ),
    ],
)
async def test_blackboard_download_rejects_oversize_or_redirect_without_writing(
    monkeypatch, tmp_path, response, expected_chunks
):
    from src.integrations.blackboard import blackboard_api

    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("BLACKBOARD_OAUTH_ALLOWED_ORIGINS", "https://blackboard.example")
    monkeypatch.setattr(
        "src.utils.security.socket.getaddrinfo", lambda *_a, **_k: PUBLIC_DNS
    )
    monkeypatch.setattr(blackboard_api, "MAX_BLACKBOARD_DOWNLOAD_BYTES", 4)
    client = blackboard_api.BlackboardAPIClient(
        "https://blackboard.example", "secret-bearer"
    )
    client.get_content_item = AsyncMock(
        return_value=SimpleNamespace(file_name="file.docx")
    )
    attachments = httpx.Response(
        200,
        request=httpx.Request("GET", "https://blackboard.example/attachments"),
        json={"results": [{"id": "attachment-1", "fileName": "file.docx"}]},
    )
    client._request = AsyncMock(return_value=attachments)
    transport = _StreamingClient(response)
    client._client = transport
    destination = tmp_path / "file.docx"

    result = await client.download_file("course-1", "content-1", str(destination))

    assert result.success is False
    assert result.error == "blackboard_download_failed"
    assert response.chunks_yielded == expected_chunks
    assert response.closed is True
    assert destination.exists() is False
    assert transport.calls[0][2]["follow_redirects"] is False
    assert transport.calls[0][2]["headers"]["Authorization"] == ("Bearer secret-bearer")
