"""
Base Cloud Integration Module

Provides abstract base classes and shared utilities for
Google Workspace and Microsoft 365 integrations.

The Export-Scan-Fix-Upload pattern:
1. Connect via OAuth 2.0
2. List files from cloud storage
3. Export to Office format (Docs→DOCX, Slides→PPTX, Sheets→XLSX)
4. Scan using existing processors
5. Fix using existing remediators
6. Re-upload fixed version to cloud storage
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any, AsyncIterator
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum
import logging
import tempfile
import os
import httpx

logger = logging.getLogger(__name__)


class CloudFileType(str, Enum):
    """Types of files supported for cloud scanning"""

    # Google native formats
    GOOGLE_DOC = "google_doc"
    GOOGLE_SLIDE = "google_slide"
    GOOGLE_SHEET = "google_sheet"

    # Microsoft native formats
    WORD = "docx"
    POWERPOINT = "pptx"
    EXCEL = "xlsx"

    # Common formats
    PDF = "pdf"

    @classmethod
    def from_mime_type(cls, mime_type: str) -> Optional["CloudFileType"]:
        """Convert MIME type to CloudFileType"""
        mime_map = {
            # Google
            "application/vnd.google-apps.document": cls.GOOGLE_DOC,
            "application/vnd.google-apps.presentation": cls.GOOGLE_SLIDE,
            "application/vnd.google-apps.spreadsheet": cls.GOOGLE_SHEET,
            # Microsoft
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": cls.WORD,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation": cls.POWERPOINT,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": cls.EXCEL,
            # PDF
            "application/pdf": cls.PDF,
        }
        return mime_map.get(mime_type)

    def to_scan_type(self) -> str:
        """Convert to ScanType enum value"""
        type_map = {
            CloudFileType.GOOGLE_DOC: "WORD",
            CloudFileType.GOOGLE_SLIDE: "POWERPOINT",
            CloudFileType.GOOGLE_SHEET: "EXCEL",
            CloudFileType.WORD: "WORD",
            CloudFileType.POWERPOINT: "POWERPOINT",
            CloudFileType.EXCEL: "EXCEL",
            CloudFileType.PDF: "PDF",
        }
        return type_map.get(self, "WORD")


@dataclass
class CloudFileInfo:
    """Information about a file in cloud storage"""

    id: str  # Provider's file ID
    name: str
    mime_type: str
    file_type: Optional[CloudFileType] = None
    size_bytes: Optional[int] = None
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    parent_id: Optional[str] = None  # Folder ID
    path: Optional[str] = None
    web_view_link: Optional[str] = None
    download_link: Optional[str] = None
    version: Optional[str] = None  # etag or version ID
    owner_email: Optional[str] = None
    is_folder: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Set file_type from mime_type if not provided"""
        if self.file_type is None and self.mime_type:
            self.file_type = CloudFileType.from_mime_type(self.mime_type)


@dataclass
class CloudFolderInfo:
    """Information about a folder in cloud storage"""

    id: str
    name: str
    parent_id: Optional[str] = None
    web_view_link: Optional[str] = None
    file_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CloudConnectionStatus:
    """Status of a cloud integration connection"""

    provider: str
    connected: bool
    email: Optional[str] = None
    name: Optional[str] = None
    last_sync: Optional[datetime] = None
    files_tracked: int = 0
    files_scanned: int = 0
    error: Optional[str] = None


@dataclass
class CloudExportResult:
    """Result of exporting a cloud file to local format"""

    success: bool
    local_path: Optional[str] = None
    file_type: Optional[CloudFileType] = None
    error: Optional[str] = None
    mime_type: Optional[str] = None
    file_name: Optional[str] = None
    size_bytes: Optional[int] = None


@dataclass
class CloudUploadResult:
    """Result of uploading a file to cloud storage"""

    success: bool
    file_id: Optional[str] = None
    web_view_link: Optional[str] = None
    error: Optional[str] = None
    failure_kind: Optional[str] = None
    status_code: Optional[int] = None
    retry_after: Optional[int] = None

    @classmethod
    def from_exception(
        cls, exc: Exception, *, body_started: bool = False
    ) -> "CloudUploadResult":
        """Retain retry classification without retaining provider error text."""
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            raw_retry = exc.response.headers.get("retry-after")
            try:
                retry_after = max(0, min(3600, int(raw_retry or 0)))
            except ValueError:
                retry_after = 0
            if status == 429 or status >= 500 or status in (408, 425):
                return cls(
                    False,
                    error="provider_retryable",
                    failure_kind="retryable",
                    status_code=status,
                    retry_after=retry_after,
                )
            if 400 <= status < 500:
                return cls(
                    False,
                    error="provider_rejected",
                    failure_kind="deterministic",
                    status_code=status,
                )
        if isinstance(exc, httpx.ConnectError):
            return cls(False, error="provider_network", failure_kind="retryable")
        if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
            return cls(
                False,
                error="provider_transport",
                failure_kind="indeterminate" if body_started else "retryable",
            )
        return cls(False, error="provider_failure", failure_kind="indeterminate")


class BaseCloudIntegration(ABC):
    """
    Abstract base class for cloud storage integrations.

    Implementations must provide methods for:
    - OAuth connection flow
    - File listing and metadata
    - File download/export
    - File upload
    - Webhook management
    """

    def __init__(self, access_token: str, credential_id: str):
        """
        Initialize the cloud integration.

        Args:
            credential_id: Database ID of the CloudOAuthCredentials record
            access_token: Decrypted OAuth access token
        """
        self.credential_id = credential_id
        self._access_token = access_token
        self._temp_dir = tempfile.mkdtemp(prefix="aelira_cloud_")

    @property
    @abstractmethod
    def provider(self) -> str:
        """Return the provider name ('google' or 'microsoft')"""
        pass

    @abstractmethod
    async def validate_connection(self) -> bool:
        """
        Validate that the OAuth token is still valid.

        Returns:
            True if connection is valid
        """
        pass

    @abstractmethod
    async def list_files(
        self,
        folder_id: Optional[str] = None,
        file_types: Optional[List[CloudFileType]] = None,
        page_token: Optional[str] = None,
        page_size: int = 100,
    ) -> tuple[List[CloudFileInfo], Optional[str]]:
        """
        List files in cloud storage.

        Args:
            folder_id: Optional folder to list (None for root)
            file_types: Optional filter by file types
            page_token: Token for pagination
            page_size: Number of results per page

        Returns:
            Tuple of (list of files, next page token or None)
        """
        pass

    @abstractmethod
    async def list_all_files(
        self,
        folder_id: Optional[str] = None,
        file_types: Optional[List[CloudFileType]] = None,
        recursive: bool = True,
    ) -> AsyncIterator[CloudFileInfo]:
        """
        Iterate through all files in cloud storage.

        Args:
            folder_id: Optional folder to start from
            file_types: Optional filter by file types
            recursive: Whether to recurse into subfolders

        Yields:
            CloudFileInfo for each file
        """
        pass

    @abstractmethod
    async def get_file_info(self, file_id: str) -> Optional[CloudFileInfo]:
        """
        Get metadata for a specific file.

        Args:
            file_id: Provider's file ID

        Returns:
            File info or None if not found
        """
        pass

    @abstractmethod
    async def download_file(
        self, file_id: str, local_path: Optional[str] = None
    ) -> CloudExportResult:
        """
        Download a file from cloud storage.

        For Google Docs/Slides/Sheets, exports to Office format.
        For other files, downloads directly.

        Args:
            file_id: Provider's file ID
            local_path: Optional local path (uses temp dir if not specified)

        Returns:
            CloudExportResult with local path or error
        """
        pass

    @abstractmethod
    async def upload_file(
        self,
        local_path: str,
        folder_id: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> CloudUploadResult:
        """
        Upload a file to cloud storage.

        Args:
            local_path: Path to local file
            folder_id: Optional folder to upload to
            file_name: Optional name for the file (uses local filename if not specified)

        Returns:
            CloudUploadResult with file ID and link or error
        """
        pass

    @abstractmethod
    async def list_folders(
        self, parent_id: Optional[str] = None
    ) -> List[CloudFolderInfo]:
        """
        List folders in cloud storage.

        Args:
            parent_id: Optional parent folder (None for root)

        Returns:
            List of folders
        """
        pass

    @abstractmethod
    async def create_webhook(
        self,
        notification_url: str,
        resource_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a webhook subscription for file changes.

        Args:
            notification_url: URL to receive notifications
            resource_id: Optional specific resource to watch (folder/drive)

        Returns:
            Webhook subscription details
        """
        pass

    @abstractmethod
    async def delete_webhook(self, subscription_id: str) -> bool:
        """
        Delete a webhook subscription.

        Args:
            subscription_id: Provider's subscription ID

        Returns:
            True if deleted successfully
        """
        pass

    def get_temp_path(self, filename: str) -> str:
        """
        Get a path in the temporary directory.

        Args:
            filename: Name for the file

        Returns:
            Full path in temp directory
        """
        return os.path.join(self._temp_dir, filename)

    def cleanup(self):
        """Clean up temporary files"""
        import shutil

        try:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Failed to cleanup temp directory: {e}")

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup temp files"""
        self.cleanup()


class CloudIntegrationError(Exception):
    """Base exception for cloud integration errors"""

    pass


class CloudAuthError(CloudIntegrationError):
    """Authentication/authorization error"""

    pass


class CloudNotFoundError(CloudIntegrationError):
    """Resource not found"""

    pass


class CloudRateLimitError(CloudIntegrationError):
    """Rate limit exceeded"""

    pass


class CloudQuotaError(CloudIntegrationError):
    """Storage quota exceeded"""

    pass
