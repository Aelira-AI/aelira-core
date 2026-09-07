"""Required, manifest-driven PDF remediation acceptance corpus."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys

import pikepdf
import pytest

from scripts.pdf_acceptance_corpus import (
    CorpusContractError,
    REQUIRED_CASE_IDS,
    generate_corpus,
    load_manifest,
    run_required_corpus,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "pdf_acceptance" / "manifest.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
DOC_PATH = ROOT / "docs" / "testing" / "pdf-remediation-acceptance-corpus.md"
RUN_COMMAND = (
    "python scripts/pdf_acceptance_corpus.py "
    "--manifest tests/fixtures/pdf_acceptance/manifest.json"
)


def _manifest_dict() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_has_exact_required_inventory_and_safe_provenance():
    manifest = load_manifest(MANIFEST_PATH)

    assert manifest.schema_version == "pdf-remediation-acceptance-corpus-v1"
    assert manifest.corpus_version == "synthetic-pdf-v1"
    assert manifest.generated_by == "scripts/pdf_acceptance_corpus.py"
    assert manifest.origin == "repository-authored synthetic content"
    assert manifest.redistribution == "CC0-1.0"
    assert tuple(case.id for case in manifest.required_cases) == REQUIRED_CASE_IDS
    assert all(case.machine_assertions for case in manifest.required_cases)
    assert all(not Path(case.fixture).is_absolute() for case in manifest.required_cases)
    assert all(".." not in Path(case.fixture).parts for case in manifest.required_cases)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["required_cases"].pop(),
        lambda payload: payload["required_cases"][0].update({"machine_assertions": []}),
        lambda payload: payload["required_cases"][0].update(
            {"fixture": "../outside.pdf"}
        ),
    ],
)
def test_manifest_fails_closed_on_erosion_or_unsafe_paths(tmp_path, mutate):
    payload = _manifest_dict()
    mutate(payload)

    with pytest.raises(CorpusContractError):
        load_manifest(_write_manifest(tmp_path, payload))


def test_generator_is_byte_reproducible_and_sources_are_valid_pdfs(tmp_path):
    manifest = load_manifest(MANIFEST_PATH)
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_hashes = generate_corpus(manifest, first)
    second_hashes = generate_corpus(manifest, second)

    assert first_hashes == second_hashes
    assert set(first_hashes) == set(REQUIRED_CASE_IDS)
    for case in manifest.required_cases:
        with pikepdf.open(first / case.fixture) as pdf:
            assert len(pdf.pages) >= 1


def test_runner_fails_closed_instead_of_cleaning_existing_directories(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sentinel = source / "keep.txt"
    sentinel.write_text("operator-owned", encoding="utf-8")

    with pytest.raises(CorpusContractError):
        run_required_corpus(MANIFEST_PATH, tmp_path)

    assert sentinel.read_text(encoding="utf-8") == "operator-owned"


def test_required_corpus_runs_complete_honest_journeys(tmp_path):
    report = run_required_corpus(MANIFEST_PATH, tmp_path)

    assert report["schema_version"] == "pdf-remediation-acceptance-report-v1"
    assert report["status"] == "passed"
    assert report["passed"] == len(REQUIRED_CASE_IDS)
    assert report["failed"] == 0
    assert report["skipped"] == 0
    assert report["quarantined"] == 1
    assert report["duration_seconds"] <= report["budget_seconds"]
    assert tuple(case["id"] for case in report["cases"]) == REQUIRED_CASE_IDS
    assert all(case["status"] == "passed" for case in report["cases"])
    assert all(case["stages"]["scan"] == "passed" for case in report["cases"])
    assert all(not Path(case["source_path"]).is_absolute() for case in report["cases"])
    assert all(
        case["output_path"] is None or not Path(case["output_path"]).is_absolute()
        for case in report["cases"]
    )


def test_remediation_preserves_source_and_publishes_distinct_integral_output(tmp_path):
    report = run_required_corpus(MANIFEST_PATH, tmp_path)
    remediated = next(case for case in report["cases"] if case["id"] == "metadata")

    assert remediated["stages"] == {
        "scan": "passed",
        "remediation": "passed",
        "publication": "passed",
        "validation": "passed",
        "rescan": "passed",
    }
    assert remediated["source_sha256_before"] == remediated["source_sha256_after"]
    assert remediated["source_path"] != remediated["output_path"]
    assert remediated["source_sha256_after"] != remediated["output_sha256"]
    source = tmp_path / remediated["source_path"]
    output = tmp_path / remediated["output_path"]
    assert stat.S_IMODE(source.stat().st_mode) == 0o444
    assert output.is_file()
    assert output.is_relative_to(tmp_path / "output")
    assert sha256_file(output) == remediated["output_sha256"]
    with pikepdf.open(output) as pdf:
        assert len(pdf.pages) == 1


def test_rescan_has_no_new_governed_findings_and_review_stays_human(tmp_path):
    report = run_required_corpus(MANIFEST_PATH, tmp_path)

    assert all(case["new_governed_findings"] == [] for case in report["cases"])
    review_cases = {
        case["id"]: case["review_outcome"]
        for case in report["cases"]
        if case["review_outcome"] is not None
    }
    assert review_cases == {
        "headings-reading-order": "human_review_required",
        "images-charts": "human_review_required",
        "math-stem": "human_review_required",
    }
    assert report["claim_boundary"] == (
        "Machine observations only; not proof of WCAG, PDF/UA, or legal conformance."
    )


def test_core_has_no_provider_calls_and_extended_case_is_quarantined(tmp_path):
    report = run_required_corpus(MANIFEST_PATH, tmp_path)

    assert report["provider_calls"] == 0
    assert report["extended_cases"] == [
        {
            "id": "visual-description-quality",
            "status": "quarantined",
            "reason": "live_provider_required",
        }
    ]
    assert report["passed"] == len(REQUIRED_CASE_IDS)
    assert report["quarantined"] == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["required_cases"].pop(),
        lambda payload: payload["required_cases"][0].update({"machine_assertions": []}),
    ],
)
def test_cli_fails_if_required_case_or_assertion_disappears(tmp_path, mutate):
    payload = _manifest_dict()
    mutate(payload)
    broken = _write_manifest(tmp_path, payload)

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "pdf_acceptance_corpus.py"),
            "--manifest",
            str(broken),
            "--work-dir",
            str(tmp_path / "work"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "corpus_contract_invalid" in completed.stderr


def test_release_bound_ci_runs_exact_required_corpus_command():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "name: Required PDF remediation acceptance corpus" in workflow
    assert f"run: {RUN_COMMAND}" in workflow
    assert "needs: ci-gate" in (
        ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")


def test_public_documentation_preserves_scope_and_reproduction_contract():
    documentation = DOC_PATH.read_text(encoding="utf-8")

    assert RUN_COMMAND in documentation
    assert "eight required" in documentation.lower()
    assert "human review" in documentation.lower()
    assert "not proof of wcag, pdf/ua, or legal conformance" in documentation.lower()
    public_corpus = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in [
            MANIFEST_PATH,
            DOC_PATH,
            ROOT / "scripts" / "pdf_acceptance_corpus.py",
        ]
    ).lower()
    for forbidden in ("customer", "/users/", "reginald", "api_key", "password"):
        assert forbidden not in public_corpus
