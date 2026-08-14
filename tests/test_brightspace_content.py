"""
Tests for Brightspace recursive content traversal and topic operations.

Tests verify that the BrightspaceAPIClient:
- Recursively walks module trees
- Classifies file and HTML topics correctly
- Skips link topics and hidden items
- Builds correct module_path breadcrumbs
"""

import pytest
from unittest.mock import AsyncMock, patch

from src.integrations.brightspace.brightspace_api import (
    BrightspaceAPIClient,
    TOPIC_TYPE_FILE,
    TOPIC_TYPE_LINK,
    TOPIC_TYPE_HTML,
    CONTENT_TYPE_MODULE,
    CONTENT_TYPE_TOPIC,
)
from src.integrations.brightspace.models import (
    BrightspaceContentInfo,
    BrightspaceScannable,
)


@pytest.fixture
def client():
    """Create a BrightspaceAPIClient for testing."""
    return BrightspaceAPIClient(
        brightspace_instance_url="https://brightspace.test.edu",
        access_token="test-token",
    )


def _make_content_info(
    id: int,
    title: str,
    type: int,
    is_hidden: bool = False,
    topic_type: int | None = None,
    url: str | None = None,
) -> BrightspaceContentInfo:
    """Helper to build a BrightspaceContentInfo."""
    return BrightspaceContentInfo(
        Id=id,
        Title=title,
        Type=type,
        IsHidden=is_hidden,
    )


def _make_module_child(
    id: int,
    title: str,
    type: int,
    topic_type: int | None = None,
    is_hidden: bool = False,
    url: str | None = None,
    description: str | None = None,
) -> dict:
    """Helper to build a raw child dict (as returned by the API)."""
    child = {
        "Id": id,
        "Title": title,
        "Type": type,
        "IsHidden": is_hidden,
        "IsLocked": False,
        "ShortTitle": None,
        "LastModifiedDate": None,
    }
    if topic_type is not None:
        child["TopicType"] = topic_type
    if url is not None:
        child["Url"] = url
    if description is not None:
        child["Description"] = description
    return child


# =============================================================================
# Recursive Content Traversal Tests
# =============================================================================


class TestRecursiveContentTraversal:
    """Test get_course_content_recursive with mocked API calls."""

    @pytest.mark.asyncio
    async def test_flat_course_with_file_topics(self, client):
        """One module with one file topic returns 1 scannable."""
        root_content = [
            _make_content_info(id=100, title="Week 1", type=CONTENT_TYPE_MODULE),
        ]
        week1_children = [
            _make_module_child(
                id=201,
                title="Lecture.pdf",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_FILE,
                url="https://brightspace.test.edu/d2l/file/201",
            ),
        ]

        with patch.object(
            client,
            "get_course_content",
            new_callable=AsyncMock,
            return_value=root_content,
        ), patch.object(
            client,
            "get_module_children",
            new_callable=AsyncMock,
            return_value=week1_children,
        ):
            result = await client.get_course_content_recursive(org_unit_id=9999)

        assert len(result) == 1
        scannable = result[0]
        assert isinstance(scannable, BrightspaceScannable)
        assert scannable.topic_id == 201
        assert scannable.org_unit_id == 9999
        assert scannable.content_type == "file"
        assert scannable.title == "Lecture.pdf"
        assert scannable.module_path == "Week 1"

    @pytest.mark.asyncio
    async def test_html_topics_detected(self, client):
        """TopicType=5 is classified as html."""
        root_content = [
            _make_content_info(id=100, title="Module A", type=CONTENT_TYPE_MODULE),
        ]
        children = [
            _make_module_child(
                id=301,
                title="Welcome Page",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_HTML,
                description="<p>Hello</p>",
            ),
        ]

        with patch.object(
            client,
            "get_course_content",
            new_callable=AsyncMock,
            return_value=root_content,
        ), patch.object(
            client, "get_module_children", new_callable=AsyncMock, return_value=children
        ):
            result = await client.get_course_content_recursive(org_unit_id=9999)

        assert len(result) == 1
        assert result[0].content_type == "html"
        assert result[0].title == "Welcome Page"

    @pytest.mark.asyncio
    async def test_link_topics_skipped(self, client):
        """TopicType=3 (link) should not be included in results."""
        root_content = [
            _make_content_info(id=100, title="Links Module", type=CONTENT_TYPE_MODULE),
        ]
        children = [
            _make_module_child(
                id=401,
                title="External Resource",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_LINK,
                url="https://example.com",
            ),
        ]

        with patch.object(
            client,
            "get_course_content",
            new_callable=AsyncMock,
            return_value=root_content,
        ), patch.object(
            client, "get_module_children", new_callable=AsyncMock, return_value=children
        ):
            result = await client.get_course_content_recursive(org_unit_id=9999)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_hidden_items_skipped(self, client):
        """IsHidden=True items should be excluded from results."""
        root_content = [
            _make_content_info(
                id=100, title="Visible Module", type=CONTENT_TYPE_MODULE
            ),
        ]
        children = [
            _make_module_child(
                id=501,
                title="Hidden File.pdf",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_FILE,
                is_hidden=True,
            ),
            _make_module_child(
                id=502,
                title="Visible File.pdf",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_FILE,
                is_hidden=False,
            ),
        ]

        with patch.object(
            client,
            "get_course_content",
            new_callable=AsyncMock,
            return_value=root_content,
        ), patch.object(
            client, "get_module_children", new_callable=AsyncMock, return_value=children
        ):
            result = await client.get_course_content_recursive(org_unit_id=9999)

        assert len(result) == 1
        assert result[0].topic_id == 502
        assert result[0].title == "Visible File.pdf"

    @pytest.mark.asyncio
    async def test_hidden_modules_skipped(self, client):
        """Hidden modules (IsHidden=True) should be entirely skipped."""
        root_content = [
            _make_content_info(
                id=100, title="Hidden Module", type=CONTENT_TYPE_MODULE, is_hidden=True
            ),
            _make_content_info(
                id=101, title="Visible Module", type=CONTENT_TYPE_MODULE
            ),
        ]
        visible_children = [
            _make_module_child(
                id=601,
                title="File.pdf",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_FILE,
            ),
        ]

        async def mock_get_children(org_unit_id, module_id):
            if module_id == 101:
                return visible_children
            # Should never be called for hidden module 100
            raise AssertionError(
                f"get_module_children called for hidden module {module_id}"
            )

        with patch.object(
            client,
            "get_course_content",
            new_callable=AsyncMock,
            return_value=root_content,
        ), patch.object(client, "get_module_children", side_effect=mock_get_children):
            result = await client.get_course_content_recursive(org_unit_id=9999)

        assert len(result) == 1
        assert result[0].topic_id == 601

    @pytest.mark.asyncio
    async def test_nested_modules(self, client):
        """Module inside module builds correct breadcrumb path."""
        root_content = [
            _make_content_info(id=100, title="Week 1", type=CONTENT_TYPE_MODULE),
        ]
        week1_children = [
            _make_module_child(id=200, title="Lecture Notes", type=CONTENT_TYPE_MODULE),
        ]
        lecture_children = [
            _make_module_child(
                id=301,
                title="Chapter1.pdf",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_FILE,
            ),
        ]

        call_count = 0

        async def mock_get_children(org_unit_id, module_id):
            nonlocal call_count
            call_count += 1
            if module_id == 100:
                return week1_children
            elif module_id == 200:
                return lecture_children
            return []

        with patch.object(
            client,
            "get_course_content",
            new_callable=AsyncMock,
            return_value=root_content,
        ), patch.object(client, "get_module_children", side_effect=mock_get_children):
            result = await client.get_course_content_recursive(org_unit_id=9999)

        assert len(result) == 1
        scannable = result[0]
        assert scannable.topic_id == 301
        assert scannable.module_path == "Week 1 / Lecture Notes"
        assert scannable.content_type == "file"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_mixed_content_types(self, client):
        """Multiple topic types in one module are handled correctly."""
        root_content = [
            _make_content_info(id=100, title="Resources", type=CONTENT_TYPE_MODULE),
        ]
        children = [
            _make_module_child(
                id=701,
                title="Syllabus.pdf",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_FILE,
            ),
            _make_module_child(
                id=702,
                title="Course Overview",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_HTML,
            ),
            _make_module_child(
                id=703,
                title="Library Link",
                type=CONTENT_TYPE_TOPIC,
                topic_type=TOPIC_TYPE_LINK,
            ),
        ]

        with patch.object(
            client,
            "get_course_content",
            new_callable=AsyncMock,
            return_value=root_content,
        ), patch.object(
            client, "get_module_children", new_callable=AsyncMock, return_value=children
        ):
            result = await client.get_course_content_recursive(org_unit_id=9999)

        assert len(result) == 2
        types = {s.content_type for s in result}
        assert types == {"file", "html"}
        titles = {s.title for s in result}
        assert "Library Link" not in titles

    @pytest.mark.asyncio
    async def test_empty_course(self, client):
        """Course with no content returns empty list."""
        with patch.object(
            client, "get_course_content", new_callable=AsyncMock, return_value=[]
        ):
            result = await client.get_course_content_recursive(org_unit_id=9999)

        assert result == []
