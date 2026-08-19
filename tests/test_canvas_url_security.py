"""Canvas API outbound URL and redirect security tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.api.canvas_routes import _get_canvas_client
from src.api.canvas_scan_routes import _rewrite_localhost_for_docker
from src.integrations.canvas.canvas_api import CanvasAPIClient
from src.integrations.canvas.safe_http import (
    CanvasSafeAsyncHTTPTransport,
    CanvasSafeNetworkBackend,
)
from src.utils.security import (
    resolve_canvas_network_origin,
    validate_canvas_outbound_url,
)

PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]


class _RecordingNetworkStream:
    def __init__(self):
        self.writes = []
        self.server_hostname = None
        self._response_sent = False

    async def read(self, max_bytes, timeout=None):
        if self._response_sent:
            return b""
        self._response_sent = True
        return b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def aclose(self):
        return None

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.server_hostname = server_hostname
        return self

    def get_extra_info(self, info):
        return None


@pytest.mark.asyncio
async def test_safe_backend_connects_to_validated_ip_not_hostname():
    wrapped = MagicMock()
    wrapped.connect_tcp = AsyncMock(return_value=MagicMock())
    backend = CanvasSafeNetworkBackend(network_backend=wrapped)

    with patch(
        "src.integrations.canvas.safe_http.socket.getaddrinfo", return_value=PUBLIC_DNS
    ):
        await backend.connect_tcp("canvas.example", 443)

    assert wrapped.connect_tcp.await_args.args[:2] == ("93.184.216.34", 443)


@pytest.mark.asyncio
async def test_safe_backend_rejects_rebound_private_address_before_connect():
    wrapped = MagicMock()
    wrapped.connect_tcp = AsyncMock()
    backend = CanvasSafeNetworkBackend(network_backend=wrapped)
    rebound_dns = [(2, 1, 6, "", ("10.0.0.8", 443))]

    with (
        patch(
            "src.integrations.canvas.safe_http.socket.getaddrinfo",
            return_value=rebound_dns,
        ),
        pytest.raises(ValueError, match="not allowed"),
    ):
        await backend.connect_tcp("canvas.example", 443)

    wrapped.connect_tcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_backend_does_not_treat_public_dev_origin_as_private_exception(
    monkeypatch,
):
    monkeypatch.setenv("ENV", "development")
    wrapped = MagicMock()
    wrapped.connect_tcp = AsyncMock()
    backend = CanvasSafeNetworkBackend(
        "https://canvas.example", network_backend=wrapped
    )
    rebound_dns = [(2, 1, 6, "", ("10.0.0.8", 443))]

    with (
        patch(
            "src.integrations.canvas.safe_http.socket.getaddrinfo",
            return_value=rebound_dns,
        ),
        pytest.raises(ValueError, match="not allowed"),
    ):
        await backend.connect_tcp("canvas.example", 443)

    wrapped.connect_tcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_backend_allows_exact_configured_dev_docker_origin(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "http://canvas-docker.internal:3999")
    wrapped = MagicMock()
    wrapped.connect_tcp = AsyncMock(return_value=MagicMock())
    backend = CanvasSafeNetworkBackend(
        "http://canvas-docker.internal:3999", network_backend=wrapped
    )
    private_dns = [(2, 1, 6, "", ("192.168.65.2", 3999))]

    with patch(
        "src.integrations.canvas.safe_http.socket.getaddrinfo", return_value=private_dns
    ):
        await backend.connect_tcp("canvas-docker.internal", 3999)

    assert wrapped.connect_tcp.await_args.args[:2] == ("192.168.65.2", 3999)


@pytest.mark.asyncio
async def test_safe_transport_retains_original_tls_sni_and_host_header():
    stream = _RecordingNetworkStream()
    wrapped = MagicMock()
    wrapped.connect_tcp = AsyncMock(return_value=stream)
    transport = CanvasSafeAsyncHTTPTransport()
    safe_backend = transport._pool._network_backend
    safe_backend._network_backend = wrapped

    with patch(
        "src.integrations.canvas.safe_http.socket.getaddrinfo", return_value=PUBLIC_DNS
    ):
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            response = await client.get("https://canvas.example/api/v1/users/self")

    assert response.status_code == 200
    assert wrapped.connect_tcp.await_args.args[:2] == ("93.184.216.34", 443)
    assert stream.server_hostname == "canvas.example"
    request_bytes = b"".join(stream.writes)
    assert b"Host: canvas.example\r\n" in request_bytes


def test_development_localhost_maps_to_exact_configured_docker_origin(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "http://host.docker.internal:3000/")

    assert (
        resolve_canvas_network_origin("http://localhost:3000")
        == "http://host.docker.internal:3000"
    )
    assert (
        resolve_canvas_network_origin("http://host.docker.internal:3000")
        == "http://host.docker.internal:3000"
    )


def test_development_localhost_uses_documented_docker_host_default(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("CANVAS_DOCKER_ORIGIN", raising=False)

    assert (
        resolve_canvas_network_origin("http://localhost:4321")
        == "http://host.docker.internal:4321"
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_private_docker_origin_is_rejected_outside_development(
    monkeypatch, environment
):
    monkeypatch.setenv("ENV", environment)
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "https://docker.canvas.example:3443")

    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        pytest.raises(ValueError),
    ):
        resolve_canvas_network_origin("https://docker.canvas.example:3443")


def test_configured_docker_origin_must_be_a_root_origin(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv(
        "CANVAS_DOCKER_ORIGIN", "http://host.docker.internal:3000/canvas"
    )

    with pytest.raises(ValueError, match="Docker origin"):
        resolve_canvas_network_origin("http://localhost:3000")


def test_hostname_containing_localhost_is_not_rewritten(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "http://host.docker.internal:3000")

    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        assert (
            resolve_canvas_network_origin("https://mylocalhost.example")
            == "https://mylocalhost.example"
        )


@pytest.mark.asyncio
async def test_canvas_client_centralizes_development_network_origin(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "http://host.docker.internal:3000")

    client = CanvasAPIClient("http://localhost:3000", "token")
    http_client = await client._get_client()
    try:
        assert client.canvas_url == "http://host.docker.internal:3000"
        assert client.api_base == "http://host.docker.internal:3000/api/v1"
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_persisted_development_localhost_gets_a_usable_canvas_client(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "http://host.docker.internal:3000")
    credential = SimpleNamespace(
        id="credential-1",
        provider_metadata={"canvas_instance_url": "http://localhost:3000"},
        token_expires_at=None,
        access_token="encrypted-access",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = credential
    token_manager = MagicMock()
    token_manager.is_token_expired.return_value = False
    token_manager.decrypt_token.return_value = "access-token"

    with patch("src.api.canvas_routes.OAuthTokenManager", return_value=token_manager):
        returned_credential, client = await _get_canvas_client("department-1", db)
        http_client = await client._get_client()

    try:
        assert returned_credential is credential
        assert client.canvas_url == "http://host.docker.internal:3000"
    finally:
        await http_client.aclose()


def test_scan_rewrite_does_not_mutate_a_constructed_client(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "http://host.docker.internal:3000")
    client = CanvasAPIClient("http://localhost:3000", "token")

    _rewrite_localhost_for_docker(client)

    assert client.canvas_url == client._canvas_origin
    assert client.api_base == f"{client._canvas_origin}/api/v1"


def test_scan_rewrite_does_not_match_localhost_substrings(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://mylocalhost.example", "token")

    _rewrite_localhost_for_docker(client)

    assert client.canvas_url == "https://mylocalhost.example"
    assert client.api_base == "https://mylocalhost.example/api/v1"


def test_canvas_client_requires_a_canonical_safe_base_origin():
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://Canvas.Example:443/", "token")

    assert client.canvas_url == "https://canvas.example"
    assert client.api_base == "https://canvas.example/api/v1"


@pytest.mark.parametrize(
    "url",
    [
        "http://canvas.example",
        "https://user@canvas.example",
        "https://canvas.example/path",
        "https://127.0.0.1",
        "https://[::1]",
        "https://[fc00::1]",
        "https://[fe80::1]",
    ],
)
def test_canvas_client_rejects_unsafe_base_origins(url):
    with pytest.raises(ValueError):
        CanvasAPIClient(url, "token")


def test_canvas_outbound_url_resolves_relative_paths_and_public_dns():
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        result = validate_canvas_outbound_url(
            "../files/7?download=1",
            "https://canvas.example/api/v1/courses/1/files",
        )

    assert result == "https://canvas.example/api/v1/courses/files/7?download=1"


@pytest.mark.parametrize(
    ("url", "dns"),
    [
        ("http://files.example/file", PUBLIC_DNS),
        ("https://user@files.example/file", PUBLIC_DNS),
        ("https://files.example/file#secret", PUBLIC_DNS),
        ("https://files.example/file", [(2, 1, 6, "", ("10.0.0.7", 443))]),
        ("https://files.example/file", [(10, 1, 6, "", ("::1", 443, 0, 0))]),
        ("https://files.example/file", [(10, 1, 6, "", ("ff02::1", 443, 0, 0))]),
    ],
)
def test_canvas_outbound_url_rejects_unsafe_targets(url, dns):
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=dns),
        pytest.raises(ValueError),
    ):
        validate_canvas_outbound_url(url, "https://canvas.example/api/v1/files/1")


def _page(data, link=None):
    return httpx.Response(
        200,
        json=data,
        headers={"Link": link} if link else {},
        request=httpx.Request("GET", "https://canvas.example/api/v1/courses/1/files"),
    )


@pytest.mark.asyncio
async def test_pagination_rejects_cross_origin_next_before_second_request():
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://canvas.example", "token")
        request = AsyncMock(
            side_effect=[
                _page(
                    [{"id": 1}],
                    '<https://evil.example/steal?page=2>; rel="next"',
                ),
                AssertionError("cross-origin second request attempted"),
            ]
        )
        client._request_with_retry = request

        with pytest.raises(ValueError, match="origin"):
            await client._paginate(
                "https://canvas.example/api/v1/courses/1/files",
                params={"per_page": 100},
            )

    assert request.await_count == 1


@pytest.mark.asyncio
async def test_pagination_resolves_safe_relative_next_link():
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://canvas.example", "token")
        request = AsyncMock(
            side_effect=[
                _page([{"id": 1}], '<?page=2&per_page=100>; rel="next"'),
                _page([{"id": 2}]),
            ]
        )
        client._request_with_retry = request

        result = await client._paginate(
            "https://canvas.example/api/v1/courses/1/files",
            params={"per_page": 100},
        )

    assert result == [{"id": 1}, {"id": 2}]
    assert request.await_args_list[1].args[:2] == (
        "GET",
        "https://canvas.example/api/v1/courses/1/files?page=2&per_page=100",
    )


@pytest.mark.asyncio
async def test_pagination_rejects_repeated_canonical_url_before_next_request():
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://canvas.example", "token")
        request = AsyncMock(
            side_effect=[
                _page(
                    [{"id": 1}],
                    '<https://canvas.example/api/v1/courses/1/files?per_page=100>; rel="next"',
                ),
                AssertionError("cyclic second request attempted"),
            ]
        )
        client._request_with_retry = request

        with pytest.raises(ValueError, match="cycle"):
            await client._paginate(
                "https://canvas.example/api/v1/courses/1/files?ignored=1",
                params={"per_page": 100},
            )

    assert request.await_count == 1


@pytest.mark.asyncio
async def test_pagination_rejects_excessive_pages_before_next_request():
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://canvas.example", "token")
        client.MAX_PAGINATION_PAGES = 2
        request = AsyncMock(
            side_effect=[
                _page([{"id": 1}], '<?page=2>; rel="next"'),
                _page([{"id": 2}], '<?page=3>; rel="next"'),
                AssertionError("page beyond ceiling requested"),
            ]
        )
        client._request_with_retry = request

        with pytest.raises(ValueError, match="page limit"):
            await client._paginate(
                "https://canvas.example/api/v1/courses/1/files",
                params={"per_page": 100},
            )

    assert request.await_count == 2


@pytest.mark.asyncio
async def test_authenticated_client_disables_automatic_redirects():
    fake_client = MagicMock()
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient",
            return_value=fake_client,
        ) as constructor,
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        assert await client._get_client() is fake_client

    assert constructor.call_args.kwargs["follow_redirects"] is False
    assert constructor.call_args.kwargs["trust_env"] is False
    transport = constructor.call_args.kwargs["transport"]
    assert isinstance(transport, CanvasSafeAsyncHTTPTransport)
    assert isinstance(transport._pool._network_backend, CanvasSafeNetworkBackend)
    # The URL hostname remains untouched for Host/:authority and TLS SNI.
    assert client.api_base == "https://canvas.example/api/v1"


def _download_client(response_side_effect):
    transport = MagicMock()
    transport.get = AsyncMock(side_effect=response_side_effect)
    return transport, _client_context(transport)


def _client_context(client):
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def _file_info(url):
    return SimpleNamespace(
        url=url,
        filename="report.pdf",
        content_type="application/pdf",
        size=7,
    )


@pytest.mark.asyncio
async def test_download_rejects_private_initial_url_before_request(tmp_path):
    transport, context = _download_client([])
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        client.get_file = AsyncMock(return_value=_file_info("https://127.0.0.1/file"))
        result = await client.download_file("7", str(tmp_path / "report.pdf"))

    assert result.success is False
    transport.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_rejects_private_redirect_before_next_request(tmp_path):
    redirect = httpx.Response(302, headers={"Location": "https://10.0.0.8/file"})
    transport, context = _download_client([redirect])
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        client.get_file = AsyncMock(
            return_value=_file_info("https://files.example/start")
        )
        result = await client.download_file("7", str(tmp_path / "report.pdf"))

    assert result.success is False
    assert transport.get.await_count == 1


@pytest.mark.asyncio
async def test_download_allows_validated_public_cross_origin_redirect(tmp_path):
    responses = [
        httpx.Response(302, headers={"Location": "https://cdn.example/report.pdf"}),
        httpx.Response(
            200,
            content=b"content",
            request=httpx.Request("GET", "https://cdn.example/report.pdf"),
        ),
    ]
    transport, context = _download_client(responses)
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        client.get_file = AsyncMock(
            return_value=_file_info("https://files.example/start")
        )
        destination = tmp_path / "report.pdf"
        result = await client.download_file("7", str(destination))

    assert result.success is True
    assert destination.read_bytes() == b"content"
    assert transport.get.await_count == 2
    assert transport.get.await_args_list[1].args[0] == "https://cdn.example/report.pdf"


@pytest.mark.asyncio
async def test_download_client_uses_safe_transport_and_disables_env_proxies(tmp_path):
    response = httpx.Response(
        200,
        content=b"content",
        request=httpx.Request("GET", "https://files.example/report.pdf"),
    )
    transport, context = _download_client([response])
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ) as constructor,
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        client.get_file = AsyncMock(
            return_value=_file_info("https://files.example/report.pdf")
        )
        result = await client.download_file("7", str(tmp_path / "report.pdf"))

    assert result.success is True
    assert constructor.call_args.kwargs["trust_env"] is False
    assert isinstance(
        constructor.call_args.kwargs["transport"], CanvasSafeAsyncHTTPTransport
    )


@pytest.mark.asyncio
async def test_download_rewrites_exact_dev_localhost_to_configured_docker_origin(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", "http://host.docker.internal:3000")
    transport, context = _download_client(
        [
            httpx.Response(
                200,
                content=b"content",
                request=httpx.Request("GET", "http://host.docker.internal:3000/file"),
            )
        ]
    )
    with patch(
        "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
    ):
        client = CanvasAPIClient("http://localhost:3000", "token")
        client.get_file = AsyncMock(
            return_value=_file_info("http://localhost:3000/files/7/download")
        )
        result = await client.download_file("7", str(tmp_path / "report.pdf"))

    assert result.success is True
    assert (
        transport.get.await_args.args[0]
        == "http://host.docker.internal:3000/files/7/download"
    )


@pytest.mark.asyncio
async def test_download_stops_a_bounded_redirect_loop(tmp_path):
    redirects = [httpx.Response(302, headers={"Location": "/loop"}) for _ in range(20)]
    transport, context = _download_client(redirects)
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        client.get_file = AsyncMock(
            return_value=_file_info("https://files.example/start")
        )
        result = await client.download_file("7", str(tmp_path / "report.pdf"))

    assert result.success is False
    assert transport.get.await_count <= 11


def _json_response(status, data, url="https://canvas.example/api/v1/upload"):
    return httpx.Response(
        status,
        json=data,
        request=httpx.Request("POST", url),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upload_url", ["https://10.0.0.9/upload", "https://[::1]/upload"]
)
async def test_upload_rejects_private_canvas_upload_url_before_request(
    tmp_path, upload_url
):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    transport = MagicMock()
    transport.post = AsyncMock(
        return_value=_json_response(
            200,
            {"upload_url": upload_url, "upload_params": {"key": "value"}},
        )
    )
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://canvas.example", "token")
        client._client = transport
        result = await client.upload_file("1", str(source))

    assert result.success is False
    assert transport.post.await_count == 1


@pytest.mark.asyncio
async def test_upload_rejects_private_confirmation_redirect_before_request(tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    transport = MagicMock()
    transport.post = AsyncMock(
        side_effect=[
            _json_response(
                200,
                {
                    "upload_url": "https://uploads.example/form",
                    "upload_params": {"key": "value"},
                },
            ),
            httpx.Response(302, headers={"Location": "https://10.0.0.9/confirm"}),
        ]
    )
    transport.get = AsyncMock()
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient",
            return_value=_client_context(transport),
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        client._client = transport
        result = await client.upload_file("1", str(source))

    assert result.success is False
    transport.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_allows_public_https_upload_and_confirmation_urls(tmp_path):
    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    transport = MagicMock()
    transport.post = AsyncMock(
        side_effect=[
            _json_response(
                200,
                {
                    "upload_url": "https://uploads.example/form",
                    "upload_params": {"key": "value"},
                },
            ),
            httpx.Response(
                302,
                headers={"Location": "https://confirm.example/files/77"},
            ),
        ]
    )
    transport.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "id": 77,
                "filename": "report.pdf",
                "url": "https://canvas.example/files/77",
            },
            request=httpx.Request("GET", "https://confirm.example/files/77"),
        )
    )
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient",
            return_value=_client_context(transport),
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        client._client = transport
        result = await client.upload_file("1", str(source))

    assert result.success is True
    assert result.file_id == "77"
    assert transport.post.await_args_list[1].args[0] == "https://uploads.example/form"
    assert transport.get.await_args.args[0] == "https://confirm.example/files/77"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured_origin", ["http://host.docker.internal:3999", None]
)
async def test_upload_maps_dev_localhost_urls_to_exact_docker_origin(
    tmp_path, monkeypatch, configured_origin
):
    monkeypatch.setenv("ENV", "development")
    if configured_origin:
        monkeypatch.setenv("CANVAS_DOCKER_ORIGIN", configured_origin)
        expected_origin = configured_origin
    else:
        monkeypatch.delenv("CANVAS_DOCKER_ORIGIN", raising=False)
        expected_origin = "http://host.docker.internal:3000"

    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    transport = MagicMock()
    transport.post = AsyncMock(
        side_effect=[
            _json_response(
                200,
                {
                    "upload_url": "http://localhost:3000/upload",
                    "upload_params": {"key": "value"},
                },
            ),
            httpx.Response(302, headers={"Location": "http://localhost:3000/confirm"}),
        ]
    )
    transport.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"id": 77, "filename": "report.pdf"},
            request=httpx.Request("GET", f"{expected_origin}/confirm"),
        )
    )
    with patch(
        "src.integrations.canvas.canvas_api.httpx.AsyncClient",
        return_value=_client_context(transport),
    ):
        client = CanvasAPIClient("http://localhost:3000", "token")
        client._client = transport
        result = await client.upload_file("1", str(source))

    assert result.success is True
    assert transport.post.await_args_list[1].args[0] == f"{expected_origin}/upload"
    assert transport.get.await_args.args[0] == f"{expected_origin}/confirm"


@pytest.mark.asyncio
async def test_upload_rejects_docker_host_outside_development(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    source = tmp_path / "report.pdf"
    source.write_bytes(b"content")
    transport = MagicMock()
    transport.post = AsyncMock(
        return_value=_json_response(
            200,
            {
                "upload_url": "https://host.docker.internal:3000/upload",
                "upload_params": {},
            },
        )
    )
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://canvas.example", "token")
        client._client = transport
        result = await client.upload_file("1", str(source))

    assert result.success is False
    assert transport.post.await_count == 1
