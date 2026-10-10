"""Synthetic LaTeX worker boundary checks for review and bounded receipts."""

import json
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.education.latex_evidence import conversion_evidence
from src.education.remediation.latex_pdf_validation import LatexPDFValidation
from src.jobs import remediation_job, remediation_subprocess


def _receipts():
    return {
        "latex_evidence": {
            "pdf": conversion_evidence(b"source", None, "pdf").model_dump(mode="json")
        },
        "latex_pdf_validation": LatexPDFValidation(
            status="unavailable", reason="no_converter"
        ).model_dump(mode="json"),
    }


@pytest.mark.parametrize("success", [True, False])
def test_child_serializes_latex_receipts_and_requires_review(
    tmp_path, monkeypatch, success
):
    source = tmp_path / "source.tex"
    source.write_text("source")
    result = SimpleNamespace(
        success=success,
        output_file=None,
        total_issues=1,
        fixed_count=0,
        manual_count=1,
        failed_count=0,
        skipped_count=0,
        original_compliance_score=None,
        remediated_compliance_score=None,
        improvement=None,
        duration_seconds=0,
        verification_passed=success,
        fixed_issues=[],
        manual_issues=[],
        failed_issues=[],
        **_receipts(),
    )
    monkeypatch.setattr(
        remediation_subprocess,
        "_build_remediator",
        lambda *_args, **_kwargs: SimpleNamespace(remediate=lambda: result),
    )
    response = remediation_subprocess._run_child(
        {
            "source_path": str(source),
            "work_dir": str(tmp_path),
            "scan_type": "LATEX",
        }
    )
    assert response["success"] is success
    assert response["human_review_required"] is True
    for field, receipt in _receipts().items():
        assert response[field] == receipt


def test_child_exception_preserves_bounded_receipts(tmp_path, monkeypatch):
    request, response = tmp_path / "request.json", tmp_path / "response.json"
    request.write_text("{}")
    error = remediation_subprocess.RemediationSubprocessError(
        "remediation_failed", **_receipts()
    )
    monkeypatch.setattr(
        remediation_subprocess, "_run_child", MagicMock(side_effect=error)
    )
    assert remediation_subprocess.child_main(request, response) == 1
    body = json.loads(response.read_text())
    assert body["error_code"] == "remediation_failed"
    for field, receipt in _receipts().items():
        assert body[field] == receipt


@pytest.mark.asyncio
async def test_worker_subprocess_failure_keeps_latex_receipts(tmp_path, monkeypatch):
    from src.db.models import Scan, ScanResult, ScanType

    source = tmp_path / "source.tex"
    source.write_text("source")
    scan = SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.LATEX,
        storage_path=str(source),
        file_name=source.name,
        file_hash=None,
    )
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = (
            scan
            if model is Scan
            else (
                SimpleNamespace(
                    issues=[{"id": "title", "type": "missing_title"}],
                    compliance_score=70.0,
                )
                if model is ScanResult
                else None
            )
        )
        return chain

    db.query.side_effect = query
    monkeypatch.setattr(
        remediation_job.RemediationArtifactService,
        "from_settings",
        lambda: SimpleNamespace(root=tmp_path / "managed"),
    )
    runner = AsyncMock(
        side_effect=remediation_subprocess.RemediationSubprocessError(
            "remediation_failed", **_receipts()
        )
    )
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", runner)

    async def owned():
        pass

    result = await remediation_job.process_remediation_job(
        {"scan_id": scan.id, "department_id": scan.department_id, "job_id": "job-1"},
        db,
        assert_owned=owned,
    )
    runner.assert_awaited_once()
    assert result["success"] is False
    safe = remediation_job._safe_failure_result(result["error"], result, scan)
    for field, receipt in _receipts().items():
        assert result[field] == safe[field] == receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining_kind", ["manual", "failed"])
@pytest.mark.parametrize("latex_format", ["tex", "pdf"])
async def test_worker_withholds_mixed_latex_candidate_before_publication(
    tmp_path, monkeypatch, remaining_kind, latex_format
):
    from src.db.models import Scan, ScanResult, ScanType
    from src.education.latex_evidence import conversion_evidence, source_evidence

    source = tmp_path / "source.tex"
    source.write_text("\\documentclass{article}\\begin{document}Source\\end{document}")
    issues = [
        {"id": "title", "type": "missing_title"},
        {"id": "unresolved", "type": "missing_alt_text"},
    ]
    scan = SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.LATEX,
        storage_path=str(source),
        file_hash=None,
    )
    scan_result = SimpleNamespace(issues=issues, compliance_score=50.0)
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = (
            scan if model is Scan else scan_result if model is ScanResult else None
        )
        chain.one_or_none.return_value = scan
        return chain

    db.query.side_effect = query
    source_bytes = source.read_bytes()
    candidate = (
        b"\\documentclass{article}\\begin{document}Candidate\\end{document}"
        if latex_format == "tex"
        else b"%PDF-1.7\n% bounded synthetic candidate\n"
    )
    receipt = (
        source_evidence(source_bytes, candidate, 1)
        if latex_format == "tex"
        else conversion_evidence(source_bytes, candidate, "pdf")
    ).model_dump(mode="json")
    assert receipt["source_sha256"] == sha256(source_bytes).hexdigest()
    assert receipt["candidate_sha256"] == sha256(candidate).hexdigest()
    if latex_format == "pdf":
        assert receipt["conversion"]["status"] == "completed"
    child_result = SimpleNamespace(
        success=True,
        total_issues=2,
        fixed_count=1,
        manual_count=int(remaining_kind == "manual"),
        failed_count=int(remaining_kind == "failed"),
        skipped_count=0,
        fixed_issues=[SimpleNamespace(issue_id="title")],
        manual_issues=(
            [SimpleNamespace(issue_id="unresolved")]
            if remaining_kind == "manual"
            else []
        ),
        failed_issues=(
            [SimpleNamespace(issue_id="unresolved")]
            if remaining_kind == "failed"
            else []
        ),
        verification_passed=True,
        has_output_claim=lambda: True,
        close_output_claim=MagicMock(),
        latex_evidence={latex_format: receipt},
    )
    runner = AsyncMock(return_value=child_result)
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", runner)
    service = SimpleNamespace(
        root=tmp_path / "managed",
        claim_and_publish_stream=MagicMock(),
        claim_and_publish=MagicMock(),
    )
    monkeypatch.setattr(
        remediation_job.RemediationArtifactService,
        "from_settings",
        classmethod(lambda cls: service),
    )

    result = await remediation_job.process_remediation_job(
        {
            "scan_id": scan.id,
            "department_id": scan.department_id,
            "job_id": "job-1",
            "file_path": str(source),
            "options": {"use_ai": False, "latex_formats": [latex_format]},
        },
        db,
        assert_owned=AsyncMock(),
        defer_final_commit=True,
    )

    runner.assert_awaited_once()
    assert result["success"] is False
    assert result["error"] == "manual_required"
    assert result["fixed_count"] == 0
    assert result["withheld_count"] == 1
    assert result["remaining_count"] == result["total_issues"] == 2
    assert [row["status"] for row in result["issue_outcomes"]] == [
        "withheld",
        remaining_kind,
    ]
    assert result["latex_evidence"] == {latex_format: receipt}
    safe = remediation_job._safe_failure_result(result["error"], result, scan)
    assert safe["artifact_id"] is None
    assert safe["fixed_count"] == 0
    assert safe["latex_evidence"] == result["latex_evidence"]
    service.claim_and_publish_stream.assert_not_called()
    service.claim_and_publish.assert_not_called()
    child_result.close_output_claim.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize("project_binding", ["zip", "structure"])
async def test_worker_refuses_project_before_child_execution(
    tmp_path, monkeypatch, project_binding
):
    from src.db.models import Scan, ScanResult, ScanType

    source = tmp_path / ("project.zip" if project_binding == "zip" else "source.tex")
    source.write_bytes(b"project source")
    result_row = SimpleNamespace(
        issues=[{"id": "title", "type": "missing_title"}],
        structure={"latex_project": {}} if project_binding == "structure" else {},
    )
    scan = SimpleNamespace(
        id="scan-1",
        department_id="dept-1",
        scan_type=ScanType.LATEX,
        storage_path=str(source),
        result=result_row,
    )
    db = MagicMock()

    def query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = (
            scan if model is Scan else result_row if model is ScanResult else None
        )
        return chain

    db.query.side_effect = query
    runner = AsyncMock()
    monkeypatch.setattr(remediation_job, "run_remediation_subprocess", runner)

    async def owned():
        pass

    result = await remediation_job.process_remediation_job(
        {"scan_id": scan.id, "department_id": scan.department_id, "job_id": "job-1"},
        db,
        assert_owned=owned,
    )
    assert result["error"] == "project_source_review_required"
    runner.assert_not_awaited()
    assert source.read_bytes() == b"project source"


def test_child_refuses_zip_before_provider_binding(tmp_path, monkeypatch):
    binding = MagicMock()
    monkeypatch.setattr(remediation_subprocess, "_purpose_clients", binding)
    with pytest.raises(
        remediation_subprocess.RemediationSubprocessError,
        match="project_source_review_required",
    ):
        remediation_subprocess._build_remediator(
            {"scan_type": "LATEX"}, tmp_path / "project.zip", tmp_path
        )
    binding.assert_not_called()
