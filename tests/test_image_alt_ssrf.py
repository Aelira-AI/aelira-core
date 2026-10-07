"""SSRF protection for the website image-alt-text helper.

`generate_image_alt_text` (src/api/main.py) downloads an image URL extracted
from caller-supplied HTML. It must refuse URLs that resolve to private/reserved
addresses BEFORE issuing any HTTP request, to prevent SSRF against internal
services and cloud metadata endpoints.
"""

import asyncio
from pathlib import Path
import socket
import ssl
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# These tests don't need the database; override any autouse DB fixture.
@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    yield


class _RecordingClient:
    """Stand-in for httpx.AsyncClient that records whether a GET was attempted."""

    get_called = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, *args, **kwargs):
        _RecordingClient.get_called = True
        raise AssertionError(f"SSRF guard failed: download attempted for {url}")


@pytest.fixture
def no_network(monkeypatch):
    import src.api.main as main

    _RecordingClient.get_called = False

    class _FakeHttpx:
        AsyncClient = _RecordingClient

    monkeypatch.setattr(main, "httpx", _FakeHttpx)
    return _RecordingClient


@pytest.mark.parametrize(
    "internal_url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://127.0.0.1/admin",
        "http://localhost/secret",
        "http://10.0.0.1/internal",
        "http://192.168.1.1/router",
        "file:///etc/passwd",
    ],
)
def test_internal_image_url_blocked_before_download(no_network, internal_url):
    from src.api.main import generate_image_alt_text

    snippet = f'<img src="{internal_url}" alt="">'
    result = asyncio.run(generate_image_alt_text(snippet))

    assert result is None
    assert no_network.get_called is False, "guard must block before any HTTP request"


class _ResponseStream:
    def __init__(self, response: bytes):
        self.response = response
        self.writes = []
        self.tls_host = None
        self.tls_context = None

    async def read(self, max_bytes, timeout=None):
        response, self.response = self.response, b""
        return response

    async def write(self, buffer, timeout=None):
        self.writes.append(buffer)

    async def aclose(self):
        pass

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls_context = ssl_context
        self.tls_host = server_hostname
        return self

    def get_extra_info(self, info):
        return None


def _dns(addresses):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, 443),
        )
        for address in addresses
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme,port", [("http", 80), ("https", 443)])
async def test_public_image_success_uses_pinned_address_and_source_bytes(
    monkeypatch, scheme, port
):
    from src.api.main import generate_image_alt_text
    from src.education import image_alt_text
    from src.utils import public_http

    body = b"\x89PNG\r\n\x1a\nimage bytes"
    stream = _ResponseStream(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 19\r\n\r\n"
        + body
    )
    network = AsyncMock()
    network.connect_tcp.return_value = stream
    monkeypatch.setattr(public_http, "AutoBackend", lambda: network)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns(["93.184.216.34"]),
    )

    class Generator:
        def __init__(self, lms_client=None):
            pass

        async def generate_alt_text(self, image_path, **kwargs):
            assert Path(image_path).read_bytes() == body
            assert image_path.endswith(".png")
            return {"success": True, "alt_text": "A sample image"}

    monkeypatch.setattr(image_alt_text, "ImageAltTextGenerator", Generator)
    result = await generate_image_alt_text(
        f'<img src="{scheme}://images.example/photo.png">'
    )

    assert result == {"success": True, "alt_text": "A sample image"}
    assert network.connect_tcp.await_args.args[:2] == ("93.184.216.34", port)
    if scheme == "https":
        assert stream.tls_host == "images.example"
        assert stream.tls_context.verify_mode == ssl.CERT_REQUIRED
        assert stream.tls_context.check_hostname is True
    else:
        assert stream.tls_host is None
    assert b"Host: images.example\r\n" in b"".join(stream.writes)


@pytest.mark.asyncio
async def test_dns_change_to_private_is_refused_at_connection(monkeypatch):
    from src.api.main import generate_image_alt_text
    from src.utils import public_http

    answers = iter([_dns(["93.184.216.34"]), _dns(["10.0.0.8"])])
    monkeypatch.setattr(
        public_http.socket, "getaddrinfo", lambda *args, **kwargs: next(answers)
    )
    network = AsyncMock()
    monkeypatch.setattr(public_http, "AutoBackend", lambda: network)

    result = await generate_image_alt_text(
        '<img src="https://images.example/photo.png">'
    )

    assert result is None
    network.connect_tcp.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        ["93.184.216.34", "10.0.0.8"],
        ["93.184.216.34", "::1"],
        ["2001:4860:4860::8888", "fe80::1"],
        ["169.254.169.254"],
        ["::ffff:127.0.0.1"],
    ],
)
async def test_connection_rejects_any_forbidden_dns_answer(monkeypatch, addresses):
    from src.utils import public_http

    network = AsyncMock()
    backend = public_http.PublicOnlyNetworkBackend(network_backend=network)
    monkeypatch.setattr(
        public_http.socket, "getaddrinfo", lambda *args, **kwargs: _dns(addresses)
    )

    with pytest.raises(ValueError, match="not allowed"):
        await backend.connect_tcp("images.example", 443)
    network.connect_tcp.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_ipv6_answer_is_pinned(monkeypatch):
    from src.utils import public_http

    network = AsyncMock()
    backend = public_http.PublicOnlyNetworkBackend(network_backend=network)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns(["2001:4860:4860::8888"]),
    )

    await backend.connect_tcp("images.example", 443)

    assert network.connect_tcp.await_args.args[:2] == ("2001:4860:4860::8888", 443)


@pytest.mark.asyncio
async def test_public_connection_tries_another_checked_answer(monkeypatch):
    from src.utils import public_http

    network = AsyncMock()
    network.connect_tcp.side_effect = [OSError("unreachable"), object()]
    backend = public_http.PublicOnlyNetworkBackend(network_backend=network)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns(["2001:4860:4860::8888", "93.184.216.34"]),
    )

    await backend.connect_tcp("images.example", 443)

    assert [call.args[0] for call in network.connect_tcp.await_args_list] == [
        "2001:4860:4860::8888",
        "93.184.216.34",
    ]


@pytest.mark.asyncio
async def test_relative_url_to_forbidden_target_is_refused(no_network):
    from src.api.main import generate_image_alt_text

    result = await generate_image_alt_text(
        '<img src="/internal/image.png">', base_url="http://127.0.0.1/page"
    )
    assert result is None
    assert no_network.get_called is False


@pytest.mark.asyncio
async def test_redirect_is_not_followed(monkeypatch):
    from src.api.main import generate_image_alt_text
    from src.utils import public_http

    stream = _ResponseStream(
        b"HTTP/1.1 302 Found\r\n"
        b"Location: http://169.254.169.254/latest/meta-data/\r\n"
        b"Content-Length: 0\r\n\r\n"
    )
    network = AsyncMock()
    network.connect_tcp.return_value = stream
    monkeypatch.setattr(public_http, "AutoBackend", lambda: network)
    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _dns(["93.184.216.34"]),
    )

    result = await generate_image_alt_text(
        '<img src="http://images.example/photo.png">'
    )

    assert result is None
    assert network.connect_tcp.await_count == 1


@pytest.mark.asyncio
async def test_non_http_scheme_never_reaches_network(no_network):
    from src.api.main import generate_image_alt_text

    assert (
        await generate_image_alt_text('<img src="gopher://example.com/secret">') is None
    )
    assert no_network.get_called is False
