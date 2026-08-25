"""
Microsoft OneDrive Integration using Microsoft Graph API

Implements the BaseCloudIntegration interface for Microsoft 365:
- OneDrive Personal and Business
- SharePoint document libraries

Uses Microsoft Graph API v1.0.
Reference: https://learn.microsoft.com/en-us/graph/api/overview
"""

import httpx
import logging
from typing import Optional, List, Dict, Any, Tuple, AsyncIterator
from pathlib import Path
from datetime import datetime, timedelta, timezone

from ..cloud_base import (
    BaseCloudIntegration,
    CloudFileInfo,
    CloudExportResult,
    CloudUploadResult,
    CloudIntegrationError,
    CloudAuthError,
    CloudNotFoundError,
    CloudRateLimitError,
)
from .models import MicrosoftDriveInfo, MicrosoftSiteInfo

logger = logging.getLogger(__name__)


class OneDriveIntegration(BaseCloudIntegration):
    """
    Microsoft OneDrive/SharePoint integration via Graph API.

    Provides file listing, download, upload, and webhook management
    for OneDrive and SharePoint document libraries.
    """

    # Microsoft Graph API base URL
    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

    @property
    def provider(self) -> str:
        return "microsoft"

    # File types we can scan for accessibility
    SCANNABLE_EXTENSIONS = {
        ".docx",
        ".doc",  # Word documents
        ".pptx",
        ".ppt",  # PowerPoint presentations
        ".xlsx",
        ".xls",  # Excel spreadsheets
        ".pdf",  # PDF files
    }

    def __init__(
        self,
        access_token: str,
        department_id: str,
        drive_id: Optional[str] = None,
        site_id: Optional[str] = None,
    ):
        """
        Initialize OneDrive integration.

        Args:
            access_token: Valid Microsoft Graph access token
            department_id: Account scope used to bind webhook notifications
            drive_id: Specific drive ID (optional, uses default user drive if not provided)
            site_id: SharePoint site ID (optional, for SharePoint access)
        """
        # Graph webhook clientState is account-scoped.  Do not alias that account
        # authority into BaseCloudIntegration.credential_id: callers must not be
        # able to mistake a department identifier for a credential identifier.
        super().__init__(access_token=access_token)
        self._department_id = department_id
        self._drive_id = drive_id
        self._site_id = site_id
        self._http_client: Optional[httpx.AsyncClient] = None

    @staticmethod
    def _raise_provider_error(response: httpx.Response) -> None:
        """Translate Graph HTTP failures into the shared provider exception types."""
        if response.status_code in (401, 403):
            raise CloudAuthError("Microsoft OAuth token expired or invalid")
        if response.status_code == 404:
            raise CloudNotFoundError("File or folder not found")
        if response.status_code == 429:
            raise CloudRateLimitError("Microsoft Graph API rate limit exceeded")
        if response.status_code >= 400:
            raise CloudIntegrationError(
                f"Microsoft Graph API error {response.status_code}"
            )

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with auth headers."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=60.0,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
            )
        return self._http_client

    async def close(self):
        """Close the HTTP client and release inherited temporary storage."""
        try:
            if self._http_client and not self._http_client.is_closed:
                await self._http_client.aclose()
        except Exception as exc:
            logger.warning(
                "Failed to close Microsoft Graph client",
                extra={"exception_type": type(exc).__name__[:64]},
            )
        finally:
            self._http_client = None
            self.cleanup()

    def _get_drive_base_url(self) -> str:
        """Get the base URL for drive operations."""
        if self._site_id:
            return f"{self.GRAPH_API_BASE}/sites/{self._site_id}/drive"
        elif self._drive_id:
            return f"{self.GRAPH_API_BASE}/drives/{self._drive_id}"
        else:
            return f"{self.GRAPH_API_BASE}/me/drive"

    async def validate_connection(self) -> bool:
        """
        Validate the Microsoft Graph connection.

        Returns:
            True if connection is valid, False otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.GRAPH_API_BASE}/me")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Microsoft connection validation failed: {e}")
            return False

    async def get_user_info(self) -> Dict[str, Any]:
        """Get current user information."""
        client = await self._get_client()
        response = await client.get(f"{self.GRAPH_API_BASE}/me")
        response.raise_for_status()
        return response.json()

    async def get_drives(self) -> List[MicrosoftDriveInfo]:
        """
        List available drives (OneDrive and SharePoint libraries).

        Returns:
            List of accessible drives
        """
        client = await self._get_client()
        drives = []

        # Get user's OneDrive
        try:
            response = await client.get(f"{self.GRAPH_API_BASE}/me/drive")
            if response.status_code == 200:
                data = response.json()
                drives.append(
                    MicrosoftDriveInfo(
                        id=data["id"],
                        name=data.get("name", "OneDrive"),
                        driveType=data.get("driveType"),
                        webUrl=data.get("webUrl"),
                    )
                )
        except Exception as e:
            logger.warning(f"Could not get user OneDrive: {e}")

        # Get shared drives / SharePoint libraries
        try:
            response = await client.get(f"{self.GRAPH_API_BASE}/me/drives")
            if response.status_code == 200:
                data = response.json()
                for drive in data.get("value", []):
                    drives.append(
                        MicrosoftDriveInfo(
                            id=drive["id"],
                            name=drive.get("name", "Unnamed Drive"),
                            driveType=drive.get("driveType"),
                            webUrl=drive.get("webUrl"),
                        )
                    )
        except Exception as e:
            logger.warning(f"Could not list drives: {e}")

        return drives

    async def get_sites(self) -> List[MicrosoftSiteInfo]:
        """
        List accessible SharePoint sites.

        Returns:
            List of SharePoint sites the user can access
        """
        client = await self._get_client()
        sites = []

        try:
            # Get sites followed by user
            response = await client.get(f"{self.GRAPH_API_BASE}/me/followedSites")
            if response.status_code == 200:
                data = response.json()
                for site in data.get("value", []):
                    sites.append(
                        MicrosoftSiteInfo(
                            id=site["id"],
                            name=site.get("name", ""),
                            displayName=site.get("displayName"),
                            webUrl=site.get("webUrl"),
                        )
                    )
        except Exception as e:
            logger.warning(f"Could not list SharePoint sites: {e}")

        return sites

    async def list_files(
        self,
        folder_id: Optional[str] = None,
        file_types: Optional[List[str]] = None,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Tuple[List[CloudFileInfo], Optional[str]]:
        """
        List files in a OneDrive/SharePoint folder.

        Args:
            folder_id: Folder item ID (None for root)
            file_types: Filter by file extensions (e.g., ['docx', 'pptx'])
            page_token: Pagination token (actually the next link URL)
            page_size: Number of files per page

        Returns:
            Tuple of (list of file info, next page token)
        """
        client = await self._get_client()

        # Build URL
        if page_token:
            # page_token is the full nextLink URL
            url = page_token
        else:
            base_url = self._get_drive_base_url()
            if folder_id:
                url = f"{base_url}/items/{folder_id}/children"
            else:
                url = f"{base_url}/root/children"
            url += f"?$top={page_size}"
            # Select fields we need
            url += "&$select=id,name,size,file,folder,parentReference,createdDateTime,lastModifiedDateTime,webUrl,@microsoft.graph.downloadUrl,cTag,eTag"

        response = await client.get(url)
        self._raise_provider_error(response)
        data = response.json()

        files = []
        for item in data.get("value", []):
            is_folder = "folder" in item

            # Get file extension
            name = item.get("name", "")
            ext = Path(name).suffix.lower() if name else ""

            # Folder DTOs are retained for normalized recursive traversal.
            if file_types and not is_folder:
                ext_without_dot = ext.lstrip(".")
                if ext_without_dot not in file_types and ext not in file_types:
                    continue

            if not is_folder and ext not in self.SCANNABLE_EXTENSIONS:
                continue

            created_at = None
            modified_at = None
            if item.get("createdDateTime"):
                created_at = datetime.fromisoformat(
                    item["createdDateTime"].replace("Z", "+00:00")
                )
            if item.get("lastModifiedDateTime"):
                modified_at = datetime.fromisoformat(
                    item["lastModifiedDateTime"].replace("Z", "+00:00")
                )

            parent_ref = item.get("parentReference", {})
            parent_path = parent_ref.get("path")
            item_path = f"{parent_path}/{name}" if parent_path and name else parent_path

            file_info = CloudFileInfo(
                id=str(item["id"]),
                name=name,
                mime_type=(
                    "application/vnd.microsoft.folder"
                    if is_folder
                    else item.get("file", {}).get("mimeType")
                    or "application/octet-stream"
                ),
                size_bytes=item.get("size"),
                created_at=created_at,
                modified_at=modified_at,
                parent_id=parent_ref.get("id"),
                path=item_path,
                is_folder=is_folder,
                web_view_link=item.get("webUrl"),
                download_link=item.get("@microsoft.graph.downloadUrl"),
                version=item.get("eTag") or item.get("cTag"),
            )
            files.append(file_info)

        # Get next page token
        next_token = data.get("@odata.nextLink")

        return files, next_token

    async def list_all_files(
        self,
        folder_id: Optional[str] = None,
        file_types: Optional[List[str]] = None,
        recursive: bool = True,
    ) -> AsyncIterator[CloudFileInfo]:
        page_token: Optional[str] = None
        while True:
            files, page_token = await self.list_files(
                folder_id=folder_id, file_types=file_types, page_token=page_token
            )
            for item in files:
                yield item
            if not page_token:
                break
        if recursive:
            for folder in await self.list_folders(folder_id):
                async for item in self.list_all_files(
                    folder_id=folder.id, file_types=file_types, recursive=True
                ):
                    yield item

    async def get_file_info(self, file_id: str) -> Optional[CloudFileInfo]:
        client = await self._get_client()
        response = await client.get(f"{self._get_drive_base_url()}/items/{file_id}")
        if response.status_code == 404:
            return None
        self._raise_provider_error(response)
        item = response.json()
        parent = item.get("parentReference", {})
        parent_path = parent.get("path")
        return CloudFileInfo(
            id=str(item["id"]),
            name=str(item.get("name") or ""),
            mime_type=str(
                item.get("file", {}).get("mimeType") or "application/octet-stream"
            ),
            size_bytes=item.get("size"),
            web_view_link=item.get("webUrl"),
            download_link=item.get("@microsoft.graph.downloadUrl"),
            version=item.get("eTag") or item.get("cTag"),
            modified_at=(
                datetime.fromisoformat(
                    item["lastModifiedDateTime"].replace("Z", "+00:00")
                )
                if item.get("lastModifiedDateTime")
                else None
            ),
            parent_id=parent.get("id"),
            path=(
                f"{parent_path}/{item.get('name')}"
                if parent_path and item.get("name")
                else parent_path
            ),
            is_folder="folder" in item,
        )

    async def list_folders(
        self,
        parent_folder_id: Optional[str] = None,
    ) -> List[Any]:  # Returns List[CloudFolderInfo]
        """
        List folders in OneDrive/SharePoint.

        Args:
            parent_folder_id: Parent folder item ID (None for root)

        Returns:
            List of CloudFolderInfo objects
        """
        from ..cloud_base import CloudFolderInfo

        client = await self._get_client()
        base_url = self._get_drive_base_url()

        # Build URL
        if parent_folder_id:
            url = f"{base_url}/items/{parent_folder_id}/children"
        else:
            url = f"{base_url}/root/children"

        # Select only folders
        url += "?$filter=folder ne null"
        url += "&$select=id,name,parentReference,webUrl,folder"

        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

        folders = []
        for item in data.get("value", []):
            # Only process items with folder property
            if "folder" not in item:
                continue

            parent_ref = item.get("parentReference", {})
            folder_info = CloudFolderInfo(
                id=item["id"],
                name=item["name"],
                parent_id=parent_ref.get("id"),
                web_view_link=item.get("webUrl"),
                file_count=item.get("folder", {}).get("childCount", 0),
            )
            folders.append(folder_info)

        return folders

    async def download_file(
        self,
        file_id: str,
        local_path: str,
    ) -> CloudExportResult:
        """
        Download a file from OneDrive/SharePoint.

        Microsoft files don't need export (unlike Google Docs),
        they're stored in native Office formats.

        Args:
            file_id: OneDrive item ID
            local_path: Local path to save the file

        Returns:
            Export result with file info
        """
        client = await self._get_client()

        try:
            # Get file metadata first
            base_url = self._get_drive_base_url()
            meta_url = f"{base_url}/items/{file_id}"
            meta_response = await client.get(meta_url)
            meta_response.raise_for_status()
            metadata = meta_response.json()

            file_name = metadata.get("name", "file")
            metadata.get("size", 0)
            mime_type = metadata.get("file", {}).get("mimeType")

            # Get download URL
            download_url = metadata.get("@microsoft.graph.downloadUrl")

            if not download_url:
                # Request download URL directly
                content_url = f"{base_url}/items/{file_id}/content"
                # Follow redirect to get actual content
                download_response = await client.get(content_url, follow_redirects=True)
                download_response.raise_for_status()
                content = download_response.content
            else:
                # Download from the direct URL (no auth needed for short-lived URL)
                async with httpx.AsyncClient(timeout=300.0) as download_client:
                    download_response = await download_client.get(download_url)
                    download_response.raise_for_status()
                    content = download_response.content

            # Ensure directory exists
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            # Save file
            with open(local_path, "wb") as f:
                f.write(content)

            actual_size = len(content)

            logger.info(
                f"Downloaded Microsoft file {file_id}: {file_name} ({actual_size} bytes)"
            )

            return CloudExportResult(
                success=True,
                local_path=local_path,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=actual_size,
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error downloading file {file_id}: {e.response.status_code}"
            )
            return CloudExportResult(
                success=False,
                error=f"Download failed: HTTP {e.response.status_code}",
            )
        except Exception as e:
            logger.error(f"Error downloading file {file_id}: {e}")
            return CloudExportResult(
                success=False,
                error=str(e),
            )

    async def upload_file(
        self,
        local_path: str,
        folder_id: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> CloudUploadResult:
        """
        Upload a file to OneDrive/SharePoint.

        Uses simple upload for files < 4MB, resumable upload for larger files.

        Args:
            local_path: Path to local file
            folder_id: Destination folder ID (None for root)
            file_name: Name for uploaded file (defaults to local filename)

        Returns:
            Upload result with file ID
        """
        file_path = Path(local_path)
        if not file_path.exists():
            return CloudUploadResult(
                success=False,
                error=f"File not found: {local_path}",
            )

        if file_name is None:
            file_name = file_path.name

        file_size = file_path.stat().st_size

        # Use simple upload for files < 4MB
        if file_size < 4 * 1024 * 1024:
            return await self._simple_upload(local_path, folder_id, file_name)
        else:
            return await self._resumable_upload(local_path, folder_id, file_name)

    async def _simple_upload(
        self,
        local_path: str,
        folder_id: Optional[str],
        file_name: str,
    ) -> CloudUploadResult:
        """Simple upload for files under 4MB."""
        client = await self._get_client()
        base_url = self._get_drive_base_url()

        try:
            with open(local_path, "rb") as f:
                content = f.read()

            # Build upload URL
            if folder_id:
                url = f"{base_url}/items/{folder_id}:/{file_name}:/content"
            else:
                url = f"{base_url}/root:/{file_name}:/content"

            # Determine content type
            ext = Path(file_name).suffix.lower()
            content_types = {
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".pdf": "application/pdf",
            }
            content_type = content_types.get(ext, "application/octet-stream")

            response = await client.put(
                url,
                content=content,
                headers={"Content-Type": content_type},
            )
            response.raise_for_status()
            data = response.json()

            logger.info(f"Uploaded file to Microsoft: {data.get('id')}")

            return CloudUploadResult(
                success=True,
                file_id=data["id"],
                web_view_link=data.get("webUrl"),
            )

        except Exception as exc:
            logger.error("Simple upload failed (%s)", type(exc).__name__)
            return CloudUploadResult.from_exception(exc, body_started=True)

    async def _resumable_upload(
        self,
        local_path: str,
        folder_id: Optional[str],
        file_name: str,
    ) -> CloudUploadResult:
        """Resumable upload for files >= 4MB."""
        client = await self._get_client()
        base_url = self._get_drive_base_url()

        try:
            # Create upload session
            if folder_id:
                session_url = (
                    f"{base_url}/items/{folder_id}:/{file_name}:/createUploadSession"
                )
            else:
                session_url = f"{base_url}/root:/{file_name}:/createUploadSession"

            session_response = await client.post(
                session_url,
                json={
                    "item": {
                        "@microsoft.graph.conflictBehavior": "rename",
                        "name": file_name,
                    }
                },
            )
            session_response.raise_for_status()
            session_data = session_response.json()
            upload_url = session_data["uploadUrl"]

            # Upload in chunks (10MB chunks)
            chunk_size = 10 * 1024 * 1024  # 10MB
            file_size = Path(local_path).stat().st_size

            with open(local_path, "rb") as f:
                offset = 0
                async with httpx.AsyncClient(timeout=300.0) as upload_client:
                    while offset < file_size:
                        chunk = f.read(chunk_size)
                        chunk_len = len(chunk)
                        end_byte = offset + chunk_len - 1

                        response = await upload_client.put(
                            upload_url,
                            content=chunk,
                            headers={
                                "Content-Length": str(chunk_len),
                                "Content-Range": f"bytes {offset}-{end_byte}/{file_size}",
                            },
                        )

                        if response.status_code in [200, 201]:
                            # Upload complete
                            data = response.json()
                            logger.info(f"Resumable upload complete: {data.get('id')}")
                            return CloudUploadResult(
                                success=True,
                                file_id=data["id"],
                                web_view_link=data.get("webUrl"),
                            )
                        elif response.status_code == 202:
                            # Chunk accepted, continue
                            offset += chunk_len
                        else:
                            response.raise_for_status()

            return CloudUploadResult(
                success=False,
                error="Upload did not complete properly",
            )

        except Exception as exc:
            logger.error("Resumable upload failed (%s)", type(exc).__name__)
            return CloudUploadResult.from_exception(exc, body_started=True)

    async def create_webhook(
        self,
        notification_url: str,
        resource_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a Microsoft Graph subscription for change notifications.

        Args:
            notification_url: URL to receive webhook notifications
            resource_id: Drive or folder ID to watch (None for default drive)

        Returns:
            Subscription info including subscription_id and expiration
        """
        client = await self._get_client()

        # Build resource path
        if self._site_id:
            resource = f"/sites/{self._site_id}/drive/root"
        elif self._drive_id:
            resource = f"/drives/{self._drive_id}/root"
        else:
            resource = "/me/drive/root"

        # Microsoft subscriptions expire after max 4230 minutes (~3 days) for driveItem
        expiration = datetime.now(timezone.utc) + timedelta(minutes=4200)

        try:
            response = await client.post(
                f"{self.GRAPH_API_BASE}/subscriptions",
                json={
                    "changeType": "updated",
                    "notificationUrl": notification_url,
                    "resource": resource,
                    "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
                    "clientState": self._department_id,
                },
            )
            response.raise_for_status()
            data = response.json()

            logger.info(f"Created Microsoft subscription: {data.get('id')}")

            return {
                "subscription_id": data["id"],
                "resource": data["resource"],
                "expiration_time": expiration,
                "notification_url": notification_url,
            }

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create subscription: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to create subscription: {e}")
            raise

    async def renew_webhook(
        self,
        subscription_id: str,
    ) -> Dict[str, Any]:
        """
        Renew an existing webhook subscription.

        Args:
            subscription_id: Existing subscription ID

        Returns:
            Updated subscription info
        """
        client = await self._get_client()

        expiration = datetime.now(timezone.utc) + timedelta(minutes=4200)

        try:
            response = await client.patch(
                f"{self.GRAPH_API_BASE}/subscriptions/{subscription_id}",
                json={
                    "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
                },
            )
            response.raise_for_status()
            data = response.json()

            logger.info(f"Renewed Microsoft subscription: {subscription_id}")

            return {
                "subscription_id": data["id"],
                "expiration_time": expiration,
            }

        except Exception as e:
            logger.error(f"Failed to renew subscription: {e}")
            raise

    async def delete_webhook(self, subscription_id: str) -> bool:
        """
        Delete a webhook subscription.

        Args:
            subscription_id: Subscription ID to delete

        Returns:
            True if deletion succeeded
        """
        client = await self._get_client()

        try:
            response = await client.delete(
                f"{self.GRAPH_API_BASE}/subscriptions/{subscription_id}"
            )
            return response.status_code == 204
        except Exception as e:
            logger.error(f"Failed to delete subscription: {e}")
            return False

    async def get_delta(
        self,
        delta_token: Optional[str] = None,
        folder_id: Optional[str] = None,
    ) -> Tuple[List[CloudFileInfo], Optional[str]]:
        """
        Get file changes using delta query.

        Delta queries are more efficient than webhooks for catching up
        on changes after being offline.

        Args:
            delta_token: Previous delta token (None for initial sync)
            folder_id: Specific folder to track (None for entire drive)

        Returns:
            Tuple of (changed files, new delta token)
        """
        client = await self._get_client()
        base_url = self._get_drive_base_url()

        if delta_token:
            # Use existing delta link
            url = delta_token
        else:
            # Start fresh delta
            if folder_id:
                url = f"{base_url}/items/{folder_id}/delta"
            else:
                url = f"{base_url}/root/delta"

        all_files = []
        next_link = url

        while next_link:
            response = await client.get(next_link)
            response.raise_for_status()
            data = response.json()

            for item in data.get("value", []):
                # Skip deleted items and folders
                if item.get("deleted") or "folder" in item:
                    continue

                name = item.get("name", "")
                ext = Path(name).suffix.lower() if name else ""

                if ext not in self.SCANNABLE_EXTENSIONS:
                    continue

                modified_at = None
                if item.get("lastModifiedDateTime"):
                    modified_at = datetime.fromisoformat(
                        item["lastModifiedDateTime"].replace("Z", "+00:00")
                    )

                parent_ref = item.get("parentReference", {})

                file_info = CloudFileInfo(
                    id=str(item["id"]),
                    name=name,
                    mime_type=(
                        item.get("file", {}).get("mimeType")
                        or "application/octet-stream"
                    ),
                    size_bytes=item.get("size"),
                    modified_at=modified_at,
                    parent_id=parent_ref.get("id"),
                    path=(
                        f"{parent_ref.get('path')}/{name}"
                        if parent_ref.get("path") and name
                        else parent_ref.get("path")
                    ),
                    is_folder=False,
                    web_view_link=item.get("webUrl"),
                    version=item.get("eTag") or item.get("cTag"),
                )
                all_files.append(file_info)

            # Check for more pages
            next_link = data.get("@odata.nextLink")
            if not next_link:
                # Get delta link for next sync
                delta_link = data.get("@odata.deltaLink")
                return all_files, delta_link

        return all_files, None
