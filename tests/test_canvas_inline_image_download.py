"""Bounded, type-verified Canvas course image downloads."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from src.integrations.canvas.canvas_api import CanvasAPIClient
from src.integrations.canvas.models import CanvasFileInfo

PUBLIC_DNS = [(2, 1, 6, "", ("93.184.216.34", 443))]


def _bytes(tmp_path, suffix, format_name):
    path = tmp_path / f"fixture{suffix}"
    Image.new("RGB", (3, 2), "blue").save(path, format=format_name)
    return path.read_bytes()


def _info(*, mime="image/png", size=10, url="https://files.example/image"):
    now = datetime.now(timezone.utc)
    return CanvasFileInfo(
        id="42",
        display_name="image",
        filename="misleading.txt",
        content_type=mime,
        size=size,
        url=url,
        created_at=now,
        updated_at=now,
    )


class _StreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        await self.response.aclose()
        return False


def _download_context(responses):
    client = MagicMock()
    queue = list(responses)
    client.stream.side_effect = lambda *_args, **_kwargs: _StreamContext(queue.pop(0))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return client, context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "format_name", "mime"),
    [
        (".png", "PNG", "image/png"),
        (".jpg", "JPEG", "image/jpeg"),
        (".gif", "GIF", "image/gif"),
        (".webp", "WEBP", "image/webp"),
        (".bmp", "BMP", "image/bmp"),
    ],
)
async def test_course_image_download_returns_observed_type(
    tmp_path, suffix, format_name, mime
):
    body = _bytes(tmp_path, suffix, format_name)
    response = httpx.Response(
        200,
        content=body,
        headers={"Content-Length": str(len(body))},
        request=httpx.Request("GET", "https://files.example/image"),
    )
    transport, context = _download_context([response])
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        result = await client.download_course_image(
            _info(mime=mime, size=len(body)), max_bytes=len(body)
        )

    assert result.success is True
    assert result.data == body
    assert result.content_type == mime
    assert result.suffix == suffix
    assert transport.stream.call_count == 1


@pytest.mark.asyncio
async def test_course_image_metadata_oversize_stops_before_request():
    with patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS):
        client = CanvasAPIClient("https://canvas.example", "token")
    with patch("src.integrations.canvas.canvas_api.httpx.AsyncClient") as constructor:
        result = await client.download_course_image(_info(size=101), max_bytes=100)

    assert result.success is False
    constructor.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["101", None])
async def test_course_image_rejects_header_or_cumulative_oversize(header):
    response = httpx.Response(
        200,
        content=b"\x89PNG\r\n\x1a\n" + b"x" * 100,
        headers={"Content-Length": header} if header else {},
        request=httpx.Request("GET", "https://files.example/image"),
    )
    transport, context = _download_context([response])
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        result = await client.download_course_image(_info(size=10), max_bytes=100)

    assert result.success is False
    assert result.data is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "mime"),
    [
        (b"<html><body>login</body></html>", "image/png"),
        (b"<?xml version='1.0'?><x/>", "image/png"),
        (b'{"error":"login"}', "image/png"),
        (b"not-an-image", "image/png"),
        (b"\x89PNG\r\n\x1a\nrest", "image/jpeg"),
    ],
)
async def test_course_image_rejects_unknown_text_and_mime_mismatch(body, mime):
    response = httpx.Response(
        200,
        content=body,
        request=httpx.Request("GET", "https://files.example/image"),
    )
    transport, context = _download_context([response])
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        result = await client.download_course_image(
            _info(mime=mime, size=len(body)), max_bytes=100
        )

    assert result.success is False
    assert result.data is None


@pytest.mark.asyncio
async def test_course_image_redirect_strips_auth_and_cookies_cross_origin(tmp_path):
    body = _bytes(tmp_path, ".png", "PNG")
    redirect = httpx.Response(
        302,
        headers={"Location": "https://cdn.example/image"},
        request=httpx.Request("GET", "https://canvas.example/file"),
    )
    final = httpx.Response(
        200,
        content=body,
        request=httpx.Request("GET", "https://cdn.example/image"),
    )
    transport, context = _download_context([redirect, final])
    with (
        patch("src.utils.security.socket.getaddrinfo", return_value=PUBLIC_DNS),
        patch(
            "src.integrations.canvas.canvas_api.httpx.AsyncClient", return_value=context
        ),
    ):
        client = CanvasAPIClient("https://canvas.example", "token")
        result = await client.download_course_image(
            _info(size=len(body), url="https://canvas.example/file"), max_bytes=1000
        )

    assert result.success is True
    assert transport.stream.call_args_list[0].kwargs["headers"]["Authorization"]
    assert transport.stream.call_args_list[1].kwargs["headers"] == {}
    assert transport.cookies.clear.call_count == 2
