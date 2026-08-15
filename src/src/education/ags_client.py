"""
Standalone AGS (Assignment and Grade Services) client.

Performs grade passback to Canvas without requiring the original
MessageLaunch object. Uses OAuth2 client credentials flow with the
LTI registration's RSA private key to authenticate.
"""

import logging
import time
from typing import Optional

import httpx
import jwt  # PyJWT

from ..db.models import LTIAGSContext, LTIRegistration

logger = logging.getLogger(__name__)


class AGSClient:
    """Submit compliance scores to Canvas gradebook."""

    def __init__(self, ags_context: LTIAGSContext, registration: LTIRegistration):
        self.context = ags_context
        self.registration = registration

    async def submit_score(self, user_id: str, score: float) -> bool:
        """Submit a compliance score (0-100) for a user."""
        try:
            access_token = await self._get_platform_token()
            if not access_token:
                return False

            lineitem_url = self.context.lineitem_url
            if not lineitem_url:
                lineitem_url = await self._create_lineitem(access_token)
                if not lineitem_url:
                    return False

            score_url = f"{lineitem_url}/scores"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    score_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/vnd.ims.lis.v1.score+json",
                    },
                    json={
                        "userId": user_id,
                        "scoreGiven": score,
                        "scoreMaximum": 100.0,
                        "activityProgress": "Completed",
                        "gradingProgress": "FullyGraded",
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    },
                )
                response.raise_for_status()
                logger.info(
                    "AGS score submitted: course=%s, score=%s",
                    self.context.course_id,
                    score,
                )
                return True

        except Exception as e:
            logger.error("AGS score submission failed: %s", e)
            return False

    async def _get_platform_token(self) -> Optional[str]:
        """Get platform access token via client credentials JWT assertion."""
        try:
            now = int(time.time())
            assertion = jwt.encode(
                {
                    "iss": self.registration.client_id,
                    "sub": self.registration.client_id,
                    "aud": self.context.token_endpoint,
                    "iat": now,
                    "exp": now + 300,
                    "jti": f"ags-{now}",
                },
                self.registration.private_key_pem,
                algorithm="RS256",
            )

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    self.context.token_endpoint,
                    data={
                        "grant_type": "client_credentials",
                        "client_assertion_type": (
                            "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
                        ),
                        "client_assertion": assertion,
                        "scope": " ".join(self.context.scopes or []),
                    },
                )
                response.raise_for_status()
                return response.json().get("access_token")

        except Exception as e:
            logger.error("AGS token request failed: %s", e)
            return None

    async def _create_lineitem(self, access_token: str) -> Optional[str]:
        """Create a line item for accessibility compliance scores."""
        try:
            lineitems_url = self.context.lineitem_url
            if not lineitems_url:
                logger.error("No lineitem URL available to create line item")
                return None

            # Strip trailing specific item ID to get the lineitems collection URL
            if "/lineitems/" in lineitems_url:
                lineitems_url = lineitems_url.split("/lineitems/")[0] + "/lineitems"

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    lineitems_url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/vnd.ims.lis.v2.lineitem+json",
                    },
                    json={
                        "label": "Accessibility Compliance",
                        "scoreMaximum": 100.0,
                        "tag": "aelira-compliance",
                    },
                )
                response.raise_for_status()
                result = response.json()
                lineitem_url = result.get("id")
                logger.info("Created AGS line item: %s", lineitem_url)
                return lineitem_url

        except Exception as e:
            logger.error("AGS line item creation failed: %s", e)
            return None
