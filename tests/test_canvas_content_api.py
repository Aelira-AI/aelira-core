"""
Tests for Canvas content data models.

Tests cover:
- CanvasContentType enum values
- Each model's from_api_response() factory method
- None/empty body handling (Canvas can return null for body fields)
- Datetime parsing from ISO strings
"""

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from src.integrations.canvas.canvas_api import CanvasAPIClient


class TestCanvasContentTypeEnum:
    """Tests for CanvasContentType enum values."""

    def test_page_value(self):
        from src.integrations.canvas.content_models import CanvasContentType

        assert CanvasContentType.PAGE == "page"

    def test_assignment_value(self):
        from src.integrations.canvas.content_models import CanvasContentType

        assert CanvasContentType.ASSIGNMENT == "assignment"

    def test_announcement_value(self):
        from src.integrations.canvas.content_models import CanvasContentType

        assert CanvasContentType.ANNOUNCEMENT == "announcement"

    def test_quiz_value(self):
        from src.integrations.canvas.content_models import CanvasContentType

        assert CanvasContentType.QUIZ == "quiz"

    def test_discussion_value(self):
        from src.integrations.canvas.content_models import CanvasContentType

        assert CanvasContentType.DISCUSSION == "discussion"

    def test_all_five_values_exist(self):
        from src.integrations.canvas.content_models import CanvasContentType

        values = {e.value for e in CanvasContentType}
        assert values == {"page", "assignment", "announcement", "quiz", "discussion"}


class TestCanvasPageInfo:
    """Tests for CanvasPageInfo model."""

    def test_from_api_response_full(self):
        from src.integrations.canvas.content_models import CanvasPageInfo

        data = {
            "page_id": 42,
            "title": "Week 1 Overview",
            "url": "week-1-overview",
            "body": "<p>Welcome to week 1</p>",
            "published": True,
            "updated_at": "2026-03-01T10:00:00Z",
        }
        page = CanvasPageInfo.from_api_response(data)
        assert page.page_id == "42"
        assert page.title == "Week 1 Overview"
        assert page.url_slug == "week-1-overview"
        assert page.body == "<p>Welcome to week 1</p>"
        assert page.published is True
        assert isinstance(page.updated_at, datetime)

    def test_from_api_response_null_body(self):
        from src.integrations.canvas.content_models import CanvasPageInfo

        data = {
            "page_id": 10,
            "title": "Empty Page",
            "url": "empty-page",
            "body": None,
            "published": False,
            "updated_at": "2026-01-15T08:30:00Z",
        }
        page = CanvasPageInfo.from_api_response(data)
        assert page.body == ""

    def test_from_api_response_missing_body_key(self):
        from src.integrations.canvas.content_models import CanvasPageInfo

        data = {
            "page_id": 5,
            "title": "No Body Key",
            "url": "no-body-key",
            "published": True,
            "updated_at": "2026-02-10T12:00:00Z",
        }
        page = CanvasPageInfo.from_api_response(data)
        assert page.body == ""

    def test_from_api_response_string_page_id(self):
        from src.integrations.canvas.content_models import CanvasPageInfo

        data = {
            "page_id": "99",
            "title": "String ID Page",
            "url": "string-id-page",
            "body": "<p>Content</p>",
            "published": True,
            "updated_at": "2026-03-10T09:00:00Z",
        }
        page = CanvasPageInfo.from_api_response(data)
        assert page.page_id == "99"


class TestCanvasAssignmentInfo:
    """Tests for CanvasAssignmentInfo model."""

    def test_from_api_response_full(self):
        from src.integrations.canvas.content_models import CanvasAssignmentInfo

        data = {
            "id": 101,
            "name": "Final Essay",
            "description": "<p>Write a 5-page essay</p>",
            "due_at": "2026-05-01T23:59:00Z",
            "published": True,
            "updated_at": "2026-03-15T14:00:00Z",
        }
        assignment = CanvasAssignmentInfo.from_api_response(data)
        assert assignment.id == "101"
        assert assignment.name == "Final Essay"
        assert assignment.description == "<p>Write a 5-page essay</p>"
        assert isinstance(assignment.due_at, datetime)
        assert assignment.published is True
        assert isinstance(assignment.updated_at, datetime)

    def test_from_api_response_null_description(self):
        from src.integrations.canvas.content_models import CanvasAssignmentInfo

        data = {
            "id": 200,
            "name": "No Description",
            "description": None,
            "due_at": None,
            "published": False,
            "updated_at": "2026-03-01T00:00:00Z",
        }
        assignment = CanvasAssignmentInfo.from_api_response(data)
        assert assignment.description == ""

    def test_from_api_response_missing_description_key(self):
        from src.integrations.canvas.content_models import CanvasAssignmentInfo

        data = {
            "id": 201,
            "name": "Missing Key",
            "due_at": None,
            "published": True,
            "updated_at": "2026-03-01T00:00:00Z",
        }
        assignment = CanvasAssignmentInfo.from_api_response(data)
        assert assignment.description == ""

    def test_from_api_response_null_due_at(self):
        from src.integrations.canvas.content_models import CanvasAssignmentInfo

        data = {
            "id": 300,
            "name": "No Due Date",
            "description": "Some text",
            "due_at": None,
            "published": True,
            "updated_at": "2026-03-20T10:00:00Z",
        }
        assignment = CanvasAssignmentInfo.from_api_response(data)
        assert assignment.due_at is None


class TestCanvasAnnouncementInfo:
    """Tests for CanvasAnnouncementInfo model."""

    def test_from_api_response_full(self):
        from src.integrations.canvas.content_models import CanvasAnnouncementInfo

        data = {
            "id": 55,
            "title": "Class Cancelled",
            "message": "<p>Class is cancelled Friday</p>",
            "posted_at": "2026-03-20T09:00:00Z",
            "updated_at": "2026-03-20T09:05:00Z",
        }
        ann = CanvasAnnouncementInfo.from_api_response(data)
        assert ann.id == "55"
        assert ann.title == "Class Cancelled"
        assert ann.message == "<p>Class is cancelled Friday</p>"
        assert isinstance(ann.posted_at, datetime)
        assert isinstance(ann.updated_at, datetime)

    def test_from_api_response_null_message(self):
        from src.integrations.canvas.content_models import CanvasAnnouncementInfo

        data = {
            "id": 56,
            "title": "Empty Announcement",
            "message": None,
            "posted_at": "2026-03-21T08:00:00Z",
            "updated_at": "2026-03-21T08:00:00Z",
        }
        ann = CanvasAnnouncementInfo.from_api_response(data)
        assert ann.message == ""

    def test_from_api_response_missing_message_key(self):
        from src.integrations.canvas.content_models import CanvasAnnouncementInfo

        data = {
            "id": 57,
            "title": "No Message Key",
            "posted_at": "2026-03-22T08:00:00Z",
            "updated_at": "2026-03-22T08:00:00Z",
        }
        ann = CanvasAnnouncementInfo.from_api_response(data)
        assert ann.message == ""


class TestCanvasQuizInfo:
    """Tests for CanvasQuizInfo model."""

    def test_from_api_response_full(self):
        from src.integrations.canvas.content_models import CanvasQuizInfo

        data = {
            "id": 77,
            "title": "Midterm Quiz",
            "description": "<p>Answer 20 questions</p>",
            "quiz_type": "assignment",
            "published": True,
            "updated_at": "2026-04-01T12:00:00Z",
        }
        quiz = CanvasQuizInfo.from_api_response(data)
        assert quiz.id == "77"
        assert quiz.title == "Midterm Quiz"
        assert quiz.description == "<p>Answer 20 questions</p>"
        assert quiz.quiz_type == "assignment"
        assert quiz.published is True
        assert isinstance(quiz.updated_at, datetime)

    def test_from_api_response_null_description(self):
        from src.integrations.canvas.content_models import CanvasQuizInfo

        data = {
            "id": 78,
            "title": "No Description Quiz",
            "description": None,
            "quiz_type": "practice_quiz",
            "published": False,
            "updated_at": "2026-04-02T12:00:00Z",
        }
        quiz = CanvasQuizInfo.from_api_response(data)
        assert quiz.description == ""

    def test_from_api_response_missing_description_key(self):
        from src.integrations.canvas.content_models import CanvasQuizInfo

        data = {
            "id": 79,
            "title": "Missing Key Quiz",
            "quiz_type": "survey",
            "published": True,
            "updated_at": "2026-04-03T12:00:00Z",
        }
        quiz = CanvasQuizInfo.from_api_response(data)
        assert quiz.description == ""

    def test_from_api_response_null_quiz_type(self):
        from src.integrations.canvas.content_models import CanvasQuizInfo

        data = {
            "id": 80,
            "title": "Unknown Type Quiz",
            "description": "Some content",
            "quiz_type": None,
            "published": True,
            "updated_at": "2026-04-04T12:00:00Z",
        }
        quiz = CanvasQuizInfo.from_api_response(data)
        assert quiz.quiz_type is None


class TestCanvasDiscussionInfo:
    """Tests for CanvasDiscussionInfo model."""

    def test_from_api_response_full(self):
        from src.integrations.canvas.content_models import CanvasDiscussionInfo

        data = {
            "id": 33,
            "title": "Week 1 Discussion",
            "message": "<p>Share your thoughts</p>",
            "posted_at": "2026-03-10T10:00:00Z",
            "updated_at": "2026-03-11T10:00:00Z",
        }
        disc = CanvasDiscussionInfo.from_api_response(data)
        assert disc.id == "33"
        assert disc.title == "Week 1 Discussion"
        assert disc.message == "<p>Share your thoughts</p>"
        assert isinstance(disc.posted_at, datetime)
        assert isinstance(disc.updated_at, datetime)

    def test_from_api_response_null_message(self):
        from src.integrations.canvas.content_models import CanvasDiscussionInfo

        data = {
            "id": 34,
            "title": "No Message Discussion",
            "message": None,
            "posted_at": "2026-03-12T10:00:00Z",
            "updated_at": "2026-03-12T10:00:00Z",
        }
        disc = CanvasDiscussionInfo.from_api_response(data)
        assert disc.message == ""

    def test_from_api_response_missing_message_key(self):
        from src.integrations.canvas.content_models import CanvasDiscussionInfo

        data = {
            "id": 35,
            "title": "Missing Key Discussion",
            "posted_at": "2026-03-13T10:00:00Z",
            "updated_at": "2026-03-13T10:00:00Z",
        }
        disc = CanvasDiscussionInfo.from_api_response(data)
        assert disc.message == ""


class TestCanvasModuleInfo:
    """Tests for CanvasModuleInfo model."""

    def test_from_api_response_full(self):
        from src.integrations.canvas.content_models import CanvasModuleInfo

        data = {
            "id": 11,
            "name": "Module 1: Introduction",
            "position": 1,
            "items_count": 5,
            "items": [
                {"id": 1, "title": "Welcome Page", "type": "Page"},
                {"id": 2, "title": "Assignment 1", "type": "Assignment"},
            ],
        }
        module = CanvasModuleInfo.from_api_response(data)
        assert module.id == "11"
        assert module.name == "Module 1: Introduction"
        assert module.position == 1
        assert module.items_count == 5
        assert len(module.items) == 2

    def test_from_api_response_no_items(self):
        from src.integrations.canvas.content_models import CanvasModuleInfo

        data = {
            "id": 12,
            "name": "Empty Module",
            "position": 2,
            "items_count": 0,
        }
        module = CanvasModuleInfo.from_api_response(data)
        assert module.items == []

    def test_from_api_response_null_items(self):
        from src.integrations.canvas.content_models import CanvasModuleInfo

        data = {
            "id": 13,
            "name": "Null Items Module",
            "position": 3,
            "items_count": 0,
            "items": None,
        }
        module = CanvasModuleInfo.from_api_response(data)
        assert module.items == []

    def test_from_api_response_string_id(self):
        from src.integrations.canvas.content_models import CanvasModuleInfo

        data = {
            "id": "99",
            "name": "String ID Module",
            "position": 1,
            "items_count": 0,
        }
        module = CanvasModuleInfo.from_api_response(data)
        assert module.id == "99"


class TestInitExports:
    """Tests that content models are exported from the canvas integration package."""

    def test_content_type_exported(self):
        from src.integrations.canvas import CanvasContentType

        assert CanvasContentType is not None

    def test_page_info_exported(self):
        from src.integrations.canvas import CanvasPageInfo

        assert CanvasPageInfo is not None

    def test_assignment_info_exported(self):
        from src.integrations.canvas import CanvasAssignmentInfo

        assert CanvasAssignmentInfo is not None

    def test_announcement_info_exported(self):
        from src.integrations.canvas import CanvasAnnouncementInfo

        assert CanvasAnnouncementInfo is not None

    def test_quiz_info_exported(self):
        from src.integrations.canvas import CanvasQuizInfo

        assert CanvasQuizInfo is not None

    def test_discussion_info_exported(self):
        from src.integrations.canvas import CanvasDiscussionInfo

        assert CanvasDiscussionInfo is not None

    def test_module_info_exported(self):
        from src.integrations.canvas import CanvasModuleInfo

        assert CanvasModuleInfo is not None


# =============================================================================
# Canvas API Content Methods Tests (Task 3)
# =============================================================================

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch


def _make_response(
    status_code: int = 200,
    json_data: Any = None,
    headers: Optional[dict] = None,
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else []
    resp.headers = headers or {"X-Rate-Limit-Remaining": "500"}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


def _make_client() -> "CanvasAPIClient":
    """Create a CanvasAPIClient for testing."""
    from src.integrations.canvas.canvas_api import CanvasAPIClient

    return CanvasAPIClient(
        canvas_instance_url="https://canvas.example.com",
        access_token="test-token-123",
        credential_id="cred-1",
    )


class TestCanvasAPIContentMethods:
    """Tests for Canvas API content methods: list, get, update for pages, assignments, etc."""

    # ---- Pages ----

    @pytest.mark.asyncio
    async def test_list_course_pages(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        page_data = [
            {
                "page_id": 1,
                "title": "Welcome",
                "url": "welcome",
                "body": "<p>Hello</p>",
                "published": True,
                "updated_at": "2026-03-01T10:00:00Z",
            },
            {
                "page_id": 2,
                "title": "Syllabus",
                "url": "syllabus",
                "body": "<p>Course outline</p>",
                "published": True,
                "updated_at": "2026-03-02T10:00:00Z",
            },
        ]
        mock_client.get = AsyncMock(return_value=_make_response(json_data=page_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        pages = await client_obj.list_course_pages("101")

        assert len(pages) == 2
        assert pages[0].page_id == "1"
        assert pages[0].title == "Welcome"
        assert pages[1].url_slug == "syllabus"

    @pytest.mark.asyncio
    async def test_get_page(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        page_data = {
            "page_id": 42,
            "title": "Week 1 Overview",
            "url": "week-1-overview",
            "body": "<p>Content</p>",
            "published": True,
            "updated_at": "2026-03-01T10:00:00Z",
        }
        mock_client.get = AsyncMock(return_value=_make_response(json_data=page_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        page = await client_obj.get_page("101", "week-1-overview")

        assert page.page_id == "42"
        assert page.title == "Week 1 Overview"
        mock_client.get.assert_called_once()
        call_url = mock_client.get.call_args[0][0]
        assert "courses/101/pages/week-1-overview" in call_url

    @pytest.mark.asyncio
    async def test_update_page(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        updated_page_data = {
            "page_id": 42,
            "title": "Week 1 Overview",
            "url": "week-1-overview",
            "body": "<p>Updated content</p>",
            "published": True,
            "updated_at": "2026-03-10T10:00:00Z",
        }
        mock_client.put = AsyncMock(
            return_value=_make_response(json_data=updated_page_data)
        )
        client_obj._get_client = AsyncMock(return_value=mock_client)

        page = await client_obj.update_page(
            "101", "week-1-overview", body="<p>Updated content</p>"
        )

        assert page.body == "<p>Updated content</p>"
        mock_client.put.assert_called_once()
        call_kwargs = mock_client.put.call_args
        # Verify Canvas form data format: wiki_page[body]
        assert "wiki_page[body]" in str(call_kwargs)

    # ---- Assignments ----

    @pytest.mark.asyncio
    async def test_list_course_assignments(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        assignment_data = [
            {
                "id": 201,
                "name": "Essay 1",
                "description": "<p>Write an essay</p>",
                "due_at": "2026-04-01T23:59:00Z",
                "published": True,
                "updated_at": "2026-03-01T10:00:00Z",
            },
        ]
        mock_client.get = AsyncMock(
            return_value=_make_response(json_data=assignment_data)
        )
        client_obj._get_client = AsyncMock(return_value=mock_client)

        assignments = await client_obj.list_course_assignments("101")

        assert len(assignments) == 1
        assert assignments[0].id == "201"
        assert assignments[0].name == "Essay 1"

    @pytest.mark.asyncio
    async def test_get_assignment(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        assignment_data = {
            "id": 201,
            "name": "Essay 1",
            "description": "<p>Write an essay</p>",
            "due_at": "2026-04-01T23:59:00Z",
            "published": True,
            "updated_at": "2026-03-01T10:00:00Z",
        }
        mock_client.get = AsyncMock(
            return_value=_make_response(json_data=assignment_data)
        )
        client_obj._get_client = AsyncMock(return_value=mock_client)

        assignment = await client_obj.get_assignment("101", "201")

        assert assignment.id == "201"
        call_url = mock_client.get.call_args[0][0]
        assert "courses/101/assignments/201" in call_url

    @pytest.mark.asyncio
    async def test_update_assignment(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        updated_data = {
            "id": 201,
            "name": "Essay 1",
            "description": "<p>Updated description</p>",
            "due_at": "2026-04-01T23:59:00Z",
            "published": True,
            "updated_at": "2026-03-10T10:00:00Z",
        }
        mock_client.put = AsyncMock(return_value=_make_response(json_data=updated_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        assignment = await client_obj.update_assignment(
            "101", "201", description="<p>Updated description</p>"
        )

        assert assignment.description == "<p>Updated description</p>"
        call_kwargs = mock_client.put.call_args
        # Verify Canvas JSON format: {"assignment": {"description": ...}}
        sent_json = call_kwargs[1]["json"]
        assert "assignment" in sent_json
        assert sent_json["assignment"]["description"] == "<p>Updated description</p>"

    # ---- Announcements ----

    @pytest.mark.asyncio
    async def test_list_course_announcements(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        ann_data = [
            {
                "id": 55,
                "title": "Class Cancelled",
                "message": "<p>No class Friday</p>",
                "posted_at": "2026-03-20T09:00:00Z",
                "updated_at": "2026-03-20T09:05:00Z",
            },
        ]
        mock_client.get = AsyncMock(return_value=_make_response(json_data=ann_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        announcements = await client_obj.list_course_announcements("101")

        assert len(announcements) == 1
        assert announcements[0].title == "Class Cancelled"
        # Should include only_announcements=true param
        call_kwargs = mock_client.get.call_args
        params = call_kwargs[1].get("params", {})
        assert params.get("only_announcements") == "true"

    @pytest.mark.asyncio
    async def test_get_announcement(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        ann_data = {
            "id": 55,
            "title": "Class Cancelled",
            "message": "<p>No class Friday</p>",
            "posted_at": "2026-03-20T09:00:00Z",
            "updated_at": "2026-03-20T09:05:00Z",
        }
        mock_client.get = AsyncMock(return_value=_make_response(json_data=ann_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        ann = await client_obj.get_announcement("101", "55")

        assert ann.id == "55"
        call_url = mock_client.get.call_args[0][0]
        assert "courses/101/discussion_topics/55" in call_url

    @pytest.mark.asyncio
    async def test_update_announcement(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        updated_data = {
            "id": 55,
            "title": "Class Cancelled",
            "message": "<p>Updated message</p>",
            "posted_at": "2026-03-20T09:00:00Z",
            "updated_at": "2026-03-21T09:05:00Z",
        }
        mock_client.put = AsyncMock(return_value=_make_response(json_data=updated_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        ann = await client_obj.update_announcement(
            "101", "55", message="<p>Updated message</p>"
        )

        assert ann.message == "<p>Updated message</p>"
        call_kwargs = mock_client.put.call_args
        sent_json = call_kwargs[1]["json"]
        assert sent_json["message"] == "<p>Updated message</p>"

    # ---- Quizzes ----

    @pytest.mark.asyncio
    async def test_list_course_quizzes(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        quiz_data = [
            {
                "id": 77,
                "title": "Midterm",
                "description": "<p>20 questions</p>",
                "quiz_type": "assignment",
                "published": True,
                "updated_at": "2026-04-01T12:00:00Z",
            },
        ]
        mock_client.get = AsyncMock(return_value=_make_response(json_data=quiz_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        quizzes = await client_obj.list_course_quizzes("101")

        assert len(quizzes) == 1
        assert quizzes[0].title == "Midterm"

    @pytest.mark.asyncio
    async def test_get_quiz(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        quiz_data = {
            "id": 77,
            "title": "Midterm",
            "description": "<p>20 questions</p>",
            "quiz_type": "assignment",
            "published": True,
            "updated_at": "2026-04-01T12:00:00Z",
        }
        mock_client.get = AsyncMock(return_value=_make_response(json_data=quiz_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        quiz = await client_obj.get_quiz("101", "77")

        assert quiz.id == "77"
        call_url = mock_client.get.call_args[0][0]
        assert "courses/101/quizzes/77" in call_url

    @pytest.mark.asyncio
    async def test_update_quiz(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        updated_data = {
            "id": 77,
            "title": "Midterm",
            "description": "<p>Updated quiz</p>",
            "quiz_type": "assignment",
            "published": True,
            "updated_at": "2026-04-10T12:00:00Z",
        }
        mock_client.put = AsyncMock(return_value=_make_response(json_data=updated_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        quiz = await client_obj.update_quiz(
            "101", "77", description="<p>Updated quiz</p>"
        )

        assert quiz.description == "<p>Updated quiz</p>"
        call_kwargs = mock_client.put.call_args
        sent_json = call_kwargs[1]["json"]
        assert sent_json["quiz"]["description"] == "<p>Updated quiz</p>"

    # ---- Discussions ----

    @pytest.mark.asyncio
    async def test_list_course_discussions(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        disc_data = [
            {
                "id": 33,
                "title": "Week 1 Discussion",
                "message": "<p>Share thoughts</p>",
                "posted_at": "2026-03-10T10:00:00Z",
                "updated_at": "2026-03-11T10:00:00Z",
            },
        ]
        mock_client.get = AsyncMock(return_value=_make_response(json_data=disc_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        discussions = await client_obj.list_course_discussions("101")

        assert len(discussions) == 1
        assert discussions[0].title == "Week 1 Discussion"

    @pytest.mark.asyncio
    async def test_get_discussion(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        disc_data = {
            "id": 33,
            "title": "Week 1 Discussion",
            "message": "<p>Share thoughts</p>",
            "posted_at": "2026-03-10T10:00:00Z",
            "updated_at": "2026-03-11T10:00:00Z",
        }
        mock_client.get = AsyncMock(return_value=_make_response(json_data=disc_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        disc = await client_obj.get_discussion("101", "33")

        assert disc.id == "33"
        call_url = mock_client.get.call_args[0][0]
        assert "courses/101/discussion_topics/33" in call_url

    @pytest.mark.asyncio
    async def test_update_discussion(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        updated_data = {
            "id": 33,
            "title": "Week 1 Discussion",
            "message": "<p>Updated message</p>",
            "posted_at": "2026-03-10T10:00:00Z",
            "updated_at": "2026-03-15T10:00:00Z",
        }
        mock_client.put = AsyncMock(return_value=_make_response(json_data=updated_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        disc = await client_obj.update_discussion(
            "101", "33", message="<p>Updated message</p>"
        )

        assert disc.message == "<p>Updated message</p>"
        call_kwargs = mock_client.put.call_args
        sent_json = call_kwargs[1]["json"]
        assert sent_json["message"] == "<p>Updated message</p>"

    # ---- Modules ----

    @pytest.mark.asyncio
    async def test_list_course_modules(self):
        client_obj = _make_client()
        mock_client = AsyncMock()
        module_data = [
            {
                "id": 11,
                "name": "Module 1",
                "position": 1,
                "items_count": 3,
                "items": [
                    {"id": 1, "title": "Welcome", "type": "Page"},
                ],
            },
        ]
        mock_client.get = AsyncMock(return_value=_make_response(json_data=module_data))
        client_obj._get_client = AsyncMock(return_value=mock_client)

        modules = await client_obj.list_course_modules("101")

        assert len(modules) == 1
        assert modules[0].name == "Module 1"
        # Should include items
        call_kwargs = mock_client.get.call_args
        params = call_kwargs[1].get("params", {})
        assert "include[]" in params


class TestCanvasAPIRateLimiting:
    """Tests for Canvas API rate limit throttling."""

    @pytest.mark.asyncio
    async def test_rate_limit_throttle_when_remaining_low(self):
        """When X-Rate-Limit-Remaining < 100, asyncio.sleep should be called."""
        client_obj = _make_client()
        mock_client = AsyncMock()
        low_rate_response = _make_response(
            json_data={
                "page_id": 1,
                "title": "T",
                "url": "t",
                "body": "",
                "published": True,
                "updated_at": "2026-03-01T10:00:00Z",
            },
            headers={"X-Rate-Limit-Remaining": "50"},
        )
        mock_client.get = AsyncMock(return_value=low_rate_response)
        client_obj._get_client = AsyncMock(return_value=mock_client)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await client_obj.get_page("101", "test-page")
            mock_sleep.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_throttle_when_remaining_high(self):
        """When X-Rate-Limit-Remaining >= 100, asyncio.sleep should NOT be called."""
        client_obj = _make_client()
        mock_client = AsyncMock()
        high_rate_response = _make_response(
            json_data={
                "page_id": 1,
                "title": "T",
                "url": "t",
                "body": "",
                "published": True,
                "updated_at": "2026-03-01T10:00:00Z",
            },
            headers={"X-Rate-Limit-Remaining": "500"},
        )
        mock_client.get = AsyncMock(return_value=high_rate_response)
        client_obj._get_client = AsyncMock(return_value=mock_client)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await client_obj.get_page("101", "test-page")
            mock_sleep.assert_not_called()


class TestCanvasAPIPagination:
    """Tests for Canvas API Link header pagination."""

    @pytest.mark.asyncio
    async def test_pagination_follows_next_link(self):
        """_paginate should follow rel='next' links until exhausted."""
        client_obj = _make_client()
        mock_client = AsyncMock()

        # First response has a next link
        page1_resp = _make_response(
            json_data=[
                {
                    "page_id": 1,
                    "title": "Page 1",
                    "url": "p1",
                    "body": "",
                    "published": True,
                    "updated_at": "2026-03-01T10:00:00Z",
                },
            ],
            headers={
                "X-Rate-Limit-Remaining": "500",
                "Link": '<https://canvas.example.com/api/v1/courses/101/pages?page=2&per_page=10>; rel="next"',
            },
        )
        # Second response has no next link
        page2_resp = _make_response(
            json_data=[
                {
                    "page_id": 2,
                    "title": "Page 2",
                    "url": "p2",
                    "body": "",
                    "published": True,
                    "updated_at": "2026-03-02T10:00:00Z",
                },
            ],
            headers={"X-Rate-Limit-Remaining": "500"},
        )
        mock_client.get = AsyncMock(side_effect=[page1_resp, page2_resp])
        client_obj._get_client = AsyncMock(return_value=mock_client)

        pages = await client_obj.list_course_pages("101")

        assert len(pages) == 2
        assert pages[0].page_id == "1"
        assert pages[1].page_id == "2"
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_pagination_single_page(self):
        """When no Link header with rel='next', return single page of results."""
        client_obj = _make_client()
        mock_client = AsyncMock()

        resp = _make_response(
            json_data=[
                {
                    "page_id": 1,
                    "title": "Only Page",
                    "url": "only",
                    "body": "",
                    "published": True,
                    "updated_at": "2026-03-01T10:00:00Z",
                },
            ],
            headers={"X-Rate-Limit-Remaining": "500"},
        )
        mock_client.get = AsyncMock(return_value=resp)
        client_obj._get_client = AsyncMock(return_value=mock_client)

        pages = await client_obj.list_course_pages("101")

        assert len(pages) == 1
        assert mock_client.get.call_count == 1


class TestCanvasAPIRetry:
    """Tests for retry logic on 403/429 responses."""

    @pytest.mark.asyncio
    async def test_retry_on_429(self):
        """Should retry on 429 Too Many Requests."""
        client_obj = _make_client()
        mock_client = AsyncMock()

        rate_limited_resp = _make_response(
            status_code=429, headers={"X-Rate-Limit-Remaining": "0"}
        )
        success_resp = _make_response(
            json_data={
                "page_id": 1,
                "title": "T",
                "url": "t",
                "body": "",
                "published": True,
                "updated_at": "2026-03-01T10:00:00Z",
            },
            headers={"X-Rate-Limit-Remaining": "500"},
        )
        mock_client.get = AsyncMock(side_effect=[rate_limited_resp, success_resp])
        client_obj._get_client = AsyncMock(return_value=mock_client)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            page = await client_obj.get_page("101", "test")

        assert page.page_id == "1"
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """Should raise after retries exhausted."""
        client_obj = _make_client()
        mock_client = AsyncMock()

        rate_limited_resp = _make_response(
            status_code=429, headers={"X-Rate-Limit-Remaining": "0"}
        )
        mock_client.get = AsyncMock(return_value=rate_limited_resp)
        client_obj._get_client = AsyncMock(return_value=mock_client)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(Exception):
                await client_obj.get_page("101", "test")
