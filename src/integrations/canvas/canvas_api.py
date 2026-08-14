"""
Canvas REST API Client

Handles file and content operations with Canvas LMS REST API.

Canvas API Documentation:
- https://canvas.instructure.com/doc/api/files.html
- https://canvas.instructure.com/doc/api/file.file_uploads.html
- https://canvas.instructure.com/doc/api/pages.html
- https://canvas.instructure.com/doc/api/assignments.html
- https://canvas.instructure.com/doc/api/discussion_topics.html
- https://canvas.instructure.com/doc/api/quizzes.html
- https://canvas.instructure.com/doc/api/modules.html
"""

import asyncio
import logging
import re
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
from .content_models import (
    CanvasPageInfo,
    CanvasAssignmentInfo,
    CanvasAnnouncementInfo,
    CanvasQuizInfo,
    CanvasDiscussionInfo,
    CanvasModuleInfo,
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

    async def get_course(self, course_id: str) -> CanvasCourseInfo:
        """
        Get a single course by ID.

        Args:
            course_id: Canvas course ID

        Returns:
            Course information
        """
        url = f"{self.api_base}/courses/{course_id}"
        response = await self._request_with_retry("GET", url)
        course = response.json()
        return CanvasCourseInfo(
            id=str(course["id"]),
            name=course.get("name", ""),
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

            # Canvas file download with manual redirect + hostname rewriting.
            # Canvas redirects /files/{id}/download through multiple hops.
            # In Docker, redirects target localhost which is unreachable —
            # we follow redirects manually, rewriting hostnames to match
            # our canvas_url (e.g. host.docker.internal).
            from urllib.parse import urlparse

            # Start with access_token in URL (Canvas accepts this for file downloads)
            sep = "&" if "?" in file_info.url else "?"
            download_url = f"{file_info.url}{sep}access_token={self.access_token}"

            # Rewrite initial URL hostname
            canvas_parsed = urlparse(self.canvas_url)

            def _rewrite_host(url: str) -> str:
                p = urlparse(url)
                if p.netloc and p.netloc != canvas_parsed.netloc:
                    return url.replace(f"{p.scheme}://{p.netloc}", self.canvas_url)
                return url

            download_url = _rewrite_host(download_url)

            async with httpx.AsyncClient(timeout=60.0) as dl_client:
                for _ in range(10):
                    response = await dl_client.get(download_url, follow_redirects=False)
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        if location:
                            download_url = _rewrite_host(location)
                        continue
                    break
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

            # Step 2: Upload file (don't follow redirect — the 302
            # goes to create_success and would drop the auth header)
            with open(local_path, "rb") as f:
                files = {"file": (file_name, f, self._guess_content_type(file_name))}
                upload_response = await client.post(
                    upload_url,
                    data=upload_params_dict,
                    files=files,
                    follow_redirects=False,
                )

            # Step 3: Confirm upload with auth via the redirect URL
            if upload_response.status_code in (301, 302, 303, 307, 308):
                confirm_url = upload_response.headers["Location"]
                confirm_response = await client.get(confirm_url)
                confirm_response.raise_for_status()
                file_info = confirm_response.json()
            else:
                upload_response.raise_for_status()
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

    # =========================================================================
    # Rate Limiting, Retry, and Pagination
    # =========================================================================

    async def _check_rate_limit(self, response: httpx.Response) -> None:
        """
        Check Canvas rate limit headers and throttle if remaining quota is low.

        Canvas allows 700 requests per 10-minute window per token.
        When X-Rate-Limit-Remaining drops below 100, we sleep briefly
        to avoid hitting the limit.

        # TODO: For multi-course batch scanning post-pilot, implement a
        # global rate limiter that coordinates across concurrent scan jobs
        # sharing the same access token.
        """
        remaining = response.headers.get("X-Rate-Limit-Remaining")
        if remaining is not None:
            try:
                remaining_int = int(float(remaining))
                if remaining_int < 100:
                    # Scale sleep time inversely with remaining quota
                    sleep_time = max(0.5, (100 - remaining_int) / 100 * 2.0)
                    logger.warning(
                        "Canvas rate limit low: %d remaining, sleeping %.1fs",
                        remaining_int,
                        sleep_time,
                    )
                    await asyncio.sleep(sleep_time)
            except (ValueError, TypeError):
                pass

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        retries: int = 3,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Make an HTTP request with retry on 403/429 responses.

        Uses exponential backoff: 1s, 2s, 4s.

        Args:
            method: HTTP method (GET, PUT, POST, DELETE)
            url: Full URL to request
            retries: Number of retry attempts
            **kwargs: Additional arguments passed to httpx client method

        Returns:
            httpx.Response on success

        Raises:
            httpx.HTTPStatusError: After all retries exhausted
        """
        client = await self._get_client()

        for attempt in range(retries + 1):
            request_method = getattr(client, method.lower())
            response = await request_method(url, **kwargs)

            if response.status_code in (403, 429):
                if attempt < retries:
                    backoff = 2**attempt  # 1s, 2s, 4s
                    logger.warning(
                        "Canvas API %d on %s %s, retrying in %ds (attempt %d/%d)",
                        response.status_code,
                        method,
                        url,
                        backoff,
                        attempt + 1,
                        retries,
                    )
                    await asyncio.sleep(backoff)
                    continue

            await self._check_rate_limit(response)
            response.raise_for_status()
            return response

        # All retries exhausted — raise the last error
        response.raise_for_status()

    async def _paginate(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Follow Canvas Link header pagination to collect all pages of results.

        Canvas uses RFC 5988 Link headers for pagination:
        Link: <url>; rel="next", <url>; rel="current"

        Args:
            url: Initial API URL
            params: Query parameters for the first request

        Returns:
            Combined list of all result items across all pages
        """
        all_items: List[Dict[str, Any]] = []
        current_url = url
        current_params = params

        while current_url:
            response = await self._request_with_retry(
                "GET", current_url, params=current_params
            )

            page_data = response.json()
            if isinstance(page_data, list):
                all_items.extend(page_data)
            else:
                all_items.append(page_data)

            # Parse Link header for next page
            current_url = self._parse_next_link(response)
            # For subsequent pages, params are embedded in the URL
            current_params = None

        return all_items

    @staticmethod
    def _parse_next_link(response: httpx.Response) -> Optional[str]:
        """
        Parse the rel="next" URL from a Canvas Link header.

        Format: <https://...?page=2&per_page=10>; rel="next"

        Returns:
            Next page URL or None if no more pages
        """
        link_header = response.headers.get("Link", "")
        if not link_header:
            return None

        # Match <url>; rel="next"
        for part in link_header.split(","):
            part = part.strip()
            match = re.match(r'<([^>]+)>;\s*rel="next"', part)
            if match:
                return match.group(1)

        return None

    # =========================================================================
    # Content Operations — Pages
    # =========================================================================

    async def list_course_pages(self, course_id: str) -> List[CanvasPageInfo]:
        """
        List all wiki pages in a course.

        Args:
            course_id: Canvas course ID

        Returns:
            List of page information
        """
        url = f"{self.api_base}/courses/{course_id}/pages"
        items = await self._paginate(url, params={"per_page": 100})
        return [CanvasPageInfo.from_api_response(item) for item in items]

    async def get_page(self, course_id: str, url_slug: str) -> CanvasPageInfo:
        """
        Get a single wiki page by URL slug.

        Args:
            course_id: Canvas course ID
            url_slug: Page URL slug (e.g., "week-1-overview")

        Returns:
            Page information with full body HTML
        """
        url = f"{self.api_base}/courses/{course_id}/pages/{url_slug}"
        response = await self._request_with_retry("GET", url)
        return CanvasPageInfo.from_api_response(response.json())

    async def update_page(
        self,
        course_id: str,
        url_slug: str,
        body: Optional[str] = None,
        title: Optional[str] = None,
        message: Optional[str] = None,
    ) -> CanvasPageInfo:
        """
        Update a wiki page.

        Canvas expects page updates as form-style params:
        wiki_page[body], wiki_page[title], wiki_page[published_revision_message]

        Args:
            course_id: Canvas course ID
            url_slug: Page URL slug
            body: Updated HTML body (optional)
            title: Updated title (optional)
            message: Revision message shown in page history (optional)

        Returns:
            Updated page information
        """
        url = f"{self.api_base}/courses/{course_id}/pages/{url_slug}"
        data: Dict[str, str] = {}
        if body is not None:
            data["wiki_page[body]"] = body
        if title is not None:
            data["wiki_page[title]"] = title
        if message is not None:
            data["wiki_page[published_revision_message]"] = message

        response = await self._request_with_retry("PUT", url, data=data)
        return CanvasPageInfo.from_api_response(response.json())

    # =========================================================================
    # Content Operations — Assignments
    # =========================================================================

    async def list_course_assignments(
        self, course_id: str
    ) -> List[CanvasAssignmentInfo]:
        """
        List all assignments in a course.

        Args:
            course_id: Canvas course ID

        Returns:
            List of assignment information
        """
        url = f"{self.api_base}/courses/{course_id}/assignments"
        items = await self._paginate(url, params={"per_page": 100})
        return [CanvasAssignmentInfo.from_api_response(item) for item in items]

    async def get_assignment(
        self, course_id: str, assignment_id: str
    ) -> CanvasAssignmentInfo:
        """
        Get a single assignment by ID.

        Args:
            course_id: Canvas course ID
            assignment_id: Canvas assignment ID

        Returns:
            Assignment information with full description HTML
        """
        url = f"{self.api_base}/courses/{course_id}/assignments/{assignment_id}"
        response = await self._request_with_retry("GET", url)
        return CanvasAssignmentInfo.from_api_response(response.json())

    async def update_assignment(
        self,
        course_id: str,
        assignment_id: str,
        description: Optional[str] = None,
        name: Optional[str] = None,
    ) -> CanvasAssignmentInfo:
        """
        Update an assignment.

        Canvas expects: {"assignment": {"description": ...}}

        Args:
            course_id: Canvas course ID
            assignment_id: Canvas assignment ID
            description: Updated HTML description (optional)
            name: Updated name (optional)

        Returns:
            Updated assignment information
        """
        url = f"{self.api_base}/courses/{course_id}/assignments/{assignment_id}"
        assignment_data: Dict[str, Any] = {}
        if description is not None:
            assignment_data["description"] = description
        if name is not None:
            assignment_data["name"] = name

        response = await self._request_with_retry(
            "PUT", url, json={"assignment": assignment_data}
        )
        return CanvasAssignmentInfo.from_api_response(response.json())

    # =========================================================================
    # Content Operations — Announcements
    # =========================================================================

    async def list_course_announcements(
        self, course_id: str
    ) -> List[CanvasAnnouncementInfo]:
        """
        List all announcements in a course.

        Uses the discussion_topics endpoint with only_announcements=true.

        Args:
            course_id: Canvas course ID

        Returns:
            List of announcement information
        """
        url = f"{self.api_base}/courses/{course_id}/discussion_topics"
        items = await self._paginate(
            url, params={"per_page": 100, "only_announcements": "true"}
        )
        return [CanvasAnnouncementInfo.from_api_response(item) for item in items]

    async def get_announcement(
        self, course_id: str, topic_id: str
    ) -> CanvasAnnouncementInfo:
        """
        Get a single announcement by ID.

        Announcements are discussion topics, so we use the discussion_topics endpoint.

        Args:
            course_id: Canvas course ID
            topic_id: Discussion topic ID

        Returns:
            Announcement information with full message HTML
        """
        url = f"{self.api_base}/courses/{course_id}/discussion_topics/{topic_id}"
        response = await self._request_with_retry("GET", url)
        return CanvasAnnouncementInfo.from_api_response(response.json())

    async def update_announcement(
        self,
        course_id: str,
        topic_id: str,
        message: Optional[str] = None,
        title: Optional[str] = None,
    ) -> CanvasAnnouncementInfo:
        """
        Update an announcement.

        Canvas expects: {"message": ...} via PUT on discussion_topics.

        Args:
            course_id: Canvas course ID
            topic_id: Discussion topic ID
            message: Updated HTML message (optional)
            title: Updated title (optional)

        Returns:
            Updated announcement information
        """
        url = f"{self.api_base}/courses/{course_id}/discussion_topics/{topic_id}"
        data: Dict[str, Any] = {}
        if message is not None:
            data["message"] = message
        if title is not None:
            data["title"] = title

        response = await self._request_with_retry("PUT", url, json=data)
        return CanvasAnnouncementInfo.from_api_response(response.json())

    # =========================================================================
    # Content Operations — Quizzes
    # =========================================================================

    async def list_course_quizzes(self, course_id: str) -> List[CanvasQuizInfo]:
        """
        List all quizzes in a course.

        Args:
            course_id: Canvas course ID

        Returns:
            List of quiz information
        """
        url = f"{self.api_base}/courses/{course_id}/quizzes"
        items = await self._paginate(url, params={"per_page": 100})
        return [CanvasQuizInfo.from_api_response(item) for item in items]

    async def get_quiz(self, course_id: str, quiz_id: str) -> CanvasQuizInfo:
        """
        Get a single quiz by ID.

        Args:
            course_id: Canvas course ID
            quiz_id: Canvas quiz ID

        Returns:
            Quiz information with full description HTML
        """
        url = f"{self.api_base}/courses/{course_id}/quizzes/{quiz_id}"
        response = await self._request_with_retry("GET", url)
        return CanvasQuizInfo.from_api_response(response.json())

    async def update_quiz(
        self,
        course_id: str,
        quiz_id: str,
        description: Optional[str] = None,
        title: Optional[str] = None,
    ) -> CanvasQuizInfo:
        """
        Update a quiz.

        Canvas expects: {"quiz": {"description": ...}}

        Args:
            course_id: Canvas course ID
            quiz_id: Canvas quiz ID
            description: Updated HTML description (optional)
            title: Updated title (optional)

        Returns:
            Updated quiz information
        """
        url = f"{self.api_base}/courses/{course_id}/quizzes/{quiz_id}"
        quiz_data: Dict[str, Any] = {}
        if description is not None:
            quiz_data["description"] = description
        if title is not None:
            quiz_data["title"] = title

        response = await self._request_with_retry("PUT", url, json={"quiz": quiz_data})
        return CanvasQuizInfo.from_api_response(response.json())

    # =========================================================================
    # Content Operations — Discussions
    # =========================================================================

    async def list_course_discussions(
        self, course_id: str
    ) -> List[CanvasDiscussionInfo]:
        """
        List all discussion topics in a course.

        Note: This returns all discussion topics without filtering.
        Announcements may be included; the caller (scanner) can distinguish
        by content type if needed.

        Args:
            course_id: Canvas course ID

        Returns:
            List of discussion information
        """
        url = f"{self.api_base}/courses/{course_id}/discussion_topics"
        items = await self._paginate(url, params={"per_page": 100})
        return [CanvasDiscussionInfo.from_api_response(item) for item in items]

    async def get_discussion(
        self, course_id: str, topic_id: str
    ) -> CanvasDiscussionInfo:
        """
        Get a single discussion topic by ID.

        Args:
            course_id: Canvas course ID
            topic_id: Discussion topic ID

        Returns:
            Discussion information with full message HTML
        """
        url = f"{self.api_base}/courses/{course_id}/discussion_topics/{topic_id}"
        response = await self._request_with_retry("GET", url)
        return CanvasDiscussionInfo.from_api_response(response.json())

    async def update_discussion(
        self,
        course_id: str,
        topic_id: str,
        message: Optional[str] = None,
        title: Optional[str] = None,
    ) -> CanvasDiscussionInfo:
        """
        Update a discussion topic.

        Canvas expects: {"message": ...} via PUT on discussion_topics.

        Args:
            course_id: Canvas course ID
            topic_id: Discussion topic ID
            message: Updated HTML message (optional)
            title: Updated title (optional)

        Returns:
            Updated discussion information
        """
        url = f"{self.api_base}/courses/{course_id}/discussion_topics/{topic_id}"
        data: Dict[str, Any] = {}
        if message is not None:
            data["message"] = message
        if title is not None:
            data["title"] = title

        response = await self._request_with_retry("PUT", url, json=data)
        return CanvasDiscussionInfo.from_api_response(response.json())

    # =========================================================================
    # Content Operations — Modules
    # =========================================================================

    async def list_course_modules(self, course_id: str) -> List[CanvasModuleInfo]:
        """
        List all modules in a course, including their items.

        Args:
            course_id: Canvas course ID

        Returns:
            List of module information with items
        """
        url = f"{self.api_base}/courses/{course_id}/modules"
        items = await self._paginate(
            url, params={"per_page": 100, "include[]": "items"}
        )
        return [CanvasModuleInfo.from_api_response(item) for item in items]


# Alias for test compatibility
CanvasAPI = CanvasAPIClient

__all__ = ["CanvasAPIClient", "CanvasAPI"]
