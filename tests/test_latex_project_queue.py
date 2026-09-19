"""The project entry belongs to the durable scan, not remediation callers."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.jobs.local_scan_job import (
    LocalScanJobError,
    normalize_local_scan_options,
    stored_latex_project_input,
)


def test_legacy_latex_queue_options_unchanged():
    assert normalize_local_scan_options("local_latex", {"use_ollama": True}) == {
        "use_ollama": True
    }


def test_project_queue_preserves_relative_entry_with_spaces():
    options = {"use_ollama": False, "entry_file": "Course notes/main.tex"}
    assert normalize_local_scan_options("local_latex", options) == options


@pytest.mark.parametrize(
    "entry",
    [
        "/main.tex",
        "../main.tex",
        "a/../main.tex",
        "a//main.tex",
        "./main.tex",
        "a\\main.tex",
        "C:main.tex",
        "main.pdf",
        "a\x00.tex",
        "a" * 513 + ".tex",
    ],
)
def test_invalid_entry_options_fail_closed(entry):
    with pytest.raises(LocalScanJobError):
        normalize_local_scan_options(
            "local_latex", {"use_ollama": False, "entry_file": entry}
        )


@pytest.mark.parametrize(
    "mutation",
    ["wrong_scan", "wrong_kind", "missing_entry", "bad_hash", "changed_hash"],
)
def test_stored_binding_cannot_be_reinterpreted(mutation):
    payload = {
        "scan_kind": "local_latex",
        "scan_id": "scan",
        "input_sha256": "a" * 64,
        "options": {"use_ollama": False, "entry_file": "main.tex"},
    }
    scan = SimpleNamespace(id="scan", department_id="department", file_hash="a" * 64)
    if mutation == "wrong_scan":
        payload["scan_id"] = "other"
    elif mutation == "wrong_kind":
        payload["scan_kind"] = "local_code"
    elif mutation == "missing_entry":
        payload["options"].pop("entry_file")
    elif mutation == "bad_hash":
        payload["input_sha256"] = "not-a-hash"
    else:
        scan.file_hash = "b" * 64
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = SimpleNamespace(
        payload=payload
    )
    with pytest.raises(LocalScanJobError):
        stored_latex_project_input(db, scan)


def test_stored_binding_recovers_exact_server_owned_entry_and_hash():
    payload = {
        "scan_kind": "local_latex",
        "scan_id": "scan",
        "input_sha256": "a" * 64,
        "options": {"use_ollama": False, "entry_file": "notes/main.tex"},
    }
    scan = SimpleNamespace(id="scan", department_id="department", file_hash="a" * 64)
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = SimpleNamespace(
        payload=payload
    )
    assert stored_latex_project_input(db, scan) == ("notes/main.tex", "a" * 64)
