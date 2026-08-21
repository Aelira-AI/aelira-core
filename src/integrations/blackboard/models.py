"""
Blackboard Learn REST API Models

Data models for Blackboard Learn REST API integration.
Supports OAuth 2.0 authentication and file operations.

Blackboard API Documentation:
- https://developer.blackboard.com/portal/displayApi
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class BlackboardFileType:
    """Blackboard file types"""

    FILE = "file"
    FOLDER = "folder"
    DOCUMENT = "document"
    LINK = "link"


class BlackboardFileInfo(BaseModel):
    """Blackboard content item (file) information"""

    id: str
    title: str
    content_handler: str  # resource/file, resource/x-bb-document, etc.
    availability: dict  # available, allowGuests, adaptiveRelease
    content_type: Optional[str] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    file_size: Optional[int] = None
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    download_url: Optional[str] = None
    parent_id: Optional[str] = None  # Content parent ID
    course_id: Optional[str] = None
    has_children: bool = False


class BlackboardFolderInfo(BaseModel):
    """Blackboard content folder information"""

    id: str
    title: str
    content_handler: str
    parent_id: Optional[str] = None
    course_id: Optional[str] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    has_children: bool = False
    children_count: int = 0


class BlackboardCourseInfo(BaseModel):
    """Blackboard course information"""

    id: str  # Course ID (e.g., _12345_1)
    course_id: str  # External course ID
    name: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    availability: dict
    enrollment: dict
    locale: Optional[str] = None
    is_available: bool = True


class BlackboardUserInfo(BaseModel):
    """Blackboard user information"""

    id: str
    user_name: str
    student_id: Optional[str] = None
    email: Optional[str] = None
    name: dict  # given, family, title
    availability: dict
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None


class BlackboardOAuthCredential(BaseModel):
    """Blackboard OAuth credentials"""

    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_at: Optional[datetime] = None
    blackboard_instance_url: str  # e.g., "https://blackboard.university.edu"
    user_id: str
    scope: Optional[str] = None


class BlackboardUploadResult(BaseModel):
    """Result of Blackboard file upload"""

    success: bool
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    content_id: Optional[str] = None  # Blackboard content item ID
    web_view_link: Optional[str] = None
    error: Optional[str] = None
    failure_kind: Optional[str] = None
    status_code: Optional[int] = None
    retry_after: Optional[int] = None


class BlackboardDownloadResult(BaseModel):
    """Result of Blackboard file download"""

    success: bool
    local_path: Optional[str] = None
    file_name: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
    error: Optional[str] = None


__all__ = [
    "BlackboardFileType",
    "BlackboardFileInfo",
    "BlackboardFolderInfo",
    "BlackboardCourseInfo",
    "BlackboardUserInfo",
    "BlackboardOAuthCredential",
    "BlackboardUploadResult",
    "BlackboardDownloadResult",
]
