"""Tests for _safe_upload_name.

The demo route writes each upload into a temp directory under its own name, so
remediation can fall back to the file stem for a document title instead of
shipping a PDF titled "tmpj1hjj0ch". That makes a client-supplied filename part
of a path, so it has to be reduced to a harmless basename first.
"""

import pytest

from src.api.demo_routes import _safe_upload_name


@pytest.mark.parametrize(
    "raw",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\config\\sam",
        "/etc/passwd",
        "subdir/report.pdf",
    ],
)
def test_directory_components_are_stripped(raw):
    """A crafted name must not escape the directory it is written into."""
    safe = _safe_upload_name(raw)
    assert "/" not in safe
    assert "\\" not in safe
    assert not safe.startswith(".")


@pytest.mark.parametrize("raw", [None, "", "   ", "...", "/", "../"])
def test_degenerate_names_get_a_usable_placeholder(raw):
    """Never return an empty name — it would produce a path ending in a slash."""
    assert _safe_upload_name(raw) == "unknown"


def test_ordinary_names_survive_intact():
    """The common case must be preserved; it becomes the document title."""
    assert _safe_upload_name("Screenshot 2026-08-12 112257.pdf") == (
        "Screenshot 2026-08-12 112257.pdf"
    )


def test_shell_and_glob_characters_are_replaced():
    assert _safe_upload_name("a;rm -rf *.pdf") == "a_rm -rf _.pdf"


def test_long_names_are_truncated():
    safe = _safe_upload_name("x" * 500 + ".pdf")
    assert len(safe) <= 200


def test_extension_is_still_derivable():
    """Extension parsing happens on the sanitized name, so it must survive."""
    safe = _safe_upload_name("../../report.PDF")
    assert safe.rsplit(".", 1)[-1].lower() == "pdf"
