"""Exercise the published offline PDF entry point, including a refused scan."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """These subprocess examples have no database dependency."""
    yield


def run_example(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "examples/scan_pdf_direct.py"), *args],
        cwd=ROOT,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql://localhost/unused",
            "JWT_SECRET": "example-test-only-secret-at-least-32-bytes",
            "ENV": "test",
            "LLM_PROVIDER": "none",
            "LLM_FALLBACK_PROVIDER": "none",
            "EMBEDDING_PROVIDER": "none",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_default_pdf_example_completes_offline():
    result = run_example()
    assert result.returncode == 0, result.stderr
    assert "simple_syllabus.pdf" in result.stdout
    assert "Issues found:" in result.stdout
    assert "Compliance score:" in result.stdout
    assert "] None" not in result.stdout


def test_incomplete_pdf_example_refuses_to_publish_score():
    result = run_example("tests/fixtures/pdfs/academic_paper.pdf")
    assert result.returncode == 1
    assert "Scan incomplete:" in result.stderr
    assert "Traceback" not in result.stderr
    assert "Compliance score:" not in result.stdout
