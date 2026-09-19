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


@pytest.mark.parametrize("formats", [["pdf"], ["tex", "pdf"], ["tex"]])
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
            self,
            path,
            formats,
            output_dir,
            *,
            validation_receipts,
            conversion_receipts=None,
        ):
            from src.education.latex_diagnostics import (
                ConversionDiagnostics,
                ConversionStage,
                diagnostic,
                sha,
            )

            conversion_receipts["pdf"] = ConversionDiagnostics(
                source_sha256=sha(Path(path).read_bytes()),
                status="refused",
                stages=[
                    ConversionStage(
                        tool="latexmlpost",
                        phase="postprocess",
                        input_sha256=sha(b"synthetic semantic XML"),
                        diagnostics=[diagnostic("missing_asset")],
                    )
                ],
            )
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
    if "pdf" in formats:
        assert body["latex_pdf_validation"] == receipt.model_dump(mode="json")
        diagnostic = body["latex_evidence"]["pdf"]["conversion_diagnostics"]
        assert diagnostic["stages"][0]["diagnostics"][0]["code"] == "missing_asset"
        assert body["latex_evidence"]["pdf"]["conversion"]["status"] == "failed"
        assert body["latex_evidence"]["pdf"]["source_check"]["status"] == "not_assessed"
    assert body["latex_evidence"]["tex"]["accessibility_status"] == "not_verified"
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
        tex = body["latex_evidence"]["tex"]
        assert tex["candidate_sha256"] == hashlib.sha256(download.content).hexdigest()
        assert tex["source_check"]["status"] == "completed"
        assert tex["human_review_required"] is True
    else:
        assert body["success"] is False
        assert body["artifact_id"] is None
        assert body["score_verified"] is False
        assert body["fixed_count"] == 0


@pytest.mark.parametrize("formats", [["pdf"], ["tex", "pdf"], ["tex"]])
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
    if "pdf" in formats:
        assert result["latex_pdf_validation"]["status"] == "unavailable", result
        assert result["latex_pdf_validation"]["reason"] == "no_converter", result
        assert (
            result["latex_evidence"]["pdf"]["conversion_diagnostics"]["status"]
            == "refused"
        )
        assert result["latex_evidence"]["pdf"]["conversion"]["status"] == "unavailable"
    assert result["latex_evidence"]["tex"]["accessibility_status"] == "not_verified"
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
    if "pdf" in formats:
        assert body["latex_pdf_validation"] == result["latex_pdf_validation"]
    assert body["latex_evidence"] == result["latex_evidence"]
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
        tex = body["latex_evidence"]["tex"]
        assert tex["candidate_sha256"] == artifact.sha256
        assert tex["source_check"]["status"] == "completed"
        assert tex["human_review_required"] is True
        # Legacy source-verified jobs also require reader review, even without receipts.
        job.result_data = {
            k: v
            for k, v in result.items()
            if k not in {"latex_evidence", "latex_pdf_validation"}
        }
        job.result_data = {**job.result_data, "human_review_required": False}
        case.db.commit()
        legacy = case.client.get(response.json()["status_url"])
        assert legacy.status_code == 200
        assert legacy.json()["human_review_required"] is True
    else:
        assert result["success"] is False
        assert body["download_available"] is False
        assert formats_response["available_formats"] == []
        assert download.status_code == 404
        assert body["score_verified"] is False


def test_historical_latex_claim_is_downgraded_on_authorized_scan_reload(latex_http):
    from src.api.education import scan_history_routes

    case = latex_http
    case.app.include_router(scan_history_routes.router, prefix="/education")
    case.scan.result.structure = {
        "equations": [
            {
                "equation_id": 1,
                "wcag_compliant": True,
                "conversion_success": True,
                "aria_label": "An unverified historical interpretation",
            }
        ]
    }
    case.db.commit()
    response = case.client.get(f"/education/scans/{case.scan.id}")
    assert response.status_code == 200, response.text
    structure = response.json()["scan"]["result"]["structure"]
    assert structure["equations"][0]["wcag_compliant"] is False
    assert structure["equations"][0]["aria_label"] is None
    assert structure["equations"][0]["description_review_required"] is True
    assert structure["accessibility_status"] == "not_verified"
    assert structure["human_review_required"] is True
    case.db.refresh(case.scan.result)
    assert case.scan.result.structure["equations"][0]["wcag_compliant"] is True


def test_description_receipt_survives_persisted_authorized_reload(latex_http):
    from src.api.education import scan_history_routes
    from src.education.latex_evidence import scan_structure

    case = latex_http
    case.app.include_router(scan_history_routes.router, prefix="/education")
    text = r"$\frac{a_{i}^{2}}{1+\frac{b}{c}}$"
    case.source.write_text(text)
    result = LaTeXProcessor(use_ai=False).process_document(str(case.source))
    case.scan.result.structure = scan_structure(result)
    case.db.commit()
    case.db.expire_all()
    response = case.client.get(f"/education/scans/{case.scan.id}")
    assert response.status_code == 200, response.text
    equation = response.json()["scan"]["result"]["structure"]["equations"][0]
    receipt = equation["latex_evidence"]["mathml"]["description"]
    assert receipt["reason"] == "not_requested"
    assert receipt["semantic_equivalence"] == "not_assessed"
    assert receipt["human_review_required"] is True
    assert (
        receipt["source_sha256"]
        == hashlib.sha256(result.equations[0].latex_source.encode()).hexdigest()
    )
    assert equation["aria_label"] is None
    assert case.source.read_text() == text
