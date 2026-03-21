"""
Moodle Web Services API Client

Handles file operations with Moodle LMS using the Web Services REST API.

Moodle API Documentation:
- https://docs.moodle.org/dev/Web_services
- https://docs.moodle.org/dev/Web_service_API_functions
- https://docs.moodle.org/dev/Creating_a_web_service_client

Important Notes:
- Moodle uses Web Services tokens (wstoken) for API authentication
- OAuth 2.0 is used for user login, then we get a web service token
- API calls use GET/POST with wsfunction parameter specifying the function
- Response format is JSON (moodlewsrestformat=json)
"""

import logging
from typing import Optional, List, Dict, Any
import httpx

from .models import (
    MoodleFileInfo,
    MoodleCourseInfo,
    MoodleUserInfo,
    MoodleUploadResult,
    MoodleDownloadResult,
)

logger = logging.getLogger(__name__)


class MoodleAPIClient:
    """
    Moodle Web Services REST API client for file operations.

    Provides methods for:
    - Browsing courses and files
    - Downloading files
    - Uploading files
    - Managing file metadata
    """

    def __init__(
        self,
        moodle_instance_url: str,
        access_token: str,
        credential_id: Optional[str] = None,
    ):
        """
        Initialize Moodle API client.

        Args:
            moodle_instance_url: Moodle instance URL (e.g., "https://moodle.university.edu")
            access_token: Moodle web service token (obtained via OAuth)
            credential_id: Optional credential ID for tracking
        """
        self.moodle_url = moodle_instance_url.rstrip("/")
        self.access_token = access_token
        self.credential_id = credential_id
        self.api_base = f"{self.moodle_url}/webservice/rest/server.php"

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

    async def _call_function(
        self,
        function_name: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Call a Moodle Web Service function.

        Args:
            function_name: Moodle function name (e.g., "core_webservice_get_site_info")
            params: Function parameters

        Returns:
            JSON response from Moodle

        Raises:
            httpx.HTTPError: If API call fails
        """
        client = await self._get_client()

        # Build request parameters
        request_params = {
            "wstoken": self.access_token,
            "wsfunction": function_name,
            "moodlewsrestformat": "json",
        }

        if params:
            request_params.update(params)

        # Make API call
        response = await client.get(self.api_base, params=request_params)
        response.raise_for_status()

        data = response.json()

        # Check for Moodle error response
        if isinstance(data, dict) and "exception" in data:
            error_msg = data.get("message", "Unknown Moodle error")
            logger.error(f"Moodle API error: {error_msg}")
            raise Exception(f"Moodle API error: {error_msg}")

        return data

    # =========================================================================
    # User and Site Operations
    # =========================================================================

    async def get_site_info(self) -> MoodleUserInfo:
        """
        Get current user and site information.

        Uses: core_webservice_get_site_info

        Returns:
            MoodleUserInfo with user details
        """
        data = await self._call_function("core_webservice_get_site_info")

        return MoodleUserInfo(
            id=str(data["userid"]),
            username=data["username"],
            firstname=data["firstname"],
            lastname=data["lastname"],
            fullname=data["fullname"],
            email=data.get("useremail"),
            userpictureurl=data.get("userpictureurl"),
            lang=data.get("lang"),
        )

    # =========================================================================
    # Course Operations
    # =========================================================================

    async def get_enrolled_courses(
        self, user_id: Optional[str] = None
    ) -> List[MoodleCourseInfo]:
        """
        Get courses the user is enrolled in.

        Uses: core_enrol_get_users_courses

        Args:
            user_id: User ID (defaults to current user)

        Returns:
            List of MoodleCourseInfo objects
        """
        params = {}
        if user_id:
            params["userid"] = user_id
        else:
            # Get current user ID first
            site_info = await self.get_site_info()
            params["userid"] = site_info.id

        data = await self._call_function("core_enrol_get_users_courses", params)

        courses = []
        for course_data in data:
            courses.append(
                MoodleCourseInfo(
                    id=str(course_data["id"]),
                    fullname=course_data["fullname"],
                    shortname=course_data["shortname"],
                    categoryid=(
                        str(course_data.get("category"))
                        if course_data.get("category")
                        else None
                    ),
                    summary=course_data.get("summary"),
                    format=course_data.get("format"),
                    visible=course_data.get("visible", True) == 1,
                )
            )

        return courses

    # =========================================================================
    # File Operations
    # =========================================================================

    async def get_course_files(self, course_id: str) -> List[MoodleFileInfo]:
        """
        Get all files in a course.

        Uses: core_course_get_contents

        Args:
            course_id: Course ID

        Returns:
            List of MoodleFileInfo objects
        """
        params = {"courseid": course_id}
        data = await self._call_function("core_course_get_contents", params)

        files = []
        for section in data:
            for module in section.get("modules", []):
                # Process module files
                for module_file in module.get("contents", []):
                    if module_file.get("type") == "file":
                        files.append(
                            MoodleFileInfo(
                                filename=module_file["filename"],
                                filepath=module_file.get("filepath", "/"),
                                filesize=module_file["filesize"],
                                fileurl=module_file["fileurl"],
                                mimetype=module_file.get("mimetype"),
                                timemodified=module_file["timemodified"],
                                author=module_file.get("author"),
                            )
                        )

        return files

    async def download_file(
        self, file_url: str, local_path: str
    ) -> MoodleDownloadResult:
        """
        Download a file from Moodle.

        Args:
            file_url: File download URL (includes token)
            local_path: Local path to save file

        Returns:
            MoodleDownloadResult with download status
        """
        try:
            client = await self._get_client()

            # Download file (URL already includes token)
            response = await client.get(file_url)
            response.raise_for_status()

            # Save to local file
            with open(local_path, "wb") as f:
                f.write(response.content)

            return MoodleDownloadResult(
                success=True,
                local_path=local_path,
                file_size=len(response.content),
            )

        except Exception as e:
            logger.error(f"Failed to download Moodle file {file_url}: {e}")
            return MoodleDownloadResult(
                success=False,
                error=str(e),
            )

    async def upload_file(
        self,
        file_path: str,
        course_id: str,
        component: str = "mod_resource",
        filearea: str = "content",
        itemid: int = 0,
    ) -> MoodleUploadResult:
        """
        Upload a file to Moodle.

        Uses: core_files_upload

        Args:
            file_path: Local file path
            course_id: Target course ID
            component: Moodle component (default: mod_resource)
            filearea: File area (default: content)
            itemid: Item ID (default: 0)

        Returns:
            MoodleUploadResult with upload status
        """
        try:
            client = await self._get_client()

            # Prepare file upload
            with open(file_path, "rb") as f:
                files = {"file": f}

                # Upload parameters
                data = {
                    "token": self.access_token,
                    "component": component,
                    "contextlevel": "course",
                    "instanceid": course_id,
                    "filearea": filearea,
                    "itemid": str(itemid),
                    "filepath": "/",
                }

                response = await client.post(
                    f"{self.moodle_url}/webservice/upload.php",
                    files=files,
                    data=data,
                )
                response.raise_for_status()

                result = response.json()

                if result and len(result) > 0:
                    return MoodleUploadResult(
                        success=True,
                        file_id=str(result[0].get("itemid")),
                        file_url=result[0].get("url"),
                    )
                else:
                    return MoodleUploadResult(
                        success=False,
                        error="No file returned from upload",
                    )

        except Exception as e:
            logger.error(f"Failed to upload file to Moodle: {e}")
            return MoodleUploadResult(
                success=False,
                error=str(e),
            )


# Alias for test compatibility
MoodleAPI = MoodleAPIClient
