"""
Google Drive Integration

Implements BaseCloudIntegration for Google Drive API v3.
Supports:
- File listing and metadata
- Export Google Docs/Slides/Sheets to Office formats
- Direct download of other files
- File upload
- Webhook subscriptions for real-time updates
"""

from typing import Optional, List, Dict, Any, AsyncIterator
from datetime import datetime, timezone
import httpx
import logging
import os
import uuid

from ..cloud_base import (
    BaseCloudIntegration,
    CloudFileType,
    CloudFileInfo,
    CloudFolderInfo,
    CloudExportResult,
    CloudUploadResult,
    CloudIntegrationError,
    CloudAuthError,
    CloudNotFoundError,
    CloudRateLimitError,
)
from .models import GoogleFileInfo, GoogleFolderInfo

logger = logging.getLogger(__name__)


class GoogleWebhookRequestError(RuntimeError):
    """Sanitized watch-request failure with explicit send state."""

    def __init__(
        self, code: str, *, request_started: bool, retryable: bool = False
    ) -> None:
        super().__init__(code)
        self.code = code
        self.request_started = request_started
        self.retryable = retryable


class IndeterminateProviderOutcome(GoogleWebhookRequestError):
    """The watch POST may have succeeded and must not be blindly repeated."""

    def __init__(self, code: str = "webhook_provider_outcome_indeterminate") -> None:
        super().__init__(code, request_started=True, retryable=False)


class GoogleDriveIntegration(BaseCloudIntegration):
    """
    Google Drive integration using Drive API v3.

    Supports scanning Google Docs, Slides, Sheets, and Office files
    stored in Google Drive.
    """

    # Google API endpoints
    DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
    UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3"

    # MIME types for scannable files
    SCANNABLE_MIME_TYPES = [
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.presentation",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/pdf",
    ]

    # Export MIME types for Google native formats
    EXPORT_MIME_TYPES = {
        "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }

    # File extensions for exports
    EXPORT_EXTENSIONS = {
        "application/vnd.google-apps.document": ".docx",
        "application/vnd.google-apps.presentation": ".pptx",
        "application/vnd.google-apps.spreadsheet": ".xlsx",
    }

    def __init__(self, access_token: str, credential_id: str):
        super().__init__(access_token=access_token, credential_id=credential_id)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def provider(self) -> str:
        return "google"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with auth headers"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Accept": "application/json",
                },
                timeout=60.0,
            )
        return self._client

    async def _handle_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Handle API response and raise appropriate errors"""
        if response.status_code == 401:
            raise CloudAuthError("Google OAuth token expired or invalid")
        elif response.status_code == 403:
            error_data = response.json() if response.content else {}
            if "rateLimitExceeded" in str(error_data):
                raise CloudRateLimitError("Google Drive API rate limit exceeded")
            raise CloudAuthError(f"Access denied: {error_data}")
        elif response.status_code == 404:
            raise CloudNotFoundError("File or folder not found")
        elif response.status_code == 429:
            raise CloudRateLimitError("Google Drive API rate limit exceeded")
        elif response.status_code >= 400:
            error_data = response.json() if response.content else {}
            raise CloudIntegrationError(
                f"Google Drive API error {response.status_code}: {error_data}"
            )
        return response.json() if response.content else {}

    async def validate_connection(self) -> bool:
        """Validate OAuth token by making a simple API call"""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.DRIVE_API_BASE}/about?fields=user")
            await self._handle_response(response)
            return True
        except CloudAuthError:
            return False
        except Exception as e:
            logger.error(f"Connection validation failed: {e}")
            return False

    async def get_user_info(self) -> Dict[str, Any]:
        """Get information about the connected user"""
        client = await self._get_client()
        response = await client.get(
            f"{self.DRIVE_API_BASE}/about",
            params={"fields": "user(displayName,emailAddress,photoLink)"},
        )
        data = await self._handle_response(response)
        return data.get("user", {})

    async def list_files(
        self,
        folder_id: Optional[str] = None,
        file_types: Optional[List[CloudFileType]] = None,
        page_token: Optional[str] = None,
        page_size: int = 100,
    ) -> tuple[List[CloudFileInfo], Optional[str]]:
        """
        List files in Google Drive.

        Args:
            folder_id: Folder ID to list (None for all accessible files)
            file_types: Filter by file types
            page_token: Token for pagination
            page_size: Results per page (max 1000)

        Returns:
            Tuple of (file list, next page token)
        """
        client = await self._get_client()

        # Build query
        query_parts = ["trashed = false"]

        if folder_id:
            query_parts.append(f"'{folder_id}' in parents")

        # Filter to scannable file types
        if file_types:
            mime_types = self._file_types_to_mime_types(file_types)
            mime_filter = " or ".join(f"mimeType = '{mt}'" for mt in mime_types)
            query_parts.append(f"({mime_filter})")
        else:
            # Default: only scannable files (not folders)
            mime_filter = " or ".join(
                f"mimeType = '{mt}'" for mt in self.SCANNABLE_MIME_TYPES
            )
            query_parts.append(f"({mime_filter})")

        params = {
            "q": " and ".join(query_parts),
            "fields": "nextPageToken,files(id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink,webContentLink,version,md5Checksum,owners)",
            "pageSize": min(page_size, 1000),
            "orderBy": "modifiedTime desc",
        }

        if page_token:
            params["pageToken"] = page_token

        response = await client.get(f"{self.DRIVE_API_BASE}/files", params=params)
        data = await self._handle_response(response)

        files = []
        for file_data in data.get("files", []):
            google_file = GoogleFileInfo(**file_data)
            files.append(self._to_cloud_file_info(google_file))

        return files, data.get("nextPageToken")

    async def list_all_files(
        self,
        folder_id: Optional[str] = None,
        file_types: Optional[List[CloudFileType]] = None,
        recursive: bool = True,
    ) -> AsyncIterator[CloudFileInfo]:
        """
        Iterate through all files in Google Drive.

        Args:
            folder_id: Starting folder (None for root)
            file_types: Filter by file types
            recursive: Whether to recurse into subfolders

        Yields:
            CloudFileInfo for each file
        """
        page_token = None

        while True:
            files, page_token = await self.list_files(
                folder_id=folder_id,
                file_types=file_types,
                page_token=page_token,
            )

            for file_info in files:
                yield file_info

            if not page_token:
                break

        # Recurse into subfolders if requested
        if recursive:
            folders = await self.list_folders(folder_id)
            for folder in folders:
                async for file_info in self.list_all_files(
                    folder_id=folder.id,
                    file_types=file_types,
                    recursive=True,
                ):
                    yield file_info

    async def get_file_info(self, file_id: str) -> Optional[CloudFileInfo]:
        """Get metadata for a specific file"""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.DRIVE_API_BASE}/files/{file_id}",
                params={
                    "fields": "id,name,mimeType,size,createdTime,modifiedTime,parents,webViewLink,webContentLink,version,md5Checksum,owners"
                },
            )
            data = await self._handle_response(response)
            google_file = GoogleFileInfo(**data)
            return self._to_cloud_file_info(google_file)
        except CloudNotFoundError:
            return None

    async def download_file(
        self, file_id: str, local_path: Optional[str] = None
    ) -> CloudExportResult:
        """
        Download a file from Google Drive.

        For Google Docs/Slides/Sheets, exports to Office format.
        For other files, downloads directly.

        Args:
            file_id: Google Drive file ID
            local_path: Local path to save file (uses temp dir if not specified)

        Returns:
            CloudExportResult with local path or error
        """
        try:
            # Get file metadata first
            file_info = await self.get_file_info(file_id)
            if not file_info:
                return CloudExportResult(success=False, error="File not found")

            client = await self._get_client()

            # Determine if we need to export or download
            google_file = GoogleFileInfo(
                id=file_info.id, name=file_info.name, mimeType=file_info.mime_type or ""
            )

            if google_file.is_google_native:
                # Export Google native files to Office format
                export_mime = google_file.export_mime_type
                extension = google_file.export_extension

                if not export_mime:
                    return CloudExportResult(
                        success=False,
                        error=f"Cannot export file type: {google_file.mime_type}",
                    )

                response = await client.get(
                    f"{self.DRIVE_API_BASE}/files/{file_id}/export",
                    params={"mimeType": export_mime},
                )

                if response.status_code != 200:
                    return CloudExportResult(
                        success=False,
                        error=f"Export failed: {response.status_code}",
                    )

                # Determine local path
                if not local_path:
                    base_name = os.path.splitext(file_info.name)[0]
                    local_path = self.get_temp_path(f"{base_name}{extension}")

                file_type = CloudFileType.from_mime_type(export_mime)

            else:
                # Direct download for non-Google files
                response = await client.get(
                    f"{self.DRIVE_API_BASE}/files/{file_id}",
                    params={"alt": "media"},
                )

                if response.status_code != 200:
                    return CloudExportResult(
                        success=False,
                        error=f"Download failed: {response.status_code}",
                    )

                if not local_path:
                    local_path = self.get_temp_path(file_info.name)

                file_type = file_info.file_type

            # Write to local file
            with open(local_path, "wb") as f:
                f.write(response.content)

            logger.info(f"Downloaded/exported file to {local_path}")

            return CloudExportResult(
                success=True,
                local_path=local_path,
                file_type=file_type,
                mime_type=file_info.mime_type,
            )

        except Exception as e:
            logger.error(f"Download failed for file {file_id}: {e}")
            return CloudExportResult(success=False, error=str(e))

    async def upload_file(
        self,
        local_path: str,
        folder_id: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> CloudUploadResult:
        """
        Upload a file to Google Drive.

        Args:
            local_path: Path to local file
            folder_id: Folder to upload to (None for root)
            file_name: Name for uploaded file (uses local filename if not specified)

        Returns:
            CloudUploadResult with file ID and link
        """
        try:
            if not os.path.exists(local_path):
                return CloudUploadResult(
                    success=False, error=f"File not found: {local_path}"
                )

            client = await self._get_client()
            file_name = file_name or os.path.basename(local_path)

            # Determine MIME type
            mime_type = self._get_mime_type(local_path)

            # Create file metadata
            metadata = {"name": file_name}
            if folder_id:
                metadata["parents"] = [folder_id]

            # Upload using multipart upload
            with open(local_path, "rb") as f:
                file_content = f.read()

            # Use resumable upload for larger files
            if len(file_content) > 5 * 1024 * 1024:  # 5MB
                return await self._upload_resumable(file_content, metadata, mime_type)

            # Simple multipart upload for smaller files
            import json

            boundary = "---aelira-boundary---"
            body = (
                (
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{json.dumps(metadata)}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: {mime_type}\r\n\r\n"
                ).encode()
                + file_content
                + f"\r\n--{boundary}--".encode()
            )

            response = await client.post(
                f"{self.UPLOAD_API_BASE}/files",
                params={"uploadType": "multipart", "fields": "id,webViewLink"},
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                content=body,
            )

            data = await self._handle_response(response)

            logger.info(f"Uploaded file: {file_name} -> {data.get('id')}")

            return CloudUploadResult(
                success=True,
                file_id=data.get("id"),
                web_view_link=data.get("webViewLink"),
            )

        except Exception as exc:
            logger.error("Upload failed (%s)", type(exc).__name__)
            return CloudUploadResult.from_exception(exc, body_started=True)

    async def _upload_resumable(
        self, content: bytes, metadata: Dict, mime_type: str
    ) -> CloudUploadResult:
        """Upload large file using resumable upload"""
        try:
            import json

            client = await self._get_client()

            # Initiate resumable upload
            init_response = await client.post(
                f"{self.UPLOAD_API_BASE}/files",
                params={"uploadType": "resumable"},
                headers={"Content-Type": "application/json"},
                content=json.dumps(metadata),
            )

            if init_response.status_code != 200:
                return CloudUploadResult(
                    success=False,
                    error=f"Failed to initiate upload: {init_response.status_code}",
                )

            upload_url = init_response.headers.get("Location")
            if not upload_url:
                return CloudUploadResult(success=False, error="No upload URL returned")

            # Upload content
            upload_response = await client.put(
                upload_url,
                headers={"Content-Type": mime_type},
                content=content,
            )

            data = await self._handle_response(upload_response)

            return CloudUploadResult(
                success=True,
                file_id=data.get("id"),
                web_view_link=data.get("webViewLink"),
            )

        except Exception as exc:
            return CloudUploadResult.from_exception(exc, body_started=True)

    async def list_folders(
        self, parent_id: Optional[str] = None
    ) -> List[CloudFolderInfo]:
        """List folders in Google Drive"""
        client = await self._get_client()

        query_parts = [
            "mimeType = 'application/vnd.google-apps.folder'",
            "trashed = false",
        ]
        if parent_id:
            query_parts.append(f"'{parent_id}' in parents")

        response = await client.get(
            f"{self.DRIVE_API_BASE}/files",
            params={
                "q": " and ".join(query_parts),
                "fields": "files(id,name,parents,webViewLink)",
                "pageSize": 1000,
            },
        )

        data = await self._handle_response(response)

        folders = []
        for folder_data in data.get("files", []):
            google_folder = GoogleFolderInfo(**folder_data)
            folders.append(
                CloudFolderInfo(
                    id=google_folder.id,
                    name=google_folder.name,
                    parent_id=(
                        google_folder.parents[0] if google_folder.parents else None
                    ),
                    web_view_link=google_folder.web_view_link,
                )
            )

        return folders

    async def create_webhook(
        self,
        notification_url: str,
        resource_id: Optional[str] = None,
        channel_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a webhook subscription for file changes.

        Args:
            notification_url: URL to receive notifications
            resource_id: Specific file/folder to watch (None watches all changes)

        Returns:
            Webhook channel details
        """
        if not isinstance(notification_url, str) or not notification_url.strip():
            raise GoogleWebhookRequestError(
                "webhook_notification_url_invalid", request_started=False
            )
        if channel_id is not None and (
            not isinstance(channel_id, str) or not channel_id.strip()
        ):
            raise GoogleWebhookRequestError(
                "webhook_channel_id_invalid", request_started=False
            )
        channel_id = channel_id or str(uuid.uuid4())
        try:
            client = await self._get_client()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise GoogleWebhookRequestError(
                "webhook_provider_unavailable",
                request_started=False,
                retryable=True,
            ) from exc

        # Watch the changes endpoint or a specific file
        if resource_id:
            watch_url = f"{self.DRIVE_API_BASE}/files/{resource_id}/watch"
        else:
            watch_url = f"{self.DRIVE_API_BASE}/changes/watch"

        # Get start page token for changes (if watching all changes)
        start_page_token = None
        if not resource_id:
            token_response = await client.get(
                f"{self.DRIVE_API_BASE}/changes/startPageToken"
            )
            token_data = await self._handle_response(token_response)
            start_page_token = token_data.get("startPageToken")

        # Create watch request
        body = {
            "id": channel_id,
            "type": "web_hook",
            "address": notification_url,
            "expiration": int(
                (datetime.now(timezone.utc).timestamp() + 7 * 24 * 3600) * 1000
            ),  # 7 days
        }

        if start_page_token:
            body["pageToken"] = start_page_token

        try:
            response = await client.post(watch_url, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise IndeterminateProviderOutcome() from exc
        if response.status_code >= 500:
            raise IndeterminateProviderOutcome()
        data = await self._handle_response(response)

        logger.info(f"Created webhook channel: {channel_id}")

        return {
            "channel_id": channel_id,
            "resource_id": data.get("resourceId"),
            "resource_uri": data.get("resourceUri"),
            "expiration": data.get("expiration"),
            "start_page_token": start_page_token,
        }

    async def delete_webhook(self, subscription_id: str) -> bool:
        """Delete a webhook subscription"""
        try:
            client = await self._get_client()

            # Google requires both channel ID and resource ID to stop watching
            # We need to have stored the resource ID when we created the channel
            response = await client.post(
                f"{self.DRIVE_API_BASE}/channels/stop",
                json={
                    "id": subscription_id,
                    "resourceId": subscription_id,  # May need actual resource ID
                },
            )

            return response.status_code in [200, 204]
        except Exception as e:
            logger.error(f"Failed to delete webhook: {e}")
            return False

    def _to_cloud_file_info(self, google_file: GoogleFileInfo) -> CloudFileInfo:
        """Convert GoogleFileInfo to CloudFileInfo"""
        owner_email = None
        if google_file.owners:
            owner_email = google_file.owners[0].get("emailAddress")

        return CloudFileInfo(
            id=google_file.id,
            name=google_file.name,
            mime_type=google_file.mime_type,
            file_type=CloudFileType.from_mime_type(google_file.mime_type),
            size_bytes=google_file.size,
            created_at=google_file.created_time,
            modified_at=google_file.modified_time,
            parent_id=google_file.parents[0] if google_file.parents else None,
            path=None,
            web_view_link=google_file.web_view_link,
            download_link=google_file.web_content_link,
            version=google_file.version or google_file.md5_checksum,
            owner_email=owner_email,
            is_folder=google_file.is_folder,
        )

    def _file_types_to_mime_types(self, file_types: List[CloudFileType]) -> List[str]:
        """Convert CloudFileType list to MIME types"""
        mime_map = {
            CloudFileType.GOOGLE_DOC: "application/vnd.google-apps.document",
            CloudFileType.GOOGLE_SLIDE: "application/vnd.google-apps.presentation",
            CloudFileType.GOOGLE_SHEET: "application/vnd.google-apps.spreadsheet",
            CloudFileType.WORD: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            CloudFileType.POWERPOINT: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            CloudFileType.EXCEL: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            CloudFileType.PDF: "application/pdf",
        }
        return [mime_map[ft] for ft in file_types if ft in mime_map]

    def _get_mime_type(self, file_path: str) -> str:
        """Determine MIME type from file extension"""
        ext_map = {
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".ppt": "application/vnd.ms-powerpoint",
            ".xls": "application/vnd.ms-excel",
        }
        ext = os.path.splitext(file_path)[1].lower()
        return ext_map.get(ext, "application/octet-stream")

    async def close(self):
        """Close the HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup on context exit"""
        await self.close()
        await super().__aexit__(exc_type, exc_val, exc_tb)


def build(service_name: str, version: str, credentials=None, **kwargs):
    """
    Stub for googleapiclient.discovery.build.

    This implementation uses httpx directly instead of googleapiclient,
    but this stub exists for test compatibility where tests patch this function.

    In production, use GoogleDriveIntegration class directly.
    """
    raise NotImplementedError(
        "This stub is for test patching only. "
        "Use GoogleDriveIntegration class for actual API calls."
    )


# Alias for test compatibility
GoogleDriveService = GoogleDriveIntegration
