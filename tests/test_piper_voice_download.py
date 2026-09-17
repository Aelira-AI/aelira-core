"""Exercise the build downloader against tiny, local HTTP responses."""

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts.download_piper_voice import VOICE_ASSETS, download_voice

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """This standalone build helper needs no application settings or database."""
    yield


@pytest.fixture
def http_fixture():
    routes = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, body, headers = routes[self.path]
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, str(value))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", routes
        finally:
            server.shutdown()
            thread.join()


def asset(base_url, filename, body):
    return filename, f"{base_url}/{filename}", hashlib.sha256(body).hexdigest()


def test_downloads_verified_pair_through_redirect_with_stable_metadata(
    http_fixture, tmp_path
):
    base_url, routes = http_fixture
    bodies = {
        "voice.onnx": b"tiny model fixture",
        "voice.onnx.json": b'{"fixture":true}',
    }
    routes["/voice.onnx"] = (302, b"", {"Location": "/redirected"})
    routes["/redirected"] = (200, bodies["voice.onnx"], {})
    routes["/voice.onnx.json"] = (200, bodies["voice.onnx.json"], {})
    assets = tuple(asset(base_url, name, body) for name, body in bodies.items())
    output = tmp_path / "voices"

    # Repeating the download leaves the same bytes, modes, and timestamps.
    for _ in range(2):
        download_voice(output, assets=assets, epoch=1234567890)
        assert {path.name for path in output.iterdir()} == set(bodies)
        for filename, body in bodies.items():
            path = output / filename
            assert path.read_bytes() == body
            assert path.stat().st_mtime == 1234567890
            assert path.stat().st_mode & 0o777 == 0o644
        assert output.stat().st_mtime == 1234567890


@pytest.mark.parametrize("status", [404, 500])
def test_http_error_never_creates_final_asset(http_fixture, tmp_path, status):
    base_url, routes = http_fixture
    body = b"fixture: HTTP error"
    routes["/voice.onnx"] = (status, body, {})
    # Even an error body matching the digest must fail on HTTP status.
    with pytest.raises(HTTPError) as error:
        download_voice(tmp_path, assets=(asset(base_url, "voice.onnx", body),))
    assert error.value.code == status
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["corrupt", "truncated"])
@pytest.mark.parametrize("existing", [False, True])
def test_failed_second_asset_leaves_no_partial_pair(
    http_fixture, tmp_path, failure, existing
):
    base_url, routes = http_fixture
    model = b"new model fixture"
    config = b'{"fixture":"complete config"}'
    bad_body = b"corrupt config" if failure == "corrupt" else config[:7]
    headers = {} if failure == "corrupt" else {"Content-Length": len(config)}
    routes["/voice.onnx"] = (200, model, {})
    routes["/voice.onnx.json"] = (200, bad_body, headers)
    assets = (
        asset(base_url, "voice.onnx", model),
        asset(base_url, "voice.onnx.json", config),
    )
    previous = {"voice.onnx": b"previous model", "voice.onnx.json": b"previous config"}
    if existing:
        for filename, body in previous.items():
            (tmp_path / filename).write_bytes(body)

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        download_voice(tmp_path, assets=assets)

    expected = previous if existing else {}
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == expected


def test_voice_manifest_retains_approved_production_assets():
    assert VOICE_ASSETS == (
        (
            "en_US-lessac-medium.onnx",
            "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true",
            "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        ),
        (
            "en_US-lessac-medium.onnx.json",
            "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json?download=true",
            "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
        ),
    )


def test_both_images_execute_shared_downloader_after_copying_it():
    invocation = "RUN python scripts/download_piper_voice.py"
    production = (ROOT / "Dockerfile").read_text()
    development = (ROOT / "Dockerfile.dev").read_text()
    assert production.index("COPY . .") < production.index(invocation)
    assert (
        development.index(
            "COPY scripts/download_piper_voice.py scripts/download_piper_voice.py"
        )
        < development.index(invocation)
        < development.index("COPY . .")
    )
    for dockerfile in (production, development):
        assert "huggingface.co/rhasspy/piper-voices" not in dockerfile
