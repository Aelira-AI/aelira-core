"""Exercise route boundaries with sensitive inputs and no database/network access."""

import importlib
import inspect
import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile

SECRET = "example_sensitive-token-8f31"
FILENAME = f"{SECRET}.png"
URL = f"https://example.edu/private/{SECRET}?access_token={SECRET}"
SCAN_ID = "6d5a104f-c75f-41a0-a678-1c1bfc8b0438"


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """These tests replace SessionLocal and never initialize a database."""
    yield


class MemorySession:
    def __init__(self):
        self.scan = SimpleNamespace(
            id=SCAN_ID,
            department_id="workspace",
            status=None,
            progress=0,
            progress_message=None,
            error_message=None,
        )
        self.added = []
        self.progress_messages = []

    def query(self, *args):
        return self

    def filter(self, *args):
        return self

    def first(self):
        return self.scan

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.progress_messages.append(self.scan.progress_message)

    def rollback(self):
        self.added.clear()

    def close(self):
        pass


@pytest.fixture
def routes(monkeypatch, caplog):
    modules = SimpleNamespace(
        **{
            name: importlib.import_module(f"src.api.education.{name}")
            for name in (
                "_shared",
                "image_routes",
                "scan_routes",
                "web_scan_routes",
                "multimedia_routes",
            )
        }
    )
    settings = importlib.import_module("src.config.settings")
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: SimpleNamespace(
            env="test",
            max_file_size_image=1024 * 1024,
            max_file_size_pdf=1024 * 1024,
        ),
    )
    database = importlib.import_module("src.db.database")
    session = MemorySession()
    monkeypatch.setattr(database, "SessionLocal", lambda: session)
    provider = importlib.import_module("src.ai.workspace_provider_runtime")
    monkeypatch.setattr(provider, "workspace_provider_runtime", lambda *_: None)
    caplog.set_level(logging.DEBUG)
    modules.session = session
    return modules


def assert_no_disclosure(caplog, session=None):
    records = [r for r in caplog.records if r.name.startswith("src.api.education")]
    assert records, "Expected the exercised route/helper to log an operational event"
    assert SECRET not in "\n".join(r.getMessage() for r in records)
    assert all(r.exc_info is None for r in records)
    if session:
        assert SECRET not in str(session.progress_messages)
        assert SECRET not in str(session.scan.error_message)


def upload():
    return UploadFile(filename=FILENAME, file=io.BytesIO(b"synthetic image"))


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["safe", "medium", "high", "critical", "exception"])
async def test_security_validation_logs_neither_filename_nor_findings(
    routes, monkeypatch, caplog, level
):
    validator = importlib.import_module("src.security.document_validator")
    if level == "exception":
        outcome = AsyncMock(side_effect=RuntimeError(f"{SECRET} /private/{SECRET}"))
    else:
        threat = validator.ThreatLevel(level)
        result = validator.ValidationResult(
            is_safe=level in {"safe", "medium"},
            threat_level=threat,
            file_type="png",
            file_hash="synthetic-hash",
            findings=[validator.SecurityFinding("synthetic", SECRET, threat)],
        )
        outcome = AsyncMock(return_value=result)
    monkeypatch.setattr(routes._shared, "validate_document", outcome)
    if level in {"critical", "high", "exception"}:
        with pytest.raises(HTTPException) as caught:
            await routes._shared.validate_uploaded_file(
                upload(), routes.session, "workspace", allow_sanitized=False
            )
        assert caught.value.status_code == 400
        if level == "exception":
            assert SECRET not in str(caught.value.detail)
    else:
        assert (
            await routes._shared.validate_uploaded_file(
                upload(), routes.session, "workspace"
            )
            == b"synthetic image"
        )
    if level != "safe":
        assert_no_disclosure(caplog)
    else:
        assert SECRET not in caplog.text
    if level != "exception":
        # Detailed findings remain in the access-controlled security result.
        assert routes.session.added[0].findings[0]["description"] == SECRET


IMAGE_METHODS = [
    ("generate_image_alt_text", "generate_alt_text", {}),
    (
        "validate_image_alt_text",
        "validate_alt_text",
        {"existing_alt_text": "Example image"},
    ),
    ("score_alt_text_quality", "score_alt_text_quality", {"alt_text": "Example image"}),
    ("detect_image_type", "detect_image_type", {}),
    (
        "describe_chart_or_graph",
        "describe_chart_or_graph",
        {"detail_level": "standard"},
    ),
    (
        "analyze_image_comprehensive",
        "analyze_image_comprehensive",
        {"existing_alt_text": None},
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("route_name,method,extra", IMAGE_METHODS)
@pytest.mark.parametrize("outcome", ["success", "exception", "provider_error"])
async def test_image_routes_keep_results_and_usage_without_sensitive_diagnostics(
    routes,
    monkeypatch,
    caplog,
    route_name,
    method,
    extra,
    outcome,
):
    module = routes.image_routes
    result = dict(
        success=outcome == "success",
        error=SECRET,
        alt_text="Example image",
        long_description="Example description",
        image_type="photo",
        image_metadata={},
        inference_time=0,
        model="test",
        overall_score=88,
        grade="B",
    )
    generate = AsyncMock(return_value=result)
    if outcome == "exception":
        generate.side_effect = RuntimeError(f"{URL} /private/{SECRET}")
    monkeypatch.setattr(
        module,
        "_workspace_image_generator",
        lambda _: SimpleNamespace(**{method: generate}),
    )
    monkeypatch.setattr(module, "check_image_analysis_quota", AsyncMock())
    monkeypatch.setattr(
        module, "validate_uploaded_file", AsyncMock(return_value=b"image")
    )
    usage = AsyncMock()
    monkeypatch.setattr(module, "increment_image_usage", usage)
    call = getattr(module, route_name)(
        file=upload(),
        context=None,
        db=routes.session,
        api_key_info=(None, SECRET, "workspace"),
        **extra,
    )
    if outcome == "success":
        response = await call
        assert response["success"] is True
        assert response.get("file_name", response.get("filename")) == FILENAME
        usage.assert_awaited_once_with(routes.session, "workspace", count=1)
    else:
        with pytest.raises(HTTPException) as caught:
            await call
        assert caught.value.status_code == 500
        assert SECRET not in str(caught.value.detail)
        usage.assert_not_awaited()
    assert_no_disclosure(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("exception", [False, True])
async def test_batch_image_failure_details_and_accounting(
    routes, monkeypatch, caplog, exception
):
    module = routes.image_routes
    generate = AsyncMock(
        return_value=dict(
            results=[{"result": {"success": False, "error": SECRET}}],
            success_count=0,
            failed_count=1,
            total_images=1,
            total_inference_time=0,
            average_time_per_image=0,
        )
    )
    if exception:
        generate.side_effect = RuntimeError(SECRET)
    monkeypatch.setattr(
        module,
        "_workspace_image_generator",
        lambda _: SimpleNamespace(batch_generate_alt_text=generate),
    )
    monkeypatch.setattr(module, "check_image_analysis_quota", AsyncMock())
    usage = AsyncMock()
    monkeypatch.setattr(module, "increment_image_usage", usage)
    call = module.batch_generate_alt_text(
        files=[upload()],
        context=None,
        db=routes.session,
        api_key_info=(None, SECRET, "workspace"),
    )
    if exception:
        with pytest.raises(HTTPException) as caught:
            await call
        assert SECRET not in str(caught.value.detail)
        usage.assert_not_awaited()
        assert_no_disclosure(caplog)
    else:
        response = await call
        assert response["failed_count"] == 1
        assert response["results"][0]["success"] is False
        assert SECRET not in response["results"][0]["error"]
        usage.assert_awaited_once_with(routes.session, "workspace", count=0)


def background_arguments(function, tmp_path):
    path = tmp_path / f"{SECRET}.pdf"
    path.write_bytes(b"synthetic")
    values = dict(
        file_path=str(path),
        file_content=b"synthetic",
        filename=FILENAME,
        scan_id=SCAN_ID,
        batch_scan_id=SCAN_ID,
        sitemap_scan_id=SCAN_ID,
        user_id=SECRET,
        department_id="workspace",
        workspace_id="workspace",
        storage_path=f"/private/{SECRET}",
        url=URL,
        urls=[URL],
        sitemap_url=URL,
        mode="quick",
        max_depth=1,
        max_pages=2,
        whisper_model="base",
        priority_patterns=[SECRET],
    )
    return {
        name: values.get(name, False)
        for name, param in inspect.signature(function).parameters.items()
        if param.default is inspect.Parameter.empty or name == "workspace_id"
    }


BACKGROUND_METHODS = [
    ("scan_routes", "process_pdf_background", "PDFProcessor"),
    ("scan_routes", "process_pptx_background", "PowerPointProcessor"),
    ("scan_routes", "process_docx_background", "DocxProcessor"),
    ("scan_routes", "process_xlsx_background", "XlsxProcessor"),
    ("scan_routes", "process_latex_background", "LaTeXProcessor"),
    ("scan_routes", "process_latex_pdf_background", "PDFProcessor"),
    ("web_scan_routes", "process_code_background", "CodeScanner"),
    ("web_scan_routes", "process_web_scan_background", "WebScanner"),
    ("multimedia_routes", "process_multimedia_background", "MultimediaProcessor"),
]


@pytest.mark.parametrize("module_name,function_name,processor_name", BACKGROUND_METHODS)
def test_background_exceptions_and_progress_do_not_persist_secrets(
    routes,
    monkeypatch,
    caplog,
    tmp_path,
    module_name,
    function_name,
    processor_name,
):
    from src.db.models import ScanStatus

    module = getattr(routes, module_name)
    owner = (
        importlib.import_module("src.education.multimedia_processor")
        if processor_name == "MultimediaProcessor"
        else module
    )

    class BrokenProcessor:
        def __init__(self, **kwargs):
            kwargs["progress_callback"](1, 2, f"Read failed: {URL} /private/{SECRET}")

        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError(f"{URL} /private/{SECRET}")

            return fail

    monkeypatch.setattr(owner, processor_name, BrokenProcessor)
    function = getattr(module, function_name)
    function(**background_arguments(function, tmp_path))
    assert routes.session.scan.status == ScanStatus.FAILED
    assert (
        routes.session.scan.error_message
        == "Processing encountered an error. Please try again."
    )
    assert "Processing in progress..." in routes.session.progress_messages
    assert not routes.session.added
    assert_no_disclosure(caplog, routes.session)
    assert "RuntimeError" in caplog.text
    assert SCAN_ID in caplog.text


@pytest.mark.parametrize("kind", ["pdf", "web", "code", "multimedia"])
def test_successful_background_scans_preserve_results(
    routes, monkeypatch, caplog, tmp_path, kind
):
    from src.db.models import ScanStatus

    results = SimpleNamespace(
        pages=[],
        pages_scanned=1,
        summary={},
        root_url=URL,
        total_scan_time=1,
        overall_compliance_score=87,
        compliance_score=87,
        issues=[],
        structure={},
        html_output="",
        ocr_used=False,
        files_analyzed=1,
        project_name=FILENAME,
        total_lines=12,
        images=[],
        recommendations=[],
        media_type="audio",
        duration=2,
        has_captions=False,
        transcription=[],
        audio_descriptions=[],
        file_name=FILENAME,
        caption_formats={},
        flashing_analysis=None,
    )
    if kind == "pdf":
        results.pages = 1

    class Processor:
        def __init__(self, **kwargs):
            self.callback = kwargs["progress_callback"]

        def __getattr__(self, name):
            def process(*args, **kwargs):
                self.callback(1, 2, f"Analyzing {SECRET}")
                return results

            return process

    spec = {
        "pdf": (routes.scan_routes, "process_pdf_background", "PDFProcessor"),
        "web": (routes.web_scan_routes, "process_web_scan_background", "WebScanner"),
        "code": (routes.web_scan_routes, "process_code_background", "CodeScanner"),
        "multimedia": (
            routes.multimedia_routes,
            "process_multimedia_background",
            "MultimediaProcessor",
        ),
    }
    module, name, processor = spec[kind]
    owner = (
        importlib.import_module("src.education.multimedia_processor")
        if kind == "multimedia"
        else module
    )
    monkeypatch.setattr(owner, processor, Processor)
    if hasattr(module, "serialize_cvd_analysis"):
        monkeypatch.setattr(module, "serialize_cvd_analysis", lambda _: None)
    function = getattr(module, name)
    function(**background_arguments(function, tmp_path))
    assert routes.session.scan.status == ScanStatus.COMPLETED
    assert routes.session.scan.progress == 100
    assert routes.session.added[0].compliance_score == 87
    assert routes.session.added[0].issues in ([], {"details": []})
    assert_no_disclosure(caplog, routes.session)


@pytest.mark.parametrize("sitemap", [False, True])
@pytest.mark.parametrize(
    "outcome", ["success", "page_error", "fatal_error", "partial", "empty"]
)
def test_batch_and_sitemap_sensitive_urls(
    routes, monkeypatch, caplog, tmp_path, sitemap, outcome
):
    from src.db.models import ScanStatus

    security = importlib.import_module("src.utils.security")
    monkeypatch.setattr(security, "validate_url_not_private", lambda _: None)
    monkeypatch.setattr(
        security,
        "safe_requests_get",
        lambda *a, **kw: SimpleNamespace(
            content=f"<urlset><url><loc>https://example.edu/{SECRET}</loc></url></urlset>".encode(),
            raise_for_status=lambda: None,
            close=lambda: None,
        ),
    )

    from src.education.web_scanner import WebPageIssue, WebPageScanResult

    issue = WebPageIssue(
        impact="critical",
        criterion="1.1.1",
        description="Synthetic missing alternative",
        help_url="https://example.org/help",
        element='<img src="example.png">',
    )
    page = WebPageScanResult(
        url=URL,
        title="Synthetic page",
        scan_time=1,
        compliance_score=87,
        issues=[issue],
    )
    calls = []

    class Scanner:
        def __init__(self, **kwargs):
            pass

        def scan_website(self, url):
            calls.append(url)
            if outcome == "page_error" or (outcome == "partial" and len(calls) == 2):
                raise RuntimeError(SECRET)
            return SimpleNamespace(
                pages=[] if outcome == "empty" else [page],
                total_scan_time=1,
                overall_compliance_score=87,
                summary={"critical": 1, "total": 1},
            )

        def _group_issues_across_pages(self, pages):
            if outcome == "fatal_error":
                raise RuntimeError(SECRET)
            return []

    module = routes.web_scan_routes
    monkeypatch.setattr(module, "WebScanner", Scanner)
    monkeypatch.setattr(module, "serialize_cvd_analysis", lambda _: None)
    function = (
        module.process_sitemap_scan_background
        if sitemap
        else module.process_batch_web_scan_background
    )
    args = background_arguments(function, tmp_path)
    routes.session.scan.compliance_score = 99
    if outcome == "partial":
        args["urls"] = [URL, URL + "-second"] if not sitemap else args.get("urls", [])
        if sitemap:
            monkeypatch.setattr(
                security,
                "safe_requests_get",
                lambda *a, **kw: SimpleNamespace(
                    content=(
                        f"<urlset><url><loc>{URL.replace('&', '&amp;')}</loc></url>"
                        f"<url><loc>https://example.edu/second</loc></url></urlset>"
                    ).encode(),
                    raise_for_status=lambda: None,
                    close=lambda: None,
                ),
            )
    function(
        **{k: v for k, v in args.items() if k in inspect.signature(function).parameters}
    )
    if outcome != "success":
        assert routes.session.scan.status == ScanStatus.FAILED
        assert routes.session.scan.compliance_score is None
        assert not routes.session.added
    else:
        assert routes.session.scan.status == ScanStatus.COMPLETED
        stored = routes.session.added[0]
        result = stored.structure
        assert result["overall_compliance_score"] == 87
        assert result["issue_summary"]["total"] == 1
        assert result["total_urls_scanned"] == 1
        assert stored.critical_issues == 1
        assert stored.issues["details"][0]["description"] == issue.description
        assert stored.issues["details"][0]["page_url"] == URL
        assert routes.session.scan.pages == 1
    assert_no_disclosure(caplog, routes.session)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["single", "batch", "sitemap"])
@pytest.mark.parametrize("rejected", [False, True])
async def test_queued_web_routes_do_not_echo_urls_or_validation_exceptions(
    routes,
    monkeypatch,
    caplog,
    kind,
    rejected,
):
    from unittest.mock import MagicMock

    module = routes.web_scan_routes
    monkeypatch.setattr(module, "require_feature", AsyncMock())
    security = importlib.import_module("src.utils.security")
    validate = MagicMock(side_effect=ValueError(SECRET) if rejected else None)
    monkeypatch.setattr(security, "validate_url_not_private", validate)
    jobs = importlib.import_module("src.jobs.local_scan_job")
    enqueue = MagicMock()
    monkeypatch.setattr(jobs, "enqueue_local_scan_job", enqueue)
    db = MagicMock()
    db.add.side_effect = lambda scan: setattr(scan, "id", SCAN_ID)
    function, request = {
        "single": (module.scan_website, module.WebScanRequest(url=URL)),
        "batch": (module.batch_scan_websites, module.BatchWebScanRequest(urls=[URL])),
        "sitemap": (
            module.scan_from_sitemap,
            module.SitemapScanRequest(sitemap_url=URL),
        ),
    }[kind]
    call = function(request=request, db=db, api_key_info=(None, SECRET, "workspace"))
    if rejected:
        with pytest.raises(HTTPException) as caught:
            await call
        assert caught.value.status_code == 400
        assert SECRET not in str(caught.value.detail)
        enqueue.assert_not_called()
    else:
        response = await call
        assert response.get("scan_id", response.get("batch_scan_id")) == SCAN_ID
        enqueue.assert_called_once()
        # Input remains available to the durable worker, not in diagnostics.
        assert SECRET in str(enqueue.call_args.kwargs["options"])
    assert_no_disclosure(caplog)


@pytest.mark.parametrize(
    "secondary_outcome", ["success", "guarded_failure", "unexpected_failure"]
)
def test_secondary_engine_keeps_rule_identity_and_failure_refusal(
    routes, monkeypatch, caplog, tmp_path, secondary_outcome
):
    from src.db.models import ScanStatus
    from src.education.web_scanner import WebPageIssue, WebPageScanResult, WebScanResult
    from src.scanners.pa11y_scanner import Pa11yResult, Pa11yScanner, Pa11yScanError
    from src.education.scan_completeness import INCOMPLETE_SCAN_MESSAGE

    issue = WebPageIssue(
        impact="critical",
        criterion="wcag2a",
        rule_id="image-alt",
        description="Image has no alternative",
        help_url="",
        selector="#chart",
        element='<img id="chart" src="chart.png">',
    )
    page = WebPageScanResult(
        url=URL,
        title="Synthetic teaching page",
        scan_time=1,
        compliance_score=87,
        issues=[issue],
    )
    primary_result = WebScanResult(
        root_url=URL,
        pages_scanned=1,
        total_scan_time=1,
        overall_compliance_score=87,
        pages=[page],
        summary={"critical": 1, "total": 1},
    )

    class Scanner:
        def __init__(self, **kwargs):
            pass

        def scan_website(self, url):
            return primary_result

    monkeypatch.setattr(routes.web_scan_routes, "WebScanner", Scanner)
    monkeypatch.setattr(
        routes.web_scan_routes, "serialize_cvd_analysis", lambda _: None
    )
    secondary = Pa11yResult(url=URL, total_issues=0, issues_by_severity={}, issues=[])
    error = (
        Pa11yScanError(SECRET)
        if secondary_outcome == "guarded_failure"
        else RuntimeError(SECRET)
    )
    scan_secondary = (
        AsyncMock(return_value=secondary)
        if secondary_outcome == "success"
        else AsyncMock(side_effect=error)
    )
    monkeypatch.setattr(Pa11yScanner, "scan", scan_secondary)
    args = background_arguments(
        routes.web_scan_routes.process_web_scan_background, tmp_path
    )
    args["mode"] = "comprehensive"
    routes.web_scan_routes.process_web_scan_background(**args)
    scan_secondary.assert_awaited_once_with(URL, runner="htmlcs")
    if secondary_outcome == "success":
        assert routes.session.scan.status == ScanStatus.COMPLETED
        stored = routes.session.added[0]
        assert stored.engines_used == ["axe-core", "htmlcs"]
        assert stored.merged_results["issues"][0]["code"] == "image-alt"
        assert stored.merged_results["issues"][0]["wcag_criteria"] == ["1.1.1"]
        assert stored.merged_results["issues"][0]["context"] == issue.element
        assert stored.pa11y_results["engine"] == "htmlcs"
    else:
        assert routes.session.scan.status == ScanStatus.FAILED
        assert not routes.session.added
        assert routes.session.scan.error_message == INCOMPLETE_SCAN_MESSAGE
    assert_no_disclosure(caplog, routes.session)
