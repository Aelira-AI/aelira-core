"""Explicit reviewed PDF recovery across the real private subprocess boundary."""

from dataclasses import replace
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pikepdf
import pytest

from src.education.remediation.pdf_recovery_plan import (
    ReviewedPDFRecovery,
    reviewed_pdf_recovery_receipt,
    serialize_reviewed_pdf_recovery,
)
from src.education.remediation.pdf_reviewed_semantics import (
    ReviewedOccurrence,
    ReviewedSemanticManifest,
    ReviewedSemanticNode,
    apply_reviewed_semantics,
    inspect_reviewed_semantic_source,
)
from src.education.remediation.pdf_verified_font_recovery import (
    recover_verified_font_maps,
)
from src.jobs import remediation_subprocess as module
from src.jobs.remediation_subprocess import RemediationSubprocessError

from test_pdf_verified_font_recovery import _pixels, _source

pytestmark = pytest.mark.unit


@pytest.fixture
def recovery():
    source, fonts, _ = _source(
        (
            "Study invitation",
            "Eligible students can participate in the planned research study.",
            "Contact the study team for information about participation.",
        )
    )
    restored = recover_verified_font_maps(source, fonts)
    inventory = inspect_reviewed_semantic_source(restored.pdf_bytes)
    nodes = tuple(
        ReviewedSemanticNode(
            f"text-{index}",
            "H1" if index == 0 else "P",
            occurrences=(
                ReviewedOccurrence(item.page_index, item.operator_index, item.text),
            ),
            outline_title=item.text if index == 0 else None,
        )
        for index, item in enumerate(inventory.occurrences)
    )
    semantics = ReviewedSemanticManifest(
        inventory.source_sha256,
        "synthetic fixture author",
        "fixture source strings and semantic roles",
        "Study invitation",
        "en",
        nodes,
        tuple(node.node_id for node in nodes),
    )
    return source, ReviewedPDFRecovery(fonts, semantics), restored


def _request(tmp_path, recovery):
    source, plan, _ = recovery
    path = tmp_path / "source.pdf"
    path.write_bytes(source)
    return {
        "source_path": str(path),
        "work_dir": str(tmp_path),
        "scan_type": "PDF",
        "issues": [],
        "options": {},
        "reviewed_pdf_recovery": serialize_reviewed_pdf_recovery(plan),
    }


def test_factory_passes_typed_manifests_without_acquiring_providers(
    tmp_path, monkeypatch, recovery
):
    request = _request(tmp_path, recovery)
    request["options"] = {"use_ai": True, "generate_alt_text": True}
    request["lms_binding"] = {"remediation": True, "alt_text": True}
    clients = Mock(side_effect=AssertionError("reviewed route must not bind providers"))
    monkeypatch.setattr(module, "_purpose_clients", clients)
    from src.education.remediation import pdf_remediator

    constructor = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(pdf_remediator, "PdfRemediator", constructor)
    from pathlib import Path

    remediator = module._build_remediator(
        request, Path(request["source_path"]), tmp_path
    )
    clients.assert_not_called()
    assert (
        constructor.call_args.kwargs["font_recovery_manifest"]
        == recovery[1].font_manifest
    )
    assert (
        constructor.call_args.kwargs["semantic_recovery_manifest"]
        == recovery[1].semantic_manifest
    )
    assert constructor.call_args.kwargs["config"].use_ai is False
    assert constructor.call_args.kwargs["config"].fix_alt_text is False
    assert remediator._reviewed_pdf_recovery_plan == recovery[1]


@pytest.mark.parametrize("name", sorted(module._PRIVATE_RECOVERY_OPTIONS))
@pytest.mark.asyncio
async def test_ordinary_options_cannot_enable_recovery(tmp_path, monkeypatch, name):
    spawn = Mock(
        side_effect=AssertionError("must reject before subprocess or filesystem")
    )
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RemediationSubprocessError, match="invalid_job_payload"):
        await module.run_remediation_subprocess(
            source_path="missing.pdf",
            scan_type="PDF",
            issues=[],
            options={name: {}},
            work_root=tmp_path / "work",
            timeout_seconds=30,
            termination_grace_seconds=0.1,
        )
    assert not (tmp_path / "work").exists()
    spawn.assert_not_called()
    from pathlib import Path

    with pytest.raises(RemediationSubprocessError, match="invalid_job_payload"):
        module._build_remediator(
            {"scan_type": "PDF", "options": {name: {}}}, Path("missing.pdf"), tmp_path
        )


@pytest.mark.parametrize(
    "scan_type,operation",
    [("DOCX", "remediation"), ("LATEX", "remediation"), ("PDF", "demo_document")],
)
@pytest.mark.asyncio
async def test_recovery_only_accepts_explicit_pdf_remediation(
    tmp_path, recovery, scan_type, operation
):
    if scan_type != "PDF":
        with pytest.raises(RemediationSubprocessError, match="invalid_job_payload"):
            await module.run_remediation_subprocess(
                source_path="missing.pdf",
                scan_type=scan_type,
                issues=[],
                options={},
                work_root=tmp_path / "work",
                reviewed_pdf_recovery=recovery[1],
                timeout_seconds=30,
                termination_grace_seconds=0.1,
            )
    request = _request(tmp_path, recovery)
    request.update(scan_type=scan_type, operation=operation)
    with pytest.raises(RemediationSubprocessError, match="invalid_job_payload"):
        module._run_child(request)


@pytest.mark.asyncio
async def test_runner_requires_typed_bundle_and_child_requires_valid_private_json(
    tmp_path, recovery
):
    with pytest.raises(RemediationSubprocessError, match="invalid_job_payload"):
        await module.run_remediation_subprocess(
            source_path="missing.pdf",
            scan_type="PDF",
            issues=[],
            options={},
            work_root=tmp_path / "work",
            reviewed_pdf_recovery={},
            timeout_seconds=30,
            termination_grace_seconds=0.1,
        )
    request = _request(tmp_path, recovery)
    for invalid in (None, {}, "{}", '{"version":1,"version":1}'):
        request["reviewed_pdf_recovery"] = invalid
        with pytest.raises(RemediationSubprocessError, match="invalid_job_payload"):
            module._run_child(request)


@pytest.mark.asyncio
async def test_real_subprocess_recovery_preserves_pixels_text_and_bound_receipt(
    tmp_path, recovery
):
    source, plan, restored = recovery
    path = tmp_path / "source.pdf"
    path.write_bytes(source)
    expected = apply_reviewed_semantics(restored.pdf_bytes, plan.semantic_manifest)
    from src.education.pdf_processor import PDFProcessor

    scan = PDFProcessor(
        generate_alt_text=False, validate_alt_text=False, require_complete_scan=True
    ).process_pdf(str(path))
    execution = await module.run_remediation_subprocess(
        source_path=str(path),
        scan_type="PDF",
        issues=[module._json_record(issue) for issue in scan.issues],
        options={},
        work_root=tmp_path / "work",
        reviewed_pdf_recovery=plan,
        timeout_seconds=30,
        termination_grace_seconds=0.1,
    )
    try:
        assert execution.output_claim is not None
        with execution.output_claim.open_stream() as stream:
            output = stream.read()
        assert output == expected.pdf_bytes
        assert path.read_bytes() == source
        assert _pixels(output) == _pixels(source)
        assert execution.verification_passed is True
        assert execution.human_review_required is True
        from src.db.models import ScanFix

        assert execution.fixed_issues
        assert all(
            fix.fix_method == "reviewed_recovery"
            and len(fix.fix_method) <= ScanFix.__table__.c.fix_method.type.length
            and fix.needs_review is True
            for fix in execution.fixed_issues
        )
        receipt = execution.reviewed_pdf_recovery
        assert all(
            receipt[key] == value
            for key, value in reviewed_pdf_recovery_receipt(plan).items()
        )
        assert receipt["applied"] is True
        assert receipt["output_sha256"] == hashlib.sha256(output).hexdigest()
        assert receipt["font_output_sha256"] == plan.semantic_manifest.source_sha256
        assert receipt["font_compiler_review_sha256"] == restored.review_sha256
        with pikepdf.open(io.BytesIO(output)) as pdf:
            assert pdf.Root.StructTreeRoot.K[0].S == pikepdf.Name.Document
            assert str(pdf.Root.Lang) == "en"
        assert list((tmp_path / "work").iterdir()) == []
    finally:
        execution.close_output_claim()


@pytest.mark.parametrize("phase", ["source", "intermediate"])
def test_child_refuses_stale_source_and_broken_compiler_chain(
    tmp_path, recovery, phase
):
    source, plan, restored = recovery
    if phase == "source":
        plan = replace(
            plan, font_manifest=replace(plan.font_manifest, source_sha256="0" * 64)
        )
    else:
        plan = replace(
            plan,
            semantic_manifest=replace(plan.semantic_manifest, source_sha256="0" * 64),
        )
    request = _request(tmp_path, (source, plan, restored))
    result = module._run_child(request)
    assert result["success"] is False
    assert result["output_file"] is None
    assert result["reviewed_pdf_recovery"]["applied"] is False
    assert "output_sha256" not in result["reviewed_pdf_recovery"]
    assert (tmp_path / "source.pdf").read_bytes() == source


def test_child_does_not_claim_success_when_manifests_were_not_applied(
    tmp_path, monkeypatch, recovery
):
    request = _request(tmp_path, recovery)
    from src.education.remediation.base import RemediationResult

    result = RemediationResult(
        original_file=request["source_path"], document_type="pdf", success=True
    )
    remediator = SimpleNamespace(
        _reviewed_pdf_recovery_plan=recovery[1], remediate=lambda: result
    )
    monkeypatch.setattr(module, "_build_remediator", lambda *args, **kwargs: remediator)
    with pytest.raises(RemediationSubprocessError, match="remediation_failed"):
        module._run_child(request)


@pytest.mark.parametrize(
    "field",
    [
        "bundle_sha256",
        "output_sha256",
        "applied",
        "independent_review_pending",
        "verification_passed",
        "human_review_required",
    ],
)
@pytest.mark.asyncio
async def test_parent_refuses_changed_receipt_or_output_binding(
    tmp_path, monkeypatch, recovery, field
):
    source, plan, _ = recovery
    path = tmp_path / "source.pdf"
    path.write_bytes(source)
    from src.education.pdf_processor import PDFProcessor

    scan = PDFProcessor(
        generate_alt_text=False, validate_alt_text=False, require_complete_scan=True
    ).process_pdf(str(path))
    original_read = module._read_bound_file
    original_claim = module._claim_output
    claims = []

    def changed_response(descriptor, name, **kwargs):
        raw = original_read(descriptor, name, **kwargs)
        if name == "response.json":
            response = json.loads(raw)
            assert response["success"] is True
            if field in {"human_review_required", "verification_passed"}:
                response[field] = False
            elif field == "independent_review_pending":
                response["reviewed_pdf_recovery"][field] = 1
            else:
                response["reviewed_pdf_recovery"][field] = (
                    False if field == "applied" else "0" * 64
                )
            return json.dumps(response).encode()
        return raw

    def capture_claim(*args, **kwargs):
        claim = original_claim(*args, **kwargs)
        claims.append(claim)
        return claim

    monkeypatch.setattr(module, "_read_bound_file", changed_response)
    monkeypatch.setattr(module, "_claim_output", capture_claim)
    with pytest.raises(RemediationSubprocessError, match="remediation_failed"):
        await module.run_remediation_subprocess(
            source_path=str(path),
            scan_type="PDF",
            issues=[module._json_record(issue) for issue in scan.issues],
            options={},
            work_root=tmp_path / "work",
            reviewed_pdf_recovery=plan,
            timeout_seconds=30,
            termination_grace_seconds=0.1,
        )
    assert claims and all(claim.closed for claim in claims)
    assert list((tmp_path / "work").iterdir()) == []
    assert path.read_bytes() == source


def test_child_propagates_review_requirements_even_after_passing_verification(
    tmp_path, monkeypatch
):
    from src.education.remediation.base import RemediationResult, VerificationResult

    source = tmp_path / "source.pdf"
    source.write_bytes(b"synthetic mocked source")
    result = RemediationResult(
        original_file=str(source),
        document_type="pdf",
        success=True,
        verification_passed=True,
        verification_result=VerificationResult(
            passed=True,
            review_requirements=["Review figure placement in reading order"],
        ),
    )
    monkeypatch.setattr(
        module,
        "_build_remediator",
        lambda *args, **kwargs: SimpleNamespace(remediate=lambda: result),
    )
    response = module._run_child(
        {"source_path": str(source), "work_dir": str(tmp_path), "scan_type": "PDF"}
    )
    assert response["verification_passed"] is True
    assert response["human_review_required"] is True
    assert (
        response["review_requirements"]
        == result.verification_result.review_requirements
    )
