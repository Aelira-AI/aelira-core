"""
Pydantic models for Microsoft 365 / Graph API responses.

These models represent the data structures returned by Microsoft Graph API
for OneDrive and SharePoint file operations.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class MicrosoftUserInfo(BaseModel):
    """Microsoft Graph user information."""

    id: str
    display_name: Optional[str] = Field(None, alias="displayName")
    email: Optional[str] = None
    user_principal_name: Optional[str] = Field(None, alias="userPrincipalName")

    class Config:
        populate_by_name = True


class MicrosoftDriveInfo(BaseModel):
    """OneDrive or SharePoint drive information."""

    id: str
    name: str
    drive_type: Optional[str] = Field(
        None, alias="driveType"
    )  # personal, business, documentLibrary
    owner: Optional[MicrosoftUserInfo] = None
    web_url: Optional[str] = Field(None, alias="webUrl")

    class Config:
        populate_by_name = True


class MicrosoftSiteInfo(BaseModel):
    """SharePoint site information."""

    id: str
    name: str
    display_name: Optional[str] = Field(None, alias="displayName")
    web_url: Optional[str] = Field(None, alias="webUrl")

    class Config:
        populate_by_name = True


class MicrosoftFileInfo(BaseModel):
    """
    Microsoft Graph drive item (file) information.

    Maps to the driveItem resource type from Microsoft Graph API.
    Reference: https://learn.microsoft.com/en-us/graph/api/resources/driveitem
    """

    id: str
    name: str
    size: Optional[int] = None
    mime_type: Optional[str] = Field(None, alias="mimeType")
    created_date_time: Optional[datetime] = Field(None, alias="createdDateTime")
    last_modified_date_time: Optional[datetime] = Field(
        None, alias="lastModifiedDateTime"
    )
    web_url: Optional[str] = Field(None, alias="webUrl")
    download_url: Optional[str] = Field(None, alias="@microsoft.graph.downloadUrl")

    # Parent reference
    parent_id: Optional[str] = None
    parent_path: Optional[str] = None

    # File metadata
    file_extension: Optional[str] = None
    c_tag: Optional[str] = Field(None, alias="cTag")  # Change tag for version detection
    e_tag: Optional[str] = Field(None, alias="eTag")

    # Folder indicator
    is_folder: bool = False

    class Config:
        populate_by_name = True

    @property
    def is_word_doc(self) -> bool:
        """Check if this is a Word document."""
        if not self.name:
            return False
        lower_name = self.name.lower()
        return lower_name.endswith((".doc", ".docx"))

    @property
    def is_powerpoint(self) -> bool:
        """Check if this is a PowerPoint presentation."""
        if not self.name:
            return False
        lower_name = self.name.lower()
        return lower_name.endswith((".ppt", ".pptx"))

    @property
    def is_excel(self) -> bool:
        """Check if this is an Excel spreadsheet."""
        if not self.name:
            return False
        lower_name = self.name.lower()
        return lower_name.endswith((".xls", ".xlsx"))

    @property
    def is_pdf(self) -> bool:
        """Check if this is a PDF."""
        if not self.name:
            return False
        return self.name.lower().endswith(".pdf")

    @property
    def is_scannable(self) -> bool:
        """Check if this file type can be scanned for accessibility."""
        return self.is_word_doc or self.is_powerpoint or self.is_excel or self.is_pdf

    @property
    def file_type(self) -> Optional[str]:
        """Get standardized file type for our processors."""
        if not self.name:
            return None

        lower_name = self.name.lower()
        if lower_name.endswith(".docx"):
            return "docx"
        elif lower_name.endswith(".doc"):
            return "doc"
        elif lower_name.endswith(".pptx"):
            return "pptx"
        elif lower_name.endswith(".ppt"):
            return "ppt"
        elif lower_name.endswith(".xlsx"):
            return "xlsx"
        elif lower_name.endswith(".xls"):
            return "xls"
        elif lower_name.endswith(".pdf"):
            return "pdf"
        return None


class MicrosoftFileListResponse(BaseModel):
    """Response model for file listing with pagination."""

    value: List[MicrosoftFileInfo] = []
    next_link: Optional[str] = Field(None, alias="@odata.nextLink")

    class Config:
        populate_by_name = True


class MicrosoftUploadSession(BaseModel):
    """Upload session for resumable uploads."""

    upload_url: str = Field(..., alias="uploadUrl")
    expiration_date_time: Optional[datetime] = Field(None, alias="expirationDateTime")

    class Config:
        populate_by_name = True


class MicrosoftSubscription(BaseModel):
    """Microsoft Graph webhook subscription."""

    id: str
    resource: str
    change_type: str = Field(..., alias="changeType")
    client_state: Optional[str] = Field(None, alias="clientState")
    notification_url: str = Field(..., alias="notificationUrl")
    expiration_date_time: datetime = Field(..., alias="expirationDateTime")

    class Config:
        populate_by_name = True


class MicrosoftDelta(BaseModel):
    """Delta response for change tracking."""

    value: List[MicrosoftFileInfo] = []
    delta_link: Optional[str] = Field(None, alias="@odata.deltaLink")
    next_link: Optional[str] = Field(None, alias="@odata.nextLink")

    class Config:
        populate_by_name = True
