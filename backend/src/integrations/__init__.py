"""
Aelira Integrations Module

Platform integrations for:
- Canvas LTI 1.3 (Learning Management System)
- Google Workspace (Drive, Docs, Slides, Sheets)
- Microsoft 365 (OneDrive, SharePoint)
- Blackboard LTI
- Moodle
- D2L Brightspace

Each integration follows the Export-Scan-Fix-Upload pattern:
1. Connect via OAuth 2.0
2. List files from cloud storage
3. Export to Office format
4. Scan using existing processors
5. Fix using existing remediators
6. Re-upload fixed version
"""

from .oauth_token_manager import (
    OAuthTokenManager,
    TokenEncryptionError,
    TokenRefreshError,
    get_token_manager,
)

from .cloud_base import (
    BaseCloudIntegration,
    CloudFileType,
    CloudFileInfo,
    CloudFolderInfo,
    CloudConnectionStatus,
    CloudExportResult,
    CloudUploadResult,
    CloudIntegrationError,
    CloudAuthError,
    CloudNotFoundError,
    CloudRateLimitError,
    CloudQuotaError,
)

__all__ = [
    # OAuth Token Manager
    "OAuthTokenManager",
    "TokenEncryptionError",
    "TokenRefreshError",
    "get_token_manager",
    # Cloud Base
    "BaseCloudIntegration",
    "CloudFileType",
    "CloudFileInfo",
    "CloudFolderInfo",
    "CloudConnectionStatus",
    "CloudExportResult",
    "CloudUploadResult",
    "CloudIntegrationError",
    "CloudAuthError",
    "CloudNotFoundError",
    "CloudRateLimitError",
    "CloudQuotaError",
]
