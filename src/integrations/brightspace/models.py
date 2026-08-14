"""
Pydantic models for D2L Brightspace Valence API responses.

API Documentation: https://docs.valence.desire2learn.com/
"""

from typing import Optional
from pydantic import BaseModel


class BrightspaceUserInfo(BaseModel):
    """Brightspace user information from whoami endpoint."""

    Identifier: str  # User ID (Brightspace uses PascalCase)
    FirstName: str
    LastName: str
    UniqueName: str  # Username
    ProfileIdentifier: Optional[str] = None  # Profile/avatar identifier


class BrightspaceCourseInfo(BaseModel):
    """Brightspace course information from enrollments endpoint."""

    OrgUnitId: int  # Course ID (Brightspace uses OrgUnitId)
    Name: str  # Course name
    Code: Optional[str] = None  # Course code
    StartDate: Optional[str] = None  # ISO 8601 datetime
    EndDate: Optional[str] = None  # ISO 8601 datetime
    IsActive: bool = True  # Course active status


class BrightspaceContentInfo(BaseModel):
    """Brightspace content module information."""

    Id: int  # Content module ID
    Title: str  # Module title
    ShortTitle: Optional[str] = None
    Type: int  # Content type (1=module, 2=topic, etc)
    ModuleStartDate: Optional[str] = None
    ModuleEndDate: Optional[str] = None
    IsHidden: bool = False
    IsLocked: bool = False


class BrightspaceFileInfo(BaseModel):
    """Brightspace file/attachment information."""

    FileId: int  # File identifier
    FileName: str  # File name
    Size: int  # File size in bytes
    Extension: Optional[str] = None  # File extension (e.g., "pdf")
    CreatedDate: Optional[str] = None  # ISO 8601 datetime
    LastModifiedDate: Optional[str] = None  # ISO 8601 datetime


class BrightspaceUploadResult(BaseModel):
    """Result from Brightspace file upload operation."""

    success: bool
    file_id: Optional[int] = None
    file_url: Optional[str] = None
    error: Optional[str] = None


class BrightspaceDownloadResult(BaseModel):
    """Result from Brightspace file download operation."""

    success: bool
    local_path: Optional[str] = None
    file_size: Optional[int] = None
    error: Optional[str] = None


class BrightspaceTopicInfo(BaseModel):
    """A topic (leaf node) in Brightspace content tree."""

    TopicId: int
    Title: str
    TopicType: int  # 1=File, 3=Link, 5=HTML
    Url: Optional[str] = None
    Description: Optional[str] = None  # HTML content for HTML topics
    IsHidden: bool = False
    IsLocked: bool = False
    FileName: Optional[str] = None
    FileSize: Optional[int] = None


class BrightspaceModuleChild(BaseModel):
    """A child item in a module — either a sub-module or a topic."""

    Id: int
    Title: str
    ShortTitle: Optional[str] = None
    Type: int  # 0=Module, 1=Topic
    TopicType: Optional[int] = None  # Only set for topics
    Url: Optional[str] = None
    Description: Optional[str] = None
    IsHidden: bool = False
    IsLocked: bool = False
    LastModifiedDate: Optional[str] = None


class BrightspaceScannable(BaseModel):
    """A content item that can be scanned for accessibility."""

    topic_id: int
    org_unit_id: int
    module_id: Optional[int] = None
    title: str
    content_type: str  # "file" or "html"
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    url: Optional[str] = None
    description: Optional[str] = None
    module_path: str = ""  # Breadcrumb like "Week 1 / Lecture Notes"
