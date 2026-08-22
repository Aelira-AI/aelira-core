"""
D2L Brightspace Valence API Client

Handles file operations with Brightspace LMS using the Valence Learning Framework API.

Brightspace Valence API Documentation:
- https://docs.valence.desire2learn.com/
- REST API with standard OAuth 2.0 authentication
- Uses /d2l/api/ base path for all API calls
- Supports versioned API endpoints (e.g., /d2l/api/lp/1.0/)

Important Notes:
- Brightspace uses PascalCase for JSON properties (OrgUnitId, FirstName, etc.)
- API version is typically 1.0 or later for each product (lp, le, etc.)
- Response format is JSON by default
- Uses standard Bearer token authentication
"""

import asyncio
import logging
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, urlsplit
import httpx

from ...utils.security import require_brightspace_oauth_allowed_origin
from .safe_http import create_brightspace_safe_transport

from .models import (
    BrightspaceUserInfo,
    BrightspaceCourseInfo,
    BrightspaceContentInfo,
    BrightspaceScannable,
    BrightspaceUploadResult,
    BrightspaceDownloadResult,
)

# Brightspace content type constants
TOPIC_TYPE_FILE = 1
TOPIC_TYPE_LINK = 3
TOPIC_TYPE_HTML = 5
CONTENT_TYPE_MODULE = 0
CONTENT_TYPE_TOPIC = 1

logger = logging.getLogger(__name__)

MAX_TOPIC_FILE_BYTES = 25 * 1024 * 1024


class BrightspaceAPIClient:
    """
    D2L Brightspace Valence API client for file operations.

    Provides methods for:
    - Browsing courses and content
    - Downloading files
    - Uploading files
    - Managing file metadata
    """

    def __init__(
        self,
        brightspace_instance_url: str,
        access_token: str,
        credential_id: Optional[str] = None,
        api_version: str = "1.50",
    ):
        """
        Initialize Brightspace API client.

        Args:
            brightspace_instance_url: Brightspace instance URL (e.g., "https://university.brightspace.com")
            access_token: OAuth 2.0 access token
            credential_id: Optional credential ID for tracking
            api_version: API version to use (default: "1.50")
        """
        self.brightspace_url = require_brightspace_oauth_allowed_origin(
            brightspace_instance_url, _resolve_dns=False
        )
        self.access_token = access_token
        self.credential_id = credential_id
        self.api_version = api_version
        self.api_base = f"{self.brightspace_url}/d2l/api"

        self._client: Optional[httpx.AsyncClient] = None

    def _bearer_url(self, url: str) -> str:
        """Reject any bearer destination outside the constructor-bound origin."""
        parsed = urlsplit(url)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("brightspace_bearer_origin_invalid")
        try:
            candidate_origin = require_brightspace_oauth_allowed_origin(
                f"{parsed.scheme}://{parsed.netloc}", _resolve_dns=False
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("brightspace_bearer_origin_invalid") from exc
        if candidate_origin != self.brightspace_url:
            raise ValueError("brightspace_bearer_origin_invalid")
        return url

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=False,
                transport=create_brightspace_safe_transport(),
                trust_env=False,
            )
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _call_api(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Call a Brightspace Valence API endpoint.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint path (e.g., "/lp/1.0/users/whoami")
            params: Query parameters
            json_data: JSON body for POST/PUT requests

        Returns:
            JSON response from Brightspace

        Raises:
            httpx.HTTPError: If API call fails
        """
        # Build full URL
        url = self._bearer_url(f"{self.api_base}{endpoint}")
        client = await self._get_client()

        # Build headers with Bearer token
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

        # Make API call
        response = await client.request(
            method=method,
            url=url,
            params=params,
            json=json_data,
            headers=headers,
        )

        response.raise_for_status()

        # Return JSON response (some endpoints return empty body)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except Exception:
            return {}

    # =========================================================================
    # User Operations
    # =========================================================================

    async def get_whoami(self) -> BrightspaceUserInfo:
        """
        Get current user information.

        Uses: /d2l/api/lp/1.0/users/whoami

        Returns:
            BrightspaceUserInfo with user details
        """
        data = await self._call_api("GET", f"/lp/{self.api_version}/users/whoami")

        return BrightspaceUserInfo(
            Identifier=data["Identifier"],
            FirstName=data["FirstName"],
            LastName=data["LastName"],
            UniqueName=data["UniqueName"],
            ProfileIdentifier=data.get("ProfileIdentifier"),
        )

    # =========================================================================
    # Course Operations
    # =========================================================================

    async def get_my_enrollments(self) -> List[BrightspaceCourseInfo]:
        """
        Get courses the user is enrolled in.

        Uses: /d2l/api/lp/1.0/enrollments/myenrollments/

        Returns:
            List of BrightspaceCourseInfo objects
        """
        data = await self._call_api(
            "GET", f"/lp/{self.api_version}/enrollments/myenrollments/"
        )

        courses = []
        # Response is an array of enrollment objects with OrgUnit property
        # Filter to Course Offerings only (Type.Id == 3) — excludes containers,
        # departments, templates, sandboxes, groups, etc.
        COURSE_OFFERING_TYPE_ID = 3

        # Brightspace system/admin courses to exclude (common D2L patterns)
        _SYSTEM_CODE_PATTERNS = (
            "co_",
            "ct_",
            "BCC_",
            "BPP",
        )
        _SYSTEM_NAME_PATTERNS = (
            "Lounge",
            "Bulk Tools",
            "Partner Program",
            "HTML Templates",
            "Implementation",
            "Build Your Course",
        )

        for enrollment in data.get("Items", []):
            org_unit = enrollment.get("OrgUnit", {})
            org_type = org_unit.get("Type") or {}
            if org_type.get("Id") != COURSE_OFFERING_TYPE_ID:
                continue

            code = org_unit.get("Code") or ""
            name = org_unit.get("Name") or ""

            # Skip D2L system/admin courses
            if code.endswith("_sb"):
                continue
            if any(code.startswith(p) for p in _SYSTEM_CODE_PATTERNS):
                continue
            if any(p in name for p in _SYSTEM_NAME_PATTERNS):
                continue

            courses.append(
                BrightspaceCourseInfo(
                    OrgUnitId=org_unit["Id"],
                    Name=org_unit["Name"],
                    Code=org_unit.get("Code"),
                    StartDate=org_unit.get("StartDate"),
                    EndDate=org_unit.get("EndDate"),
                    IsActive=org_unit.get("IsActive", True),
                )
            )

        return courses

    # =========================================================================
    # Content Operations
    # =========================================================================

    async def get_course_content(
        self, org_unit_id: int
    ) -> List[BrightspaceContentInfo]:
        """
        Get content modules/topics for a course.

        Uses: /d2l/api/le/1.0/{orgUnitId}/content/root/

        Args:
            org_unit_id: Course ID (OrgUnitId)

        Returns:
            List of BrightspaceContentInfo objects
        """
        data = await self._call_api(
            "GET",
            f"/le/{self.api_version}/{org_unit_id}/content/root/",
        )

        content_items = []
        for item in data:
            content_items.append(
                BrightspaceContentInfo(
                    Id=item["Id"],
                    Title=item["Title"],
                    ShortTitle=item.get("ShortTitle"),
                    Type=item["Type"],
                    ModuleStartDate=item.get("ModuleStartDate"),
                    ModuleEndDate=item.get("ModuleEndDate"),
                    IsHidden=item.get("IsHidden", False),
                    IsLocked=item.get("IsLocked", False),
                )
            )

        return content_items

    async def get_module_children(self, org_unit_id: int, module_id: int) -> list:
        """
        Get child items of a content module.

        Uses: /d2l/api/le/{version}/{orgUnitId}/content/modules/{moduleId}/structure/

        Args:
            org_unit_id: Course ID (OrgUnitId)
            module_id: Parent module ID

        Returns:
            List of child item dicts (modules and topics)
        """
        data = await self._call_api(
            "GET",
            f"/le/{self.api_version}/{org_unit_id}/content/modules/{module_id}/structure/",
        )
        return data

    async def get_course_content_recursive(
        self, org_unit_id: int
    ) -> List[BrightspaceScannable]:
        """
        Recursively walk the course content tree and return scannable items.

        Gets root content, then depth-first traverses all modules. Collects
        file topics (TopicType=1) and HTML topics (TopicType=5), skipping
        link topics (TopicType=3), hidden items, and unknown types.

        Args:
            org_unit_id: Course ID (OrgUnitId)

        Returns:
            Flat list of BrightspaceScannable objects with module_path breadcrumbs
        """
        root_items = await self.get_course_content(org_unit_id)
        scannables: List[BrightspaceScannable] = []
        semaphore = asyncio.Semaphore(5)

        async def walk(
            items: list, path_parts: List[str], parent_module_id: Optional[int] = None
        ):
            """Depth-first walk of module tree."""
            for item in items:
                # Handle both BrightspaceContentInfo objects and raw dicts
                if isinstance(item, dict):
                    item_id = item["Id"]
                    item_title = item["Title"]
                    item_type = item["Type"]
                    is_hidden = item.get("IsHidden", False)
                    topic_type = item.get("TopicType")
                    item_url = item.get("Url")
                    item_description = item.get("Description")
                    item_file_name = item.get("FileName")
                    item_file_size = item.get("FileSize")
                else:
                    item_id = item.Id
                    item_title = item.Title
                    item_type = item.Type
                    is_hidden = item.IsHidden
                    topic_type = getattr(item, "TopicType", None)
                    item_url = getattr(item, "Url", None)
                    item_description = getattr(item, "Description", None)
                    item_file_name = getattr(item, "FileName", None)
                    item_file_size = getattr(item, "FileSize", None)

                # Skip hidden items entirely
                if is_hidden:
                    continue

                if item_type == CONTENT_TYPE_MODULE:
                    # Recurse into sub-module
                    async with semaphore:
                        children = await self.get_module_children(org_unit_id, item_id)
                    await walk(
                        children, path_parts + [item_title], parent_module_id=item_id
                    )

                elif item_type == CONTENT_TYPE_TOPIC:
                    if topic_type == TOPIC_TYPE_FILE:
                        scannables.append(
                            BrightspaceScannable(
                                topic_id=item_id,
                                org_unit_id=org_unit_id,
                                module_id=parent_module_id,
                                title=item_title,
                                content_type="file",
                                file_name=item_file_name,
                                file_size=item_file_size,
                                url=item_url,
                                module_path=" / ".join(path_parts),
                            )
                        )
                    elif topic_type == TOPIC_TYPE_HTML:
                        scannables.append(
                            BrightspaceScannable(
                                topic_id=item_id,
                                org_unit_id=org_unit_id,
                                module_id=parent_module_id,
                                title=item_title,
                                content_type="html",
                                description=item_description,
                                module_path=" / ".join(path_parts),
                            )
                        )
                    # TopicType 3 (Link) and others: skip

        await walk(root_items, [])
        return scannables

    async def get_topic_file(self, org_unit_id: int, topic_id: int) -> tuple:
        """
        Download a topic's file content as raw bytes.

        Tries the official file download endpoint first. If that returns 403
        (common when the OAuth app lacks file download permissions), falls back
        to fetching the file via the topic's Url field from the content service.

        Args:
            org_unit_id: Course ID (OrgUnitId)
            topic_id: Topic ID

        Returns:
            Tuple of (file_bytes, content_type)
        """
        if type(org_unit_id) is not int or type(topic_id) is not int:
            raise ValueError("brightspace_download_scope_invalid")
        instance = urlsplit(self.brightspace_url)
        if (
            instance.scheme != "https"
            or not instance.hostname
            or instance.username is not None
            or instance.password is not None
        ):
            raise ValueError("brightspace_download_origin_invalid")

        def validate_download_url(value: str) -> str:
            candidate = urlsplit(value)
            if (
                candidate.scheme != "https"
                or candidate.hostname != instance.hostname
                or candidate.port != instance.port
                or candidate.username is not None
                or candidate.password is not None
            ):
                raise ValueError("brightspace_download_origin_invalid")
            return value

        url = self._bearer_url(
            f"{self.api_base}/le/{self.api_version}/{org_unit_id}/content/topics/{topic_id}/file"
        )
        client = await self._get_client()
        headers = {"Authorization": f"Bearer {self.access_token}"}

        async def stream_download(download_url: str) -> tuple[int, bytes, str]:
            """Read at most the bounded body while retaining redirect control."""
            async with client.stream(
                "GET", download_url, headers=headers, follow_redirects=False
            ) as response:
                if 300 <= response.status_code < 400:
                    await response.aclose()
                    raise ValueError("brightspace_download_redirect_rejected")
                if response.status_code in (403, 404):
                    return response.status_code, b"", "application/octet-stream"
                response.raise_for_status()

                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except (TypeError, ValueError):
                        declared_size = -1
                    if declared_size > MAX_TOPIC_FILE_BYTES:
                        await response.aclose()
                        raise ValueError("brightspace_download_too_large")

                chunks = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_TOPIC_FILE_BYTES:
                        await response.aclose()
                        raise ValueError("brightspace_download_too_large")
                    chunks.append(chunk)
                content_type = (
                    response.headers.get("content-type", "application/octet-stream")
                    .split(";")[0]
                    .strip()
                )
                return response.status_code, b"".join(chunks), content_type

        # Try official file download endpoint.
        url = validate_download_url(url)
        status_code, content, content_type = await stream_download(url)
        if status_code == 200:
            return content, content_type

        # If 403/404, try fetching via the topic's Url field. The fallback URL
        # is validated before the bearer token is attached.
        logger.info(
            f"File endpoint returned {status_code} for topic {topic_id}, "
            f"falling back to content URL"
        )
        topic_data = await self._call_api(
            "GET",
            f"/le/{self.api_version}/{org_unit_id}/content/topics/{topic_id}",
        )
        content_url = topic_data.get("Url", "")
        if isinstance(content_url, str) and content_url:
            content_url = validate_download_url(
                urljoin(f"{self.brightspace_url}/", content_url)
            )
            fallback_status, content, content_type = await stream_download(content_url)
            if fallback_status == 200:
                return content, content_type

        raise ValueError("brightspace_download_failed")

    async def get_topic_html(self, org_unit_id: int, topic_id: int) -> str:
        """
        Get the HTML description content of a topic.

        Fetches the topic detail and extracts the Description field.
        Brightspace may return Description as a plain string or as
        {"Html": "...", "Text": "..."}.

        Args:
            org_unit_id: Course ID (OrgUnitId)
            topic_id: Topic ID

        Returns:
            HTML content string
        """
        data = await self._call_api(
            "GET",
            f"/le/{self.api_version}/{org_unit_id}/content/topics/{topic_id}",
        )
        description = data.get("Description", "")
        if isinstance(description, dict):
            return description.get("Html", "")
        return description or ""

    async def update_topic_html(
        self, org_unit_id: int, topic_id: int, html_content: str
    ) -> dict:
        """
        Update the HTML description content of a topic.

        Uses: PUT /d2l/api/le/{version}/{orgUnitId}/content/topics/{topicId}

        Args:
            org_unit_id: Course ID (OrgUnitId)
            topic_id: Topic ID
            html_content: New HTML content

        Returns:
            Updated topic data from Brightspace
        """
        # First get the existing topic data to preserve other fields
        existing = await self._call_api(
            "GET",
            f"/le/{self.api_version}/{org_unit_id}/content/topics/{topic_id}",
        )
        existing["Description"] = {"Html": html_content, "Text": ""}

        return await self._call_api(
            "PUT",
            f"/le/{self.api_version}/{org_unit_id}/content/topics/{topic_id}",
            json_data=existing,
        )

    async def replace_topic_file(
        self,
        org_unit_id: int,
        topic_id: int,
        file_bytes: bytes,
        filename: str,
    ) -> dict:
        """
        Replace a topic's file with new content.

        Uses: PUT /d2l/api/le/{version}/{orgUnitId}/content/topics/{topicId}/file
        Uploads via multipart form data.

        Args:
            org_unit_id: Course ID (OrgUnitId)
            topic_id: Topic ID
            file_bytes: New file content
            filename: File name for the upload

        Returns:
            Response data from Brightspace
        """
        url = self._bearer_url(
            f"{self.api_base}/le/{self.api_version}/{org_unit_id}/content/topics/{topic_id}/file"
        )
        client = await self._get_client()
        response = await client.put(
            url,
            files={"file": (filename, file_bytes)},
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except Exception:
            return {}

    # =========================================================================
    # File Operations
    # =========================================================================

    async def download_file(
        self, file_url: str, local_path: str
    ) -> BrightspaceDownloadResult:
        """Fail closed for the deprecated caller-supplied URL transport.

        Authenticated downloads must use :meth:`get_topic_file`, whose course and
        topic identifiers construct the fixed API endpoint. Retaining this method
        as an inert compatibility shim avoids ever attaching a bearer token to a
        URL selected by a caller.
        """
        return BrightspaceDownloadResult(
            success=False,
            error="brightspace_caller_url_download_unsupported",
        )

    async def upload_file(
        self,
        file_path: str,
        org_unit_id: int,
        module_id: Optional[int] = None,
    ) -> BrightspaceUploadResult:
        """
        Upload a file to Brightspace course content.

        Uses: /d2l/api/le/1.0/{orgUnitId}/content/modules/{moduleId}/files

        Args:
            file_path: Local file path
            org_unit_id: Target course ID
            module_id: Optional content module ID

        Returns:
            BrightspaceUploadResult with upload status
        """
        try:
            # Prepare file upload
            with open(file_path, "rb") as f:
                files = {"file": f}

                # Upload endpoint
                if module_id:
                    upload_url = f"{self.api_base}/le/{self.api_version}/{org_unit_id}/content/modules/{module_id}/files"
                else:
                    # Upload to root content
                    upload_url = f"{self.api_base}/le/{self.api_version}/{org_unit_id}/content/root/files"

                upload_url = self._bearer_url(upload_url)
                client = await self._get_client()

                response = await client.post(
                    upload_url,
                    files=files,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
                response.raise_for_status()

                result = response.json()

                if result:
                    return BrightspaceUploadResult(
                        success=True,
                        file_id=result.get("FileId"),
                        file_url=result.get("Url"),
                    )
                else:
                    return BrightspaceUploadResult(
                        success=False,
                        error="No file returned from upload",
                    )

        except Exception as e:
            logger.error(f"Failed to upload file to Brightspace: {e}")
            return BrightspaceUploadResult(
                success=False,
                error=str(e),
            )
