"""PostgreSQL and mounted API proof of refusal and authorized TEX delivery.

Converter outcomes are controlled here; compiler behavior is replayed separately.
No PDF/UA or browser/assistive-technology conformance is claimed.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from test_managed_artifact_routes import artifact_http  # noqa: F401
from src.db.models import RemediationArtifact, ScanResult, ScanType
from src.education.latex_processor import LaTeXProcessor
from src.education.remediation.latex_pdf_validation import LatexPDFValidation

pytestmark = pytest.mark.integration


@pytest.fixture
def latex_http(artifact_http, tmp_path):  # noqa: F811
    case = artifact_http
    source = tmp_path / "source.tex"
    text = r"\documentclass{article}\begin{document}Text\end{document}"
    source.write_text(text)
    issue = next(
        row
        for row in LaTeXProcessor(use_ai=False).scan_source(text)["issues"]
        if row["issue_type"] == "missing_title"
    )
    issue.update(id="title", type="missing_title")
    case.artifact.lifecycle_status = "superseded"
    case.scan.current_remediation_artifact_id = None
    case.scan.scan_type = ScanType.LATEX
    case.scan.storage_path = str(source)
    case.scan.file_name = source.name
    case.scan.file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    case.scan.result = ScanResult(compliance_score=70.0, issues=[issue])
    case.db.commit()
    case.source = source
    return case


@pytest.mark.parametrize("formats", [["pdf"], ["tex", "pdf"]])
async def test_direct_helper_refusal_and_supported_tex_delivery(
    latex_http, monkeypatch, formats
):
    from src.education.remediation import latex_remediator

    case = latex_http
    data = case.source.read_bytes()
    receipt = LatexPDFValidation(
        status="unavailable", reason="independent_validator_disabled"
    )

    class Converter:
        def convert_all_formats(
            self, path, formats, output_dir, *, validation_receipts
        ):
            validation_receipts["pdf"] = receipt
            # A leftover PDF diagnostic must not become a download.
            Path(path).with_suffix(".pdf").write_bytes(b"%PDF-1.7 diagnostic only")
            return {"pdf": None}

    monkeypatch.setattr(latex_remediator, "get_latex_converter", Converter)
    from starlette.requests import Request
    from src.api.education import remediation_routes as routes
    from src.auth.dependencies import get_authenticated_principal

    body = await routes.remediate_scan(
        str(case.scan.id),
        Request({"type": "http", "headers": []}),
        options=routes.RemediationOptions(
            use_ai=False, generate_alt_text=False, latex_formats=formats
        ),
        use_ai=False,
        db=case.db,
        principal=case.app.dependency_overrides[get_authenticated_principal](),
    )
    assert body["latex_pdf_validation"] == receipt.model_dump(mode="json")
    assert body["human_review_required"] is True
    assert case.source.read_bytes() == data
    assert not case.client.get(
        f"/education/scans/{case.scan.id}/remediated/formats"
    ).json()[
        "available_formats"
    ]  # compatibility formats are queued-job-owned
    if "tex" in formats:
        assert body["success"] is True, body
        assert body["artifact_id"]
        download = case.client.get(
            f"/education/scans/{case.scan.id}/artifacts/{body['artifact_id']}/download"
        )
        assert download.status_code == 200, download.text
        assert download.headers["content-type"].startswith("text/plain")
        assert b"\\title{" in download.content
        assert not download.content.startswith(b"%PDF")
    else:
        assert body["success"] is False
        assert body["artifact_id"] is None
        assert body["score_verified"] is False
        assert body["fixed_count"] == 0


@pytest.mark.parametrize("formats", [["pdf"], ["tex", "pdf"]])
async def test_queued_process_persists_receipt_and_only_delivers_supported_tex(
    latex_http, monkeypatch, formats
):
    from src.db.models import CloudJobQueue
    from src.jobs.remediation_job import process_remediation_job, _safe_failure_result
    from src.services.remediation_artifact_service import RemediationArtifactService

    case = latex_http
    monkeypatch.setenv(
        "PATH", "/usr/bin:/bin"
    )  # real child with no optional TeX binaries
    monkeypatch.setenv(
        "AELIRA_REMEDIATION_DIR", str(RemediationArtifactService.from_settings().root)
    )
    response = case.client.post(
        f"/education/remediate/{case.scan.id}",
        headers={"Prefer": "respond-async"},
        json={"use_ai": False, "generate_alt_text": False, "latex_formats": formats},
    )
    assert response.status_code == 202
    job = case.db.get(CloudJobQueue, response.json()["job_id"])

    async def owned():
        pass  # ownership fencing is covered by the separate queue race suite

    result = await process_remediation_job(
        {
            "scan_id": case.scan.id,
            "department_id": case.scan.department_id,
            "actor_id": case.user.id,
            "file_path": str(case.source),
            "job_id": job.id,
            "options": job.payload["options"],
        },
        case.db,
        assert_owned=owned,
    )
    assert result["latex_pdf_validation"]["status"] == "unavailable", result
    assert result["latex_pdf_validation"]["reason"] == "no_converter", result
    # Commit the worker outcome, then reload it through the mounted job API.
    # Dispatcher/lease completion itself is exercised by the existing worker suite.
    job.status = "completed" if result["success"] else "failed"
    job.completed_at = datetime.now(timezone.utc)
    job.result_data = (
        result
        if result["success"]
        else _safe_failure_result(result["error"], result, case.scan)
    )
    case.db.commit()
    status = case.client.get(response.json()["status_url"])
    assert status.status_code == 200
    body = status.json()
    assert body["latex_pdf_validation"] == result["latex_pdf_validation"]
    assert body["human_review_required"] is True
    formats_response = case.client.get(
        f"/education/scans/{case.scan.id}/remediated/formats"
    ).json()
    download = case.client.get(f"{response.json()['status_url']}/download")
    if "tex" in formats:
        assert result["success"] is True, result
        assert body["download_available"] is True
        assert [row["format"] for row in formats_response["available_formats"]] == [
            "tex"
        ]
        assert download.status_code == 200
        assert b"\\title{" in download.content
        artifact = case.db.get(
            RemediationArtifact,
            body["artifact_id"],
        )
        assert hashlib.sha256(download.content).hexdigest() == artifact.sha256
    else:
        assert result["success"] is False
        assert body["download_available"] is False
        assert formats_response["available_formats"] == []
        assert download.status_code == 404
        assert body["score_verified"] is False
