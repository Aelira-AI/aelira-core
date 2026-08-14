"""
Google Docs Export Service

Provides functionality to export Google Docs to Office formats.
"""

import logging
from typing import Optional, Dict, Any
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)


class GoogleDocsService:
    """
    Service for exporting Google Docs.

    Handles export of Google Docs to DOCX format.
    """

    def __init__(self, credentials: Credentials = None):
        """
        Initialize Google Docs service.

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

    def export_to_docx(
        self,
        file_id: str,
    ) -> bytes:
        """
        Export Google Doc to DOCX format.

        Args:
            file_id: Google Drive file ID

        Returns:
            Bytes content of the DOCX file

        Raises:
            HttpError: If export fails
        """
        try:
            service = self._get_service()
            if not service:
                raise ValueError("Google Drive service not initialized")

            # Export as DOCX using Drive API
            request = service.files().export_media(
                fileId=file_id,
                mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

            content = request.execute()
            logger.info(f"Successfully exported Google Doc {file_id} to DOCX")
            return content

        except HttpError as e:
            logger.error(f"Error exporting Google Doc {file_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error exporting Google Doc {file_id}: {e}")
            raise

    def get_document_info(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Get document metadata.

        Args:
            file_id: Google Drive file ID

        Returns:
            Dict with document info or None if not found
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
            logger.error(f"Error getting document info for {file_id}: {e}")
            return None


__all__ = ["GoogleDocsService"]
