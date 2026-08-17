"""
Tests for Canvas content scanner service.

Tests cover:
- HTML fragment wrapping/unwrapping roundtrip
- HTML sanitization (XSS prevention)
- Course content scanning orchestration
- Stale content detection for write-back safety
- Rollback from ContentWritebackLog
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import uuid

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

        result = await scanner.scan_course_content("COURSE123")

        # All 5 list methods should have been called
        canvas_client.list_course_pages.assert_awaited_once_with("COURSE123")
        canvas_client.list_course_assignments.assert_awaited_once_with("COURSE123")
        canvas_client.list_course_announcements.assert_awaited_once_with("COURSE123")
        canvas_client.list_course_quizzes.assert_awaited_once_with("COURSE123")
        canvas_client.list_course_discussions.assert_awaited_once_with("COURSE123")

        # Should have created 5 CloudFile records (one per content item)
        assert db.add.call_count >= 5

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
        existing_file.content_body = "<p>Old body</p>"
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = existing_file
        db.query.return_value = mock_query

        scanner = CanvasContentScanner(
            canvas_client=canvas_client,
            db=db,
            department_id=department_id,
            credential_id=credential_id,
        )

        result = await scanner.scan_course_content("COURSE123")

        # Should update existing record, not add new one
        assert existing_file.content_body == "<p>Updated body</p>"


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

        result = await scanner.scan_course_content("COURSE123")

        canvas_client.list_course_files.assert_awaited_once_with("COURSE123")
        assert result["counts"]["file"] == 1
        assert result["counts"]["page"] == 1
        assert len(result["file_cloud_file_ids"]) == 1
        # Page still goes through the normal cloud_file_ids list — files
        # must NOT be mixed into it (that list drives the axe-core-only
        # background task loop in the route handler).
        assert result["file_cloud_file_ids"][0] not in result["cloud_file_ids"]

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

        await scanner.scan_course_content("COURSE123")

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
        assert result["file_cloud_file_ids"] == ["existing-cf-id"]

    @pytest.mark.asyncio
    async def test_files_scan_job_queued_via_cloud_job_queue_not_background_task(self):
        # Regression guard for the core design decision: files must NOT
        # flow through cloud_file_ids (the axe-core background-task list) —
        # they'd silently no-op ("No content body") in scan_content_item.
        canvas_client = AsyncMock()
        db = MagicMock()
        scanner = self._scanner_with_empty_html_types(canvas_client, db)
        canvas_client.list_course_files = AsyncMock(
            return_value=[_make_file_info(id="file-1")]
        )
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        db.query.return_value = mock_query

        result = await scanner.scan_course_content("COURSE123")

        assert result["cloud_file_ids"] == []
        assert len(result["file_cloud_file_ids"]) == 1
        jobs = [
            call.args[0]
            for call in db.add.call_args_list
            if type(call.args[0]).__name__ == "CloudJobQueue"
        ]
        assert len(jobs) == 1
        assert jobs[0].provider == "canvas"
        assert jobs[0].provider_file_id == "file-1"
        assert jobs[0].status == "pending"


# ---------------------------------------------------------------------------
# TestStaleDetection
# ---------------------------------------------------------------------------


class TestStaleDetection:
    """Test stale content detection during write-back."""

    @pytest.mark.asyncio
    async def test_write_back_returns_stale_error_when_canvas_updated(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
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
    async def test_write_back_assignment_calls_update_assignment(self):
        from src.education.canvas_content_scanner import CanvasContentScanner

        canvas_client = AsyncMock()
        db = MagicMock()
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
