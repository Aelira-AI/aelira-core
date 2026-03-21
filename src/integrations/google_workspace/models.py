"""
Google Workspace Data Models

Pydantic models for Google Drive API responses and internal data structures.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class GoogleFileInfo(BaseModel):
    """Information about a file in Google Drive"""

    id: str = Field(..., description="Google Drive file ID")
    name: str = Field(..., description="File name")
    mime_type: str = Field(..., alias="mimeType", description="MIME type")
    size: Optional[int] = Field(None, description="File size in bytes")
    created_time: Optional[datetime] = Field(None, alias="createdTime")
    modified_time: Optional[datetime] = Field(None, alias="modifiedTime")
    parents: Optional[List[str]] = Field(None, description="Parent folder IDs")
    web_view_link: Optional[str] = Field(None, alias="webViewLink")
    web_content_link: Optional[str] = Field(None, alias="webContentLink")
    version: Optional[str] = Field(None, description="File version/etag")
    md5_checksum: Optional[str] = Field(None, alias="md5Checksum")
    owners: Optional[List[Dict[str, Any]]] = None
    shared: Optional[bool] = None
    trashed: Optional[bool] = None

    class Config:
        populate_by_name = True
        extra = "allow"

    @property
    def is_google_doc(self) -> bool:
        return self.mime_type == "application/vnd.google-apps.document"

    @property
    def is_google_slide(self) -> bool:
        return self.mime_type == "application/vnd.google-apps.presentation"

    @property
    def is_google_sheet(self) -> bool:
        return self.mime_type == "application/vnd.google-apps.spreadsheet"

    @property
    def is_google_native(self) -> bool:
        return self.mime_type.startswith("application/vnd.google-apps.")

    @property
    def is_folder(self) -> bool:
        return self.mime_type == "application/vnd.google-apps.folder"

    @property
    def is_scannable(self) -> bool:
        """Check if this file type can be scanned"""
        scannable_types = [
            "application/vnd.google-apps.document",
            "application/vnd.google-apps.presentation",
            "application/vnd.google-apps.spreadsheet",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/pdf",
        ]
        return self.mime_type in scannable_types

    @property
    def export_mime_type(self) -> Optional[str]:
        """Get the MIME type to export Google native files to"""
        export_map = {
            "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.google-apps.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        return export_map.get(self.mime_type)

    @property
    def export_extension(self) -> Optional[str]:
        """Get the file extension for exported files"""
        ext_map = {
            "application/vnd.google-apps.document": ".docx",
            "application/vnd.google-apps.presentation": ".pptx",
            "application/vnd.google-apps.spreadsheet": ".xlsx",
        }
        return ext_map.get(self.mime_type)


class GoogleFolderInfo(BaseModel):
    """Information about a folder in Google Drive"""

    id: str
    name: str
    parents: Optional[List[str]] = None
    web_view_link: Optional[str] = Field(None, alias="webViewLink")
    created_time: Optional[datetime] = Field(None, alias="createdTime")
    modified_time: Optional[datetime] = Field(None, alias="modifiedTime")

    class Config:
        populate_by_name = True


class GoogleUserInfo(BaseModel):
    """Information about a Google user"""

    id: str
    email: str
    name: Optional[str] = None
    picture: Optional[str] = None


class GoogleDriveInfo(BaseModel):
    """Information about a shared drive"""

    id: str
    name: str
    color_rgb: Optional[str] = Field(None, alias="colorRgb")
    created_time: Optional[datetime] = Field(None, alias="createdTime")
    hidden: Optional[bool] = None
    restrictions: Optional[Dict[str, bool]] = None

    class Config:
        populate_by_name = True


class GoogleWebhookChannel(BaseModel):
    """Google Drive webhook channel/subscription"""

    id: str  # Our channel ID (UUID)
    resource_id: str = Field(..., alias="resourceId")  # Google's resource ID
    resource_uri: Optional[str] = Field(None, alias="resourceUri")
    expiration: Optional[int] = None  # Unix timestamp in milliseconds
    token: Optional[str] = None  # Our verification token

    class Config:
        populate_by_name = True

    @property
    def expiration_datetime(self) -> Optional[datetime]:
        if self.expiration:
            return datetime.fromtimestamp(self.expiration / 1000)
        return None


class GoogleExportRequest(BaseModel):
    """Request to export a Google file"""

    file_id: str
    export_format: Optional[str] = None  # MIME type to export to


class GoogleUploadRequest(BaseModel):
    """Request to upload a file to Google Drive"""

    name: str
    parent_id: Optional[str] = None
    mime_type: Optional[str] = None
    description: Optional[str] = None


class GoogleScanRequest(BaseModel):
    """Request to scan a Google Drive file or folder"""

    file_id: Optional[str] = None
    folder_id: Optional[str] = None
    recursive: bool = True
    file_types: Optional[List[str]] = None  # Filter by MIME types
