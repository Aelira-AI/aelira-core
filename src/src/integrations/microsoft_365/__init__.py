"""
Microsoft 365 Integration for Aelira

Provides accessibility scanning for:
- OneDrive files (Word, PowerPoint, Excel, PDFs)
- SharePoint document libraries
- Microsoft Office documents

Uses Microsoft Graph API for file operations.
"""

from .onedrive import OneDriveIntegration
from .models import MicrosoftFileInfo, MicrosoftDriveInfo
from .microsoft_oauth import MicrosoftOAuthService
from .microsoft_graph import GraphClient

__all__ = [
    "OneDriveIntegration",
    "MicrosoftFileInfo",
    "MicrosoftDriveInfo",
    "MicrosoftOAuthService",
    "GraphClient",
]
