"""
Canvas LMS Integration

Provides integration with Canvas Learning Management System for:
- OAuth 2.0 authentication (REST API)
- LTI 1.3 deep integration (via canvas_lti.py in parent directory)
- Course file browsing and management
- File download/upload for remediation

Uses Canvas REST API v1.
"""

from .models import (
    CanvasFileType,
    CanvasFileInfo,
    CanvasFolderInfo,
    CanvasCourseInfo,
    CanvasUserInfo,
    CanvasOAuthCredential,
    CanvasUploadResult,
    CanvasDownloadResult,
)
from .canvas_oauth import CanvasOAuthService
from .canvas_api import CanvasAPIClient

# Alias for backwards compatibility with tests
CanvasAPI = CanvasAPIClient

__all__ = [
    "CanvasFileType",
    "CanvasFileInfo",
    "CanvasFolderInfo",
    "CanvasCourseInfo",
    "CanvasUserInfo",
    "CanvasOAuthCredential",
    "CanvasUploadResult",
    "CanvasDownloadResult",
    "CanvasOAuthService",
    "CanvasAPIClient",
    "CanvasAPI",
]
