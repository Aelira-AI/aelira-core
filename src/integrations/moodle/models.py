"""
Moodle Integration Data Models

Pydantic models for Moodle API responses and requests.
"""

from typing import Optional
from pydantic import BaseModel, Field


class MoodleUserInfo(BaseModel):
    """Moodle user information from get_site_info or core_webservice_get_site_info"""

    id: str = Field(..., description="Moodle user ID")
    username: str = Field(..., description="Username")
    firstname: str = Field(..., description="First name")
    lastname: str = Field(..., description="Last name")
    fullname: str = Field(..., description="Full name")
    email: Optional[str] = Field(None, description="Email address")
    userpictureurl: Optional[str] = Field(None, description="Profile picture URL")
    lang: Optional[str] = Field(None, description="Language preference")


class MoodleCourseInfo(BaseModel):
    """Moodle course information from core_course_get_courses"""

    id: str = Field(..., description="Course ID")
    fullname: str = Field(..., description="Course full name")
    shortname: str = Field(..., description="Course short name")
    categoryid: Optional[str] = Field(None, description="Course category ID")
    summary: Optional[str] = Field(None, description="Course summary")
    format: Optional[str] = Field(
        None, description="Course format (e.g., topics, weeks)"
    )
    visible: bool = Field(True, description="Whether course is visible")


class MoodleFileInfo(BaseModel):
    """Moodle file information from core_files_get_files or repository responses"""

    filename: str = Field(..., description="File name")
    filepath: str = Field(..., description="File path within Moodle")
    filesize: int = Field(..., description="File size in bytes")
    fileurl: str = Field(..., description="Direct download URL with token")
    mimetype: Optional[str] = Field(None, description="MIME type")
    timemodified: int = Field(..., description="Unix timestamp of last modification")
    contextid: Optional[str] = Field(None, description="Moodle context ID")
    component: Optional[str] = Field(None, description="Component (e.g., mod_resource)")
    filearea: Optional[str] = Field(None, description="File area")
    itemid: Optional[str] = Field(None, description="Item ID")
    author: Optional[str] = Field(None, description="File author")


class MoodleFolderInfo(BaseModel):
    """Moodle folder/directory information"""

    name: str = Field(..., description="Folder name")
    path: str = Field(..., description="Full path")
    contextid: Optional[str] = Field(None, description="Moodle context ID")
    component: Optional[str] = Field(None, description="Component")
    filearea: Optional[str] = Field(None, description="File area")


class MoodleUploadResult(BaseModel):
    """Result of uploading a file to Moodle"""

    success: bool = Field(..., description="Whether upload succeeded")
    file_id: Optional[str] = Field(None, description="Moodle file ID")
    file_url: Optional[str] = Field(None, description="URL of uploaded file")
    error: Optional[str] = Field(None, description="Error message if failed")


class MoodleDownloadResult(BaseModel):
    """Result of downloading a file from Moodle"""

    success: bool = Field(..., description="Whether download succeeded")
    local_path: Optional[str] = Field(None, description="Local file path")
    file_size: Optional[int] = Field(None, description="Downloaded file size")
    error: Optional[str] = Field(None, description="Error message if failed")
