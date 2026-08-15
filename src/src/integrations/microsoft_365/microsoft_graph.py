"""
Microsoft Graph API Client

Provides HTTP client wrapper for Microsoft Graph API operations.
"""

import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)


class GraphClient:
    """
    Microsoft Graph API HTTP client.

    Provides methods for making authenticated requests to Microsoft Graph API.
    """

    BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, access_token: str = None):
        """
        Initialize Graph client.

        Args:
            access_token: Microsoft OAuth access token
        """
        self.access_token = access_token
        self._client = None

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            headers = {}
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"

            self._client = httpx.Client(
                base_url=self.BASE_URL,
                headers=headers,
                timeout=60.0,
            )

        return self._client

    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make GET request to Microsoft Graph API.

        Args:
            endpoint: API endpoint (e.g., "/me/drive/root/children")
            params: Query parameters

        Returns:
            Response JSON data

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        try:
            client = self._get_client()
            response = client.get(endpoint, params=params)
            response.raise_for_status()

            # Handle binary responses (file downloads)
            if response.headers.get("content-type", "").startswith(
                "application/octet-stream"
            ):
                return response.content

            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"Graph API GET error for {endpoint}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Graph GET {endpoint}: {e}")
            raise

    def post(
        self,
        endpoint: str,
        json_data: Optional[Dict[str, Any]] = None,
        data: Any = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make POST request to Microsoft Graph API.

        Args:
            endpoint: API endpoint
            json_data: JSON body data
            data: Raw body data
            params: Query parameters

        Returns:
            Response JSON data

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        try:
            client = self._get_client()
            response = client.post(
                endpoint, json=json_data, content=data, params=params
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"Graph API POST error for {endpoint}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Graph POST {endpoint}: {e}")
            raise

    def put(
        self,
        endpoint: str,
        json_data: Optional[Dict[str, Any]] = None,
        data: Any = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make PUT request to Microsoft Graph API.

        Args:
            endpoint: API endpoint
            json_data: JSON body data
            data: Raw body data
            params: Query parameters

        Returns:
            Response JSON data

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        try:
            client = self._get_client()
            response = client.put(endpoint, json=json_data, content=data, params=params)
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"Graph API PUT error for {endpoint}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Graph PUT {endpoint}: {e}")
            raise

    def patch(
        self,
        endpoint: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make PATCH request to Microsoft Graph API.

        Args:
            endpoint: API endpoint
            json_data: JSON body data
            params: Query parameters

        Returns:
            Response JSON data

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        try:
            client = self._get_client()
            response = client.patch(endpoint, json=json_data, params=params)
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"Graph API PATCH error for {endpoint}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Graph PATCH {endpoint}: {e}")
            raise

    def delete(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Make DELETE request to Microsoft Graph API.

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            True if successful

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        try:
            client = self._get_client()
            response = client.delete(endpoint, params=params)
            response.raise_for_status()
            return True

        except httpx.HTTPStatusError as e:
            logger.error(f"Graph API DELETE error for {endpoint}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in Graph DELETE {endpoint}: {e}")
            raise

    def close(self):
        """Close HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


__all__ = ["GraphClient"]
