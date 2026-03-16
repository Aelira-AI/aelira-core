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

import logging
from typing import Optional, List, Dict, Any
import httpx

from .models import (
    BrightspaceUserInfo,
    BrightspaceCourseInfo,
    BrightspaceContentInfo,
    BrightspaceUploadResult,
    BrightspaceDownloadResult,
)

logger = logging.getLogger(__name__)


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
        api_version: str = "1.0",
    ):
        """
        Initialize Brightspace API client.

        Args:
            brightspace_instance_url: Brightspace instance URL (e.g., "https://university.brightspace.com")
            access_token: OAuth 2.0 access token
            credential_id: Optional credential ID for tracking
            api_version: API version to use (default: "1.0")
        """
        self.brightspace_url = brightspace_instance_url.rstrip("/")
        self.access_token = access_token
        self.credential_id = credential_id
        self.api_version = api_version
        self.api_base = f"{self.brightspace_url}/d2l/api"

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
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
        client = await self._get_client()

        # Build full URL
        url = f"{self.api_base}{endpoint}"

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

        # Return JSON response
        return response.json()

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
        for enrollment in data.get("Items", []):
            org_unit = enrollment.get("OrgUnit", {})
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

    # =========================================================================
    # File Operations
    # =========================================================================

    async def download_file(
        self, file_url: str, local_path: str
    ) -> BrightspaceDownloadResult:
        """
        Download a file from Brightspace.

        Args:
            file_url: File download URL (with token)
            local_path: Local path to save file

        Returns:
            BrightspaceDownloadResult with download status
        """
        try:
            client = await self._get_client()

            # Download file (URL should include token)
            response = await client.get(
                file_url,
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            response.raise_for_status()

            # Save to local file
            with open(local_path, "wb") as f:
                f.write(response.content)

            return BrightspaceDownloadResult(
                success=True,
                local_path=local_path,
                file_size=len(response.content),
            )

        except Exception as e:
            logger.error(f"Failed to download Brightspace file {file_url}: {e}")
            return BrightspaceDownloadResult(
                success=False,
                error=str(e),
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
            client = await self._get_client()

            # Prepare file upload
            with open(file_path, "rb") as f:
                files = {"file": f}

                # Upload endpoint
                if module_id:
                    upload_url = f"{self.api_base}/le/{self.api_version}/{org_unit_id}/content/modules/{module_id}/files"
                else:
                    # Upload to root content
                    upload_url = f"{self.api_base}/le/{self.api_version}/{org_unit_id}/content/root/files"

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
