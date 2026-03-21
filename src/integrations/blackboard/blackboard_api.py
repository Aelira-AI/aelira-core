"""
Blackboard Learn REST API Client

Handles file operations with Blackboard Learn REST API.

Blackboard API Documentation:
- https://developer.blackboard.com/portal/displayApi/Learn
- Content API: https://developer.blackboard.com/portal/displayApi/Learn/REST/Public/Contents
"""

import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
import httpx

from .models import (
    BlackboardFileInfo,
    BlackboardCourseInfo,
    BlackboardUserInfo,
    BlackboardUploadResult,
    BlackboardDownloadResult,
)

logger = logging.getLogger(__name__)


class BlackboardAPIClient:
    """
    Blackboard Learn REST API client for file operations.

    Provides methods for:
    - Browsing courses and content
    - Downloading files
    - Uploading files
    - Managing content metadata
    """

    def __init__(
        self,
        blackboard_instance_url: str,
        access_token: str,
        credential_id: Optional[str] = None,
    ):
        """
        Initialize Blackboard API client.

        Args:
            blackboard_instance_url: Blackboard instance URL (e.g., "https://blackboard.university.edu")
            access_token: Blackboard OAuth access token
            credential_id: Optional credential ID for tracking
        """
        self.blackboard_url = blackboard_instance_url.rstrip("/")
        self.access_token = access_token
        self.credential_id = credential_id
        self.api_base = f"{self.blackboard_url}/learn/api/public/v1"

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # User and Course Operations
    # =========================================================================

    async def get_current_user(self) -> BlackboardUserInfo:
        """Get current user information"""
        client = await self._get_client()
        response = await client.get(f"{self.api_base}/users/me")
        response.raise_for_status()
        data = response.json()

        return BlackboardUserInfo(
            id=data["id"],
            user_name=data["userName"],
            student_id=data.get("studentId"),
            email=data.get("contact", {}).get("email"),
            name=data.get("name", {}),
            availability=data.get("availability", {}),
            created_at=data.get("created"),
            modified_at=data.get("modified"),
        )

    async def list_courses(
        self,
        availability: str = "available",
    ) -> List[BlackboardCourseInfo]:
        """
        List courses for current user.

        Args:
            availability: Filter by availability (available, disabled, term)

        Returns:
            List of course information
        """
        client = await self._get_client()
        params = {
            "fields": "id,courseId,name,description,created,modified,availability,enrollment,locale",
        }

        response = await client.get(f"{self.api_base}/users/me/courses", params=params)
        response.raise_for_status()
        courses_data = response.json()

        courses = []
        for course in courses_data.get("results", []):
            courses.append(
                BlackboardCourseInfo(
                    id=course["id"],
                    course_id=course.get("courseId", ""),
                    name=course.get("name", ""),
                    description=course.get("description"),
                    created_at=course.get("created"),
                    modified_at=course.get("modified"),
                    availability=course.get("availability", {}),
                    enrollment=course.get("enrollment", {}),
                    locale=course.get("locale"),
                    is_available=course.get("availability", {}).get("available", False),
                )
            )

        return courses

    # =========================================================================
    # Content Operations (Files and Folders)
    # =========================================================================

    async def list_course_content(
        self,
        course_id: str,
        content_id: Optional[str] = None,
    ) -> List[BlackboardFileInfo]:
        """
        List content in a course or folder.

        Args:
            course_id: Blackboard course ID
            content_id: Optional parent content ID (default: root)

        Returns:
            List of content items (files and folders)
        """
        client = await self._get_client()

        if content_id:
            url = f"{self.api_base}/courses/{course_id}/contents/{content_id}/children"
        else:
            url = f"{self.api_base}/courses/{course_id}/contents"

        response = await client.get(url)
        response.raise_for_status()
        content_data = response.json()

        content_items = []
        for item in content_data.get("results", []):
            content_items.append(self._parse_content_item(item, course_id))

        return content_items

    async def get_content_item(
        self, course_id: str, content_id: str
    ) -> BlackboardFileInfo:
        """
        Get content item information by ID.

        Args:
            course_id: Blackboard course ID
            content_id: Content item ID

        Returns:
            Content item information
        """
        client = await self._get_client()
        response = await client.get(
            f"{self.api_base}/courses/{course_id}/contents/{content_id}"
        )
        response.raise_for_status()
        item_data = response.json()

        return self._parse_content_item(item_data, course_id)

    async def download_file(
        self,
        course_id: str,
        content_id: str,
        local_path: str,
    ) -> BlackboardDownloadResult:
        """
        Download a file from Blackboard.

        Args:
            course_id: Blackboard course ID
            content_id: Content item ID
            local_path: Local path to save file

        Returns:
            BlackboardDownloadResult with download status
        """
        try:
            # Get content item to find attachment
            content_item = await self.get_content_item(course_id, content_id)

            # Get attachments for this content item
            client = await self._get_client()
            attachments_response = await client.get(
                f"{self.api_base}/courses/{course_id}/contents/{content_id}/attachments"
            )
            attachments_response.raise_for_status()
            attachments = attachments_response.json().get("results", [])

            if not attachments:
                return BlackboardDownloadResult(
                    success=False,
                    error="No attachments found for this content item",
                )

            # Download first attachment (most common case)
            attachment = attachments[0]
            attachment_id = attachment["id"]

            # Download attachment
            download_response = await client.get(
                f"{self.api_base}/courses/{course_id}/contents/{content_id}/attachments/{attachment_id}/download",
                follow_redirects=True,
            )
            download_response.raise_for_status()

            # Save to local file
            local_path_obj = Path(local_path)
            local_path_obj.parent.mkdir(parents=True, exist_ok=True)

            with open(local_path, "wb") as f:
                f.write(download_response.content)

            logger.info(f"Downloaded Blackboard content {content_id} to {local_path}")

            return BlackboardDownloadResult(
                success=True,
                local_path=local_path,
                file_name=attachment.get("fileName", content_item.file_name),
                content_type=attachment.get("mimeType"),
                size=len(download_response.content),
            )

        except Exception as e:
            logger.error(f"Failed to download Blackboard content {content_id}: {e}")
            return BlackboardDownloadResult(
                success=False,
                error=str(e),
            )

    async def upload_file(
        self,
        course_id: str,
        local_path: str,
        parent_content_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> BlackboardUploadResult:
        """
        Upload a file to Blackboard course.

        Args:
            course_id: Blackboard course ID
            local_path: Local file path
            parent_content_id: Optional parent content ID (default: root)
            title: Optional title for content item

        Returns:
            BlackboardUploadResult with upload status
        """
        try:
            local_path_obj = Path(local_path)
            if not local_path_obj.exists():
                return BlackboardUploadResult(
                    success=False,
                    error=f"File not found: {local_path}",
                )

            title = title or local_path_obj.name
            client = await self._get_client()

            # Step 1: Create content item
            content_data = {
                "title": title,
                "contentHandler": {"id": "resource/x-bb-file"},
                "availability": {"available": "Yes"},
            }

            if parent_content_id:
                content_data["parentId"] = parent_content_id

            create_response = await client.post(
                f"{self.api_base}/courses/{course_id}/contents",
                json=content_data,
            )
            create_response.raise_for_status()
            content_item = create_response.json()
            content_id = content_item["id"]

            # Step 2: Upload file as attachment
            with open(local_path, "rb") as f:
                files = {
                    "file": (
                        local_path_obj.name,
                        f,
                        self._guess_content_type(local_path_obj.name),
                    )
                }

                # Remove Content-Type header for multipart upload
                upload_client = httpx.AsyncClient(
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                    },
                    timeout=60.0,
                )

                try:
                    upload_response = await upload_client.post(
                        f"{self.api_base}/courses/{course_id}/contents/{content_id}/attachments",
                        files=files,
                    )
                    upload_response.raise_for_status()
                finally:
                    await upload_client.aclose()

            attachment_data = upload_response.json()

            logger.info(
                f"Uploaded file to Blackboard course {course_id}: {title} -> {content_id}"
            )

            return BlackboardUploadResult(
                success=True,
                file_id=attachment_data.get("id"),
                file_name=attachment_data.get("fileName", title),
                content_id=content_id,
                web_view_link=f"{self.blackboard_url}/webapps/blackboard/content/listContent.jsp?course_id={course_id}&content_id={content_id}",
            )

        except Exception as e:
            logger.error(f"Failed to upload file to Blackboard: {e}")
            return BlackboardUploadResult(
                success=False,
                error=str(e),
            )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _parse_content_item(
        self, item_data: Dict[str, Any], course_id: str
    ) -> BlackboardFileInfo:
        """Parse Blackboard content item data into BlackboardFileInfo"""
        return BlackboardFileInfo(
            id=item_data["id"],
            title=item_data.get("title", ""),
            content_handler=item_data.get("contentHandler", {}).get("id", ""),
            availability=item_data.get("availability", {}),
            content_type=item_data.get("contentHandler", {}).get("id"),
            created_at=item_data.get("created"),
            modified_at=item_data.get("modified"),
            parent_id=item_data.get("parentId"),
            course_id=course_id,
            has_children=item_data.get("hasChildren", False),
        )

    def _guess_content_type(self, filename: str) -> str:
        """Guess MIME type from filename extension"""
        import mimetypes

        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or "application/octet-stream"


__all__ = ["BlackboardAPIClient"]
