"""
Google Slides Export Service

Provides functionality to export Google Slides to Office formats.
"""

import logging
from typing import Optional, Dict, Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)


class GoogleSlidesService:
    """
    Service for exporting Google Slides.

    Handles export of Google Slides to PPTX format.
    """

    def __init__(self, credentials: Credentials = None):
        """
        Initialize Google Slides service.

        Args:
            credentials: Google OAuth credentials
        """
        self.credentials = credentials
        self._service = None

    def _get_service(self):
        """Get or create Google Drive service."""
        if self._service is None and self.credentials:
            self._service = build("drive", "v3", credentials=self.credentials)
        return self._service

    def export_to_pptx(
        self,
        file_id: str,
    ) -> bytes:
        """
        Export Google Slides to PPTX format.

        Args:
            file_id: Google Drive file ID

        Returns:
            Bytes content of the PPTX file

        Raises:
            HttpError: If export fails
        """
        try:
            service = self._get_service()
            if not service:
                raise ValueError("Google Drive service not initialized")

            # Export as PPTX using Drive API
            request = service.files().export_media(
                fileId=file_id,
                mimeType="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )

            content = request.execute()
            logger.info(f"Successfully exported Google Slides {file_id} to PPTX")
            return content

        except HttpError as e:
            logger.error(f"Error exporting Google Slides {file_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error exporting Google Slides {file_id}: {e}")
            raise

    def get_presentation_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Get presentation metadata.

        Args:
            file_id: Google Drive file ID

        Returns:
            Dict with presentation info or None if not found
        """
        try:
            service = self._get_service()
            if not service:
                return None

            file_metadata = (
                service.files()
                .get(fileId=file_id, fields="id,name,mimeType,size,modifiedTime")
                .execute()
            )

            return {
                "id": file_metadata.get("id"),
                "name": file_metadata.get("name"),
                "mime_type": file_metadata.get("mimeType"),
                "size": file_metadata.get("size"),
                "modified_time": file_metadata.get("modifiedTime"),
            }

        except HttpError as e:
            logger.error(f"Error getting presentation info for {file_id}: {e}")
            return None


__all__ = ["GoogleSlidesService"]
