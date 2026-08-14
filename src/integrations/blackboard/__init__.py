"""
Blackboard Learn Integration

Provides integration with Blackboard Learn LMS for:
- OAuth 2.0 authentication (REST API)
- LTI 1.3 deep integration (via blackboard_lti_routes.py in api directory)
- Course content browsing and management
- File download/upload for remediation

Uses Blackboard Learn REST API v1.
"""

from .models import (
    BlackboardFileType,
    BlackboardFileInfo,
    BlackboardFolderInfo,
    BlackboardCourseInfo,
    BlackboardUserInfo,
    BlackboardOAuthCredential,
    BlackboardUploadResult,
    BlackboardDownloadResult,
)
from .blackboard_oauth import BlackboardOAuthService
from .blackboard_api import BlackboardAPIClient

__all__ = [
    "BlackboardFileType",
    "BlackboardFileInfo",
    "BlackboardFolderInfo",
    "BlackboardCourseInfo",
    "BlackboardUserInfo",
    "BlackboardOAuthCredential",
    "BlackboardUploadResult",
    "BlackboardDownloadResult",
    "BlackboardOAuthService",
    "BlackboardAPIClient",
]
