"""Token transport boundaries for Canvas downloads and uploads."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest

from src.integrations.canvas.canvas_api import CanvasAPIClient
from src.integrations.canvas.safe_http import CanvasSafeAsyncHTTPTransport
from src.utils.security import redact_sensitive_url

PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]
TOKEN = "OAUTH_TOKEN_SENTINEL"
SIGNED_SECRET = "SIGNED_QUERY_SENTINEL"


def _file_info(url: str) -> SimpleNamespace:
    return SimpleNamespace(
        url=url,
        filename="report.pdf",
        content_type="application/pdf",
        size=7,
    )


def _context_client(client: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def _assert_no_oauth_token(url: str) -> None:
    assert TOKEN not in url
    assert all(
        key.lower() != "access_token" for key, _ in parse_qsl(urlparse(url).query)
    )


def test_sensitive_url_redaction_removes_userinfo_and_signature_variants():
    redacted = redact_sensitive_url(
        "https://alice:password@example.com/file?"
        f"access_token={TOKEN}&X-Amz-Signature={SIGNED_SECRET}&safe=page-2"
    )

    assert "alice" not in redacted
    assert "password" not in redacted
    assert TOKEN not in redacted
    assert SIGNED_SECRET not in redacted
    assert "example.com/file" in redacted
    assert "safe=page-2" in redacted


@pytest.mark.asyncio
async def test_download_uses_bearer_only_for_exact_canvas_origin_and_never_query_token(
    tmp_path,
):
    requests = MagicMock()
    requests.get = AsyncMock(
        side_effect=[
            httpx.Response(
                302,
                headers={
                    "Location": f"https://cdn.example/report.pdf?X-Amz-Signature={SIGNED_SECRET}"
                },
            ),
            httpx.Response(
                200,
                content=b"content",
                request=httpx.Request("GET", "https://cdn.example/report.pdf"),
            ),
        ]
    )
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient",
            return_value=_context_client(requests),
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", TOKEN)
        client.get_file = AsyncMock(
            return_value=_file_info("https://canvas.example/files/7/download")
        )
        result = await client.download_file("7", str(tmp_path / "report.pdf"))

    assert result.success is True
    first, second = requests.get.await_args_list
    _assert_no_oauth_token(first.args[0])
    _assert_no_oauth_token(second.args[0])
    assert first.kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert "Authorization" not in second.kwargs["headers"]
    assert "Cookie" not in second.kwargs["headers"]


@pytest.mark.asyncio
async def test_download_relative_redirect_retains_bearer(tmp_path):
    requests = MagicMock()
    requests.get = AsyncMock(
        side_effect=[
            httpx.Response(302, headers={"Location": "/files/7/content"}),
            httpx.Response(
                200,
                content=b"content",
                request=httpx.Request("GET", "https://canvas.example/files/7/content"),
            ),
        ]
    )
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient",
            return_value=_context_client(requests),
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", TOKEN)
        client.get_file = AsyncMock(
            return_value=_file_info("https://canvas.example/files/7/download")
        )
        result = await client.download_file("7", str(tmp_path / "report.pdf"))

    assert result.success is True
    for request in requests.get.await_args_list:
        _assert_no_oauth_token(request.args[0])
        assert request.kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.asyncio
async def test_download_failure_does_not_return_or_log_tokens_or_signed_url(
    tmp_path, caplog
):
    signed_url = (
        f"https://cdn.example/report.pdf?token={TOKEN}&signature={SIGNED_SECRET}"
    )
    requests = MagicMock()
    requests.get = AsyncMock(
        return_value=httpx.Response(
            500,
            request=httpx.Request("GET", signed_url),
        )
    )
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient",
            return_value=_context_client(requests),
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", TOKEN)
        client.get_file = AsyncMock(return_value=_file_info(signed_url))
        result = await client.download_file("7", str(tmp_path / "report.pdf"))

    assert result.success is False
    exposed = f"{result.error}\n{caplog.text}"
    assert TOKEN not in exposed
    assert SIGNED_SECRET not in exposed
    assert signed_url not in exposed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upload_url", "confirm_url", "expects_upload_auth", "expects_confirm_auth"),
    [
        (
            "https://uploads.example/form",
            f"https://confirm.example/files/77?signature={SIGNED_SECRET}",
            False,
            False,
        ),
        (
            "https://canvas.example/upload",
            "https://canvas.example/files/77",
            True,
            True,
        ),
    ],
)
async def test_upload_uses_isolated_safe_transport_and_origin_scoped_bearer(
    tmp_path,
    upload_url,
    confirm_url,
    expects_upload_auth,
    expects_confirm_auth,
):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    authenticated = MagicMock()
    authenticated.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"upload_url": upload_url, "upload_params": {"key": "value"}},
            request=httpx.Request(
                "POST", "https://canvas.example/api/v1/courses/1/files"
            ),
        )
    )
    safe = MagicMock()
    safe.post = AsyncMock(
        return_value=httpx.Response(302, headers={"Location": confirm_url})
    )
    safe.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"id": 77, "filename": "report.pdf"},
            request=httpx.Request("GET", confirm_url),
        )
    )

    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient",
            return_value=_context_client(safe),
        ) as constructor,
    ):
        client = CanvasAPIClient("https://canvas.example", TOKEN)
        client._client = authenticated
        result = await client.upload_file("1", str(source))

    assert result.success is True
    authenticated.post.assert_awaited_once()
    upload_call = safe.post.await_args
    confirm_call = safe.get.await_args
    _assert_no_oauth_token(upload_call.args[0])
    _assert_no_oauth_token(confirm_call.args[0])
    expected_upload_headers = (
        {"Authorization": f"Bearer {TOKEN}"} if expects_upload_auth else {}
    )
    expected_confirm_headers = (
        {"Authorization": f"Bearer {TOKEN}"} if expects_confirm_auth else {}
    )
    assert upload_call.kwargs["headers"] == expected_upload_headers
    assert confirm_call.kwargs["headers"] == expected_confirm_headers
    assert "Cookie" not in upload_call.kwargs["headers"]
    assert "Cookie" not in confirm_call.kwargs["headers"]
    assert constructor.call_args.kwargs["trust_env"] is False
    assert isinstance(
        constructor.call_args.kwargs["transport"], CanvasSafeAsyncHTTPTransport
    )


@pytest.mark.asyncio
async def test_upload_failure_does_not_return_or_log_tokens_or_signed_url(
    tmp_path, caplog
):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    signed_url = f"https://uploads.example/form?token={TOKEN}&signature={SIGNED_SECRET}"
    authenticated = MagicMock()
    authenticated.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"upload_url": signed_url, "upload_params": {}},
            request=httpx.Request(
                "POST", "https://canvas.example/api/v1/courses/1/files"
            ),
        )
    )
    safe = MagicMock()
    safe.post = AsyncMock(
        return_value=httpx.Response(500, request=httpx.Request("POST", signed_url))
    )

    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient",
            return_value=_context_client(safe),
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", TOKEN)
        client._client = authenticated
        result = await client.upload_file("1", str(source))

    assert result.success is False
    exposed = f"{result.error}\n{caplog.text}"
    assert TOKEN not in exposed
    assert SIGNED_SECRET not in exposed
    assert signed_url not in exposed
