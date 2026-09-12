"""The real worker acceptance gate must block the same CI used by publication."""

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_document_gate_uses_real_services_and_cannot_be_skipped():
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    job = jobs["document-stack"]
    assert "if" not in job
    assert not job.get("continue-on-error")
    assert set(job["services"]) == {"postgres", "redis", "mailpit"}
    assert job["env"]["ALLOW_MOCK_AUTH"] == "false"
    run = "\n".join(step.get("run", "") for step in job["steps"])
    assert "alembic upgrade head" in run
    assert "uvicorn src.api.main:app" in run
    assert "python -m src.jobs.worker" in run
    assert "scripts/verify_document_stack.ts" in run
    assert "canvas-testbed" not in run
    assert all(not step.get("continue-on-error") for step in job["steps"])


def test_document_gate_checks_success_refusal_and_real_download_rescan():
    source = (ROOT / "scripts/verify_document_stack.ts").read_text()
    for contract in (
        "respond-async",
        "score_measurement.output_sha256",
        "score_measurement.source_sha256",
        "job.remaining_count, total",
        "job.outcome_unreported_count",
        "scanBytes(saved",
        "downloaded.status, 404",
        "failures.length, 0",
    ):
        assert contract in source
    for fixture in ("course.docx", "course.pptx", "course.xlsx", "metadata.pdf"):
        assert (ROOT / "tests/fixtures/document_stack" / fixture).is_file()
