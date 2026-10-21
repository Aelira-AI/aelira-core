"""Jobs that are not LMS jobs describe images through a purpose-bound view.

Equation recognition and image descriptions accept only a client bound to the
``alt_text`` purpose. These tests drive the real workspace provider runtime,
with a stub snapshot loader and provider factory, through the production client
selection and the PDF remediator.
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw

from src.ai.providers.base import LLMResponse
from src.ai.workspace_provider_runtime import (
    WorkspaceProviderRow,
    WorkspaceProviderRuntime,
    WorkspaceProviderSnapshot,
)
from src.db.models import Scan

pytestmark = pytest.mark.unit

WORKSPACE = "workspace-a"
VISION_MODEL = "stub-vision"
DESCRIPTION = "Bar chart of course enrolment by year."
PRINTED_PROMPT_START = "Classify this single bounded image."
HANDWRITTEN_PROMPT_START = "Independently transcribe this single bounded handwritten"
SECOND_READING_PROMPT_START = "Independently transcribe the printed mathematics"


class _StubVisionProvider:
    """One disposable provider instance, as the runtime constructs per call."""

    def __init__(self, config, ledger):
        self.config = config
        self.ledger = ledger

    async def initialize(self):
        return True

    async def close(self):
        self.ledger["closed"] += 1

    async def analyze_image(self, *, image_data, prompt, max_tokens=500):
        assert image_data
        self.ledger["prompts"].append(prompt)
        if prompt.startswith(PRINTED_PROMPT_START):
            content = self.ledger["printed"]
        elif prompt.startswith(SECOND_READING_PROMPT_START):
            content = self.ledger["second"]
        elif prompt.startswith(HANDWRITTEN_PROMPT_START):
            content = self.ledger["handwritten"].pop(0)
        else:
            content = DESCRIPTION
        return LLMResponse.success_response(
            content=content,
            provider="ollama",
            model=self.ledger["model"] or self.config.vision_model,
            inference_time=0.01,
        )

    async def generate_text(self, *, prompt, **_kwargs):
        self.ledger["prompts"].append(prompt)
        return LLMResponse.success_response(
            content=DESCRIPTION,
            provider="ollama",
            model=self.config.text_model or VISION_MODEL,
            inference_time=0.01,
        )

    async def generate_code(self, **_kwargs):
        raise AssertionError("image descriptions never generate code")


def _runtime(
    *,
    printed='{"classification":"not_equation","latex":null}',
    second=None,
    handwritten=(),
    model=None,
):
    ledger = {
        "prompts": [],
        "closed": 0,
        "printed": printed,
        "second": printed if second is None else second,
        "handwritten": list(handwritten),
        "model": model,
    }
    snapshot = WorkspaceProviderSnapshot(
        workspace_id=WORKSPACE,
        primary="ollama",
        fallback=None,
        rows={
            "ollama": WorkspaceProviderRow(
                provider="ollama",
                api_key_encrypted=None,
                text_model=None,
                code_model=None,
                vision_model=VISION_MODEL,
            )
        },
    )
    runtime = WorkspaceProviderRuntime(
        WORKSPACE,
        snapshot_loader=lambda workspace_id: (
            snapshot if workspace_id == WORKSPACE else None
        ),
        decryptor=lambda _ciphertext: pytest.fail("ollama has no credential"),
        provider_factory=lambda _provider_type, config: _StubVisionProvider(
            config, ledger
        ),
    )
    return runtime, ledger


# ---------------------------------------------------------------------------
# The view itself
# ---------------------------------------------------------------------------


def test_alt_text_view_binds_purpose_and_delegates_without_widening():
    from src.ai.workspace_provider_runtime import WorkspaceAltTextClient

    runtime, ledger = _runtime()
    view = WorkspaceAltTextClient(runtime)

    assert view.purpose == "alt_text"
    assert getattr(runtime, "purpose", None) is None
    assert view.workspace_id == WORKSPACE
    response = view.analyze_image_sync(
        image_data=b"\xff\xd8pixels", prompt="Describe", max_tokens=50
    )
    assert response["success"] is True
    assert response["content"] == DESCRIPTION
    # The identity of the provider that answered passes through unchanged.
    assert (response["provider"], response["model"]) == ("ollama", VISION_MODEL)
    assert view.generate_text_sync(prompt="Describe the chart")["success"] is True
    async_response = asyncio.run(view.analyze_image(b"\xff\xd8pixels", "Describe"))
    assert async_response.success is True

    # Usage is counted on the wrapped runtime and read back through the view.
    assert runtime.ollama_usage.successful_calls == 3
    assert view.ollama_usage == runtime.ollama_usage
    assert ledger["prompts"] == ["Describe", "Describe the chart", "Describe"]
    assert ledger["closed"] == 3

    # Nothing the runtime cannot do, and nothing description does not need.
    for name in ("generate_code_sync", "generate_code", "generate_embedding"):
        assert not hasattr(view, name)
    assert getattr(view, "provider", None) is None
    assert getattr(view, "model", None) is None
    with pytest.raises(AttributeError):
        view.purpose = "remediation"
    with pytest.raises(AttributeError):
        view._runtime = object()


@pytest.mark.parametrize(
    "client",
    [
        SimpleNamespace(purpose="remediation", provider="openai"),
        MagicMock(),
        object(),
        None,
    ],
)
def test_alt_text_view_refuses_to_relabel_any_other_client(client):
    from src.ai.workspace_provider_runtime import WorkspaceAltTextClient

    with pytest.raises(TypeError):
        WorkspaceAltTextClient(client)


# ---------------------------------------------------------------------------
# End to end: queued child selection, real runtime, real PDF remediator
# ---------------------------------------------------------------------------


def _png(draw) -> bytes:
    image = Image.new("RGB", (180, 60), "white")
    draw(ImageDraw.Draw(image))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


EQUATION_RECT = fitz.Rect(72, 120, 252, 180)
CHART_RECT = fitz.Rect(320, 120, 500, 180)


def _equation_and_chart_pdf(path) -> None:
    equation = _png(lambda draw: draw.text((20, 20), "x + 1 = 2", fill="black"))
    chart = _png(
        lambda draw: [
            draw.rectangle((20 + 40 * index, 50 - 10 * index, 45 + 40 * index, 55))
            for index in range(4)
        ]
    )
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 100), "Solve the equation below.", fontsize=11)
        page.insert_image(EQUATION_RECT, stream=equation)
        page.insert_image(CHART_RECT, stream=chart)
        page.insert_textbox(
            fitz.Rect(72, 300, 540, 760),
            "Readable course material for accessibility review. " * 30,
            fontsize=11,
        )
        pdf.save(path)


def test_local_pdf_reaches_equation_recognition_and_describes_its_other_image(
    tmp_path, monkeypatch
):
    from src.education.math_contracts import IMAGE_EQUATION_ISSUE_TYPE
    from src.education.pdf_processor import PDFProcessor
    from src.education.remediation import equation_verifier
    from src.education.remediation.base import IssueCategory
    from src.education.remediation.outcome_explanations import manual_explanation
    from src.jobs import remediation_subprocess

    source = tmp_path / "lecture.pdf"
    _equation_and_chart_pdf(source)
    issues = (
        PDFProcessor(generate_alt_text=False, validate_alt_text=False)
        .process_pdf(str(source))
        .issues
    )
    assert [
        issue
        for issue in issues
        if issue.get("issue_type") == IMAGE_EQUATION_ISSUE_TYPE
    ], "fixture must hold one equation candidate"

    runtime, ledger = _runtime(
        printed='{"classification":"printed_equation","latex":"x+1=2"}'
    )
    factory = MagicMock(return_value=runtime)
    monkeypatch.setattr(
        "src.ai.workspace_provider_runtime.workspace_provider_runtime", factory
    )
    verified = []

    class RejectingVerifier:
        canonicalize_mathml = staticmethod(equation_verifier.canonicalize_mathml)

        def verify(self, _image, latex):
            verified.append(latex)
            raise equation_verifier.EquationVerificationRejected("visual_mismatch")

    # The real verifier renders in Chromium; the recognition boundary is the
    # subject here, so the check is replaced by one that always refuses.
    monkeypatch.setattr(equation_verifier, "EquationVerifier", RejectingVerifier)

    remediator = remediation_subprocess._build_remediator(
        {
            "scan_type": "PDF",
            "issues": issues,
            "options": {"use_ai": True},
            "workspace_id": WORKSPACE,
            "lms_binding": None,
        },
        source,
        tmp_path,
    )
    result = remediator.remediate()

    factory.assert_called_once_with(WORKSPACE)
    assert result.success is True, result.error_message
    # Equation recognition was reached through the workspace's own provider.
    assert any(prompt.startswith(PRINTED_PROMPT_START) for prompt in ledger["prompts"])
    assert verified == ["x+1=2"]

    by_id = {issue.id: issue for issue in remediator.issues}

    def _bbox(issue_id):
        return tuple(round(value) for value in by_id[issue_id].metadata["bbox"])

    chart_fixes = [
        fix
        for fix in result.fixed_issues
        if fix.category is IssueCategory.ALT_TEXT
        and _bbox(fix.issue_id) == tuple(round(value) for value in CHART_RECT)
    ]
    assert [fix.fixed_content for fix in chart_fixes] == [DESCRIPTION]
    assert chart_fixes[0].needs_review is True
    assert not [
        manual
        for manual in result.manual_issues
        if manual.reason == "Alt-text AI provider unavailable"
    ]

    [equation] = [
        manual
        for manual in result.manual_issues
        if by_id[manual.issue_id].metadata.get("issue_type")
        == IMAGE_EQUATION_ISSUE_TYPE
    ]
    assert equation.reason == "equation_verification_failed"
    assert manual_explanation(equation)["reason_code"] == "equation_verification_failed"


# ---------------------------------------------------------------------------
# Recognition through the view: printed identity and the handwritten pair
# ---------------------------------------------------------------------------


def test_printed_equation_records_the_answering_provider_and_model():
    from src.ai.workspace_provider_runtime import WorkspaceAltTextClient
    from src.education.remediation.math_fixer import MathFixer
    from tests.test_equation_alt_text_coordination import _Verifier
    from tests.test_handwritten_math_fixer import METADATA, Source

    runtime, ledger = _runtime(
        printed='{"classification":"printed_equation","latex":"x^2 + 1 = 0"}'
    )
    fixer = MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=SimpleNamespace(),
        alt_text_client=WorkspaceAltTextClient(runtime),
        image_source=Source("legible-linear"),
        # Stands in for the browser round trip only.
        equation_verifier=_Verifier(True),
    )
    result = fixer._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.error == "image_equation_association_pending"
    assert result.needs_review is True
    assert (result.provider_used, result.model_used) == ("ollama", VISION_MODEL)
    assert result.pending_association.provider_used == "ollama"
    assert result.pending_association.model_used == VISION_MODEL
    # The first reading and the blind second reading both went through the view.
    assert len(ledger["prompts"]) == 2
    assert ledger["prompts"][0].startswith(PRINTED_PROMPT_START)
    assert ledger["prompts"][1].startswith(SECOND_READING_PROMPT_START)


def test_handwritten_equation_takes_both_readings_through_the_view():
    from src.ai.workspace_provider_runtime import WorkspaceAltTextClient
    from src.education.remediation.equation_verifier import canonicalize_mathml
    from src.education.remediation.math_fixer import MathFixer
    from tests.test_handwritten_math_fixer import METADATA, Source

    reading = '{"classification":"handwritten_equation","latex":"%s"}'
    runtime, ledger = _runtime(
        handwritten=[reading % "x^2+y=10", reading % "x^2 + y = 10"]
    )
    fixer = MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=SimpleNamespace(),
        alt_text_client=WorkspaceAltTextClient(runtime),
        image_source=Source("legible-linear"),
        equation_verifier=SimpleNamespace(canonicalize_mathml=canonicalize_mathml),
    )

    result = fixer._fix_math_issue(SimpleNamespace(metadata=METADATA))

    assert result.error == "image_equation_association_pending"
    assert result.needs_review is True
    evidence = result.verification_evidence
    assert evidence.agreement_count == 2
    assert (evidence.primary_provider, evidence.primary_model) == (
        "ollama",
        VISION_MODEL,
    )
    assert (evidence.verifier_provider, evidence.verifier_model) == (
        "ollama",
        VISION_MODEL,
    )
    # The printed reading declines, then the primary and the second reading.
    assert len(ledger["prompts"]) == 3
    assert ledger["prompts"][0].startswith(PRINTED_PROMPT_START)
    assert all(
        prompt.startswith(HANDWRITTEN_PROMPT_START) for prompt in ledger["prompts"][1:]
    )
    assert ledger["handwritten"] == []


def test_runtime_without_the_purpose_never_reaches_recognition():
    from src.education.remediation.math_fixer import MathFixer
    from tests.test_handwritten_math_fixer import METADATA, Source

    runtime, ledger = _runtime()
    fixer = MathFixer(
        SimpleNamespace(pages=[object()]),
        SimpleNamespace(),
        struct_tree=SimpleNamespace(),
        alt_text_client=runtime,
        image_source=Source("legible-linear"),
    )

    assert fixer.alt_text_client is None
    assert fixer.equation_recognizer is None
    assert fixer.handwritten_recognizer is None
    assert fixer.handwritten_verifier is None
    result = fixer._fix_math_issue(SimpleNamespace(metadata=METADATA))
    assert result.error == "alt_text_client_unavailable"
    assert ledger["prompts"] == []


# ---------------------------------------------------------------------------
# Job-level selection
# ---------------------------------------------------------------------------


def _local_job(options, execution_context=None):
    scan = SimpleNamespace(
        id="scan-1",
        department_id="department-1",
        scan_type="PDF",
        storage_path="/uploads/source.pdf",
        status="processing",
        remediation_outcome=None,
        completed_at=None,
    )
    job = SimpleNamespace(
        id="job-1",
        department_id="department-1",
        provider="local",
        cloud_file_id=None,
        credential_id=None,
        payload={
            "scan_id": "scan-1",
            "options": options,
            "requested_by_id": "user-1",
        },
        execution_context=execution_context or {},
        claim_token=None,
        worker_id=None,
    )
    db = MagicMock()
    db.get.side_effect = lambda model, identity, **_kwargs: (
        scan if model is Scan and identity == "scan-1" else None
    )
    return job, db


def _process():
    return AsyncMock(
        return_value={
            "success": True,
            "scan_id": "scan-1",
            "fixed_count": 0,
            "manual_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"use_ai": True, "generate_alt_text": True},
        # Requests that never mention alt text keep the historical default.
        {"use_ai": True},
        None,
    ],
)
async def test_local_job_gets_a_distinct_alt_text_view(monkeypatch, options):
    from src.ai.workspace_provider_runtime import WorkspaceAltTextClient
    from src.jobs import remediation_job

    runtime, _ = _runtime()
    factory = MagicMock(return_value=runtime)
    process = _process()
    bind = MagicMock()
    monkeypatch.setattr(remediation_job, "workspace_provider_runtime", factory)
    monkeypatch.setattr(remediation_job, "process_remediation_job", process)
    monkeypatch.setattr(remediation_job.LMSRemediationClient, "bind_if_allowed", bind)
    job, db = _local_job(options)

    await remediation_job.handle_remediation_job(job, db, object())

    factory.assert_called_once_with("department-1")
    bind.assert_not_called()
    kwargs = process.await_args.kwargs
    assert kwargs["lms_policy_authoritative"] is False
    assert kwargs["ai_client"] is runtime
    view = kwargs["alt_text_client"]
    assert isinstance(view, WorkspaceAltTextClient)
    assert view is not kwargs["ai_client"]
    assert view.purpose == "alt_text"
    assert view._runtime is runtime


@pytest.mark.asyncio
async def test_local_job_that_declines_alt_text_gets_no_alt_text_client(monkeypatch):
    from src.jobs import remediation_job

    runtime, _ = _runtime()
    process = _process()
    monkeypatch.setattr(
        remediation_job, "workspace_provider_runtime", MagicMock(return_value=runtime)
    )
    monkeypatch.setattr(remediation_job, "process_remediation_job", process)
    job, db = _local_job(
        {"use_ai": True, "generate_alt_text": False},
        {"ai_requested": True, "requested_purposes": ["remediation"]},
    )

    await remediation_job.handle_remediation_job(job, db, object())

    kwargs = process.await_args.kwargs
    assert kwargs["ai_client"] is runtime
    assert kwargs["alt_text_client"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google", "microsoft"])
async def test_cloud_storage_job_without_options_gets_the_view(monkeypatch, provider):
    from src.ai.workspace_provider_runtime import WorkspaceAltTextClient
    from src.jobs import remediation_job
    from tests.test_lms_remediation_execution_policy import _job_graph

    runtime, _ = _runtime()
    process = _process()
    monkeypatch.setattr(
        remediation_job, "workspace_provider_runtime", MagicMock(return_value=runtime)
    )
    monkeypatch.setattr(remediation_job, "process_remediation_job", process)
    # The Google and Microsoft routes enqueue without options or a context.
    job, db = _job_graph(provider=provider, context={})

    await remediation_job.handle_remediation_job(job, db, MagicMock())

    kwargs = process.await_args.kwargs
    assert kwargs["ai_client"] is runtime
    assert isinstance(kwargs["alt_text_client"], WorkspaceAltTextClient)
    assert kwargs["alt_text_client"]._runtime is runtime


@pytest.mark.asyncio
async def test_lms_job_still_uses_only_its_policy_bound_client(monkeypatch):
    from src.jobs import remediation_job
    from tests.test_lms_remediation_execution_policy import _job_graph

    policy_client = SimpleNamespace(purpose="alt_text", provider="ollama")
    bind = MagicMock(return_value=policy_client)
    process = _process()
    monkeypatch.setattr(remediation_job.LMSRemediationClient, "bind_if_allowed", bind)
    monkeypatch.setattr(
        remediation_job,
        "workspace_provider_runtime",
        lambda _workspace: pytest.fail("LMS jobs must stay policy-bound"),
    )
    monkeypatch.setattr(remediation_job, "process_remediation_job", process)
    job, db = _job_graph(
        context={"alt_text_requested": True, "requested_purposes": ["alt_text"]}
    )

    await remediation_job.handle_remediation_job(job, db, MagicMock())

    assert bind.call_args.kwargs["purpose"] == "alt_text"
    assert process.await_args.kwargs["alt_text_client"] is policy_client
    assert process.await_args.kwargs["ai_client"] is None
    assert process.await_args.kwargs["lms_policy_authoritative"] is True


# ---------------------------------------------------------------------------
# The subprocess child re-derives the view from its own request
# ---------------------------------------------------------------------------


def _child(monkeypatch, tmp_path, request):
    from src.jobs import remediation_subprocess

    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    runtime, _ = _runtime()
    factory = MagicMock(return_value=runtime)
    constructor = MagicMock(return_value=object())
    monkeypatch.setattr(
        "src.ai.workspace_provider_runtime.workspace_provider_runtime", factory
    )
    monkeypatch.setattr(
        "src.education.remediation.pdf_remediator.PdfRemediator", constructor
    )
    remediation_subprocess._build_remediator(
        {"scan_type": "PDF", "issues": [], "lms_binding": None, **request},
        source,
        tmp_path,
    )
    return runtime, factory, constructor.call_args.kwargs


def test_child_rebuilds_a_distinct_alt_text_view_from_the_request(
    monkeypatch, tmp_path
):
    from src.ai.workspace_provider_runtime import WorkspaceAltTextClient

    runtime, factory, kwargs = _child(
        monkeypatch,
        tmp_path,
        {"options": {"use_ai": True}, "workspace_id": WORKSPACE},
    )

    factory.assert_called_once_with(WORKSPACE)
    assert kwargs["ai_client"] is runtime
    assert isinstance(kwargs["alt_text_client"], WorkspaceAltTextClient)
    assert kwargs["alt_text_client"]._runtime is runtime
    assert kwargs["alt_text_client"].purpose == "alt_text"
    assert kwargs["config"].fix_alt_text is True
    assert kwargs["config"].allow_legacy_nested_ai is False


@pytest.mark.parametrize(
    "request_fields",
    [
        # A parent-supplied flag is not the request's own intent.
        {
            "options": {"use_ai": True, "generate_alt_text": False},
            "alt_text": True,
            "alt_text_requested": True,
        },
        {"options": {"use_ai": True, "generate_alt_text": False}},
    ],
)
def test_child_honours_a_declined_alt_text_request(
    monkeypatch, tmp_path, request_fields
):
    runtime, _, kwargs = _child(
        monkeypatch, tmp_path, {"workspace_id": WORKSPACE, **request_fields}
    )

    assert kwargs["ai_client"] is runtime
    assert kwargs["alt_text_client"] is None
    # Declined image descriptions are a disabled category, not a missing
    # provider, so the public reason does not blame the configuration.
    assert kwargs["config"].fix_alt_text is False


def test_child_without_ai_has_no_alt_text_client(monkeypatch, tmp_path):
    _, factory, kwargs = _child(
        monkeypatch, tmp_path, {"options": {"use_ai": False}, "workspace_id": WORKSPACE}
    )

    factory.assert_not_called()
    assert kwargs["ai_client"] is None
    assert kwargs["alt_text_client"] is None


# ---------------------------------------------------------------------------
# The in-process path applies the same rule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("declined", [False, True])
async def test_in_process_job_builds_the_view_from_its_options(
    tmp_path, monkeypatch, declined
):
    from src.ai.workspace_provider_runtime import WorkspaceAltTextClient
    from src.db.models import ScanStatus, ScanType
    from src.jobs import remediation_job
    from tests.test_lms_remediation_execution_policy import (
        _ProcessDB,
        _worker_remediation_result,
    )

    path = tmp_path / "file.docx"
    path.write_bytes(b"document")
    scan = Scan(
        id="scan-inprocess",
        department_id="dept-1",
        scan_type=ScanType.WORD,
        storage_path=str(path),
        metadata={},
        status=ScanStatus.PROCESSING,
        file_name="file.docx",
    )
    db = _ProcessDB(scan, SimpleNamespace(issues=[{"category": "alt_text"}]))
    runtime, _ = _runtime()
    remediator = MagicMock()
    remediator.remediate.return_value = _worker_remediation_result(path, success=False)
    get_remediator = MagicMock(return_value=remediator)
    monkeypatch.setattr(
        remediation_job, "workspace_provider_runtime", MagicMock(return_value=runtime)
    )
    monkeypatch.setattr(
        remediation_job, "_get_remediator_for_scan_type", get_remediator
    )
    monkeypatch.setattr(remediation_job, "_send_remediation_notification", AsyncMock())
    options = {"use_ai": True}
    if declined:
        options["generate_alt_text"] = False

    await remediation_job.process_remediation_job(
        {
            "scan_id": scan.id,
            "department_id": "dept-1",
            "file_path": str(path),
            "options": options,
        },
        db,
    )

    kwargs = get_remediator.call_args.kwargs
    assert kwargs["ai_client"] is runtime
    if declined:
        assert kwargs["alt_text_client"] is None
        assert kwargs["allow_embedded_alt"] is False
    else:
        assert isinstance(kwargs["alt_text_client"], WorkspaceAltTextClient)
        assert kwargs["alt_text_client"]._runtime is runtime
        assert kwargs["allow_embedded_alt"] is True
