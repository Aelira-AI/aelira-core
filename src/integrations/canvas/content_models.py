"""
Canvas Content Data Models

Pydantic models for Canvas LMS content types retrieved via REST API.
These represent the HTML-bearing content items that Aelira scans for
accessibility issues.
"""

from typing import Any, Optional
from pydantic import BaseModel
from datetime import datetime
from enum import Enum


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 datetime string from the Canvas API, returning None for null."""
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CanvasContentType(str, Enum):
    """Types of course content items scanned for accessibility.

    FILE differs from the other five: it's not HTML-bearing (no
    content_body) — it's a real uploaded file (pdf/docx/pptx/...) scanned
    via the CloudJobQueue file-download pipeline rather than the in-process
    axe-core pipeline the other types use. See
    CanvasContentScanner.scan_course_content().
    """

    PAGE = "page"
    ASSIGNMENT = "assignment"
    ANNOUNCEMENT = "announcement"
    QUIZ = "quiz"
    DISCUSSION = "discussion"
    FILE = "file"


class CanvasPageInfo(BaseModel):
    """Canvas wiki page information."""

    page_id: str
    title: str
    url_slug: str
    body: str  # HTML content; empty string when Canvas returns null
    published: bool
    updated_at: Optional[datetime] = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "CanvasPageInfo":
        """Construct from a Canvas REST API page object."""
        return cls(
            page_id=str(data["page_id"]),
            title=data["title"],
            url_slug=data["url"],
            body=data.get("body") or "",
            published=data.get("published", False),
            updated_at=_parse_datetime(data.get("updated_at")),
        )


class CanvasAssignmentInfo(BaseModel):
    """Canvas assignment information."""

    id: str
    name: str
    description: str  # HTML content; empty string when Canvas returns null
    due_at: Optional[datetime] = None
    published: bool
    updated_at: Optional[datetime] = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "CanvasAssignmentInfo":
        """Construct from a Canvas REST API assignment object."""
        return cls(
            id=str(data["id"]),
            name=data["name"],
            description=data.get("description") or "",
            due_at=_parse_datetime(data.get("due_at")),
            published=data.get("published", False),
            updated_at=_parse_datetime(data.get("updated_at")),
        )


class CanvasAnnouncementInfo(BaseModel):
    """Canvas announcement (discussion topic marked as announcement) information."""

    id: str
    title: str
    message: str  # HTML content; empty string when Canvas returns null
    posted_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "CanvasAnnouncementInfo":
        """Construct from a Canvas REST API discussion topic (announcement) object."""
        return cls(
            id=str(data["id"]),
            title=data["title"],
            message=data.get("message") or "",
            posted_at=_parse_datetime(data.get("posted_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )


class CanvasQuizInfo(BaseModel):
    """Canvas quiz information."""

    id: str
    title: str
    description: str  # HTML content; empty string when Canvas returns null
    quiz_type: Optional[str] = None  # e.g. "assignment", "practice_quiz", "survey"
    published: bool
    updated_at: Optional[datetime] = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "CanvasQuizInfo":
        """Construct from a Canvas REST API quiz object."""
        return cls(
            id=str(data["id"]),
            title=data["title"],
            description=data.get("description") or "",
            quiz_type=data.get("quiz_type"),
            published=data.get("published", False),
            updated_at=_parse_datetime(data.get("updated_at")),
        )


class CanvasDiscussionInfo(BaseModel):
    """Canvas discussion topic information."""

    id: str
    title: str
    message: str  # HTML content; empty string when Canvas returns null
    posted_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "CanvasDiscussionInfo":
        """Construct from a Canvas REST API discussion topic object."""
        return cls(
            id=str(data["id"]),
            title=data["title"],
            message=data.get("message") or "",
            posted_at=_parse_datetime(data.get("posted_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )


class CanvasModuleInfo(BaseModel):
    """Canvas module information (navigational container, no HTML body of its own)."""

    id: str
    name: str
    position: int
    items_count: int
    items: list[dict[str, Any]]

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "CanvasModuleInfo":
        """Construct from a Canvas REST API module object."""
        return cls(
            id=str(data["id"]),
            name=data["name"],
            position=data.get("position", 0),
            items_count=data.get("items_count", 0),
            items=data.get("items") or [],
        )


__all__ = [
    "CanvasContentType",
    "CanvasPageInfo",
    "CanvasAssignmentInfo",
    "CanvasAnnouncementInfo",
    "CanvasQuizInfo",
    "CanvasDiscussionInfo",
    "CanvasModuleInfo",
]
