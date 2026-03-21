"""
Moodle LMS Integration

Provides OAuth 2.0 authentication and file access for Moodle LMS instances.

Moodle is the world's most widely-used LMS:
- 60-70% market share in Australia
- ~20% market share in US higher education
- Popular with community colleges and small institutions (open-source, free)
- Often self-hosted by universities

Integration Components:
- OAuth 2.0 authentication (user login and consent)
- Moodle Web Services API (REST-based)
- Course and file access
- LTI 1.3 support (optional deep linking)
"""

from .moodle_api import MoodleAPIClient
from .moodle_oauth import (
    get_moodle_authorization_url,
    exchange_moodle_code_for_token,
    get_moodle_webservice_token,
)
from .models import (
    MoodleUserInfo,
    MoodleCourseInfo,
    MoodleFileInfo,
    MoodleFolderInfo,
    MoodleUploadResult,
    MoodleDownloadResult,
)

# Alias for backwards compatibility with tests
MoodleAPI = MoodleAPIClient

__all__ = [
    "MoodleAPIClient",
    "MoodleAPI",
    "get_moodle_authorization_url",
    "exchange_moodle_code_for_token",
    "get_moodle_webservice_token",
    "MoodleUserInfo",
    "MoodleCourseInfo",
    "MoodleFileInfo",
    "MoodleFolderInfo",
    "MoodleUploadResult",
    "MoodleDownloadResult",
]
