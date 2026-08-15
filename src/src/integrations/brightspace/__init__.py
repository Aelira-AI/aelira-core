"""
D2L Brightspace LMS Integration

Provides OAuth 2.0 authentication and Valence API access for Brightspace LMS.

Brightspace is widely used in community colleges and growing institutions:
- 15% market share in US higher education
- 10% market share in Australia
- Popular with community colleges and mid-sized universities
- Cloud-hosted (brightspace.com) or self-hosted

API Documentation:
- https://docs.valence.desire2learn.com/
- Valence Learning Framework (VLF) REST API
- OAuth 2.0 for authentication
"""

from .brightspace_api import BrightspaceAPIClient
from .brightspace_oauth import (
    get_brightspace_authorization_url,
    exchange_brightspace_code_for_token,
)
from .models import (
    BrightspaceUserInfo,
    BrightspaceCourseInfo,
    BrightspaceContentInfo,
    BrightspaceFileInfo,
    BrightspaceUploadResult,
    BrightspaceDownloadResult,
)

__all__ = [
    "BrightspaceAPIClient",
    "get_brightspace_authorization_url",
    "exchange_brightspace_code_for_token",
    "BrightspaceUserInfo",
    "BrightspaceCourseInfo",
    "BrightspaceContentInfo",
    "BrightspaceFileInfo",
    "BrightspaceUploadResult",
    "BrightspaceDownloadResult",
]
