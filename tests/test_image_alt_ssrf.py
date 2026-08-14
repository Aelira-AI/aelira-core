"""SSRF protection for the website image-alt-text helper.

`generate_image_alt_text` (src/api/main.py) downloads an image URL extracted
from caller-supplied HTML. It must refuse URLs that resolve to private/reserved
addresses BEFORE issuing any HTTP request, to prevent SSRF against internal
services and cloud metadata endpoints.
"""

import asyncio
from pathlib import Path
import sys

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
