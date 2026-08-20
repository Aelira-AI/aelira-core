"""Task 14 slice 2: LMS scan execution is deterministic-only."""

import ast
import inspect
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


DETERMINISTIC_METADATA = {
    "operation_kind": "deterministic_scan",
    "external_ai_used": False,
    "ai_used": False,
}


def test_canvas_content_scan_request_defaults_and_legacy_true_flags_are_disabled():
    from src.api.canvas_content_routes import CanvasContentScanRequest

    default_request = CanvasContentScanRequest(course_id="course-1")
    legacy_true_request = CanvasContentScanRequest(
        course_id="course-1",
        generate_alt_text=True,
        auto_remediate=True,
        detect_decorative=True,
    )

    assert default_request.generate_alt_text is False
    assert default_request.auto_remediate is False
    assert default_request.to_scan_options() == {
        "generate_alt_text": False,
        "auto_remediate": False,
        "detect_decorative": False,
    }
    assert legacy_true_request.to_scan_options() == default_request.to_scan_options()


def test_canvas_scan_response_identifies_deterministic_non_ai_operation():
    from src.api.canvas_content_routes import CanvasContentScanResponse
    from src.api.canvas_scan_routes import CanvasScanResponse

    content = CanvasContentScanResponse(
        total_items=1, jobs_queued=1, skipped=0, by_type={"page": 1}
    ).model_dump()
    file_scan = CanvasScanResponse(job_id="job-1", cloud_file_id="file-1").model_dump()

    for response in (content, file_scan):
        assert DETERMINISTIC_METADATA.items() <= response.items()
        assert "remediation" not in response


@pytest.mark.parametrize(
    ("route_name", "request_name", "expected_code", "expected_message"),
    [
        (
            "scan_canvas_file",
            "CanvasScanRequest",
            "CANVAS_SCAN_QUEUE_FAILED",
            "Failed to queue Canvas scan",
        ),
        (
            "scan_canvas_course_files",
            "CanvasBulkScanRequest",
            "CANVAS_BULK_SCAN_QUEUE_FAILED",
            "Failed to queue Canvas bulk scan",
        ),
    ],
)
@pytest.mark.asyncio
async def test_canvas_queue_failures_do_not_expose_or_log_raw_exception(
    route_name, request_name, expected_code, expected_message, caplog
):
    from fastapi import HTTPException
    from src.api import canvas_scan_routes

    sensitive_marker = "private-detail:/customer/path"
    route = getattr(canvas_scan_routes, route_name)
    request_class = getattr(canvas_scan_routes, request_name)
    request_kwargs = {"course_id": "course-1"}
    if request_name == "CanvasScanRequest":
        request_kwargs["file_id"] = "file-1"

    with (
        patch.object(canvas_scan_routes, "require_lti_course_access"),
        patch.object(canvas_scan_routes, "verify_department_access"),
        patch.object(
            canvas_scan_routes, "require_feature", new=AsyncMock(return_value=None)
        ),
        patch.object(
            canvas_scan_routes,
            "_get_canvas_client",
            new=AsyncMock(side_effect=RuntimeError(sensitive_marker)),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await route(
            request_class(**request_kwargs),
            MagicMock(),
            MagicMock(),
            SimpleNamespace(department_id="department-1"),
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == {
        "code": expected_code,
        "message": expected_message,
    }
    assert sensitive_marker not in repr(exc.value.detail)
    assert sensitive_marker not in caplog.text


@pytest.mark.parametrize(
    ("route_name", "content_type", "expected_detail", "expected_message"),
    [
        (
            "scan_course_content",
            None,
            "Failed to scan course content",
            "Failed to scan course content",
        ),
        (
            "scan_course_content_by_type",
            "page",
            "Failed to scan content",
            "Failed to scan course content type",
        ),
    ],
)
@pytest.mark.asyncio
async def test_canvas_content_scan_boundaries_log_sanitized_context(
    route_name, content_type, expected_detail, expected_message, caplog
):
    from fastapi import HTTPException
    from src.api import canvas_content_routes

    sensitive_marker = "canvas-secret:/customer/private/path"
    request = canvas_content_routes.CanvasContentScanRequest(course_id="course-1")
    args = [request, MagicMock(), MagicMock(), SimpleNamespace(department_id="dept-1")]
    if content_type is not None:
        args.insert(0, canvas_content_routes.ContentTypeParam(content_type))

    with (
        patch.object(canvas_content_routes, "verify_department_access"),
        patch.object(canvas_content_routes, "require_lti_course_access"),
        patch.object(
            canvas_content_routes, "require_feature", new=AsyncMock(return_value=None)
        ),
        patch.object(
            canvas_content_routes,
            "_get_canvas_client",
            new=AsyncMock(side_effect=RuntimeError(sensitive_marker)),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await getattr(canvas_content_routes, route_name)(*args)

    assert exc.value.status_code == 500
    assert exc.value.detail == expected_detail
    assert sensitive_marker not in caplog.text
    record = next(
        record for record in caplog.records if record.message == expected_message
    )
    assert record.course_id == "course-1"
    assert record.department_id == "dept-1"
    assert record.error_type == "RuntimeError"
    assert record.exc_info is None


@pytest.mark.asyncio
async def test_canvas_background_scan_boundary_logs_sanitized_context(caplog):
    from src.api.canvas_scan_routes import _canvas_scan_file_task
    from src.db.models import CloudJobStatus

    sensitive_marker = "background-secret:/customer/private/path"
    job = SimpleNamespace(
        status=CloudJobStatus.PENDING.value,
        started_at=None,
        progress=0,
        progress_message=None,
        result_data=None,
        error_message=None,
        completed_at=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = job
    db_context = MagicMock()
    db_context.__enter__.return_value = db

    with (
        patch("src.db.database.get_db", return_value=db_context),
        patch(
            "src.jobs.cloud_scan_job.handle_scan_job",
            new=AsyncMock(side_effect=RuntimeError(sensitive_marker)),
        ),
    ):
        await _canvas_scan_file_task("job-1", "file-1", "credential-1")

    assert job.status == CloudJobStatus.FAILED.value
    assert job.error_message == "Accessibility scan failed"
    assert sensitive_marker not in caplog.text
    record = next(
        record
        for record in caplog.records
        if record.message == "Canvas scan background job failed"
    )
    assert record.job_id == "job-1"
    assert record.cloud_file_id == "file-1"
    assert record.credential_id == "credential-1"
    assert record.error_type == "RuntimeError"
    assert record.exc_info is None


@pytest.mark.asyncio
async def test_content_scan_task_ignores_legacy_true_flags_and_never_remediates():
    from src.api.canvas_content_routes import _content_scan_task

    cloud_file = SimpleNamespace(id="file-1")
    credential = SimpleNamespace(
        id="credential-1",
        provider_metadata={"canvas_instance_url": "https://canvas.example.edu"},
        access_token="x",
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        cloud_file,
        credential,
    ]
    db_context = MagicMock()
    db_context.__enter__.return_value = db
    scanner = MagicMock()
    scanner.scan_content_item = AsyncMock(
        return_value={"scan_id": "scan-1", "issues": 1, **DETERMINISTIC_METADATA}
    )
    scanner.remediate_content_item = AsyncMock()
    canvas_client = AsyncMock()

    with (
        patch("src.db.database.get_db", return_value=db_context),
        patch(
            "src.api.canvas_content_routes.require_persisted_canvas_origin",
            return_value="https://canvas.example.edu",
        ),
        patch("src.integrations.oauth_token_manager.OAuthTokenManager") as tokens,
        patch("src.integrations.canvas.CanvasAPIClient", return_value=canvas_client),
        patch(
            "src.api.canvas_content_routes.CanvasContentScanner", return_value=scanner
        ) as scanner_class,
    ):
        tokens.return_value.decrypt_token.return_value = "token"
        result = await _content_scan_task(
            "file-1",
            "department-1",
            "credential-1",
            scan_options={
                "generate_alt_text": True,
                "auto_remediate": True,
                "provider": "gemini",
            },
        )

    scanner.scan_content_item.assert_awaited_once_with(cloud_file)
    scanner.remediate_content_item.assert_not_awaited()
    assert scanner_class.call_args.kwargs["scan_options"] == {
        "generate_alt_text": False,
        "auto_remediate": False,
        "detect_decorative": False,
    }
    assert DETERMINISTIC_METADATA.items() <= result.items()


@pytest.mark.parametrize(
    "failure_point",
    ["origin_validation", "token_decryption", "client_creation", "client_close"],
)
@pytest.mark.asyncio
async def test_content_scan_task_returns_sanitized_authoritative_failure(
    failure_point, caplog
):
    from src.api.canvas_content_routes import _content_scan_task

    sensitive_marker = "private-detail:/customer/path"
    cloud_file = SimpleNamespace(id="file-1")
    credential = SimpleNamespace(id="credential-1", access_token="x")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        cloud_file,
        credential,
    ]
    db_context = MagicMock()
    db_context.__enter__.return_value = db
    canvas_client = AsyncMock()
    scanner = MagicMock()
    scanner.scan_content_item = AsyncMock(return_value={"success": True})

    with (
        patch("src.db.database.get_db", return_value=db_context),
        patch(
            "src.api.canvas_content_routes.require_persisted_canvas_origin",
            return_value="https://canvas.example.edu",
        ) as validate_origin,
        patch("src.integrations.oauth_token_manager.OAuthTokenManager") as tokens,
        patch(
            "src.integrations.canvas.CanvasAPIClient", return_value=canvas_client
        ) as client_class,
        patch(
            "src.api.canvas_content_routes.CanvasContentScanner", return_value=scanner
        ),
    ):
        tokens.return_value.decrypt_token.return_value = "token"
        if failure_point == "origin_validation":
            validate_origin.side_effect = RuntimeError(sensitive_marker)
        elif failure_point == "token_decryption":
            tokens.return_value.decrypt_token.side_effect = RuntimeError(
                sensitive_marker
            )
        elif failure_point == "client_creation":
            client_class.side_effect = RuntimeError(sensitive_marker)
        else:
            canvas_client.close.side_effect = RuntimeError(sensitive_marker)

        result = await _content_scan_task("file-1", "department-1", "credential-1")

    assert result == {
        "success": False,
        "error": "Deterministic accessibility scan unavailable",
        "error_code": "DETERMINISTIC_SCAN_UNAVAILABLE",
        **DETERMINISTIC_METADATA,
    }
    assert sensitive_marker not in caplog.text


def test_canvas_content_scanner_fallback_options_are_deterministic():
    from src.education.canvas_content_scanner import CanvasContentScanner

    scanner = CanvasContentScanner(
        canvas_client=AsyncMock(),
        db=MagicMock(),
        department_id="department-1",
        credential_id="credential-1",
    )

    assert scanner.scan_options == {
        "generate_alt_text": False,
        "auto_remediate": False,
        "detect_decorative": False,
    }


def test_pdf_processor_does_not_initialize_provider_when_ai_options_are_disabled():
    from src.education.pdf_processor import PDFProcessor

    with patch("src.education.pdf_processor.get_provider_manager") as provider_manager:
        processor = PDFProcessor(
            generate_alt_text=False,
            validate_alt_text=False,
            enhance_descriptions=False,
            simulate_color_blindness=False,
        )

    provider_manager.assert_not_called()
    assert processor.llm_client is None
    assert processor._enhance_fix_description({"rule": "WCAG 3.1.1"}) is None


def test_pdf_processor_initializes_provider_and_enhances_when_enabled():
    from src.education.pdf_processor import PDFProcessor

    guideline = SimpleNamespace(
        wcag_criterion="3.1.1",
        title="Language of Page",
        wcag_level="A",
        description="The default human language can be programmatically determined.",
        best_practices=["Set the document language."],
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = guideline
    provider = MagicMock()
    enhanced = (
        "Set the PDF document language so assistive technology can read it correctly."
    )
    provider.generate_text_sync.return_value = {
        "success": True,
        "content": enhanced,
        "provider": "test",
    }

    with patch(
        "src.education.pdf_processor.get_provider_manager", return_value=provider
    ) as provider_manager:
        processor = PDFProcessor(enhance_descriptions=True, db_session=db)

    assert processor.llm_client is provider
    provider_manager.assert_called_once_with()
    with patch("src.db.models.WCAGGuideline", create=True):
        result = processor._enhance_fix_description(
            {
                "rule": "WCAG 3.1.1",
                "message": "Missing document language",
                "impact": "Screen readers may use the wrong pronunciation rules",
            }
        )

    assert result == enhanced
    provider.generate_text_sync.assert_called_once()


@pytest.mark.parametrize(
    ("file_type", "class_path", "method_name", "expected_options"),
    [
        (
            "docx",
            "src.education.docx_processor.DocxProcessor",
            "process_docx",
            {
                "generate_alt_text": False,
                "validate_alt_text": False,
                "enhance_descriptions": False,
                "simulate_color_blindness": False,
            },
        ),
        (
            "pptx",
            "src.education.pptx_processor.PowerPointProcessor",
            "process_pptx",
            {
                "generate_alt_text": False,
                "validate_alt_text": False,
                "simulate_color_blindness": False,
                "detect_images_of_text": False,
            },
        ),
        (
            "xlsx",
            "src.education.xlsx_processor.XlsxProcessor",
            "process_xlsx",
            {
                "generate_chart_descriptions": False,
                "generate_alt_text": False,
                "validate_alt_text": False,
                "simulate_color_blindness": False,
            },
        ),
        (
            "pdf",
            "src.education.pdf_processor.PDFProcessor",
            "process_pdf",
            {
                "generate_alt_text": False,
                "validate_alt_text": False,
                "enhance_descriptions": False,
                "simulate_color_blindness": False,
            },
        ),
        (
            "mp4",
            "src.education.multimedia_processor.MultimediaProcessor",
            "process_media",
            {"use_gemini": False},
        ),
    ],
)
@pytest.mark.asyncio
async def test_cloud_lms_scan_explicitly_disables_processor_ai_options(
    file_type, class_path, method_name, expected_options
):
    from src.jobs.cloud_scan_job import CloudScanJob

    credential = SimpleNamespace(department_id="department-1")
    cloud_file = SimpleNamespace(
        id="file-1",
        file_type=file_type,
        file_name=f"example.{file_type}",
        file_size_bytes=10,
        last_scan_id=None,
        last_scanned_at=None,
        last_compliance_score=None,
        needs_rescan=True,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    processor = MagicMock()
    getattr(processor, method_name).return_value = {
        "success": True,
        "issues": [{"severity": "high", "description": "deterministic finding"}],
        "compliance_score": 50.0,
    }

    with (
        patch(class_path, return_value=processor) as processor_class,
        patch("src.jobs.email_alert_job.trigger_scan_alerts", new=AsyncMock()),
    ):
        result = await CloudScanJob(credential, cloud_file, MagicMock())._scan_file(
            f"/tmp/example.{file_type}", db
        )

    assert expected_options.items() <= processor_class.call_args.kwargs.items()
    assert DETERMINISTIC_METADATA.items() <= result.items()
    assert result["compliance_score"] == 50.0
    assert result["issues_found"] == 1


@pytest.mark.asyncio
async def test_lms_image_scan_emits_manual_review_finding_without_ai_provider_calls():
    from src.jobs.cloud_scan_job import CloudScanJob

    credential = SimpleNamespace(department_id="department-1")
    cloud_file = SimpleNamespace(
        id="image-1",
        file_type="png",
        file_name="diagram.png",
        file_size_bytes=10,
        last_scan_id=None,
        last_scanned_at=None,
        last_compliance_score=None,
        needs_rescan=True,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    with (
        patch(
            "src.education.image_alt_text.ImageAltTextGenerator",
            side_effect=AssertionError("scan must not construct an alt-text generator"),
        ) as generator,
        patch("src.ai.providers.get_provider_manager") as provider_manager,
        patch("src.jobs.email_alert_job.trigger_scan_alerts", new=AsyncMock()),
    ):
        result = await CloudScanJob(credential, cloud_file, MagicMock())._scan_file(
            "/tmp/diagram.png", db
        )

    generator.assert_not_called()
    provider_manager.assert_not_called()
    assert result["success"] is True
    assert result["compliance_score"] == 0.0
    assert result["issues_found"] == 1
    assert DETERMINISTIC_METADATA.items() <= result.items()

    persisted_scan_result = next(
        call.args[0]
        for call in db.add.call_args_list
        if type(call.args[0]).__name__ == "ScanResult"
    )
    issue = persisted_scan_result.issues[0]
    assert issue["description"] == "Image requires alt text"
    assert issue["manual_review_required"] is True
    assert DETERMINISTIC_METADATA.items() <= issue.items()
    assert "suggested_alt" not in issue


@pytest.mark.asyncio
async def test_cloud_scan_processor_import_failure_is_authoritative_non_ai_result():
    from src.jobs.cloud_scan_job import CloudScanJob

    credential = SimpleNamespace(department_id="department-1")
    cloud_file = SimpleNamespace(
        id="file-1", file_type="docx", file_name="example.docx"
    )

    with patch.dict(sys.modules, {"src.education.docx_processor": None}):
        result = await CloudScanJob(credential, cloud_file, MagicMock())._scan_file(
            "/tmp/example.docx", MagicMock()
        )

    assert result["success"] is False
    assert result["error"] == "Accessibility scan failed"
    assert result["error_code"] == "PROCESSOR_UNAVAILABLE"
    assert result["file_id"] == "file-1"
    assert DETERMINISTIC_METADATA.items() <= result.items()


@pytest.mark.asyncio
async def test_cloud_scan_processor_runtime_failure_is_authoritative_non_ai_result(
    caplog,
):
    from src.jobs.cloud_scan_job import CloudScanJob

    credential = SimpleNamespace(department_id="department-1")
    cloud_file = SimpleNamespace(
        id="file-1", file_type="docx", file_name="example.docx"
    )
    processor = MagicMock()
    sensitive_marker = "processor-secret:/customer/path"
    processor.process_docx.side_effect = RuntimeError(sensitive_marker)

    with patch("src.education.docx_processor.DocxProcessor", return_value=processor):
        result = await CloudScanJob(credential, cloud_file, MagicMock())._scan_file(
            "/tmp/example.docx", MagicMock()
        )

    assert result["success"] is False
    assert result["error"] == "Accessibility scan failed"
    assert result["error_code"] == "SCAN_PROCESSING_FAILED"
    assert result["file_id"] == "file-1"
    assert DETERMINISTIC_METADATA.items() <= result.items()
    assert sensitive_marker not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize(
    "score",
    [None, float("nan"), float("inf"), float("-inf"), "75", True, -0.1, 100.1],
)
@pytest.mark.asyncio
async def test_cloud_scan_rejects_invalid_explicit_success_scores_through_persistence(
    score,
):
    from src.db.models import ScanStatus
    from src.jobs.cloud_scan_job import CloudScanJob

    credential = SimpleNamespace(department_id="dept-1")
    cloud_file = SimpleNamespace(
        id="file-1",
        file_type="docx",
        file_name="example.docx",
        file_size_bytes=10,
        last_scan_id="old-scan",
        last_scanned_at=None,
        last_compliance_score=88.0,
        needs_rescan=False,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    processor = MagicMock()
    processor.process_docx.return_value = {
        "success": True,
        "issues": [],
        "compliance_score": score,
    }

    with patch("src.education.docx_processor.DocxProcessor", return_value=processor):
        result = await CloudScanJob(credential, cloud_file, MagicMock())._scan_file(
            "/tmp/example.docx", db
        )

    scan = next(
        call.args[0]
        for call in db.add.call_args_list
        if type(call.args[0]).__name__ == "Scan"
    )
    assert scan.status == ScanStatus.FAILED
    assert scan.error_message == "SCAN_PROCESSING_FAILED"
    assert result["success"] is False
    assert result["compliance_score"] is None
    assert result["error"] == "Accessibility scan failed"
    assert result["error_code"] == "SCAN_PROCESSING_FAILED"
    assert cloud_file.last_scan_id == "old-scan"
    assert cloud_file.last_compliance_score == 88.0
    assert cloud_file.needs_rescan is True
    assert not any(
        type(call.args[0]).__name__ == "ScanResult" for call in db.add.call_args_list
    )


@pytest.mark.parametrize(
    "explicit_success",
    [None, 0, 1, "false", "true", [], {}],
    ids=["none", "zero", "one", "false-string", "true-string", "list", "dict"],
)
@pytest.mark.asyncio
async def test_cloud_scan_rejects_malformed_explicit_success_through_persistence(
    explicit_success,
):
    from src.db.models import ScanStatus
    from src.jobs.cloud_scan_job import CloudScanJob

    credential = SimpleNamespace(department_id="dept-1")
    cloud_file = SimpleNamespace(
        id="file-1",
        file_type="docx",
        file_name="example.docx",
        file_size_bytes=10,
        last_scan_id="old-scan",
        last_scanned_at=None,
        last_compliance_score=88.0,
        needs_rescan=False,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    processor = MagicMock()
    processor.process_docx.return_value = {
        "success": explicit_success,
        "issues": [],
        "compliance_score": 75.0,
    }

    with patch("src.education.docx_processor.DocxProcessor", return_value=processor):
        result = await CloudScanJob(credential, cloud_file, MagicMock())._scan_file(
            "/tmp/example.docx", db
        )

    scan = next(
        call.args[0]
        for call in db.add.call_args_list
        if type(call.args[0]).__name__ == "Scan"
    )
    assert scan.status == ScanStatus.FAILED
    assert scan.error_message == "SCAN_PROCESSING_FAILED"
    assert result["success"] is False
    assert result["compliance_score"] is None
    assert result["error"] == "Accessibility scan failed"
    assert result["error_code"] == "SCAN_PROCESSING_FAILED"
    assert cloud_file.last_scan_id == "old-scan"
    assert cloud_file.last_compliance_score == 88.0
    assert cloud_file.needs_rescan is True
    assert not any(
        type(call.args[0]).__name__ == "ScanResult" for call in db.add.call_args_list
    )


@pytest.mark.parametrize("score", [0, 100])
def test_cloud_scan_normalization_accepts_finite_score_boundaries(score):
    from src.jobs.cloud_scan_job import _normalize_processor_result

    assert (
        _normalize_processor_result(
            {"success": True, "compliance_score": score, "issues": []}
        )["success"]
        is True
    )


def test_cloud_scan_normalization_rejects_explicit_true_with_error():
    from src.jobs.cloud_scan_job import _normalize_processor_result

    result = _normalize_processor_result(
        {
            "success": True,
            "compliance_score": 75,
            "error": "processor reported failure",
        }
    )

    assert result["success"] is False
    assert result["compliance_score"] is None
    assert result["error"] == "Accessibility scan failed"
    assert result["error_code"] == "SCAN_PROCESSING_FAILED"


@pytest.mark.parametrize(
    "score",
    [None, float("nan"), float("inf"), float("-inf"), "75", True, -1, 101],
)
def test_cloud_scan_normalization_rejects_invalid_inferred_success_scores(score):
    from src.jobs.cloud_scan_job import _normalize_processor_result

    result = _normalize_processor_result({"compliance_score": score, "issues": []})

    assert result["success"] is False
    assert result["compliance_score"] is None
    assert result["error"] == "Accessibility scan failed"
    assert result["error_code"] == "SCAN_PROCESSING_FAILED"


def test_scan_execution_functions_have_no_direct_generative_ai_dependencies():
    """Narrow AST guard: remediation methods remain allowed to use AI."""
    from src.api import canvas_content_routes, canvas_scan_routes
    from src.education import canvas_content_scanner
    from src.jobs import cloud_scan_job

    guarded = [
        canvas_content_routes._content_scan_task,
        canvas_scan_routes._canvas_scan_file_task,
        canvas_content_scanner.CanvasContentScanner.scan_course_content,
        canvas_content_scanner.CanvasContentScanner.scan_content_item,
        canvas_content_scanner.CanvasContentScanner._verify_remediation,
        cloud_scan_job.CloudScanJob.run,
        cloud_scan_job.CloudScanJob._scan_file,
    ]
    forbidden = {"get_provider_manager", "GeminiClient", "ImageAltTextGenerator"}

    violations = []
    for function in guarded:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        found = forbidden & (names | attrs)
        if found:
            violations.append(f"{function.__qualname__}: {sorted(found)}")

    assert violations == []


@pytest.mark.asyncio
async def test_deterministic_axe_strips_active_html_and_blocks_network_before_content():
    from src.education import deterministic_axe

    events = []
    page = AsyncMock()
    page.route.side_effect = lambda pattern, handler: events.append(("route", pattern))
    page.set_content.side_effect = lambda html: events.append(("content", html))
    browser = AsyncMock()
    browser.new_page.return_value = page
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser))
    )
    axe = AsyncMock()
    axe.run.return_value = SimpleNamespace(
        response={
            "passes": [{"id": "document-title", "nodes": []}],
            "violations": [],
        }
    )

    with patch.object(
        deterministic_axe,
        "_load_runtime",
        return_value=(lambda: _AsyncContext(playwright), lambda: axe),
    ):
        result = await deterministic_axe.run_deterministic_axe(
            '<meta http-equiv="refresh" content="0;url=https://evil.test">'
            '<script src="https://evil.test/x.js">alert(1)</script>'
            '<iframe src="https://evil.test/frame"></iframe>'
            '<img src="https://evil.test/pixel" onerror="fetch(\'/secret\')" alt="chart">'
            '<div srcdoc="<script>alert(2)</script>">Useful text</div>'
        )

    assert result == {
        "passes": [{"id": "document-title", "nodes": []}],
        "violations": [],
    }
    assert events[0] == ("route", "**/*")
    sanitized = events[1][1]
    assert "evil.test/x.js" not in sanitized
    assert "<script" not in sanitized
    assert "<iframe" not in sanitized
    assert "onerror" not in sanitized
    assert "srcdoc" not in sanitized
    assert "http-equiv" not in sanitized
    assert 'alt="chart"' in sanitized
    axe.run.assert_awaited_once_with(page, options=None)
    browser.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_deterministic_axe_runtime_and_malformed_results_fail_closed():
    from src.education import deterministic_axe

    with patch.object(
        deterministic_axe,
        "_load_runtime",
        side_effect=ImportError("secret package path"),
    ):
        with pytest.raises(deterministic_axe.DeterministicScanUnavailable) as exc:
            await deterministic_axe.run_deterministic_axe("<p>Hello</p>")
    assert str(exc.value) == "Deterministic accessibility scan unavailable"

    page = AsyncMock()
    browser = AsyncMock()
    browser.new_page.return_value = page
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(launch=AsyncMock(return_value=browser))
    )
    axe = AsyncMock()
    axe.run.return_value = SimpleNamespace(response={"passes": [], "violations": []})
    with patch.object(
        deterministic_axe,
        "_load_runtime",
        return_value=(lambda: _AsyncContext(playwright), lambda: axe),
    ):
        with pytest.raises(deterministic_axe.DeterministicScanUnavailable):
            await deterministic_axe.run_deterministic_axe("<p>Hello</p>")


@pytest.mark.parametrize(
    "payload",
    [
        {"passes": [None], "violations": []},
        {"passes": ["invalid"], "violations": []},
        {"passes": [{}], "violations": []},
        {"passes": [{"id": "", "nodes": []}], "violations": []},
        {"passes": [{"id": "rule"}], "violations": []},
        {"passes": [{"id": "rule", "nodes": None}], "violations": []},
        {"passes": [{"id": "rule", "nodes": []}], "violations": [None]},
        {
            "passes": [{"id": "rule", "nodes": []}],
            "violations": [{"id": "violation"}],
        },
    ],
)
def test_deterministic_axe_rejects_malformed_result_entries(payload):
    from src.education.deterministic_axe import (
        DeterministicScanUnavailable,
        _validated_response,
    )

    with pytest.raises(DeterministicScanUnavailable):
        _validated_response(payload)


@pytest.mark.asyncio
async def test_canvas_consumer_cannot_score_malformed_axe_pass_entry():
    from src.education.canvas_content_scanner import CanvasContentScanner

    db = MagicMock()
    cloud_file = SimpleNamespace(
        id="content-malformed",
        content_body="<p>Hello</p>",
        file_name="Page",
        last_scan_id="old-scan",
        last_scanned_at=None,
        last_compliance_score=88.0,
        needs_rescan=True,
    )
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")

    with patch(
        "src.education.canvas_content_scanner.run_deterministic_axe",
        new=AsyncMock(
            side_effect=lambda _html: __import__(
                "src.education.deterministic_axe", fromlist=["_validated_response"]
            )._validated_response({"passes": ["invalid"], "violations": []})
        ),
    ):
        result = await scanner.scan_content_item(cloud_file)

    assert result["success"] is False
    assert result["compliance_score"] is None
    assert result["error_code"] == "DETERMINISTIC_SCAN_UNAVAILABLE"
    assert cloud_file.last_scan_id == "old-scan"
    assert cloud_file.last_compliance_score == 88.0
    assert cloud_file.needs_rescan is True


@pytest.mark.asyncio
async def test_canvas_axe_failure_is_failed_without_score_or_raw_error(caplog):
    from src.db.models import ScanStatus
    from src.education.canvas_content_scanner import CanvasContentScanner

    db = MagicMock()
    cloud_file = SimpleNamespace(
        id="content-1",
        content_body="<p>Hello</p>",
        file_name="Page",
        last_scan_id="old-scan",
        last_scanned_at=None,
        last_compliance_score=88.0,
        needs_rescan=True,
    )
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    scanner._run_axe_scan = AsyncMock(side_effect=RuntimeError("token=/private/secret"))

    result = await scanner.scan_content_item(cloud_file)

    scan = next(
        c.args[0] for c in db.add.call_args_list if type(c.args[0]).__name__ == "Scan"
    )
    assert scan.status == ScanStatus.FAILED
    assert scan.error_message == "DETERMINISTIC_SCAN_UNAVAILABLE"
    assert result["success"] is False
    assert result["compliance_score"] is None
    assert result["error_code"] == "DETERMINISTIC_SCAN_UNAVAILABLE"
    assert "secret" not in repr(result)
    assert cloud_file.last_scan_id == "old-scan"
    assert cloud_file.last_compliance_score == 88.0
    assert cloud_file.needs_rescan is True
    assert "token=/private/secret" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_cloud_real_processor_model_normalizes_success_and_failure_is_not_score_zero():
    from src.education.multimedia_processor import MultimediaProcessingResult
    from src.jobs.cloud_scan_job import CloudScanJob

    credential = SimpleNamespace(department_id="dept-1")
    cloud_file = SimpleNamespace(
        id="media-1",
        file_type="mp4",
        file_name="video.mp4",
        file_size_bytes=10,
        last_scan_id="old",
        last_scanned_at=None,
        last_compliance_score=77.0,
        needs_rescan=True,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    model = MultimediaProcessingResult(
        file_path="/tmp/video.mp4",
        file_name="video.mp4",
        media_type="video",
        duration=1.0,
        has_captions=True,
        compliance_score=91.0,
        issues=[],
    )
    processor = MagicMock()
    processor.process_media.return_value = model
    with (
        patch(
            "src.education.multimedia_processor.MultimediaProcessor",
            return_value=processor,
        ),
        patch("src.jobs.email_alert_job.trigger_scan_alerts", new=AsyncMock()),
    ):
        result = await CloudScanJob(credential, cloud_file, MagicMock())._scan_file(
            "/tmp/video.mp4", db
        )
    assert result["success"] is True
    assert cloud_file.last_compliance_score == 91.0
    assert cloud_file.needs_rescan is False

    failed_file = SimpleNamespace(**cloud_file.__dict__)
    failed_file.id = "failed-1"
    failed_file.last_scan_id = "older"
    failed_file.last_compliance_score = 77.0
    failed_file.needs_rescan = True
    failed_processor = MagicMock()
    failed_processor.process_media.return_value = {
        "success": False,
        "error": "decoder leaked /private/path",
        "issues": [],
        "compliance_score": 0,
    }
    db.reset_mock()
    db.query.return_value.filter.return_value.first.return_value = None
    with patch(
        "src.education.multimedia_processor.MultimediaProcessor",
        return_value=failed_processor,
    ):
        failed = await CloudScanJob(credential, failed_file, MagicMock())._scan_file(
            "/tmp/video.mp4", db
        )
    scan = next(
        c.args[0] for c in db.add.call_args_list if type(c.args[0]).__name__ == "Scan"
    )
    assert failed["success"] is False
    assert failed["compliance_score"] is None
    assert failed["error_code"] == "SCAN_PROCESSING_FAILED"
    assert scan.status == "FAILED"
    assert failed_file.last_scan_id == "older"
    assert failed_file.last_compliance_score == 77.0
    assert failed_file.needs_rescan is True
    assert not any(
        type(c.args[0]).__name__ == "ScanResult" for c in db.add.call_args_list
    )


@pytest.mark.asyncio
async def test_handle_scan_job_raises_sanitized_typed_error_for_structured_failure():
    from src.jobs.cloud_scan_job import CloudScanJob, ScanJobFailed, handle_scan_job

    credential = SimpleNamespace(id="cred")
    cloud_file = SimpleNamespace(id="file")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        credential,
        cloud_file,
    ]
    with patch.object(
        CloudScanJob,
        "run",
        new=AsyncMock(
            return_value={
                "success": False,
                "error": "secret token",
                "error_code": "SCAN_PROCESSING_FAILED",
            }
        ),
    ):
        with pytest.raises(ScanJobFailed) as exc:
            await handle_scan_job(
                SimpleNamespace(credential_id="cred", cloud_file_id="file"),
                db,
                MagicMock(),
            )
    assert str(exc.value) == "Accessibility scan failed"
    assert exc.value.code == "SCAN_PROCESSING_FAILED"


def test_all_real_processor_models_without_success_are_normalized_from_score():
    from src.education.docx_processor import DocxProcessingResult
    from src.education.multimedia_processor import MultimediaProcessingResult
    from src.education.pdf_checks.models import PDFProcessingResult
    from src.education.pptx_processor import PowerPointProcessingResult
    from src.education.xlsx_processor import XlsxProcessingResult
    from src.jobs.cloud_scan_job import _normalize_processor_result

    models = [
        DocxProcessingResult(
            file_path="x.docx",
            file_name="x.docx",
            total_paragraphs=0,
            total_images=0,
            total_tables=0,
            total_lists=0,
            total_links=0,
            heading_issues=[],
            image_issues=[],
            table_issues=[],
            list_issues=[],
            link_issues=[],
            language_issues=[],
            summary={},
            compliance_score=80,
            html_output="",
            remediation_suggestions=[],
        ),
        PowerPointProcessingResult(
            file_path="x.pptx",
            file_name="x.pptx",
            total_slides=0,
            total_shapes=0,
            total_images=0,
            slides=[],
            summary={},
            compliance_score=81,
            remediation_suggestions=[],
        ),
        XlsxProcessingResult(
            file_path="x.xlsx",
            file_name="x.xlsx",
            total_sheets=0,
            total_rows=0,
            total_charts=0,
            total_images=0,
            sheet_name_issues=[],
            sheets=[],
            summary={},
            compliance_score=82,
            remediation_suggestions=[],
        ),
        PDFProcessingResult(
            file_path="x.pdf",
            file_name="x.pdf",
            pages=1,
            text_extracted=True,
            ocr_used=False,
            structure={},
            html_output="",
            compliance_score=83,
            issues=[],
        ),
        MultimediaProcessingResult(
            file_path="x.mp4",
            file_name="x.mp4",
            media_type="video",
            duration=1,
            has_captions=True,
            compliance_score=84,
            issues=[],
        ),
    ]

    normalized = [_normalize_processor_result(model) for model in models]
    assert [item["success"] for item in normalized] == [True] * 5
    assert [item["compliance_score"] for item in normalized] == [80, 81, 82, 83, 84]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module_path", "task_name"),
    [
        ("src.api.canvas_scan_routes", "_canvas_scan_file_task"),
        ("src.api.microsoft_routes", "_scan_file_task"),
    ],
)
async def test_background_scan_callers_mark_typed_scan_failure_failed(
    module_path, task_name
):
    import importlib

    from src.db.models import CloudJobStatus
    from src.jobs.cloud_scan_job import ScanJobFailed

    module = importlib.import_module(module_path)
    task = getattr(module, task_name)
    job = SimpleNamespace(
        status=CloudJobStatus.PENDING.value,
        started_at=None,
        progress=0,
        progress_message=None,
        result_data=None,
        error_message=None,
        completed_at=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = job
    db_context = MagicMock()
    db_context.__enter__.return_value = db

    handler_target = "src.jobs.cloud_scan_job.handle_scan_job"
    with (
        patch("src.db.database.get_db", return_value=db_context),
        patch(
            handler_target,
            new=AsyncMock(side_effect=ScanJobFailed("SCAN_PROCESSING_FAILED")),
        ),
    ):
        await task("job-1", "file-1", "cred-1")

    assert job.status == CloudJobStatus.FAILED.value
    assert job.error_message == "Accessibility scan failed"
    assert "secret" not in str(job.progress_message)


def test_scan_execution_uses_shared_bundled_axe_without_cdn_fallbacks():
    from src.education import canvas_content_scanner, deterministic_axe
    from src.jobs import cloud_scan_job

    helper_source = inspect.getsource(deterministic_axe)
    assert "axe_playwright_python.async_playwright" in helper_source
    assert 'page.route("**/*"' in helper_source
    assert "options=None" in helper_source
    for module in (canvas_content_scanner, cloud_scan_job, deterministic_axe):
        source = inspect.getsource(module)
        assert "cdnjs.cloudflare.com" not in source
        assert "node_modules" not in source


def test_uv_lock_is_not_created():
    assert not (Path(__file__).parents[1] / "uv.lock").exists()
