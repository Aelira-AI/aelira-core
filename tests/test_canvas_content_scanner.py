"""
Tests for Canvas content scanner service.

Tests cover:
- HTML fragment wrapping/unwrapping roundtrip
- HTML sanitization (XSS prevention)
- Course content scanning orchestration
- Stale content detection for write-back safety
- Rollback from ContentWritebackLog
"""

import ast
import inspect
from pathlib import Path

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid


class _ConstraintError(Exception):
    def __init__(self, message: str, constraint_name: str):
        super().__init__(message)
        self.diag = SimpleNamespace(constraint_name=constraint_name)


def _enumeration_enqueue(db):
    """Keep enumeration tests focused while preserving durable queue outputs."""
    from src.db.models import CloudJobQueue, CloudJobStatus

    def enqueue(bound_db, **kwargs):
        assert bound_db is db
        job = CloudJobQueue(
            id=str(uuid.uuid4()),
            department_id=kwargs["department_id"],
            job_type=kwargs["job_type"],
            payload=kwargs["payload"],
            dedupe_key=kwargs["dedupe_key"],
            provider=kwargs.get("provider"),
            credential_id=kwargs.get("credential_id"),
            cloud_file_id=kwargs.get("cloud_file_id"),
            provider_file_id=kwargs.get("provider_file_id"),
            status=CloudJobStatus.PENDING.value,
        )
        db.add(job)
        return job

    return patch(
        "src.education.canvas_content_scanner.enqueue_cloud_job",
        side_effect=enqueue,
    )


# ---------------------------------------------------------------------------
# TestHtmlWrapping
# ---------------------------------------------------------------------------


class TestHtmlWrapping:
    """Test _wrap_html_fragment and _unwrap_html_fragment roundtrip."""

    def test_wrap_simple_paragraph(self):
        from src.education.canvas_content_scanner import _wrap_html_fragment

        fragment = "<p>Hello world</p>"
        wrapped = _wrap_html_fragment(fragment)
        assert "<!DOCTYPE html>" in wrapped
        assert '<html lang="en">' in wrapped
        assert "<title>Scan</title>" in wrapped
        assert "<body>" in wrapped
        assert fragment in wrapped
        assert "</body>" in wrapped
        assert "</html>" in wrapped

    def test_unwrap_extracts_body(self):
        from src.education.canvas_content_scanner import (
            _wrap_html_fragment,
            _unwrap_html_fragment,
        )

        fragment = "<p>Hello world</p>"
        wrapped = _wrap_html_fragment(fragment)
        result = _unwrap_html_fragment(wrapped)
        assert "<p>Hello world</p>" in result

    def test_roundtrip_preserves_content(self):
        from src.education.canvas_content_scanner import (
            _wrap_html_fragment,
            _unwrap_html_fragment,
        )

        fragment = '<h2>Title</h2><p>Body with <a href="#">link</a></p>'
        wrapped = _wrap_html_fragment(fragment)
        result = _unwrap_html_fragment(wrapped)
        assert "Title" in result
        assert '<a href="#">link</a>' in result

    def test_roundtrip_complex_html(self):
        from src.education.canvas_content_scanner import (
            _wrap_html_fragment,
            _unwrap_html_fragment,
        )

        fragment = (
            '<div class="assignment">'
            '<img src="photo.jpg" alt="">'
            "<table><tr><td>Data</td></tr></table>"
            "</div>"
        )
        wrapped = _wrap_html_fragment(fragment)
        result = _unwrap_html_fragment(wrapped)
        assert "photo.jpg" in result
        assert "<table>" in result

    def test_wrap_empty_fragment(self):
        from src.education.canvas_content_scanner import (
            _wrap_html_fragment,
            _unwrap_html_fragment,
        )

        wrapped = _wrap_html_fragment("")
        result = _unwrap_html_fragment(wrapped)
        # Empty body should roundtrip to empty or whitespace-only
        assert result.strip() == ""

    def test_unwrap_returns_inner_body_only(self):
        from src.education.canvas_content_scanner import _unwrap_html_fragment

        doc = (
            "<!DOCTYPE html><html lang='en'><head><title>X</title></head>"
            "<body><p>Only this</p></body></html>"
        )
        result = _unwrap_html_fragment(doc)
        assert "<p>Only this</p>" in result
        assert "<html" not in result
        assert "<head>" not in result
        assert "<title>" not in result


# ---------------------------------------------------------------------------
# TestHtmlSanitization
# ---------------------------------------------------------------------------


class TestHtmlSanitization:
    """Test _sanitize_html strips dangerous content, preserves safe HTML."""

    def test_strips_script_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<p>Hello</p><script>alert("xss")</script>'
        result = _sanitize_html(html)
        assert "<script>" not in result
        assert "alert" not in result
        assert "<p>Hello</p>" in result

    def test_strips_on_event_handlers(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<p onclick="alert(1)" onmouseover="evil()">Text</p>'
        result = _sanitize_html(html)
        assert "onclick" not in result
        assert "onmouseover" not in result
        assert "Text" in result

    def test_strips_javascript_urls(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<a href="javascript:alert(1)">Click me</a>'
        result = _sanitize_html(html)
        assert "javascript:" not in result
        assert "Click me" in result

    def test_strips_iframe_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<p>Before</p><iframe src="evil.com"></iframe><p>After</p>'
        result = _sanitize_html(html)
        assert "<iframe" not in result
        assert "Before" in result
        assert "After" in result

    def test_preserves_safe_html(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = (
            "<h2>Title</h2>"
            "<p>Paragraph with <strong>bold</strong> and <em>italic</em>.</p>"
            "<ul><li>Item 1</li><li>Item 2</li></ul>"
            '<img src="photo.jpg" alt="A photo">'
            '<a href="https://example.com">Safe link</a>'
        )
        result = _sanitize_html(html)
        assert "<h2>Title</h2>" in result
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result
        assert "<ul>" in result
        assert "<img" in result
        assert 'alt="A photo"' in result
        assert 'href="https://example.com"' in result

    def test_strips_nested_script(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = "<div><p><script>bad()</script></p></div>"
        result = _sanitize_html(html)
        assert "<script>" not in result
        assert "<div>" in result

    def test_strips_mixed_case_event_handlers(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<p ONCLICK="bad()" OnLoad="bad()">Text</p>'
        result = _sanitize_html(html)
        # BeautifulSoup lowercases attrs, so check lowercase
        assert "onclick" not in result.lower()
        assert "onload" not in result.lower()
        assert "Text" in result

    def test_strips_object_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<p>Before</p><object data="evil.swf"></object><p>After</p>'
        result = _sanitize_html(html)
        assert "<object" not in result
        assert "Before" in result
        assert "After" in result

    def test_strips_embed_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<p>Safe</p><embed src="evil.swf"><p>Also safe</p>'
        result = _sanitize_html(html)
        assert "<embed" not in result
        assert "Safe" in result

    def test_strips_base_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<base href="https://evil.com"><p>Content</p>'
        result = _sanitize_html(html)
        assert "<base" not in result
        assert "Content" in result

    def test_strips_meta_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<meta http-equiv="refresh" content="0;url=evil"><p>Content</p>'
        result = _sanitize_html(html)
        assert "<meta" not in result
        assert "Content" in result

    def test_strips_style_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<style>body { background: url("evil") }</style><p>Content</p>'
        result = _sanitize_html(html)
        assert "<style" not in result
        assert "Content" in result

    def test_strips_link_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<link rel="stylesheet" href="evil.css"><p>Content</p>'
        result = _sanitize_html(html)
        assert "<link" not in result
        assert "Content" in result

    def test_strips_form_tags(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<form action="https://evil.com/steal"><input type="text"></form><p>Safe</p>'
        result = _sanitize_html(html)
        assert "<form" not in result
        assert "Safe" in result

    def test_strips_vbscript_urls(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<a href="vbscript:MsgBox(1)">Click</a>'
        result = _sanitize_html(html)
        assert "vbscript:" not in result
        assert "Click" in result

    def test_strips_data_urls(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<a href="data:text/html,<script>alert(1)</script>">Click</a>'
        result = _sanitize_html(html)
        assert "data:" not in result
        assert "Click" in result

    def test_strips_xlink_href_javascript(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<svg><use xlink:href="javascript:alert(1)"></use></svg>'
        result = _sanitize_html(html)
        assert "javascript:" not in result

    def test_strips_data_url_in_src(self):
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<img src="data:text/html,<script>alert(1)</script>">'
        result = _sanitize_html(html)
        assert "data:" not in result

    def test_preserves_safe_data_attributes(self):
        """data- custom attributes should NOT be removed (they're not URL-bearing)."""
        from src.education.canvas_content_scanner import _sanitize_html

        html = '<div data-id="123">Content</div>'
        result = _sanitize_html(html)
        assert 'data-id="123"' in result
        assert "Content" in result


# ---------------------------------------------------------------------------
# TestCanvasContentScanner
# ---------------------------------------------------------------------------


class TestCanvasContentScanner:
    """Test scan_course_content orchestration."""

    @pytest.mark.asyncio
    async def test_scan_course_content_calls_all_five_list_methods(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        # Set up mock returns — each type returns one item with body content
        from src.integrations.canvas.content_models import (
            CanvasPageInfo,
            CanvasAssignmentInfo,
            CanvasAnnouncementInfo,
            CanvasQuizInfo,
            CanvasDiscussionInfo,
        )

        now = datetime.now(timezone.utc)

        page_info = CanvasPageInfo(
            page_id="1",
            title="Page 1",
            url_slug="page-1",
            body="<p>Page body</p>",
            published=True,
            updated_at=now,
        )
        canvas_client.list_course_pages = AsyncMock(return_value=[page_info])
        canvas_client.get_page = AsyncMock(return_value=page_info)
        canvas_client.list_course_assignments = AsyncMock(
            return_value=[
                CanvasAssignmentInfo(
                    id="2",
                    name="Assignment 1",
                    description="<p>Assignment desc</p>",
                    published=True,
                    updated_at=now,
                )
            ]
        )
        canvas_client.list_course_announcements = AsyncMock(
            return_value=[
                CanvasAnnouncementInfo(
                    id="3",
                    title="Announcement 1",
                    message="<p>Announcement body</p>",
                    posted_at=now,
                    updated_at=now,
                )
            ]
        )
        canvas_client.list_course_quizzes = AsyncMock(
            return_value=[
                CanvasQuizInfo(
                    id="4",
                    title="Quiz 1",
                    description="<p>Quiz desc</p>",
                    published=True,
                    updated_at=now,
                )
            ]
        )
        canvas_client.list_course_discussions = AsyncMock(
            return_value=[
                CanvasDiscussionInfo(
                    id="5",
                    title="Discussion 1",
                    message="<p>Discussion body</p>",
                    posted_at=now,
                    updated_at=now,
                )
            ]
        )

        # Mock db.query to return None (no existing CloudFile)
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        db.query.return_value = mock_query

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        with _enumeration_enqueue(db) as enqueue:
            result = await scanner.scan_course_content("COURSE123")

        # All 5 list methods should have been called
        canvas_client.list_course_pages.assert_awaited_once_with("COURSE123")
        canvas_client.list_course_assignments.assert_awaited_once_with("COURSE123")
        canvas_client.list_course_announcements.assert_awaited_once_with("COURSE123")
        canvas_client.list_course_quizzes.assert_awaited_once_with("COURSE123")
        canvas_client.list_course_discussions.assert_awaited_once_with("COURSE123")

        # Should have created 5 CloudFile records (one per content item)
        assert db.add.call_count >= 5
        for call in enqueue.call_args_list:
            if call.kwargs["payload"]["scan_kind"] != "canvas_content":
                continue
            content_source = call.kwargs["payload"]["content_source"]
            assert f":COURSE123:{content_source}:" in call.kwargs["dedupe_key"]

    @pytest.mark.asyncio
    async def test_scan_course_content_skips_empty_body(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        from src.integrations.canvas.content_models import CanvasPageInfo

        now = datetime.now(timezone.utc)

        # Return one page with empty body
        empty_page = CanvasPageInfo(
            page_id="1",
            title="Empty Page",
            url_slug="empty-page",
            body="",
            published=True,
            updated_at=now,
        )
        canvas_client.list_course_pages = AsyncMock(return_value=[empty_page])
        canvas_client.get_page = AsyncMock(return_value=empty_page)
        canvas_client.list_course_assignments = AsyncMock(return_value=[])
        canvas_client.list_course_announcements = AsyncMock(return_value=[])
        canvas_client.list_course_quizzes = AsyncMock(return_value=[])
        canvas_client.list_course_discussions = AsyncMock(return_value=[])

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        db.query.return_value = mock_query

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        with _enumeration_enqueue(db):
            result = await scanner.scan_course_content("COURSE123")

        # Empty content should be skipped — no CloudFile created
        assert db.add.call_count == 0

    @pytest.mark.asyncio
    async def test_scan_course_content_upserts_existing_cloud_file(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        from src.integrations.canvas.content_models import CanvasPageInfo

        now = datetime.now(timezone.utc)

        existing_page = CanvasPageInfo(
            page_id="1",
            title="Existing Page",
            url_slug="existing-page",
            body="<p>Updated body</p>",
            published=True,
            updated_at=now,
        )
        canvas_client.list_course_pages = AsyncMock(return_value=[existing_page])
        canvas_client.get_page = AsyncMock(return_value=existing_page)
        canvas_client.list_course_assignments = AsyncMock(return_value=[])
        canvas_client.list_course_announcements = AsyncMock(return_value=[])
        canvas_client.list_course_quizzes = AsyncMock(return_value=[])
        canvas_client.list_course_discussions = AsyncMock(return_value=[])

        # Mock existing CloudFile found in DB
        existing_file = MagicMock()
        existing_file.id = "existing-page"
        existing_file.content_body = "<p>Old body</p>"
        existing_file.content_updated_at = now - timedelta(days=1)
        existing_file.remediated_body = "<p>Old remediation</p>"
        existing_file.has_remediated_version = True
        existing_file.current_remediation_artifact_id = "artifact-old"
        existing_file.writeback_status = "written_back"
        existing_file.writeback_at = now - timedelta(hours=1)
        existing_file.needs_rescan = False
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = existing_file
        db.query.return_value = mock_query

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        with _enumeration_enqueue(db):
            result = await scanner.scan_course_content("COURSE123")

        # Should update existing record, not add new one
        assert existing_file.content_body == "<p>Updated body</p>"
        assert existing_file.remediated_body is None
        assert existing_file.has_remediated_version is False
        assert existing_file.current_remediation_artifact_id is None
        assert existing_file.writeback_status is None
        assert existing_file.writeback_at is None
        assert existing_file.needs_rescan is True


# ---------------------------------------------------------------------------
# TestCanvasContentScannerFiles — Files section is course content too
# ---------------------------------------------------------------------------


def _make_file_info(**overrides):
    from src.integrations.canvas import CanvasFileInfo

    now = datetime.now(timezone.utc)
    defaults = dict(
        id="file-1",
        display_name="Syllabus.pdf",
        filename="Syllabus.pdf",
        content_type="application/pdf",
        size=123456,
        url="https://canvas.example.edu/files/file-1/download",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return CanvasFileInfo(**defaults)


class TestCanvasContentScannerFiles:
    """Files are enumerated, upserted, and queued for scan alongside the
    5 HTML content types — via a different pipeline (CloudJobQueue), since
    files have no HTML content_body for axe-core to run on directly."""

    def _scanner_with_empty_html_types(self, canvas_client, db, **kwargs):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client.list_course_pages = AsyncMock(return_value=[])
        canvas_client.list_course_assignments = AsyncMock(return_value=[])
        canvas_client.list_course_announcements = AsyncMock(return_value=[])
        canvas_client.list_course_quizzes = AsyncMock(return_value=[])
        canvas_client.list_course_discussions = AsyncMock(return_value=[])

        return CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=kwargs.get("department_id", str(uuid.uuid4())),
            credential_id=kwargs.get("credential_id", str(uuid.uuid4())),
        )

    def test_file_upsert_lookup_uses_course_and_normalized_file_source(self):
        canvas_client = AsyncMock()
        db = MagicMock()
        scanner = self._scanner_with_empty_html_types(canvas_client, db)
        query = MagicMock()
        query.filter.return_value = query
        query.first.return_value = None
        db.query.return_value = query

        file_info = _make_file_info(id="7")
        result = scanner._upsert_file_cloud_file("COURSE123", file_info)

        predicates = " ".join(str(arg) for arg in query.filter.call_args.args)
        assert "cloud_files.provider_parent_id" in predicates
        assert "cloud_files.content_source" in predicates
        assert result.provider_version == file_info.updated_at.isoformat()
        assert result.provider_modified_at == file_info.updated_at

    def test_file_upsert_recovers_the_concurrent_composite_identity_winner(self):
        from sqlalchemy.exc import IntegrityError

        canvas_client = AsyncMock()
        db = MagicMock()
        scanner = self._scanner_with_empty_html_types(canvas_client, db)
        winner = MagicMock(id="winner")
        query = MagicMock()
        query.filter.return_value = query
        query.first.side_effect = [None, winner]
        db.query.return_value = query
        original = _ConstraintError(
            "duplicate identity", "uq_cloud_files_canvas_content_identity"
        )
        db.flush.side_effect = IntegrityError("duplicate identity", {}, original)

        result = scanner._upsert_file_cloud_file("COURSE123", _make_file_info(id="7"))

        assert result is winner
        db.begin_nested.return_value.rollback.assert_called_once_with()

    def test_file_upsert_rethrows_unrelated_integrity_error_even_with_identity_winner(
        self,
    ):
        from sqlalchemy.exc import IntegrityError

        canvas_client = AsyncMock()
        db = MagicMock()
        scanner = self._scanner_with_empty_html_types(canvas_client, db)
        query = MagicMock()
        query.filter.return_value = query
        query.first.side_effect = [None, MagicMock(id="winner")]
        db.query.return_value = query
        original = _ConstraintError("foreign key failure", "fk_cloud_files_department")
        db.flush.side_effect = IntegrityError("foreign key failure", {}, original)

        with pytest.raises(IntegrityError, match="foreign key failure"):
            scanner._upsert_file_cloud_file("COURSE123", _make_file_info(id="7"))

        db.begin_nested.return_value.rollback.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_files_enumerated_and_upserted_alongside_pages(self):
        canvas_client = AsyncMock()
        db = MagicMock()

        page_info_mod = __import__(
            "src.integrations.canvas.content_models", fromlist=["CanvasPageInfo"]
        )
        now = datetime.now(timezone.utc)
        page = page_info_mod.CanvasPageInfo(
            page_id="1",
            title="Page 1",
            url_slug="page-1",
            body="<p>Page body</p>",
            published=True,
            updated_at=now,
        )
        canvas_client.list_course_pages = AsyncMock(return_value=[page])
        canvas_client.get_page = AsyncMock(return_value=page)
        canvas_client.list_course_assignments = AsyncMock(return_value=[])
        canvas_client.list_course_announcements = AsyncMock(return_value=[])
        canvas_client.list_course_quizzes = AsyncMock(return_value=[])
        canvas_client.list_course_discussions = AsyncMock(return_value=[])
        canvas_client.list_course_files = AsyncMock(
            return_value=[_make_file_info(id="file-1", filename="Syllabus.pdf")]
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        db.query.return_value = mock_query

        from src.education.canvas_content_scanner import CanvasContentScanner

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=str(uuid.uuid4()),
            credential_id=str(uuid.uuid4()),
        )

        with _enumeration_enqueue(db):
            result = await scanner.scan_course_content("COURSE123")

        canvas_client.list_course_files.assert_awaited_once_with("COURSE123")
        assert result["counts"]["file"] == 1
        assert result["counts"]["page"] == 1
        assert len(result["file_scan_jobs"]) == 1
        # HTML content and uploaded files remain separate durable scan kinds;
        # document files must never enter the HTML-only content list.
        assert (
            result["file_scan_jobs"][0]["cloud_file_id"] not in result["cloud_file_ids"]
        )

    @pytest.mark.asyncio
    async def test_new_file_upserted_with_content_source_file(self):
        canvas_client = AsyncMock()
        db = MagicMock()
        scanner = self._scanner_with_empty_html_types(canvas_client, db)
        canvas_client.list_course_files = AsyncMock(
            return_value=[_make_file_info(id="file-1", filename="Notes.docx")]
        )

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        db.query.return_value = mock_query

        with _enumeration_enqueue(db):
            result = await scanner.scan_course_content("COURSE123")

        # One CloudFile add + one CloudJobQueue add
        added = [call.args[0] for call in db.add.call_args_list]
        cloud_files = [obj for obj in added if type(obj).__name__ == "CloudFile"]
        jobs = [obj for obj in added if type(obj).__name__ == "CloudJobQueue"]
        assert len(cloud_files) == 1
        assert cloud_files[0].content_source == "file"
        assert cloud_files[0].file_type == "docx"
        assert cloud_files[0].mime_type == "application/pdf"
        assert cloud_files[0].provider_parent_id == "COURSE123"
        assert len(jobs) == 1
        assert jobs[0].job_type == "scan"
        assert jobs[0].cloud_file_id == cloud_files[0].id
        # The job's id must be the same one handed back in file_scan_jobs so
        # the durable worker can claim the exact persisted scan request.
        assert len(result["file_scan_jobs"]) == 1
        assert result["file_scan_jobs"][0]["job_id"] == jobs[0].id
        assert result["file_scan_jobs"][0]["cloud_file_id"] == cloud_files[0].id

    @pytest.mark.asyncio
    async def test_rescan_upserts_existing_file_row_no_duplicate(self):
        canvas_client = AsyncMock()
        db = MagicMock()
        scanner = self._scanner_with_empty_html_types(canvas_client, db)
        canvas_client.list_course_files = AsyncMock(
            return_value=[
                _make_file_info(
                    id="file-1", display_name="Notes.docx", filename="Notes.docx"
                )
            ]
        )

        existing_file = MagicMock()
        existing_file.id = "existing-cf-id"
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = existing_file
        db.query.return_value = mock_query

        with _enumeration_enqueue(db):
            result = await scanner.scan_course_content("COURSE123")

        # No new CloudFile created for the file — only the CloudJobQueue row
        added = [call.args[0] for call in db.add.call_args_list]
        cloud_files = [obj for obj in added if type(obj).__name__ == "CloudFile"]
        jobs = [obj for obj in added if type(obj).__name__ == "CloudJobQueue"]
        assert len(cloud_files) == 0
        assert existing_file.content_source == "file"
        assert existing_file.file_name == "Notes.docx"
        assert len(jobs) == 1
        assert jobs[0].cloud_file_id == "existing-cf-id"
        assert result["file_scan_jobs"] == [
            {"job_id": jobs[0].id, "cloud_file_id": "existing-cf-id"}
        ]

    @pytest.mark.asyncio
    async def test_files_scan_job_queued_for_durable_document_worker(self):
        # Regression guard: files must not flow through cloud_file_ids, where
        # the HTML scanner would silently no-op with "No content body".
        canvas_client = AsyncMock()
        db = MagicMock()
        scanner = self._scanner_with_empty_html_types(canvas_client, db)
        canvas_client.list_course_files = AsyncMock(
            return_value=[_make_file_info(id="file-1")]
        )
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        db.query.return_value = mock_query

        with _enumeration_enqueue(db):
            result = await scanner.scan_course_content("COURSE123")

        assert result["cloud_file_ids"] == []
        assert len(result["file_scan_jobs"]) == 1
        jobs = [
            call.args[0]
            for call in db.add.call_args_list
            if type(call.args[0]).__name__ == "CloudJobQueue"
        ]
        assert len(jobs) == 1
        assert jobs[0].provider == "canvas"
        assert jobs[0].provider_file_id == "file-1"
        assert jobs[0].status == "pending"
        assert ":COURSE123:file:file-1:" in jobs[0].dedupe_key


# ---------------------------------------------------------------------------
# TestStaleDetection
# ---------------------------------------------------------------------------


class TestStaleDetection:
    """Test stale content detection during write-back."""

    @pytest.fixture(autouse=True)
    def current_candidate(self):
        with patch(
            "src.education.canvas_content_scanner.lock_current_canvas_content_candidate",
            side_effect=lambda _db, row: row,
        ):
            yield

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing_side", ["stored", "current"])
    async def test_write_back_fails_closed_when_version_baseline_is_missing(
        self, missing_side
    ):
        from src.education.canvas_content_scanner import CanvasContentScanner

        baseline = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)
        cloud_file = MagicMock()
        cloud_file.id = "cloud-1"
        cloud_file.content_source = "page"
        cloud_file.content_slug = "test-page"
        cloud_file.provider_parent_id = "COURSE123"
        cloud_file.content_updated_at = None if missing_side == "stored" else baseline
        cloud_file.remediated_body = "<p>Fixed content</p>"
        cloud_file.writeback_status = "approved"
        cloud_file.needs_rescan = False
        canvas_client = AsyncMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id="dept-1",
            credential_id="cred-1",
        )
        scanner._get_canvas_updated_at = AsyncMock(
            return_value=None if missing_side == "current" else baseline
        )

        result = await scanner.write_back_content(cloud_file, approved_by="user-1")

        assert result["success"] is False
        assert result["stale"] is True
        assert "re-scan" in result["error"].lower()
        canvas_client.update_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_write_back_returns_stale_error_when_canvas_updated(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        from src.integrations.canvas.content_models import CanvasPageInfo

        scan_time = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)
        newer_time = datetime(2026, 3, 21, 12, 0, 0, tzinfo=timezone.utc)

        # Cloud file was scanned at scan_time
        cloud_file = MagicMock()
        cloud_file.content_source = "page"
        cloud_file.content_slug = "test-page"
        cloud_file.provider_parent_id = "COURSE123"
        cloud_file.content_updated_at = scan_time
        cloud_file.remediated_body = "<p>Fixed content</p>"
        cloud_file.writeback_status = "approved"
        cloud_file.needs_rescan = False

        # Canvas now has a newer updated_at
        canvas_client.get_page = AsyncMock(
            return_value=CanvasPageInfo(
                page_id="1",
                title="Test Page",
                url_slug="test-page",
                body="<p>Someone edited this</p>",
                published=True,
                updated_at=newer_time,
            )
        )

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        result = await scanner.write_back_content(
            cloud_file, approved_by="admin@uni.edu"
        )

        assert result["success"] is False
        assert result["stale"] is True
        assert (
            "stale" in result["error"].lower() or "modified" in result["error"].lower()
        )

    @pytest.mark.asyncio
    async def test_write_back_succeeds_when_content_not_stale(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        from src.integrations.canvas.content_models import CanvasPageInfo

        scan_time = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)

        cloud_file = MagicMock()
        cloud_file.id = str(uuid.uuid4())
        cloud_file.content_source = "page"
        cloud_file.content_slug = "test-page"
        cloud_file.content_body = "<p>Original</p>"
        cloud_file.provider_parent_id = "COURSE123"
        cloud_file.content_updated_at = scan_time
        cloud_file.remediated_body = "<p>Fixed content</p>"
        cloud_file.writeback_status = "approved"
        cloud_file.needs_rescan = False

        # Canvas still has the same updated_at
        canvas_client.get_page = AsyncMock(
            return_value=CanvasPageInfo(
                page_id="1",
                title="Test Page",
                url_slug="test-page",
                body="<p>Original</p>",
                published=True,
                updated_at=scan_time,
            )
        )
        canvas_client.update_page = AsyncMock(
            return_value=CanvasPageInfo(
                page_id="1",
                title="Test Page",
                url_slug="test-page",
                body="<p>Fixed content</p>",
                published=True,
                updated_at=datetime.now(timezone.utc),
            )
        )

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        result = await scanner.write_back_content(
            cloud_file, approved_by="admin@uni.edu"
        )

        assert result["success"] is True
        canvas_client.update_page.assert_awaited_once()
        # Should have created a ContentWritebackLog
        db.add.assert_called()

    @pytest.mark.asyncio
    async def test_write_back_rejects_candidate_replaced_after_intent_commit(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        scan_time = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)
        original = MagicMock()
        original.id = str(uuid.uuid4())
        original.content_source = "page"
        original.content_slug = "test-page"
        original.provider_file_id = "page-1"
        original.provider_parent_id = "COURSE123"
        original.content_updated_at = scan_time
        original.content_body = "<p>Original</p>"
        original.remediated_body = "<p>First candidate</p>"
        original.writeback_status = "approved"
        original.needs_rescan = False
        original.provider_metadata = {
            "canvas_content_candidate": {"fingerprint": "a" * 64}
        }
        replacement = MagicMock()
        replacement.content_body = original.content_body
        replacement.remediated_body = "<p>Replacement candidate</p>"
        replacement.writeback_status = "approved"
        replacement.provider_metadata = {
            "canvas_content_candidate": {"fingerprint": "b" * 64}
        }
        persisted_log = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        db.get.return_value = persisted_log
        scanner = CanvasContentScanner(
            canvas_client=AsyncMock(),
            db=db,
            department_id="dept-1",
            credential_id="cred-1",
        )
        scanner._get_canvas_updated_at = AsyncMock(return_value=scan_time)
        scanner._update_canvas_content = AsyncMock()

        with patch(
            "src.education.canvas_content_scanner.lock_current_canvas_content_candidate",
            side_effect=[original, replacement],
        ):
            result = await scanner.write_back_content(original, approved_by="user-1")

        assert result == {
            "success": False,
            "stale": True,
            "error": "Remediated content changed before write-back",
        }
        scanner._update_canvas_content.assert_not_awaited()
        assert persisted_log.reconciliation_status == "manual_required"
        assert (
            persisted_log.reconciliation_last_error
            == "canvas_candidate_changed_before_writeback"
        )

    @pytest.mark.asyncio
    async def test_write_back_assignment_calls_update_assignment(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        from src.integrations.canvas.content_models import CanvasAssignmentInfo

        scan_time = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)

        cloud_file = MagicMock()
        cloud_file.id = str(uuid.uuid4())
        cloud_file.content_source = "assignment"
        cloud_file.provider_file_id = "42"
        cloud_file.content_body = "<p>Original desc</p>"
        cloud_file.provider_parent_id = "COURSE123"
        cloud_file.content_updated_at = scan_time
        cloud_file.remediated_body = "<p>Fixed desc</p>"
        cloud_file.writeback_status = "approved"
        cloud_file.needs_rescan = False

        canvas_client.get_assignment = AsyncMock(
            return_value=CanvasAssignmentInfo(
                id="42",
                name="Assignment",
                description="<p>Original desc</p>",
                published=True,
                updated_at=scan_time,
            )
        )
        canvas_client.update_assignment = AsyncMock(
            return_value=CanvasAssignmentInfo(
                id="42",
                name="Assignment",
                description="<p>Fixed desc</p>",
                published=True,
                updated_at=datetime.now(timezone.utc),
            )
        )

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        result = await scanner.write_back_content(
            cloud_file, approved_by="admin@uni.edu"
        )

        assert result["success"] is True
        canvas_client.update_assignment.assert_awaited_once_with(
            "COURSE123", "42", description="<p>Fixed desc</p>"
        )


# ---------------------------------------------------------------------------
# TestRollback
# ---------------------------------------------------------------------------


class TestRollback:
    """Test rollback_content restores original body from ContentWritebackLog."""

    @pytest.mark.asyncio
    async def test_rollback_reads_original_from_log_and_updates_canvas(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        from src.integrations.canvas.content_models import CanvasPageInfo

        # Set up cloud file
        cloud_file = MagicMock()
        cloud_file.id = str(uuid.uuid4())
        cloud_file.content_source = "page"
        cloud_file.content_slug = "test-page"
        cloud_file.provider_parent_id = "COURSE123"

        # Set up writeback log with original body
        writeback_log = MagicMock()
        writeback_log.original_body = "<p>Original content before fix</p>"
        writeback_log.rollback_status = None

        # Mock db query to find the most recent writeback log
        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.first.return_value = (
            writeback_log
        )
        db.query.return_value = mock_query

        canvas_client.update_page = AsyncMock(
            return_value=CanvasPageInfo(
                page_id="1",
                title="Test Page",
                url_slug="test-page",
                body="<p>Original content before fix</p>",
                published=True,
                updated_at=datetime.now(timezone.utc),
            )
        )

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        result = await scanner.rollback_content(cloud_file)

        assert result["success"] is True
        canvas_client.update_page.assert_awaited_once_with(
            "COURSE123",
            "test-page",
            body="<p>Original content before fix</p>",
            message=None,
        )
        # Writeback log should be marked as rolled back
        assert writeback_log.rollback_status == "rolled_back"

    @pytest.mark.asyncio
    async def test_rollback_discussion_calls_update_discussion(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        cloud_file = MagicMock()
        cloud_file.id = str(uuid.uuid4())
        cloud_file.content_source = "discussion"
        cloud_file.provider_file_id = "99"
        cloud_file.provider_parent_id = "COURSE123"

        writeback_log = MagicMock()
        writeback_log.original_body = "<p>Original discussion</p>"
        writeback_log.rollback_status = None

        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.first.return_value = (
            writeback_log
        )
        db.query.return_value = mock_query

        from src.integrations.canvas.content_models import CanvasDiscussionInfo

        canvas_client.update_discussion = AsyncMock(
            return_value=CanvasDiscussionInfo(
                id="99",
                title="Discussion",
                message="<p>Original discussion</p>",
                posted_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        result = await scanner.rollback_content(cloud_file)

        assert result["success"] is True
        canvas_client.update_discussion.assert_awaited_once_with(
            "COURSE123", "99", message="<p>Original discussion</p>"
        )

    @pytest.mark.asyncio
    async def test_rollback_fails_when_no_writeback_log(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        cloud_file = MagicMock()
        cloud_file.id = str(uuid.uuid4())
        cloud_file.content_source = "page"
        cloud_file.content_slug = "test-page"
        cloud_file.provider_parent_id = "COURSE123"

        # No writeback log found
        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.first.return_value = None
        db.query.return_value = mock_query

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        result = await scanner.rollback_content(cloud_file)

        assert result["success"] is False


# ---------------------------------------------------------------------------
# TestScanContentItemNullUserId
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("with_client", [False, True])
async def test_content_remediation_uses_only_injected_remediation_client(with_client):
    from src.education.canvas_content_scanner import CanvasContentScanner

    db = MagicMock()
    scan_result = SimpleNamespace(issues=[{"id": "link-name", "nodes": [{}]}])
    db.query.return_value.filter.return_value.first.return_value = scan_result
    cloud_file = MagicMock(
        id="file-1",
        file_name="Page",
        content_body="<a href='/course'>read more</a>",
        last_scan_id="scan-1",
        last_compliance_score=80.0,
    )
    remediation_client = MagicMock() if with_client else None
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")

    fake_result = SimpleNamespace(
        success=True,
        output_file=None,
        fixed_count=0,
        manual_count=1,
        failed_count=0,
        remediated_compliance_score=None,
    )
    with (
        patch.object(
            scanner,
            "_describe_images",
            new_callable=AsyncMock,
            side_effect=AssertionError("alt text must not run without its client"),
        ),
        patch.object(
            scanner, "_verify_remediation", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator"
        ) as remediator_cls,
    ):
        remediator_cls.return_value.remediate.return_value = fake_result
        result = await scanner.remediate_content_item(
            cloud_file, remediation_client=remediation_client
        )

    assert result["success"] is True
    kwargs = remediator_cls.call_args.kwargs
    assert (
        kwargs["ai_client"] is None
        if not with_client
        else (kwargs["ai_client"].wrapped_client is remediation_client)
    )
    assert kwargs["config"].use_ai is with_client


@pytest.mark.parametrize(
    "raw_issue",
    [
        {"category": "Alternative Text"},
        {"category": "image"},
        {"category": "image_of_text"},
        {"type": "alternative-text"},
        {"type": "image alt text"},
        {"rule": "image-description"},
        {"id": "image-alt"},
        {"id": "input image alt"},
        {"id": "svg-img-alt"},
        {"id": "role img alt"},
        {"id": "object-alt"},
        {"id": "AREA ALT"},
        {"id": "figure-alt"},
        {"id": "missing alt text"},
        {"id": "missing-figure-caption"},
        {"id": "missing image description"},
    ],
)
def test_is_alt_text_issue_recognizes_repository_aliases(raw_issue):
    from src.education.canvas_content_scanner import _is_alt_text_issue

    assert _is_alt_text_issue(raw_issue, {"category": "other"}) is True


@pytest.mark.parametrize(
    "raw_issue,normalized_issue",
    [
        ({"category": "image_contrast"}, {"category": "image_contrast"}),
        ({"type": "image processing"}, {"category": "other"}),
        ({"id": "decorative-image"}, {"category": "other"}),
        ({"rule": "link-name"}, {"category": "link"}),
        ({"category": "alternative"}, {"category": "alternative"}),
    ],
)
def test_is_alt_text_issue_does_not_overclassify_unrelated_categories(
    raw_issue, normalized_issue
):
    from src.education.canvas_content_scanner import _is_alt_text_issue

    assert _is_alt_text_issue(raw_issue, normalized_issue) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "alt_issue",
    [
        {"category": "Alternative Text"},
        {"category": "image"},
        {"category": "image_of_text"},
        {"type": "alternative-text"},
        {"type": "image alt text"},
        {"rule": "image-description"},
        {"id": "image-alt"},
        {"id": "input image alt"},
        {"id": "svg-img-alt"},
        {"id": "role img alt"},
        {"id": "object-alt"},
        {"id": "AREA ALT"},
        {"id": "figure-alt"},
        {"id": "missing alt text"},
        {"id": "missing-figure-caption"},
        {"id": "missing image description"},
    ],
)
async def test_alt_text_issues_never_reach_html_remediation_client(alt_issue):
    """Alt aliases stay isolated from the remediation-purpose model."""
    issues = [
        {"id": "custom-alt-rule", "nodes": [{}], **alt_issue},
        {"id": "link-name", "nodes": [{}]},
    ]
    expected_remediation_ids = ["link-name"]
    expected_manual = 1
    from src.education.canvas_content_scanner import CanvasContentScanner

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=issues
    )
    cloud_file = MagicMock(
        id="file-1",
        file_name="Page",
        content_body='<img src="/files/42"><a href="/course">read more</a>',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
    )
    remediation_client = MagicMock(provider="gemini", purpose="remediation")
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    fake_result = SimpleNamespace(
        success=True,
        output_file=None,
        fixed_count=0,
        manual_count=0,
        failed_count=0,
        remediated_compliance_score=None,
    )

    with (
        patch.object(
            scanner,
            "_describe_images",
            new_callable=AsyncMock,
            side_effect=AssertionError("alt client was absent"),
        ),
        patch.object(
            scanner, "_verify_remediation", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator"
        ) as remediator_cls,
    ):
        remediator_cls.return_value.remediate.return_value = fake_result
        result = await scanner.remediate_content_item(
            cloud_file,
            remediation_client=remediation_client,
            requested_purposes={"remediation", "alt_text"},
        )

    passed_issues = remediator_cls.call_args.args[1]
    assert [issue["id"] for issue in passed_issues] == expected_remediation_ids
    assert all(issue["category"] != "alt_text" for issue in passed_issues)
    assert result["manual_count"] == expected_manual
    assert result["issues_remaining"] == expected_manual
    assert result["fixed_count"] == 0
    assert result["purpose_decisions"]["alt_text"] == "denied_at_dispatch"
    assert result["purpose_decisions"]["remediation"] == "allowed_not_used"
    assert not any(
        "alt text" in str(call).lower() or "image description" in str(call).lower()
        for call in remediation_client.method_calls
    )


@pytest.mark.asyncio
async def test_alt_only_success_uses_alt_client_without_remediation_client_call():
    from src.education.canvas_content_scanner import CanvasContentScanner

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "image-alt", "nodes": [{}]}]
    )
    cloud_file = MagicMock(
        id="file-1",
        file_name="Page",
        content_body='<img src="/files/42">',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
    )
    remediation_client = MagicMock(provider="gemini", purpose="remediation")
    alt_client = MagicMock(provider="gemini", purpose="alt_text")
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    fake_result = SimpleNamespace(
        success=True,
        output_file=None,
        fixed_count=0,
        manual_count=0,
        failed_count=0,
        remediated_compliance_score=None,
    )

    with (
        patch.object(
            scanner,
            "_describe_images",
            new_callable=AsyncMock,
            return_value=('<img src="/files/42" alt="Chart">', 1),
        ) as describe,
        patch.object(
            scanner, "_verify_remediation", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator"
        ) as remediator_cls,
    ):
        remediator_cls.return_value.remediate.return_value = fake_result
        result = await scanner.remediate_content_item(
            cloud_file,
            remediation_client=remediation_client,
            alt_text_client=alt_client,
            requested_purposes={"remediation", "alt_text"},
        )

    describe.assert_awaited_once()
    assert remediator_cls.call_args.args[1] == []
    remediation_client.generate_text_sync.assert_not_called()
    assert result["fixed_count"] == 1
    assert result["manual_count"] == 0
    assert result["issues_remaining"] == 0
    assert result["purpose_decisions"] == {
        "remediation": "allowed_not_used",
        "alt_text": "allowed_not_used",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_result,expected_outcome",
    [
        (
            {
                "success": False,
                "error": "policy_denied",
                "provider": "gemini",
                "ai_used": False,
                "external_ai_used": False,
                "purpose_outcome": "denied_at_dispatch",
            },
            "denied_at_dispatch",
        ),
        (
            {
                "success": False,
                "error": "provider_call_failed",
                "provider": "gemini",
                "ai_used": True,
                "external_ai_used": True,
                "purpose_outcome": "attempted_failed",
            },
            "attempted_failed",
        ),
    ],
)
async def test_scanner_returns_authoritative_remediation_dispatch_outcome(
    client_result, expected_outcome
):
    from src.education.canvas_content_scanner import CanvasContentScanner

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "link-name", "nodes": [{}]}]
    )
    cloud_file = MagicMock(
        id="file-1",
        file_name="Page",
        content_body='<a href="/course">read more</a>',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
    )
    remediation_client = MagicMock(provider="gemini", purpose="remediation")
    remediation_client.generate_text_sync.return_value = client_result
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    fake_result = SimpleNamespace(
        success=True,
        output_file=None,
        fixed_count=0,
        manual_count=1,
        failed_count=0,
        remediated_compliance_score=None,
    )

    with (
        patch.object(
            scanner, "_verify_remediation", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator"
        ) as remediator_cls,
    ):

        def remediate():
            tracker = remediator_cls.call_args.kwargs["ai_client"]
            tracker.generate_text_sync("sanitized remediation prompt")
            return fake_result

        remediator_cls.return_value.remediate.side_effect = remediate
        result = await scanner.remediate_content_item(
            cloud_file,
            remediation_client=remediation_client,
            requested_purposes={"remediation"},
        )

    assert result["purpose_decisions"]["remediation"] == expected_outcome
    assert result["ai_used"] is (expected_outcome == "attempted_failed")
    assert result["provider"] == "gemini"
    assert "content" not in result
    assert "raw" not in str(result).lower()


@pytest.mark.parametrize(
    "results,expected_outcome",
    [
        (
            [
                {"success": True, "ai_used": True, "purpose_outcome": "used"},
                {
                    "success": False,
                    "ai_used": True,
                    "purpose_outcome": "attempted_failed",
                },
            ],
            "used",
        ),
        (
            [
                {"success": True, "ai_used": True, "purpose_outcome": "used"},
                {
                    "success": False,
                    "ai_used": False,
                    "purpose_outcome": "denied_at_dispatch",
                },
            ],
            "used",
        ),
        (
            [
                {
                    "success": False,
                    "ai_used": True,
                    "purpose_outcome": "attempted_failed",
                },
                {"success": True, "ai_used": True, "purpose_outcome": "used"},
            ],
            "used",
        ),
        (
            [
                {
                    "success": False,
                    "ai_used": False,
                    "purpose_outcome": "denied_at_dispatch",
                },
                {"success": False, "call_made": True, "ai_used": False},
            ],
            "attempted_failed",
        ),
        (
            [
                {"success": True, "ai_used": False},
                {"success": True, "ai_used": False},
            ],
            "allowed_not_used",
        ),
    ],
)
def test_ai_usage_tracker_aggregates_authoritative_outcome(results, expected_outcome):
    from src.education.canvas_content_scanner import _AIUsageTracker

    client = MagicMock(provider="gemini")
    client.generate_text_sync.side_effect = results
    tracker = _AIUsageTracker(client, requested=True)

    for _ in results:
        tracker.generate_text_sync("sanitized prompt")

    assert tracker.outcome == expected_outcome


def test_ai_usage_tracker_does_not_count_failed_provider_output_as_used():
    from src.education.canvas_content_scanner import _AIUsageTracker

    client = MagicMock(provider="gemini")
    client.generate_text_sync.return_value = {
        "success": False,
        "call_made": True,
        "ai_used": True,
        "external_ai_used": True,
        "provider": "gemini",
        "purpose_outcome": "used",
    }
    tracker = _AIUsageTracker(client, requested=True)

    tracker.generate_text_sync("sanitized prompt")

    assert tracker.outcome == "attempted_failed"
    assert tracker.ai_used is True
    assert tracker.external_ai_used is True
    assert tracker.provider_used == "gemini"


@pytest.mark.asyncio
async def test_failed_rescan_log_uses_only_stable_sanitized_diagnostics(caplog):
    from src.education.canvas_content_scanner import CanvasContentScanner

    scanner = CanvasContentScanner(AsyncMock(), MagicMock(), "dept-1", "cred-1")
    cloud_file = SimpleNamespace(
        id="cf-1", file_name="Welcome Page", last_scan_id="scan-7"
    )
    sensitive_marker = "SENSITIVE-RESCAN-DETAIL"

    with patch.object(
        scanner,
        "_run_axe_scan",
        new=AsyncMock(side_effect=RuntimeError(sensitive_marker)),
    ):
        result = await scanner._verify_remediation(cloud_file, "<p>x</p>", [])

    assert result is None
    assert sensitive_marker not in caplog.text
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.error_code == "REMEDIATION_RESCAN_FAILED"
    assert record.cloud_file_id == "cf-1"
    assert record.scan_id == "scan-7"
    assert record.error_type == "RuntimeError"
    assert record.exc_info is None


@pytest.mark.asyncio
async def test_canvas_html_remediation_contains_and_removes_real_output_artifacts():
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.education.remediation.html_remediator import (
        HtmlRemediator as RealRemediator,
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "link-name", "nodes": [{}]}]
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        file_name="Page",
        content_body='<a href="/course">read more</a>',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
        remediated_body=None,
        writeback_status=None,
        has_remediated_version=False,
        remediation_origin=None,
        remediated_compliance_score=None,
    )
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    captured = {}

    def construct(file_path, issues, *, config, ai_client):
        captured["source"] = Path(file_path)
        captured["config"] = config
        return RealRemediator(file_path, issues, config=config, ai_client=ai_client)

    with (
        patch.object(
            scanner, "_verify_remediation", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator",
            side_effect=construct,
        ),
    ):
        result = await scanner.remediate_content_item(cloud_file)

    assert result["success"] is True
    assert cloud_file.remediation_origin == "automatic"
    assert captured["config"].create_backup is False
    assert captured["config"].output_directory == str(captured["source"].parent)
    assert not captured["source"].parent.exists()


@pytest.mark.asyncio
async def test_verified_response_and_persistence_use_authoritative_rescan_counts():
    from src.education.canvas_content_scanner import (
        CanvasContentScanner,
        _PendingVerification,
    )

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "link-name", "nodes": [{}, {}, {}]}]
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        file_name="Page",
        content_body='<a href="/course">read more</a>',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
        remediated_body=None,
        writeback_status=None,
        has_remediated_version=False,
        remediation_origin=None,
        remediated_compliance_score=None,
        remediated_issues_fixed=None,
        remediated_issues_remaining=None,
    )
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    verification = _PendingVerification(
        scan_id="verified-scan",
        score=70.0,
        fixed=1,
        remaining=2,
        introduced=3,
        axe_results_json='{"passes": [], "violations": []}',
        issues_json="[]",
        critical_issues=0,
        high_issues=0,
        medium_issues=0,
        low_issues=0,
    )
    fake_result = SimpleNamespace(
        success=True,
        output_file=None,
        fixed_count=99,
        manual_count=99,
        failed_count=99,
        remediated_compliance_score=99.0,
    )

    with (
        patch.object(
            scanner, "_verify_remediation", AsyncMock(return_value=verification)
        ),
        patch("src.education.remediation.html_remediator.HtmlRemediator") as cls,
    ):
        cls.return_value.remediate.return_value = fake_result
        result = await scanner.remediate_content_item(cloud_file)

    assert result["verified"] is True
    assert result["fixed_count"] == 1
    assert result["manual_count"] == 2
    assert result["issues_remaining"] == 2
    assert result["issues_introduced"] == 3
    assert result["remediated_score"] == 70.0
    assert cloud_file.remediated_issues_fixed == 1
    assert cloud_file.remediated_issues_remaining == 2
    assert cloud_file.remediated_compliance_score == 70.0


@pytest.mark.asyncio
async def test_failed_remediator_result_is_fail_closed_sanitized_and_cleaned():
    from src.education.canvas_content_scanner import CanvasContentScanner

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "link-name", "nodes": [{}]}]
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        file_name="Page",
        content_body='<a href="/course">read more</a>',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
        remediated_body="ORIGINAL REMEDIATED BODY",
        writeback_status="approved",
        has_remediated_version=False,
        remediated_compliance_score=77.0,
    )
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    captured = {}
    verify = AsyncMock(side_effect=AssertionError("failed result must not be verified"))

    class FailedRemediator:
        def __init__(self, file_path, _issues, *, config, ai_client):
            captured["root"] = Path(file_path).parent
            self.output = captured["root"] / "sensitive-output.html"

        def remediate(self):
            self.output.write_text("<p>MUTATED</p>", encoding="utf-8")
            return SimpleNamespace(
                success=False,
                error_message=f"secret token at {self.output}",
                output_file=str(self.output),
                fixed_count=999,
                manual_count=0,
                failed_count=1,
            )

    with (
        patch.object(scanner, "_verify_remediation", verify),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator",
            FailedRemediator,
        ),
    ):
        result = await scanner.remediate_content_item(
            cloud_file,
            remediation_client=MagicMock(provider="gemini"),
            requested_purposes={"remediation"},
        )

    assert result == {
        "success": False,
        "error": "Content remediation failed",
        "error_code": "REMEDIATION_FAILED",
        "ai_used": False,
        "external_ai_used": False,
        "provider": None,
        "purpose_decisions": {
            "remediation": "allowed_not_used",
            "alt_text": "not_requested",
        },
    }
    assert "secret" not in str(result).lower()
    assert "sensitive-output" not in str(result)
    assert cloud_file.remediated_body == "ORIGINAL REMEDIATED BODY"
    assert cloud_file.writeback_status == "approved"
    assert cloud_file.has_remediated_version is False
    assert cloud_file.remediated_compliance_score == 77.0
    db.commit.assert_not_called()
    verify.assert_not_awaited()
    assert not captured["root"].exists()


@pytest.mark.asyncio
async def test_cleanup_failure_after_valid_output_prevents_durable_mutation_and_commit():
    import tempfile

    from src.education import canvas_content_scanner as scanner_module
    from src.education.canvas_content_scanner import (
        CanvasContentScanner,
        _PendingVerification,
    )

    durable_fields = {
        "remediated_body",
        "writeback_status",
        "has_remediated_version",
        "remediated_compliance_score",
        "remediated_issues_fixed",
        "remediated_issues_remaining",
    }

    class TrackingCloudFile(SimpleNamespace):
        def __setattr__(self, name, value):
            if getattr(self, "_tracking", False) and name in durable_fields:
                self.mutations.append((name, value))
            super().__setattr__(name, value)

    cloud_file = TrackingCloudFile(
        id="file-1",
        file_name="Page",
        content_body='<a href="/course">read more</a>',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
        remediated_body="ORIGINAL",
        writeback_status="approved",
        has_remediated_version=False,
        remediated_compliance_score=77.0,
        remediated_issues_fixed=None,
        remediated_issues_remaining=None,
        mutations=[],
        _tracking=True,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "link-name", "nodes": [{}]}]
    )
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    real_temporary_directory = tempfile.TemporaryDirectory
    captured = {}

    class CleanupFailureDirectory:
        def __init__(self, *args, **kwargs):
            self.owned = real_temporary_directory(*args, **kwargs)

        def __enter__(self):
            path = self.owned.__enter__()
            captured["root"] = Path(path)
            return path

        def __exit__(self, exc_type, exc, traceback):
            self.owned.__exit__(exc_type, exc, traceback)
            raise OSError("cleanup marker secret")

    result_value = SimpleNamespace(
        success=True,
        output_file=None,
        fixed_count=1,
        manual_count=0,
        failed_count=0,
        remediated_compliance_score=90.0,
    )
    verification = _PendingVerification(
        scan_id="verification-scan",
        score=100.0,
        fixed=1,
        remaining=0,
        introduced=0,
        axe_results_json='{"passes": [], "violations": []}',
        issues_json="[]",
        critical_issues=0,
        high_issues=0,
        medium_issues=0,
        low_issues=0,
    )

    with (
        patch.object(
            scanner,
            "_verify_remediation",
            new_callable=AsyncMock,
            return_value=verification,
        ) as verify,
        patch.object(
            scanner_module.tempfile, "TemporaryDirectory", CleanupFailureDirectory
        ),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator"
        ) as remediator_cls,
    ):
        remediator_cls.return_value.remediate.return_value = result_value
        result = await scanner.remediate_content_item(
            cloud_file,
            remediation_client=MagicMock(provider="gemini"),
            requested_purposes={"remediation"},
        )

    assert result["success"] is False
    assert result["error"] == "Content remediation failed"
    assert result["error_code"] == "REMEDIATION_FAILED"
    assert "cleanup marker" not in str(result)
    assert result["purpose_decisions"] == {
        "remediation": "allowed_not_used",
        "alt_text": "not_requested",
    }
    verify.assert_awaited_once()
    assert cloud_file.mutations == []
    assert cloud_file.remediated_body == "ORIGINAL"
    db.commit.assert_not_called()
    db.rollback.assert_called_once()
    assert not captured["root"].exists()


@pytest.mark.asyncio
async def test_raising_remediator_removes_backup_partial_and_source_without_leak(
    caplog,
):
    from src.education.canvas_content_scanner import CanvasContentScanner

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "link-name", "nodes": [{}]}]
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        file_name="Page",
        content_body='<a href="/course">read more</a>',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
        remediated_body=None,
        writeback_status=None,
        has_remediated_version=False,
        remediation_origin=None,
        remediated_compliance_score=None,
    )
    scanner = CanvasContentScanner(AsyncMock(), db, "dept-1", "cred-1")
    captured = {}

    class RaisingRemediator:
        def __init__(self, file_path, _issues, *, config, ai_client):
            captured["root"] = Path(file_path).parent

        def remediate(self):
            root = captured["root"]
            (root / "backups").mkdir()
            (root / "backups" / "source-backup.html").write_text("backup")
            (root / "partial-output.html").write_text("partial")
            raise RuntimeError(f"secret path {root / 'partial-output.html'}")

    with patch(
        "src.education.remediation.html_remediator.HtmlRemediator",
        RaisingRemediator,
    ):
        result = await scanner.remediate_content_item(cloud_file)

    assert result["success"] is False
    assert result["error"] == "Content remediation failed"
    assert result["error_code"] == "REMEDIATION_FAILED"
    assert "secret" not in str(result).lower()
    assert "partial-output" not in str(result)
    assert "secret path" not in caplog.text
    assert "partial-output" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert not captured["root"].exists()


@pytest.mark.asyncio
async def test_non_lms_image_log_does_not_disclose_signed_url(caplog):
    import logging

    from src.education.canvas_content_scanner import CanvasContentScanner

    caplog.set_level(logging.INFO)

    marker = "SIGNED-URL-SECRET-MARKER"
    scanner = CanvasContentScanner(AsyncMock(), MagicMock(), "dept-1", "cred-1")

    html, described = await scanner._describe_images(
        SimpleNamespace(id="file-1", file_name="Page"),
        f'<img src="https://cdn.example/image.png?token={marker}">',
        alt_text_client=MagicMock(),
    )

    assert described == 0
    assert marker not in caplog.text
    assert "cdn.example" not in caplog.text
    assert html.endswith(f'token={marker}">')


@pytest.mark.parametrize(
    ("source", "expected_file_id"),
    [
        ("/files/42", "42"),
        ("/files/42/preview", "42"),
        ("/files/42?download=1", "42"),
        ("/files/42#preview", "42"),
        ("https://canvas.example/courses/7/files/42/preview", "42"),
        ("/files/42evil", None),
        ("/files/42.5", None),
        ("/files/42_1", None),
        ("/files/42%32", None),
        ("/files/420evil", None),
    ],
)
def test_canvas_file_id_requires_a_url_boundary(source, expected_file_id):
    from src.education.canvas_content_scanner import CanvasContentScanner

    match = CanvasContentScanner._FILE_ID_PATTERN.search(source)

    assert (match.group(1) if match else None) == expected_file_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "/files/42evil",
        "/files/42.5",
        "/files/42_1",
        "/files/42%32",
        "https://canvas.example/courses/7/files/420evil",
    ],
)
async def test_malformed_canvas_file_reference_never_reaches_sensitive_sinks(source):
    from src.education.canvas_content_scanner import CanvasContentScanner

    canvas = AsyncMock()
    canvas.list_course_files.return_value = [SimpleNamespace(id="42")]
    alt_text_client = MagicMock(provider="gemini")
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = f'<img src="{source}">'

    html, described = await scanner._describe_images(
        SimpleNamespace(
            id="cloud-1", file_name="Page", provider_parent_id="course-101"
        ),
        original,
        alt_text_client=alt_text_client,
    )

    assert (html, described) == (original, 0)
    canvas.list_course_files.assert_not_awaited()
    canvas.download_file.assert_not_awaited()
    alt_text_client.analyze_image_sync.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source",
    [
        "/files/42",
        "/files/42/preview",
        "/files/42?download=1&amp;ver=2",
        "/files/42#preview",
        "https://untrusted.example/courses/not-the-course/files/42/preview",
    ],
)
async def test_canvas_file_boundary_variants_use_course_inventory_once(source):
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    body = _task15_image_bytes()
    file_info = _task15_canvas_image_info(size=len(body))
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [file_info]
    canvas.download_course_image.return_value = CanvasImageDownloadResult(
        success=True, data=body, content_type="image/png", suffix=".png"
    )
    alt_text_client = MagicMock(provider="gemini")
    alt_text_client.analyze_image_sync.return_value = {
        "success": True,
        "content": "Blue square",
        "provider": "gemini",
    }
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")

    html, described = await scanner._describe_images(
        SimpleNamespace(
            id="cloud-1", file_name="Page", provider_parent_id="course-101"
        ),
        f'<img src="{source}">',
        alt_text_client=alt_text_client,
    )

    assert described == 1
    assert 'alt="Blue square"' in html
    canvas.list_course_files.assert_awaited_once_with("course-101")
    canvas.download_course_image.assert_awaited_once()
    assert canvas.download_course_image.await_args.args[0] is file_info
    canvas.download_file.assert_not_awaited()
    alt_text_client.analyze_image_sync.assert_called_once()


@pytest.mark.asyncio
async def test_image_description_downloads_only_course_inventory_members_once():
    """Mixed embedded IDs are bound to the stored course at operation time."""
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    body = _task15_image_bytes()
    first = _task15_canvas_image_info(file_id="42", size=len(body))
    second = _task15_canvas_image_info(file_id="0007", size=len(body))
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [
        first,
        second,
        SimpleNamespace(id=None),
        {"name": "malformed"},
    ]
    canvas.download_course_image.side_effect = [
        CanvasImageDownloadResult(
            success=True, data=body, content_type="image/png", suffix=".png"
        ),
        CanvasImageDownloadResult(
            success=True, data=body, content_type="image/png", suffix=".png"
        ),
    ]
    alt_text_client = MagicMock(provider="gemini")
    alt_text_client.analyze_image_sync.side_effect = [
        {"success": True, "content": "First", "provider": "gemini"},
        {"success": True, "content": "Second", "provider": "gemini"},
    ]
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = (
        '<img src="/files/042">'
        '<img src="/courses/evil/files/999/preview">'
        '<img data-api-endpoint="/api/v1/files/7" src="ignored">'
    )

    html, described = await scanner._describe_images(
        SimpleNamespace(
            id="cloud-1", file_name="Page", provider_parent_id=" course-101 "
        ),
        original,
        alt_text_client=alt_text_client,
    )

    assert described == 2
    assert html.count("alt=") == 2
    canvas.list_course_files.assert_awaited_once_with("course-101")
    assert [call.args[0] for call in canvas.download_course_image.await_args_list] == [
        first,
        second,
    ]
    canvas.download_file.assert_not_awaited()
    canvas.get_file.assert_not_awaited()
    assert alt_text_client.analyze_image_sync.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("course_id", [None, "", "   "])
async def test_image_description_missing_course_fails_closed(course_id):
    from src.education.canvas_content_scanner import CanvasContentScanner

    canvas = AsyncMock()
    alt_text_client = MagicMock(provider="gemini")
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = '<img src="/files/42">'

    html, described = await scanner._describe_images(
        SimpleNamespace(id="cloud-1", file_name="Page", provider_parent_id=course_id),
        original,
        alt_text_client=alt_text_client,
    )

    assert (html, described) == (original, 0)
    canvas.list_course_files.assert_not_awaited()
    canvas.download_file.assert_not_awaited()
    alt_text_client.analyze_image_sync.assert_not_called()


@pytest.mark.asyncio
async def test_guessed_or_cross_course_image_id_never_reaches_sensitive_sinks():
    from src.education.canvas_content_scanner import CanvasContentScanner

    canvas = AsyncMock()
    canvas.list_course_files.return_value = [SimpleNamespace(id="41")]
    alt_text_client = MagicMock(provider="gemini")
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = '<img src="/courses/other-course/files/42/preview">'

    html, described = await scanner._describe_images(
        SimpleNamespace(
            id="cloud-1", file_name="Page", provider_parent_id="course-101"
        ),
        original,
        alt_text_client=alt_text_client,
    )

    assert (html, described) == (original, 0)
    canvas.list_course_files.assert_awaited_once_with("course-101")
    canvas.download_file.assert_not_awaited()
    alt_text_client.analyze_image_sync.assert_not_called()


@pytest.mark.asyncio
async def test_course_file_inventory_failure_fails_closed_without_sensitive_log_data(
    caplog,
):
    from src.education.canvas_content_scanner import CanvasContentScanner

    marker = "INVENTORY-SECRET-MARKER"
    canvas = AsyncMock()
    canvas.list_course_files.side_effect = RuntimeError(marker)
    alt_text_client = MagicMock(provider="gemini")
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = '<img src="/files/42?token=SIGNED-URL-SECRET">'

    html, described = await scanner._describe_images(
        SimpleNamespace(
            id="cloud-1", file_name="Page", provider_parent_id="course-101"
        ),
        original,
        alt_text_client=alt_text_client,
    )

    assert (html, described) == (original, 0)
    canvas.list_course_files.assert_awaited_once_with("course-101")
    canvas.download_file.assert_not_awaited()
    alt_text_client.analyze_image_sync.assert_not_called()
    assert marker not in caplog.text
    assert "SIGNED-URL-SECRET" not in caplog.text
    record = caplog.records[-1]
    assert record.error_code == "IMAGE_COURSE_INVENTORY_FAILED"
    assert record.cloud_file_id == "cloud-1"
    assert record.course_id == "course-101"
    assert record.file_id == "42"
    assert record.error_type == "RuntimeError"
    assert record.exc_info is None


@pytest.mark.asyncio
async def test_image_download_exception_log_does_not_disclose_raw_error(caplog):
    from src.education.canvas_content_scanner import CanvasContentScanner

    marker = "DOWNLOAD-EXCEPTION-SECRET-MARKER"
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [_task15_canvas_image_info()]
    canvas.download_course_image.side_effect = RuntimeError(marker)
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = '<img src="/courses/1/files/42/preview">'

    html, described = await scanner._describe_images(
        SimpleNamespace(id="file-1", file_name="Page", provider_parent_id="course-1"),
        original,
        alt_text_client=MagicMock(provider="gemini"),
    )

    assert html == original
    assert described == 0
    assert marker not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_image_description_uses_only_injected_alt_text_client(tmp_path):
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    body = _task15_image_bytes()
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [_task15_canvas_image_info(size=len(body))]
    canvas.download_course_image.return_value = CanvasImageDownloadResult(
        success=True, data=body, content_type="image/png", suffix=".png"
    )
    alt_text_client = MagicMock()
    alt_text_client.provider = "gemini"
    alt_text_client.analyze_image_sync.return_value = {
        "success": True,
        "content": "Blue square",
        "provider": "gemini",
        "model": "vision-safe",
        "inference_time": 0.1,
    }
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    cloud_file = SimpleNamespace(
        id="file-1", file_name="Page", provider_parent_id="course-1"
    )

    html, described = await scanner._describe_images(
        cloud_file,
        '<img src="/courses/1/files/42/preview">',
        alt_text_client=alt_text_client,
    )

    assert described == 1
    assert 'alt="Blue square"' in html
    alt_text_client.analyze_image_sync.assert_called_once()
    assert alt_text_client.analyze_image_sync.call_args.kwargs["image_data"] == body


@pytest.mark.asyncio
async def test_control_bearing_image_description_stays_manual_and_unfixed_on_rescan():
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    body = _task15_image_bytes()
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [_task15_canvas_image_info(size=len(body))]
    canvas.download_course_image.return_value = CanvasImageDownloadResult(
        success=True, data=body, content_type="image/png", suffix=".png"
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "image-alt", "nodes": [{}]}]
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        file_name="Page",
        content_body='<img src="/courses/1/files/42/preview">',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
        remediated_body=None,
        writeback_status=None,
        has_remediated_version=False,
        remediation_origin=None,
        remediated_compliance_score=None,
        remediated_issues_fixed=None,
        remediated_issues_remaining=None,
        provider_parent_id="course-1",
    )
    alt_text_client = MagicMock(provider="gemini")
    alt_text_client.analyze_image_sync.return_value = {
        "success": True,
        "content": "Blue\tsquare",
        "provider": "gemini",
        "model": "vision-safe",
        "inference_time": 0.1,
    }
    scanner = CanvasContentScanner(canvas, db, "dept-1", "cred-1")
    fake_result = SimpleNamespace(
        success=True,
        output_file=None,
        fixed_count=0,
        manual_count=0,
        failed_count=0,
        remediated_compliance_score=None,
    )

    with (
        patch.object(
            scanner, "_verify_remediation", new_callable=AsyncMock, return_value=None
        ) as verify,
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator"
        ) as remediator_cls,
    ):
        remediator_cls.return_value.remediate.return_value = fake_result
        result = await scanner.remediate_content_item(
            cloud_file,
            alt_text_client=alt_text_client,
            requested_purposes={"alt_text"},
        )

    assert result["success"] is True
    assert result["fixed_count"] == 0
    assert result["manual_count"] == 1
    rescanned_html = verify.await_args.args[1]
    assert "alt=" not in rescanned_html
    assert "Blue square" not in rescanned_html


@pytest.mark.asyncio
async def test_image_provider_exception_log_does_not_disclose_raw_error(caplog):
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    marker = "PROVIDER-EXCEPTION-SECRET-MARKER"
    body = _task15_image_bytes()
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [_task15_canvas_image_info(size=len(body))]
    canvas.download_course_image.return_value = CanvasImageDownloadResult(
        success=True, data=body, content_type="image/png", suffix=".png"
    )
    alt_text_client = MagicMock(provider="gemini")
    alt_text_client.analyze_image_sync.side_effect = RuntimeError(marker)
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = '<img src="/courses/1/files/42/preview">'

    html, described = await scanner._describe_images(
        SimpleNamespace(id="file-1", file_name="Page", provider_parent_id="course-1"),
        original,
        alt_text_client=alt_text_client,
    )

    assert html == original
    assert described == 0
    assert marker not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.asyncio
async def test_image_cleanup_failure_aborts_remediation_before_mutation(tmp_path):
    import tempfile

    from src.education import canvas_content_scanner as scanner_module
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "image-alt", "nodes": [{}]}]
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        file_name="Page",
        content_body='<img src="/courses/1/files/42/preview">',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
        remediated_body="ORIGINAL",
        writeback_status="approved",
        has_remediated_version=False,
        remediated_compliance_score=77.0,
        remediated_issues_fixed=None,
        remediated_issues_remaining=None,
        provider_parent_id="course-1",
    )

    body = _task15_image_bytes()
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [_task15_canvas_image_info(size=len(body))]
    canvas.download_course_image.return_value = CanvasImageDownloadResult(
        success=True, data=body, content_type="image/png", suffix=".png"
    )
    scanner = CanvasContentScanner(canvas, db, "dept-1", "cred-1")
    alt_text_client = MagicMock(provider="gemini")
    alt_text_client.analyze_image_sync.return_value = {
        "success": True,
        "content": "Blue square",
        "provider": "gemini",
        "model": "vision-safe",
        "inference_time": 0.1,
        "ai_used": True,
        "external_ai_used": True,
        "purpose_outcome": "used",
    }
    real_temporary_directory = tempfile.TemporaryDirectory
    captured = {}

    class SelectiveCleanupFailureDirectory:
        def __init__(self, *args, **kwargs):
            self.prefix = kwargs.get("prefix", "")
            self.owned = real_temporary_directory(*args, **kwargs)

        def __enter__(self):
            path = self.owned.__enter__()
            if self.prefix == "aelira-canvas-images-":
                captured["root"] = Path(path)
            return path

        def __exit__(self, exc_type, exc, traceback):
            self.owned.__exit__(exc_type, exc, traceback)
            if self.prefix == "aelira-canvas-images-":
                raise OSError("image cleanup secret marker")
            return False

    with (
        patch.object(
            scanner_module.tempfile,
            "TemporaryDirectory",
            SelectiveCleanupFailureDirectory,
        ),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator"
        ) as remediator_cls,
    ):
        result = await scanner.remediate_content_item(
            cloud_file,
            alt_text_client=alt_text_client,
            requested_purposes={"alt_text"},
        )

    assert result["success"] is False
    assert result["error_code"] == "REMEDIATION_FAILED"
    assert "image cleanup" not in str(result)
    assert result["purpose_decisions"]["alt_text"] == "used"
    assert cloud_file.remediated_body == "ORIGINAL"
    assert cloud_file.writeback_status == "approved"
    assert cloud_file.has_remediated_version is False
    assert cloud_file.remediated_compliance_score == 77.0
    db.commit.assert_not_called()
    db.rollback.assert_called_once()
    remediator_cls.assert_not_called()
    assert not captured["root"].exists()


@pytest.mark.asyncio
async def test_failed_injected_alt_text_leaves_image_for_manual_review(tmp_path):
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    body = _task15_image_bytes()
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [_task15_canvas_image_info(size=len(body))]
    canvas.download_course_image.return_value = CanvasImageDownloadResult(
        success=True, data=body, content_type="image/png", suffix=".png"
    )
    alt_text_client = MagicMock(provider="gemini")
    alt_text_client.analyze_image_sync.return_value = {
        "success": False,
        "error": "policy_denied",
        "provider": "gemini",
        "model": "",
        "ai_used": False,
        "external_ai_used": False,
    }
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = '<img src="/courses/1/files/42/preview">'

    html, described = await scanner._describe_images(
        SimpleNamespace(id="file-1", file_name="Page", provider_parent_id="course-1"),
        original,
        alt_text_client=alt_text_client,
    )

    assert html == original
    assert described == 0
    assert "alt=" not in html


@pytest.mark.asyncio
async def test_animated_inline_image_stays_manual_without_provider_call():
    from io import BytesIO

    from PIL import Image

    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    output = BytesIO()
    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=10,
        loop=0,
    )
    body = output.getvalue()
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [
        _task15_canvas_image_info(mime="image/gif", size=len(body))
    ]
    canvas.download_course_image.return_value = CanvasImageDownloadResult(
        success=True, data=body, content_type="image/gif", suffix=".gif"
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        issues=[{"id": "image-alt", "nodes": [{}]}]
    )
    cloud_file = SimpleNamespace(
        id="file-1",
        file_name="Page",
        content_body='<img src="/courses/1/files/42/preview">',
        last_scan_id="scan-1",
        last_compliance_score=80.0,
        remediated_body=None,
        writeback_status=None,
        has_remediated_version=False,
        remediation_origin=None,
        remediated_compliance_score=None,
        remediated_issues_fixed=None,
        remediated_issues_remaining=None,
        provider_parent_id="course-1",
    )
    alt_text_client = MagicMock(provider="gemini")
    scanner = CanvasContentScanner(canvas, db, "dept-1", "cred-1")
    fake_result = SimpleNamespace(
        success=True,
        output_file=None,
        fixed_count=0,
        manual_count=0,
        failed_count=0,
        remediated_compliance_score=None,
    )

    with (
        patch.object(
            scanner, "_verify_remediation", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "src.education.remediation.html_remediator.HtmlRemediator"
        ) as remediator_cls,
    ):
        remediator_cls.return_value.remediate.return_value = fake_result
        result = await scanner.remediate_content_item(
            cloud_file,
            alt_text_client=alt_text_client,
            requested_purposes={"alt_text"},
        )

    assert result["success"] is True
    assert result["fixed_count"] == 0
    assert result["manual_count"] == 1
    assert "alt=" not in cloud_file.remediated_body
    alt_text_client.analyze_image_sync.assert_not_called()


def _task15_image_bytes(format_name="PNG"):
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (10, 10), color="blue").save(output, format=format_name)
    return output.getvalue()


def _task15_canvas_image_info(*, file_id="42", mime="image/png", size=None):
    from src.integrations.canvas.models import CanvasFileInfo

    now = datetime.now(timezone.utc)
    return CanvasFileInfo(
        id=file_id,
        display_name="misleading.txt",
        filename="misleading.txt",
        content_type=mime,
        size=(len(_task15_image_bytes()) if size is None else size),
        url="https://files.example/download",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_inline_image_requires_exact_complete_bounded_inventory_metadata():
    from src.config.settings import get_settings
    from src.education.canvas_content_scanner import CanvasContentScanner

    maximum = get_settings().max_file_size_image
    boolean_size = _task15_canvas_image_info(file_id="44")
    boolean_size = boolean_size.model_copy(update={"size": True})
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [
        SimpleNamespace(id="42", content_type="image/png", size=77),
        _task15_canvas_image_info(file_id="43", mime="text/html", size=77),
        boolean_size,
        _task15_canvas_image_info(file_id="45", size=maximum + 1),
    ]
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")
    original = "".join(f'<img src="/files/{file_id}">' for file_id in range(42, 46))

    html, described = await scanner._describe_images(
        SimpleNamespace(id="cloud-1", file_name="Page", provider_parent_id="course-1"),
        original,
        alt_text_client=MagicMock(provider="gemini"),
    )

    assert (html, described) == (original, 0)
    canvas.download_course_image.assert_not_awaited()
    canvas.download_file.assert_not_awaited()
    canvas.get_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_image_writes_part_then_renames_to_observed_suffix_before_vision(
    tmp_path,
):
    from PIL import Image

    from src.config.settings import get_settings
    from src.education.canvas_content_scanner import CanvasContentScanner
    from src.integrations.canvas.canvas_api import CanvasImageDownloadResult

    fixture = tmp_path / "fixture.webp"
    Image.new("RGB", (5, 4), "blue").save(fixture, format="WEBP")
    body = fixture.read_bytes()
    file_info = _task15_canvas_image_info(mime="image/webp", size=len(body))
    canvas = AsyncMock()
    canvas.list_course_files.return_value = [file_info]
    canvas.download_course_image.return_value = CanvasImageDownloadResult(
        success=True,
        data=body,
        content_type="image/webp",
        suffix=".webp",
    )
    seen = {}
    alt_text_client = MagicMock(provider="gemini")

    def analyze_image_sync(*, image_data, **_kwargs):
        seen["bytes"] = image_data
        return {"success": True, "content": "Blue square", "provider": "gemini"}

    alt_text_client.analyze_image_sync.side_effect = analyze_image_sync
    scanner = CanvasContentScanner(canvas, MagicMock(), "dept-1", "cred-1")

    html, described = await scanner._describe_images(
        SimpleNamespace(id="cloud-1", file_name="Page", provider_parent_id="course-1"),
        '<img src="https://untrusted.example/files/42?token=secret">',
        alt_text_client=alt_text_client,
    )

    assert described == 1
    assert 'alt="Blue square"' in html
    assert seen["bytes"] == body
    canvas.download_course_image.assert_awaited_once_with(
        file_info, max_bytes=get_settings().max_file_size_image
    )
    canvas.download_file.assert_not_awaited()
    canvas.get_file.assert_not_awaited()


def test_canvas_lms_remediation_ast_has_no_provider_acquisition_or_legacy_fallback():
    from src.education.canvas_content_scanner import CanvasContentScanner

    class_tree = ast.parse(inspect.getsource(CanvasContentScanner))
    methods = {
        node.name: node
        for node in ast.walk(class_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden = {
        "get_provider_manager",
        "_generate_with_gemini",
        "_generate_with_ollama",
    }
    violations = []
    for name in ("remediate_content_item", "_describe_images"):
        tree = methods[name]
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attrs = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        found = forbidden & (names | attrs)
        if found:
            violations.append(f"{name}: {sorted(found)}")

    generator_calls = [
        node
        for node in ast.walk(methods["_describe_images"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ImageAltTextGenerator"
    ]
    assert violations == []
    assert len(generator_calls) == 1
    assert {keyword.arg for keyword in generator_calls[0].keywords} == {"lms_client"}


class TestScanContentItemNullUserId:
    """
    Regression tests for AELIRA-BACKEND-31/32.

    Canvas LTI-initiated scans have no authenticated user, so scan.user_id
    must be None (not the string "system" which violates the FK constraint).
    """

    @pytest.mark.asyncio
    async def test_scan_content_item_sets_user_id_to_none(self):
        """scan_content_item must create a Scan with user_id=None, not 'system'."""
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        cloud_file = MagicMock()
        cloud_file.id = str(uuid.uuid4())
        cloud_file.file_name = "test-page"
        cloud_file.content_body = "<p>Hello world</p>"

        # Capture the Scan object passed to db.add
        added_objects = []
        db.add.side_effect = lambda obj: added_objects.append(obj)
        db.flush = MagicMock()
        db.commit = MagicMock()

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        # Patch _run_axe_scan to avoid Playwright dependency
        with patch.object(
            scanner,
            "_run_axe_scan",
            new_callable=AsyncMock,
            return_value={"violations": [], "passes": []},
        ):
            await scanner.scan_content_item(cloud_file)

        # Find the Scan object that was added
        from src.db.models import Scan

        scan_objects = [obj for obj in added_objects if isinstance(obj, Scan)]
        assert len(scan_objects) == 1, "Expected exactly one Scan to be added"
        scan = scan_objects[0]

        assert scan.user_id is None, (
            f"Expected scan.user_id=None for LTI system scan, got {scan.user_id!r}. "
            "Setting user_id='system' causes FK violation (AELIRA-BACKEND-31/32)."
        )

    @pytest.mark.asyncio
    async def test_scan_content_item_user_id_not_system_string(self):
        """Explicitly assert the broken value 'system' is NOT used."""
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
        department_id = str(uuid.uuid4())
        credential_id = str(uuid.uuid4())

        cloud_file = MagicMock()
        cloud_file.id = str(uuid.uuid4())
        cloud_file.file_name = "announcement"
        cloud_file.content_body = "<p>Announcement body</p>"

        added_objects = []
        db.add.side_effect = lambda obj: added_objects.append(obj)
        db.flush = MagicMock()
        db.commit = MagicMock()

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        with patch.object(
            scanner,
            "_run_axe_scan",
            new_callable=AsyncMock,
            return_value={"violations": [], "passes": []},
        ):
            await scanner.scan_content_item(cloud_file)

        from src.db.models import Scan

        scan_objects = [obj for obj in added_objects if isinstance(obj, Scan)]
        assert len(scan_objects) == 1
        scan = scan_objects[0]

        assert scan.user_id != "system", (
            "scan.user_id must not be 'system' — that string is not a valid user UUID "
            "and violates the FK constraint (AELIRA-BACKEND-31/32)."
        )
