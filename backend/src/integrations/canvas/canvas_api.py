"""
Canvas REST API Client

Handles file operations with Canvas LMS REST API.

Canvas API Documentation:
- https://canvas.instructure.com/doc/api/files.html
- https://canvas.instructure.com/doc/api/file.file_uploads.html
"""

import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
import httpx

from .models import (
    CanvasFileInfo,
    CanvasFolderInfo,
    CanvasCourseInfo,
    CanvasUserInfo,
    CanvasUploadResult,
    CanvasDownloadResult,
)

logger = logging.getLogger(__name__)


class CanvasAPIClient:
    """
    Canvas REST API client for file operations.

    Provides methods for:
    - Browsing courses and folders
    - Downloading files
    - Uploading files
    - Managing file metadata
    """

    def __init__(
        self,
        canvas_instance_url: str,
        access_token: str,
        credential_id: Optional[str] = None,
    ):
        """
        Initialize Canvas API client.

        Args:
            canvas_instance_url: Canvas instance URL (e.g., "https://canvas.university.edu")
            access_token: Canvas OAuth access token
            credential_id: Optional credential ID for tracking
        """
        self.canvas_url = canvas_instance_url.rstrip("/")
        self.access_token = access_token
        self.credential_id = credential_id
        self.api_base = f"{self.canvas_url}/api/v1"

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
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

    async def get_current_user(self) -> CanvasUserInfo:
        """Get current user information"""
        client = await self._get_client()
        response = await client.get(f"{self.api_base}/users/self")
        response.raise_for_status()
        data = response.json()

        return CanvasUserInfo(
            id=str(data["id"]),
            name=data["name"],
            sortable_name=data.get("sortable_name", ""),
            short_name=data.get("short_name", ""),
            login_id=data.get("login_id"),
            email=data.get("email"),
            avatar_url=data.get("avatar_url"),
            locale=data.get("locale"),
        )

    async def list_courses(
        self,
        enrollment_state: str = "active",
        include: Optional[List[str]] = None,
    ) -> List[CanvasCourseInfo]:
        """
        List courses for current user.

        Args:
            enrollment_state: Filter by enrollment state (active, completed, etc.)
            include: Optional list of additional data to include

        Returns:
            List of course information
        """
        client = await self._get_client()
        params = {
            "enrollment_state": enrollment_state,
            "per_page": 100,
        }
        if include:
            params["include[]"] = include

        response = await client.get(f"{self.api_base}/courses", params=params)
        response.raise_for_status()
        courses = response.json()

        return [
            CanvasCourseInfo(
                id=str(course["id"]),
                name=course["name"],
                course_code=course.get("course_code", ""),
                account_id=str(course.get("account_id", "")),
                workflow_state=course.get("workflow_state", ""),
                enrollment_term_id=(
                    str(course.get("enrollment_term_id"))
                    if course.get("enrollment_term_id")
                    else None
                ),
                start_at=course.get("start_at"),
                end_at=course.get("end_at"),
                public_description=course.get("public_description"),
                storage_quota_mb=course.get("storage_quota_mb"),
                storage_quota_used_mb=course.get("storage_quota_used_mb"),
                is_public=course.get("is_public", False),
                is_public_to_auth_users=course.get("is_public_to_auth_users", False),
            )
            for course in courses
        ]

    # =========================================================================
    # File and Folder Operations
    # =========================================================================

    async def list_course_files(
        self,
        course_id: str,
        search_term: Optional[str] = None,
        content_types: Optional[List[str]] = None,
    ) -> List[CanvasFileInfo]:
        """
        List files in a course.

        Args:
            course_id: Canvas course ID
            search_term: Optional search query
            content_types: Optional filter by MIME types

        Returns:
            List of file information
        """
        client = await self._get_client()
        params = {"per_page": 100}
        if search_term:
            params["search_term"] = search_term
        if content_types:
            params["content_types[]"] = content_types

        response = await client.get(
            f"{self.api_base}/courses/{course_id}/files", params=params
        )
        response.raise_for_status()
        files = response.json()

        return [self._parse_file_info(file) for file in files]

    async def list_folder_files(
        self,
        folder_id: str,
    ) -> List[CanvasFileInfo]:
        """
        List files in a specific folder.

        Args:
            folder_id: Canvas folder ID

        Returns:
            List of file information
        """
        client = await self._get_client()
        response = await client.get(
            f"{self.api_base}/folders/{folder_id}/files", params={"per_page": 100}
        )
        response.raise_for_status()
        files = response.json()

        return [self._parse_file_info(file) for file in files]

    async def list_course_folders(
        self,
        course_id: str,
    ) -> List[CanvasFolderInfo]:
        """
        List folders in a course.

        Args:
            course_id: Canvas course ID

        Returns:
            List of folder information
        """
        client = await self._get_client()
        response = await client.get(
            f"{self.api_base}/courses/{course_id}/folders", params={"per_page": 100}
        )
        response.raise_for_status()
        folders = response.json()

        return [self._parse_folder_info(folder) for folder in folders]

    async def get_file(self, file_id: str) -> CanvasFileInfo:
        """
        Get file information by ID.

        Args:
            file_id: Canvas file ID

        Returns:
            File information
        """
        client = await self._get_client()
        response = await client.get(f"{self.api_base}/files/{file_id}")
        response.raise_for_status()
        file_data = response.json()

        return self._parse_file_info(file_data)

    async def download_file(
        self,
        file_id: str,
        local_path: str,
    ) -> CanvasDownloadResult:
        """
        Download a file from Canvas.

        Args:
            file_id: Canvas file ID
            local_path: Local path to save file

        Returns:
            CanvasDownloadResult with download status
        """
        try:
            # Get file info first
            file_info = await self.get_file(file_id)

            # Download file content
            client = await self._get_client()
            response = await client.get(file_info.url, follow_redirects=True)
            response.raise_for_status()

            # Save to local file
            local_path_obj = Path(local_path)
            local_path_obj.parent.mkdir(parents=True, exist_ok=True)

            with open(local_path, "wb") as f:
                f.write(response.content)

            logger.info(f"Downloaded Canvas file {file_id} to {local_path}")

            return CanvasDownloadResult(
                success=True,
                local_path=local_path,
                file_name=file_info.filename,
                content_type=file_info.content_type,
                size=file_info.size,
            )

        except Exception as e:
            logger.error(f"Failed to download Canvas file {file_id}: {e}")
            return CanvasDownloadResult(
                success=False,
                error=str(e),
            )

    async def upload_file(
        self,
        course_id: str,
        local_path: str,
        folder_id: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> CanvasUploadResult:
        """
        Upload a file to Canvas course.

        Canvas uses a 3-step upload process:
        1. Request upload URL
        2. Upload file to returned URL
        3. Confirm upload completion

        Args:
            course_id: Canvas course ID
            local_path: Local file path
            folder_id: Optional folder ID (default: course files root)
            file_name: Optional custom file name

        Returns:
            CanvasUploadResult with upload status
        """
        try:
            local_path_obj = Path(local_path)
            if not local_path_obj.exists():
                return CanvasUploadResult(
                    success=False,
                    error=f"File not found: {local_path}",
                )

            file_name = file_name or local_path_obj.name
            file_size = local_path_obj.stat().st_size

            client = await self._get_client()

            # Step 1: Request upload URL
            upload_params = {
                "name": file_name,
                "size": file_size,
                "content_type": self._guess_content_type(file_name),
            }
            if folder_id:
                upload_params["parent_folder_id"] = folder_id

            response = await client.post(
                f"{self.api_base}/courses/{course_id}/files",
                json=upload_params,
            )
            response.raise_for_status()
            upload_data = response.json()

            upload_url = upload_data["upload_url"]
            upload_params_dict = upload_data["upload_params"]

            # Step 2: Upload file
            with open(local_path, "rb") as f:
                files = {"file": (file_name, f, self._guess_content_type(file_name))}
                upload_response = await client.post(
                    upload_url,
                    data=upload_params_dict,
                    files=files,
                    follow_redirects=True,
                )
                upload_response.raise_for_status()

            # Step 3: Get uploaded file info
            file_info = upload_response.json()

            logger.info(
                f"Uploaded file to Canvas course {course_id}: {file_name} -> {file_info.get('id')}"
            )

            return CanvasUploadResult(
                success=True,
                file_id=str(file_info["id"]),
                file_name=file_info["filename"],
                web_view_link=file_info.get("url"),
            )

        except Exception as e:
            logger.error(f"Failed to upload file to Canvas: {e}")
            return CanvasUploadResult(
                success=False,
                error=str(e),
            )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _parse_file_info(self, file_data: Dict[str, Any]) -> CanvasFileInfo:
        """Parse Canvas file data into CanvasFileInfo"""
        return CanvasFileInfo(
            id=str(file_data["id"]),
            display_name=file_data.get("display_name", ""),
            filename=file_data.get("filename", ""),
            content_type=file_data.get("content-type", ""),
            size=file_data.get("size", 0),
            url=file_data.get("url", ""),
            created_at=file_data.get("created_at"),
            updated_at=file_data.get("updated_at"),
            folder_id=(
                str(file_data.get("folder_id")) if file_data.get("folder_id") else None
            ),
            thumbnail_url=file_data.get("thumbnail_url"),
            preview_url=file_data.get("preview_url"),
            locked=file_data.get("locked", False),
            hidden=file_data.get("hidden", False),
        )

    def _parse_folder_info(self, folder_data: Dict[str, Any]) -> CanvasFolderInfo:
        """Parse Canvas folder data into CanvasFolderInfo"""
        return CanvasFolderInfo(
            id=str(folder_data["id"]),
            name=folder_data.get("name", ""),
            full_name=folder_data.get("full_name", ""),
            parent_folder_id=(
                str(folder_data.get("parent_folder_id"))
                if folder_data.get("parent_folder_id")
                else None
            ),
            context_type=folder_data.get("context_type", ""),
            context_id=str(folder_data.get("context_id", "")),
            files_count=folder_data.get("files_count", 0),
            folders_count=folder_data.get("folders_count", 0),
            created_at=folder_data.get("created_at"),
            updated_at=folder_data.get("updated_at"),
            locked=folder_data.get("locked", False),
            hidden=folder_data.get("hidden", False),
        )

    def _guess_content_type(self, filename: str) -> str:
        """Guess MIME type from filename extension"""
        import mimetypes

        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or "application/octet-stream"


# Alias for test compatibility
CanvasAPI = CanvasAPIClient

__all__ = ["CanvasAPIClient", "CanvasAPI"]
