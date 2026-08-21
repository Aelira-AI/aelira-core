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
from dataclasses import dataclass
import logging
import re
from typing import Optional, List, Dict, Any, BinaryIO
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from src.integrations.cloud_base import CloudUploadResult

from src.utils.security import (
    prepare_canvas_outbound_url,
    redact_sensitive_url,
    resolve_canvas_network_origin,
    validate_canvas_outbound_url,
)

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
from .safe_http import create_canvas_safe_transport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CanvasImageDownloadResult:
    """Observed bytes and type from a bounded course image download."""

    success: bool
    data: Optional[bytes] = None
    content_type: Optional[str] = None
    suffix: Optional[str] = None
    error: Optional[str] = None


def _sniff_image_type(data: bytes) -> Optional[tuple[str, str]]:
    """Recognize only the image formats accepted by the vision pipeline."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", ".gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if data.startswith(b"BM"):
        return "image/bmp", ".bmp"
    return None


def _complete_origin(url: str) -> tuple[str, str, int]:
    """Return a complete, normalized origin tuple for an already validated URL."""
    parsed = urlparse(url)
    default_port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname or "", parsed.port or default_port


class CanvasAPIClient:
    """
    Canvas REST API client for file operations.

    Provides methods for:
    - Browsing courses and folders
    - Downloading files
    - Uploading files
    - Managing file metadata
    """

    MAX_PAGINATION_PAGES = 1000

    # Canvas can definitively reject an upload body with these documented client
    # errors. Every other post-body HTTP failure is conservative: Canvas, its
    # object store, or an intermediary may have committed the bytes even though
    # the client did not receive a usable success response.
    DEFINITE_POST_BODY_REJECTION_STATUSES = frozenset(
        {400, 401, 403, 404, 409, 413, 415, 422}
    )

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
        self.canvas_url = resolve_canvas_network_origin(canvas_instance_url)
        self._canvas_origin = self.canvas_url
        self.access_token = access_token
        self.credential_id = credential_id
        self.api_base = f"{self.canvas_url}/api/v1"

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        current_origin = resolve_canvas_network_origin(self.canvas_url)
        if (
            current_origin != self._canvas_origin
            or self.api_base != f"{self._canvas_origin}/api/v1"
        ):
            raise ValueError("Canvas API origin changed after client construction")
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                },
                timeout=30.0,
                follow_redirects=False,
                transport=create_canvas_safe_transport(self._canvas_origin),
                trust_env=False,
            )
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _authorization_headers(self, url: str) -> Dict[str, str]:
        """Authorize only requests whose complete origin is the Canvas origin."""
        if _complete_origin(url) == _complete_origin(self._canvas_origin):
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

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
        params = {"per_page": 100}
        if search_term:
            params["search_term"] = search_term
        if content_types:
            params["content_types[]"] = content_types

        url = f"{self.api_base}/courses/{course_id}/files"
        files = await self._paginate(url, params=params)

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

            download_url = file_info.url

            def _prepare_url(url: str, base_url: str) -> str:
                return prepare_canvas_outbound_url(
                    url,
                    base_url,
                    development_origin=self._canvas_origin,
                )

            download_url = _prepare_url(download_url, self.canvas_url)
            max_redirects = 10
            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=False,
                transport=create_canvas_safe_transport(self._canvas_origin),
                trust_env=False,
            ) as dl_client:
                for redirect_count in range(max_redirects + 1):
                    # Re-resolve immediately before every request/hop.
                    download_url = _prepare_url(download_url, download_url)
                    # Never persist cookies between hops. In particular, a
                    # Canvas response must not seed credentials for a public CDN.
                    dl_client.cookies.clear()
                    response = await dl_client.get(
                        download_url,
                        headers=self._authorization_headers(download_url),
                        follow_redirects=False,
                    )
                    if response.status_code not in (301, 302, 303, 307, 308):
                        break
                    if redirect_count == max_redirects:
                        raise ValueError(f"Too many redirects (>{max_redirects})")
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Canvas download redirect is missing Location")
                    download_url = _prepare_url(location, download_url)
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

        except Exception as exc:
            logger.error(
                "Failed to download Canvas file %s (%s)",
                file_id,
                type(exc).__name__,
            )
            return CanvasDownloadResult(
                success=False,
                error="Canvas file download failed",
            )

    async def download_course_image(
        self,
        file_info: CanvasFileInfo,
        *,
        max_bytes: int,
    ) -> CanvasImageDownloadResult:
        """Download trusted course inventory image metadata with hard bounds.

        Unlike ``download_file``, this method never performs an account-level
        metadata lookup. The caller supplies the exact ``CanvasFileInfo`` from
        a just-fetched course inventory, and the response bytes must prove the
        same supported image MIME before they can reach vision.
        """
        allowed_mimes = {
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
            "image/bmp",
        }
        if (
            type(file_info) is not CanvasFileInfo
            or isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
            or not isinstance(file_info.content_type, str)
            or not isinstance(file_info.url, str)
            or not file_info.url
            or isinstance(file_info.size, bool)
            or not isinstance(file_info.size, int)
            or file_info.size <= 0
            or file_info.size > max_bytes
            or file_info.content_type.casefold() not in allowed_mimes
        ):
            return CanvasImageDownloadResult(
                success=False, error="Invalid course image metadata"
            )

        def _prepare(url: str, base: str) -> str:
            return prepare_canvas_outbound_url(
                url,
                base,
                development_origin=self._canvas_origin,
            )

        try:
            download_url = _prepare(file_info.url, self.canvas_url)
            max_redirects = 10
            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=False,
                transport=create_canvas_safe_transport(self._canvas_origin),
                trust_env=False,
            ) as image_client:
                for redirect_count in range(max_redirects + 1):
                    download_url = _prepare(download_url, download_url)
                    image_client.cookies.clear()
                    async with image_client.stream(
                        "GET",
                        download_url,
                        headers=self._authorization_headers(download_url),
                        follow_redirects=False,
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            if redirect_count == max_redirects:
                                raise ValueError("Too many Canvas image redirects")
                            location = response.headers.get("location")
                            if not location:
                                raise ValueError(
                                    "Canvas image redirect missing Location"
                                )
                            download_url = _prepare(location, download_url)
                            continue

                        response.raise_for_status()
                        content_length = response.headers.get("content-length")
                        if content_length is not None:
                            try:
                                declared_size = int(content_length)
                            except ValueError as exc:
                                raise ValueError("Invalid Content-Length") from exc
                            if declared_size < 0 or declared_size > max_bytes:
                                raise ValueError("Canvas image exceeds size limit")

                        chunks = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(chunks) + len(chunk) > max_bytes:
                                raise ValueError("Canvas image exceeds size limit")
                            chunks.extend(chunk)
                        data = bytes(chunks)
                        observed = _sniff_image_type(data)
                        if observed is None:
                            raise ValueError("Unsupported Canvas image bytes")
                        observed_mime, suffix = observed
                        if observed_mime != file_info.content_type.casefold():
                            raise ValueError("Canvas image MIME mismatch")
                        return CanvasImageDownloadResult(
                            success=True,
                            data=data,
                            content_type=observed_mime,
                            suffix=suffix,
                        )
        except Exception:
            return CanvasImageDownloadResult(
                success=False, error="Canvas course image download failed"
            )

        return CanvasImageDownloadResult(
            success=False, error="Canvas course image download failed"
        )

    async def upload_file_stream(
        self,
        *,
        course_id: str,
        stream: BinaryIO,
        size_bytes: int,
        mime_type: str,
        file_name: str,
        folder_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> CanvasUploadResult:
        """Upload a verified stream and classify whether retry is safe."""
        correlation_id = correlation_id or str(uuid.uuid4())
        try:
            parsed_correlation = uuid.UUID(correlation_id)
            if (
                parsed_correlation.version != 4
                or str(parsed_correlation) != correlation_id
            ):
                raise ValueError("invalid correlation id")
        except (AttributeError, ValueError):
            return CanvasUploadResult(
                success=False,
                outcome="definite_failure",
                error="Canvas file upload metadata is invalid",
            )
        body_started = False
        provider_result: Dict[str, Any] = {"phase": "preaccept"}
        try:
            if size_bytes < 0 or not file_name:
                raise ValueError("invalid upload metadata")
            client = await self._get_client()
            upload_params: Dict[str, Any] = {
                "name": file_name,
                "size": size_bytes,
                "content_type": mime_type,
            }
            if folder_id:
                upload_params["parent_folder_id"] = folder_id
            response = await client.post(
                f"{self.api_base}/courses/{course_id}/files", json=upload_params
            )
            response.raise_for_status()
            upload_data = response.json()
            upload_url = prepare_canvas_outbound_url(
                upload_data["upload_url"],
                self.canvas_url,
                development_origin=self._canvas_origin,
            )
            provider_result = {"phase": "upload", "request_accepted": True}
            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=False,
                transport=create_canvas_safe_transport(self._canvas_origin),
                trust_env=False,
            ) as upload_client:
                upload_client.cookies.clear()
                body_started = True
                upload_response = await upload_client.post(
                    upload_url,
                    data=upload_data["upload_params"],
                    files={"file": (file_name, stream, mime_type)},
                    headers=self._authorization_headers(upload_url),
                    follow_redirects=False,
                )
                provider_result["upload_status"] = upload_response.status_code
                if upload_response.status_code in (301, 302, 303, 307, 308):
                    location = upload_response.headers.get("Location")
                    if not location:
                        raise RuntimeError(
                            "accepted upload redirect lacks confirmation"
                        )
                    confirm_url = prepare_canvas_outbound_url(
                        location,
                        upload_url,
                        development_origin=self._canvas_origin,
                    )
                    upload_client.cookies.clear()
                    confirmed = await upload_client.get(
                        confirm_url,
                        headers=self._authorization_headers(confirm_url),
                        follow_redirects=False,
                    )
                    provider_result["confirmation_status"] = confirmed.status_code
                    confirmed.raise_for_status()
                    file_info = confirmed.json()
                else:
                    upload_response.raise_for_status()
                    file_info = upload_response.json()
            provider_result.update(
                {"phase": "complete", "canvas_file_id": str(file_info["id"])}
            )
            return CanvasUploadResult(
                success=True,
                outcome="success",
                correlation_id=correlation_id,
                provider_result=provider_result,
                file_id=str(file_info["id"]),
                file_name=file_info["filename"],
                web_view_link=file_info.get("url"),
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if not body_started:
                indeterminate = False
            elif provider_result.get("upload_status") in {
                301,
                302,
                303,
                307,
                308,
            }:
                # The upload service accepted the body and redirected to a
                # confirmation endpoint; a confirmation rejection cannot prove
                # that the upload itself was rolled back.
                indeterminate = True
            else:
                indeterminate = (
                    status_code not in self.DEFINITE_POST_BODY_REJECTION_STATUSES
                )
            provider_result.update({"status_code": status_code})
            return CanvasUploadResult(
                success=False,
                outcome="indeterminate" if indeterminate else "definite_failure",
                correlation_id=correlation_id if indeterminate else None,
                provider_result=provider_result,
                error=(
                    "Canvas file upload outcome is indeterminate"
                    if indeterminate
                    else "Canvas file upload failed"
                ),
            )
        except Exception as exc:
            indeterminate = body_started
            logger.warning(
                "Canvas verified-stream upload failed (%s, indeterminate=%s)",
                type(exc).__name__,
                indeterminate,
            )
            return CanvasUploadResult(
                success=False,
                outcome="indeterminate" if indeterminate else "definite_failure",
                correlation_id=correlation_id if indeterminate else None,
                provider_result=provider_result,
                error=(
                    "Canvas file upload outcome is indeterminate"
                    if indeterminate
                    else "Canvas file upload failed"
                ),
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

            upload_url = prepare_canvas_outbound_url(
                upload_data["upload_url"],
                self.canvas_url,
                development_origin=self._canvas_origin,
            )
            upload_params_dict = upload_data["upload_params"]

            # Steps 2 and 3 use an isolated transport with no default auth,
            # cookies, or environment proxy. Bearer auth is added per request
            # only when the complete target origin is the Canvas origin.
            async with httpx.AsyncClient(
                timeout=60.0,
                follow_redirects=False,
                transport=create_canvas_safe_transport(self._canvas_origin),
                trust_env=False,
            ) as upload_client:
                upload_client.cookies.clear()
                with open(local_path, "rb") as f:
                    files = {
                        "file": (file_name, f, self._guess_content_type(file_name))
                    }
                    upload_response = await upload_client.post(
                        upload_url,
                        data=upload_params_dict,
                        files=files,
                        headers=self._authorization_headers(upload_url),
                        follow_redirects=False,
                    )

                if upload_response.status_code in (301, 302, 303, 307, 308):
                    location = upload_response.headers.get("Location")
                    if not location:
                        raise ValueError("Canvas upload redirect is missing Location")
                    confirm_url = prepare_canvas_outbound_url(
                        location,
                        upload_url,
                        development_origin=self._canvas_origin,
                    )
                    upload_client.cookies.clear()
                    confirm_response = await upload_client.get(
                        confirm_url,
                        headers=self._authorization_headers(confirm_url),
                        follow_redirects=False,
                    )
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

        except Exception as exc:
            logger.error(
                "Failed to upload file to Canvas course %s (%s)",
                course_id,
                type(exc).__name__,
            )
            failure = CloudUploadResult.from_exception(exc, body_started=True)
            return CanvasUploadResult(
                success=False,
                error="Canvas file upload failed",
                failure_kind=failure.failure_kind,
                status_code=failure.status_code,
                retry_after=failure.retry_after,
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

        # TODO: For multi-course batch scanning, implement a global rate
        # limiter that coordinates across concurrent scan jobs sharing the
        # same access token.
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

    def _validate_api_url(self, url: str, base_url: Optional[str] = None) -> str:
        """Return a safe URL only when it remains on the Canvas API origin."""
        validated = validate_canvas_outbound_url(
            url,
            base_url or f"{self.api_base}/",
            development_origin=self._canvas_origin,
        )
        parsed = urlparse(validated)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != self._canvas_origin:
            raise ValueError("Canvas API URL must remain on the configured origin")
        return validated

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
            validated_url = self._validate_api_url(url)
            request_method = getattr(client, method.lower())
            response = await request_method(validated_url, **kwargs)

            if response.status_code in (403, 429):
                if attempt < retries:
                    backoff = 2**attempt  # 1s, 2s, 4s
                    logger.warning(
                        "Canvas API %d on %s %s, retrying in %ds (attempt %d/%d)",
                        response.status_code,
                        method,
                        redact_sensitive_url(validated_url),
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
        visited_urls = set()
        page_count = 0

        while current_url:
            canonical_url = self._canonical_pagination_url(current_url, current_params)
            if canonical_url in visited_urls:
                raise ValueError("Canvas pagination cycle detected")
            if page_count >= self.MAX_PAGINATION_PAGES:
                raise ValueError("Canvas pagination page limit exceeded")
            visited_urls.add(canonical_url)

            response = await self._request_with_retry(
                "GET", current_url, params=current_params
            )
            page_count += 1

            page_data = response.json()
            if isinstance(page_data, list):
                all_items.extend(page_data)
            else:
                all_items.append(page_data)

            # Parse and validate the Link target before the bearer-authenticated
            # client can make another request.
            next_link = self._parse_next_link(response)
            next_url = (
                self._validate_api_url(next_link, base_url=current_url)
                if next_link
                else None
            )
            if next_url:
                if self._canonical_pagination_url(next_url) in visited_urls:
                    raise ValueError("Canvas pagination cycle detected")
                if page_count >= self.MAX_PAGINATION_PAGES:
                    raise ValueError("Canvas pagination page limit exceeded")
            current_url = next_url
            # For subsequent pages, params are embedded in the URL
            current_params = None

        return all_items

    @staticmethod
    def _canonical_pagination_url(
        url: str, params: Optional[Dict[str, Any]] = None
    ) -> str:
        """Canonicalize a page target so query ordering cannot hide a cycle."""
        parsed = urlparse(url)
        query_items = (
            parse_qsl(parsed.query, keep_blank_values=True)
            if params is None
            else list(httpx.QueryParams(params).multi_items())
        )
        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path,
                parsed.params,
                urlencode(sorted(query_items), doseq=True),
                "",
            )
        )

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
