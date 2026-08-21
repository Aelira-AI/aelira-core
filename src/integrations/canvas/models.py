"""
Canvas Integration Models

Data models for Canvas LMS REST API integration.
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel
from datetime import datetime
from enum import Enum


class CanvasFileType(str, Enum):
    """Canvas file types"""

    PDF = "application/pdf"
    WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    POWERPOINT = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    EXCEL = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    IMAGE = "image"
    VIDEO = "video"
    UNKNOWN = "unknown"


class CanvasFileInfo(BaseModel):
    """Canvas file information"""

    id: str
    display_name: str
    filename: str
    content_type: str
    size: int
    url: str
    created_at: datetime
    updated_at: datetime
    folder_id: Optional[str] = None
    folder_path: Optional[str] = None
    thumbnail_url: Optional[str] = None
    preview_url: Optional[str] = None
    locked: bool = False
    hidden: bool = False


class CanvasFolderInfo(BaseModel):
    """Canvas folder information"""

    id: str
    name: str
    full_name: str
    parent_folder_id: Optional[str] = None
    context_type: str
    context_id: str
    files_count: int
    folders_count: int
    created_at: datetime
    updated_at: datetime
    locked: bool = False
    hidden: bool = False


class CanvasCourseInfo(BaseModel):
    """Canvas course information"""

    id: str
    name: str
    course_code: str
    account_id: str
    workflow_state: str
    enrollment_term_id: Optional[str] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    public_description: Optional[str] = None
    storage_quota_mb: Optional[int] = None
    storage_quota_used_mb: Optional[int] = None
    is_public: Optional[bool] = False
    is_public_to_auth_users: Optional[bool] = False


class CanvasUserInfo(BaseModel):
    """Canvas user information"""

    id: str
    name: str
    sortable_name: str
    short_name: str
    login_id: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None
    locale: Optional[str] = None


class CanvasOAuthCredential(BaseModel):
    """Canvas OAuth credentials"""

    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_at: Optional[datetime] = None
    scope: Optional[str] = None
    canvas_instance_url: str  # e.g., "https://canvas.university.edu"
    user_id: str


class CanvasUploadResult(BaseModel):
    """Result of file upload to Canvas with retry-safety classification."""

    success: bool
    outcome: str = "definite_failure"
    correlation_id: Optional[str] = None
    provider_result: Optional[Dict[str, Any]] = None
    file_id: Optional[str] = None
    file_name: Optional[str] = None
    web_view_link: Optional[str] = None
    error: Optional[str] = None


class CanvasDownloadResult(BaseModel):
    """Result of file download from Canvas"""

    success: bool
    local_path: Optional[str] = None
    file_name: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None
    error: Optional[str] = None


__all__ = [
    "CanvasFileType",
    "CanvasFileInfo",
    "CanvasFolderInfo",
    "CanvasCourseInfo",
    "CanvasUserInfo",
    "CanvasOAuthCredential",
    "CanvasUploadResult",
    "CanvasDownloadResult",
]
