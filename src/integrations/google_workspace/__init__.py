"""
Google Workspace Integration for Aelira

Provides accessibility scanning for:
- Google Docs (exported as DOCX)
- Google Slides (exported as PPTX)
- Google Sheets (exported as XLSX)
- PDFs stored in Google Drive

Uses Google Drive API v3 and Google Docs/Slides/Sheets APIs for export.
"""

from .google_drive import GoogleDriveIntegration
from .models import GoogleFileInfo, GoogleFolderInfo
from .google_oauth import GoogleOAuthService
from .google_docs import GoogleDocsService
from .google_slides import GoogleSlidesService
from .google_sheets import GoogleSheetsService

__all__ = [
    "GoogleDriveIntegration",
    "GoogleFileInfo",
    "GoogleFolderInfo",
    "GoogleOAuthService",
    "GoogleDocsService",
    "GoogleSlidesService",
    "GoogleSheetsService",
]
