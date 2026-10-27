"""Exercise the build downloader against tiny, local HTTP responses."""

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts import download_piper_voice as piper
from scripts.download_piper_voice import VOICE_ASSETS, download_voice

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """This standalone build helper needs no application settings or database."""
    yield


@pytest.fixture(autouse=True)
def build_inputs_dir(monkeypatch, tmp_path_factory):
    """An empty cache, so a real /build-inputs never affects these tests."""
    path = tmp_path_factory.mktemp("build-inputs")
    monkeypatch.setenv("AELIRA_BUILD_INPUTS", str(path))
    return path


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Record the retry waits instead of sleeping."""
    waits = []
    monkeypatch.setattr(piper.time, "sleep", waits.append)
    return waits


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


def cache(build_inputs_dir, body):
    store = build_inputs_dir / "sha256"
    store.mkdir(exist_ok=True)
    return store / hashlib.sha256(body).hexdigest()


def test_http_error_is_retried_twice_before_it_is_raised(
    http_fixture, tmp_path, sleeps
):
    base_url, routes = http_fixture
    body = b"fixture: HTTP error"
    routes["/voice.onnx"] = (503, body, {})

    with pytest.raises(HTTPError) as error:
        download_voice(tmp_path, assets=(asset(base_url, "voice.onnx", body),))

    assert error.value.code == 503
    assert sleeps == [1, 2]


def test_wrong_bytes_on_every_attempt_fail_after_two_waits(
    http_fixture, tmp_path, sleeps
):
    base_url, routes = http_fixture
    routes["/voice.onnx"] = (200, b"tampered", {})

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        download_voice(
            tmp_path, assets=(asset(base_url, "voice.onnx", b"pinned bytes"),)
        )

    assert sleeps == [1, 2]
    assert list(tmp_path.iterdir()) == []


def test_transient_failure_then_good_response_succeeds_after_one_wait(
    http_fixture, tmp_path, sleeps, monkeypatch
):
    base_url, routes = http_fixture
    body = b"model fixture"
    routes["/voice.onnx"] = (503, b"busy", {})
    monkeypatch.setattr(
        piper.time,
        "sleep",
        lambda delay: (
            sleeps.append(delay),
            routes.update({"/voice.onnx": (200, body, {})}),
        ),
    )

    download_voice(tmp_path, assets=(asset(base_url, "voice.onnx", body),))

    assert (tmp_path / "voice.onnx").read_bytes() == body
    assert sleeps == [1]


def test_cached_file_matching_the_pin_is_used_without_the_network(
    build_inputs_dir, tmp_path, sleeps, monkeypatch
):
    body = b"cached model fixture"
    cache(build_inputs_dir, body).write_bytes(body)

    def refuse(*_args, **_kwargs):
        raise AssertionError("a valid cache hit must not touch the network")

    monkeypatch.setattr(piper, "urlopen", refuse)
    assets = (
        (
            "voice.onnx",
            "https://example.invalid/voice.onnx",
            hashlib.sha256(body).hexdigest(),
        ),
    )
    output = tmp_path / "voices"

    download_voice(output, assets=assets, epoch=1234567890)

    assert (output / "voice.onnx").read_bytes() == body
    assert (output / "voice.onnx").stat().st_mode & 0o777 == 0o644
    assert sleeps == []


def test_cached_file_with_wrong_bytes_is_ignored_for_the_network_copy(
    http_fixture, build_inputs_dir, tmp_path, sleeps, capsys
):
    base_url, routes = http_fixture
    body = b"genuine model fixture"
    cache(build_inputs_dir, body).write_bytes(b"corrupted cache entry")
    routes["/voice.onnx"] = (200, body, {})

    download_voice(tmp_path, assets=(asset(base_url, "voice.onnx", body),))

    assert (tmp_path / "voice.onnx").read_bytes() == body
    assert sleeps == []
    assert "cache=mismatch" in capsys.readouterr().err


def test_default_cache_directory_is_build_inputs(monkeypatch, tmp_path):
    monkeypatch.delenv("AELIRA_BUILD_INPUTS")
    seen = []
    real_is_file = Path.is_file

    def is_file(path):
        seen.append(str(path))
        return real_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file)
    body = b"x"
    monkeypatch.setattr(
        piper, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline"))
    )

    with pytest.raises(OSError):
        piper.fetch_asset(
            "f",
            "https://example.invalid/f",
            hashlib.sha256(body).hexdigest(),
            tmp_path / "f",
        )

    assert f"/build-inputs/sha256/{hashlib.sha256(body).hexdigest()}" in seen


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
    invocation = (
        "RUN --mount=type=bind,from=build-inputs,target=/build-inputs \\\n"
        "    python scripts/download_piper_voice.py"
    )
    production = (ROOT / "Dockerfile").read_text()
    development = (ROOT / "Dockerfile.dev").read_text()
    assert production.count(invocation) == 1
    assert development.count(invocation) == 1
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
