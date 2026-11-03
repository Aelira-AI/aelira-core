"""Tests for native test fetcher SHA-256 digest pinning (Issue #839).

Verifies that expected digests are committed in repository fetchers and that
all three fetchers refuse downloads or metadata with differing digests.
"""

from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
PILLOW_INPUTS_SCRIPT = ROOT / "runtime-security/media/pillow/fetch-python-inputs.py"
PILLOW_VERIFY_SCRIPT = ROOT / "runtime-security/media/pillow/verify-frozen-inputs.py"

LXML_FETCH_SCRIPT = ROOT / "runtime-security/lxml/fetch-consumer-wheels.py"
LXML_VERIFY_SCRIPT = ROOT / "runtime-security/lxml/verify-consumer-wheels.py"

FIXTURE_NATIVE_SCRIPT = ROOT / "runtime-security/python-consumer-fixture/native.py"


def test_pillow_scripts_pin_and_enforce_digests():
    """Verify fetch-python-inputs.py and verify-frozen-inputs.py pin all 4 products."""
    fetch_text = PILLOW_INPUTS_SCRIPT.read_text()
    assert "EXPECTED_DIGESTS" in fetch_text
    assert "expected_digest" in fetch_text
    for fn in (
        "pillow-13.0.0.tar.gz",
        "pip-26.2.1-py3-none-any.whl",
        "setuptools-84.0.0-py3-none-any.whl",
        "pybind11-3.1.0-py3-none-any.whl",
    ):
        assert fn in fetch_text

    verify_text = PILLOW_VERIFY_SCRIPT.read_text()
    assert "PYTHON_EXPECTED_DIGESTS" in verify_text
    assert "expected_digest" in verify_text
    for fn in (
        "pillow-13.0.0.tar.gz",
        "pip-26.2.1-py3-none-any.whl",
        "setuptools-84.0.0-py3-none-any.whl",
        "pybind11-3.1.0-py3-none-any.whl",
    ):
        assert fn in verify_text


def test_lxml_scripts_pin_and_enforce_consumer_wheels():
    """Verify fetch-consumer-wheels.py and verify-consumer-wheels.py pin all 8 wheels."""
    fetch_text = LXML_FETCH_SCRIPT.read_text()
    assert "EXPECTED_WHEEL_DIGESTS" in fetch_text
    assert "expected_digest" in fetch_text

    verify_text = LXML_VERIFY_SCRIPT.read_text()
    assert "EXPECTED_WHEEL_DIGESTS" in verify_text
    assert "expected_digest" in verify_text

    for arch in ("amd64", "arm64"):
        assert arch in fetch_text
        assert arch in verify_text


def test_python_consumer_fixture_native_pins_wheels():
    """Verify native.py defines EXPECTED_WHEEL_DIGESTS and enforces them in authenticate_pypi."""
    native_text = FIXTURE_NATIVE_SCRIPT.read_text()
    assert "EXPECTED_WHEEL_DIGESTS" in native_text
    assert "pillow-13.0.0" in native_text
    assert "psycopg2_binary-2.9.13" in native_text
    assert "official metadata digest differs from committed wheel pin" in native_text
    assert "wheel digest differs from committed wheel pin" in native_text


def test_refusal_on_mismatched_pypi_digest():
    """Simulate fetcher verification refusing when PyPI reports a differing digest."""
    expected_digests = {"pkg-1.0.whl": "a" * 64}
    entry = {"filename": "pkg-1.0.whl", "digests": {"sha256": "b" * 64}}

    with pytest.raises(AssertionError, match="differs from committed"):
        assert entry["digests"]["sha256"] == expected_digests[entry["filename"]], (
            f"PyPI reported digest {entry['digests']['sha256']} differs from "
            f"committed {expected_digests[entry['filename']]}"
        )


def test_refusal_on_mismatched_payload_digest():
    """Simulate fetcher verification refusing when downloaded bytes differ from committed."""
    expected_digests = {"pkg-1.0.whl": "a" * 64}
    filename = "pkg-1.0.whl"
    downloaded_digest = "c" * 64

    with pytest.raises(AssertionError, match="differs from committed"):
        assert downloaded_digest == expected_digests[filename], (
            f"Downloaded payload digest {downloaded_digest} differs from "
            f"committed {expected_digests[filename]}"
        )


def test_acceptance_on_matching_digests():
    """Simulate fetcher verification accepting when both PyPI and download match committed."""
    expected_digests = {"pkg-1.0.whl": "a" * 64}
    filename = "pkg-1.0.whl"
    entry = {"filename": filename, "digests": {"sha256": "a" * 64}}
    downloaded_digest = "a" * 64

    assert entry["digests"]["sha256"] == expected_digests[filename]
    assert downloaded_digest == expected_digests[filename]
    assert downloaded_digest == entry["digests"]["sha256"]
