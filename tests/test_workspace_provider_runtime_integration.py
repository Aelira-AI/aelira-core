from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.jobs import local_scan_job, remediation_subprocess
from src.jobs import remediation_job
from src.db.models import Scan


@pytest.mark.asyncio
async def test_authenticated_ai_route_binds_department_runtime(monkeypatch):
    from src.api import main

    runtime = object()
    bound = SimpleNamespace(
        classify_severity=AsyncMock(
            return_value={
                "severity": "High",
                "provider": "anthropic",
                "model": "workspace-model",
            }
        )
    )
    runtime_factory = MagicMock(return_value=runtime)
    binder = MagicMock(return_value=bound)
    monkeypatch.setattr(main, "workspace_provider_runtime", runtime_factory)
    monkeypatch.setattr(main.accessibility_ai_client, "bind_provider_manager", binder)

    response = await main.test_ai(api_key_info=(None, "user-a", "workspace-a"))

    runtime_factory.assert_called_once_with("workspace-a")
    binder.assert_called_once_with(runtime)
    bound.classify_severity.assert_awaited_once()
    assert response["provider"] == "anthropic"


@pytest.mark.parametrize("scan_kind", ["local_pdf", "local_web", "local_code"])
def test_durable_local_job_propagates_scan_workspace(monkeypatch, scan_kind):
    calls = []
    scan = SimpleNamespace(
        id="scan-a",
        department_id="workspace-a",
        user_id="user-a",
        file_name="input.pdf",
        storage_path="/bounded/input.pdf",
    )
    if scan_kind == "local_pdf":
        from src.api.education import scan_routes

        monkeypatch.setattr(
            scan_routes,
            "process_pdf_background",
            lambda *_args, **kwargs: calls.append(kwargs),
        )
        options = {"generate_alt_text": True, "enhance_descriptions": True}
    elif scan_kind == "local_code":
        from src.api.education import web_scan_routes

        monkeypatch.setattr(
            web_scan_routes,
            "process_code_background",
            lambda *_args, **kwargs: calls.append(kwargs),
        )
        options = {
            "scan_images": True,
            "generate_fixes": True,
            "validate_alt_text": True,
        }
    else:
        from src.api.education import web_scan_routes

        monkeypatch.setattr(
            web_scan_routes,
            "process_web_scan_background",
            lambda *_args, **kwargs: calls.append(kwargs),
        )
        options = {
            "url": "https://example.edu",
            "mode": "quick",
            "scan_images": False,
            "scan_multimedia": False,
            "scan_math": False,
            "validate_alt_text": False,
            "max_depth": 1,
            "max_pages": 1,
            "generate_code_fixes": True,
            "capture_screenshots": False,
        }

    local_scan_job._run_local_processor(
        scan_kind, scan, options, "/tmp/input", b"content"
    )

    assert calls == [{"workspace_id": "workspace-a"}]


def test_non_lms_subprocess_binds_workspace_runtime_without_global_manager(
    monkeypatch, tmp_path
):
    source = tmp_path / "source.html"
    source.write_text("<html lang='en'></html>")
    runtime = object()
    runtime_factory = MagicMock(return_value=runtime)
    constructor = MagicMock(return_value=object())
    monkeypatch.setattr(
        "src.ai.workspace_provider_runtime.workspace_provider_runtime",
        runtime_factory,
    )
    monkeypatch.setattr(
        "src.education.remediation.html_remediator.HtmlRemediator", constructor
    )
    monkeypatch.setattr(
        "src.ai.providers.get_provider_manager",
        lambda: pytest.fail("tenant remediation must not use global provider state"),
    )

    remediation_subprocess._build_remediator(
        {
            "scan_type": "CODE",
            "issues": [],
            "options": {"use_ai": True},
            "workspace_id": "workspace-a",
            "lms_binding": None,
        },
        source,
        tmp_path,
    )

    runtime_factory.assert_called_once_with("workspace-a")
    assert constructor.call_args.kwargs["ai_client"] is runtime
    assert constructor.call_args.kwargs["config"].allow_legacy_nested_ai is False


def test_non_lms_subprocess_refuses_ai_without_workspace_identity(tmp_path):
    source = tmp_path / "source.html"
    source.write_text("<html lang='en'></html>")

    with pytest.raises(
        remediation_subprocess.RemediationSubprocessError,
        match="workspace_identity_required",
    ):
        remediation_subprocess._build_remediator(
            {
                "scan_type": "CODE",
                "issues": [],
                "options": {"use_ai": True},
                "lms_binding": None,
            },
            source,
            tmp_path,
        )


@pytest.mark.parametrize(
    ("scan_type", "suffix", "expected_class"),
    [
        ("PDF", ".pdf", "PdfRemediator"),
        ("LATEX", ".pdf", "PdfRemediator"),
        ("LATEX", ".tex", "LatexRemediator"),
        ("WORD", ".docx", "DocxRemediator"),
        ("DOCX", ".docx", "DocxRemediator"),
        ("POWERPOINT", ".pptx", "PptxRemediator"),
        ("PPTX", ".pptx", "PptxRemediator"),
        ("EXCEL", ".xlsx", "XlsxRemediator"),
        ("XLSX", ".xlsx", "XlsxRemediator"),
        ("MULTIMEDIA", ".mp4", "MultimediaRemediator"),
        ("VIDEO", ".mp4", "MultimediaRemediator"),
        ("CODE", ".html", "HtmlRemediator"),
        ("CANVAS_CONTENT", ".html", "HtmlRemediator"),
        ("WEBSITE", ".html", "HtmlRemediator"),
        ("HTML", ".html", "HtmlRemediator"),
    ],
)
def test_subprocess_constructs_every_supported_remediator_with_workspace_clients(
    monkeypatch, tmp_path, scan_type, suffix, expected_class
):
    source = tmp_path / f"source{suffix}"
    source.write_bytes(b"bounded fixture")
    runtime = SimpleNamespace(purpose="workspace")
    runtime_factory = MagicMock(return_value=runtime)
    monkeypatch.setattr(
        "src.ai.workspace_provider_runtime.workspace_provider_runtime",
        runtime_factory,
    )
    monkeypatch.setattr(
        "src.ai.providers.get_provider_manager",
        lambda: pytest.fail("tenant remediation must not use global provider state"),
    )

    remediator = remediation_subprocess._build_remediator(
        {
            "scan_type": scan_type,
            "issues": [],
            "options": {"use_ai": True},
            "workspace_id": "workspace-a",
            "lms_binding": None,
        },
        source,
        tmp_path,
    )

    assert type(remediator).__name__ == expected_class
    assert remediator.ai_client is runtime
    assert remediator.alt_text_client is runtime
    runtime_factory.assert_called_once_with("workspace-a")


@pytest.mark.parametrize(
    ("processor_type", "kwargs"),
    [
        (
            "pdf",
            {"generate_alt_text": True, "enhance_descriptions": True},
        ),
        (
            "powerpoint",
            {
                "generate_alt_text": True,
                "simulate_color_blindness": False,
            },
        ),
        ("word", {"generate_alt_text": True}),
        ("excel", {"generate_alt_text": True}),
    ],
)
def test_document_nested_vision_uses_injected_workspace_runtime(processor_type, kwargs):
    from src.education.docx_processor import DocxProcessor
    from src.education.pdf_processor import PDFProcessor
    from src.education.pptx_processor import PowerPointProcessor
    from src.education.xlsx_processor import XlsxProcessor

    runtime = object()
    classes = {
        "pdf": PDFProcessor,
        "powerpoint": PowerPointProcessor,
        "word": DocxProcessor,
        "excel": XlsxProcessor,
    }

    processor = classes[processor_type](llm_client=runtime, **kwargs)

    assert processor.image_generator.lms_client is runtime
    assert processor.image_generator.allow_legacy_transport is False


def test_multimedia_nested_ai_uses_injected_workspace_runtime():
    from src.education.multimedia_processor import MultimediaProcessor

    runtime = object()
    processor = MultimediaProcessor(llm_client=runtime)

    assert processor._get_llm_client() is runtime
    assert processor._get_image_generator().lms_client is runtime


def test_multimedia_remediator_forwards_purpose_clients_without_global_fallback(
    monkeypatch, tmp_path
):
    from src.education.remediation.base import RemediationConfig
    from src.education.remediation.multimedia_remediator import MultimediaRemediator

    media = tmp_path / "lecture.mp4"
    media.write_bytes(b"bounded fixture")
    remediation_client = SimpleNamespace(purpose="remediation")
    alt_text_client = SimpleNamespace(purpose="alt_text")
    monkeypatch.setattr(
        "src.ai.providers.get_provider_manager",
        lambda: pytest.fail("tenant remediation must not use global provider state"),
    )

    remediator = MultimediaRemediator(
        str(media),
        [],
        RemediationConfig(use_ai=True, allow_legacy_nested_ai=False),
        remediation_client,
        alt_text_client=alt_text_client,
    )
    processor = remediator._get_processor()

    assert processor._get_llm_client() is remediation_client
    image_generator = processor._get_image_generator()
    assert image_generator.lms_client is alt_text_client
    assert image_generator.allow_legacy_transport is False


def test_lms_multimedia_subprocess_preserves_distinct_purpose_clients(
    monkeypatch, tmp_path
):
    from src.ai.lms_remediation_client import LMSRemediationClient

    media = tmp_path / "lecture.mp4"
    media.write_bytes(b"bounded fixture")
    remediation_client = SimpleNamespace(purpose="remediation")
    alt_text_client = SimpleNamespace(purpose="alt_text")

    def bind_if_allowed(*, purpose, **_kwargs):
        return remediation_client if purpose == "remediation" else alt_text_client

    monkeypatch.setattr(
        LMSRemediationClient,
        "bind_if_allowed",
        MagicMock(side_effect=bind_if_allowed),
    )
    monkeypatch.setattr(
        "src.ai.workspace_provider_runtime.workspace_provider_runtime",
        lambda _workspace_id: pytest.fail("LMS execution must remain policy-bound"),
    )
    monkeypatch.setattr(
        "src.ai.providers.get_provider_manager",
        lambda: pytest.fail("LMS remediation must not use global provider state"),
    )

    remediator = remediation_subprocess._build_remediator(
        {
            "scan_type": "MULTIMEDIA",
            "issues": [],
            "options": {"use_ai": True},
            "lms_binding": {
                "department_id": "workspace-a",
                "remediation": True,
                "alt_text": True,
            },
        },
        media,
        tmp_path,
    )
    processor = remediator._get_processor()

    assert remediator.ai_client is remediation_client
    assert remediator.alt_text_client is alt_text_client
    assert processor._get_llm_client() is remediation_client
    assert processor._get_image_generator().lms_client is alt_text_client


def test_authenticated_image_route_helper_binds_workspace_runtime(monkeypatch):
    from src.api.education import image_routes

    runtime = object()
    factory = MagicMock(return_value=runtime)
    monkeypatch.setattr(image_routes, "workspace_provider_runtime", factory)

    generator = image_routes._workspace_image_generator("workspace-a")

    factory.assert_called_once_with("workspace-a")
    assert generator.lms_client is runtime
    assert generator.allow_legacy_transport is False


@pytest.mark.asyncio
async def test_remediation_job_refuses_mismatched_scan_workspace_before_ai():
    scan = SimpleNamespace(id="scan-a", department_id="workspace-a")
    db = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.first.return_value = scan
    db.query.return_value = query

    result = await remediation_job.process_remediation_job(
        {
            "scan_id": "scan-a",
            "department_id": "workspace-b",
            "options": {"use_ai": True},
        },
        db,
    )

    assert result == {
        "success": False,
        "error": "invalid_job_payload",
        "scan_id": "scan-a",
    }
    db.query.assert_called_once_with(Scan)
