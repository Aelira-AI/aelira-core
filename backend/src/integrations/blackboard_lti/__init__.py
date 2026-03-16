"""
Blackboard LTI 1.3 Integration Module

Provides Learning Tools Interoperability (LTI) 1.3 integration
with Blackboard Learn for accessibility scanning within the LMS.

Features:
- LTI 1.3 launch handling with OAuth 2.0
- Deep linking for course content scanning
- Assignment and Grade Services (AGS) for compliance score passback
- Names and Role Provisioning Service (NRPS) for course roster access
"""

from .blackboard_lti import (
    BlackboardLTIService,
    get_blackboard_lti_service,
    BlackboardLaunchData,
)

__all__ = [
    "BlackboardLTIService",
    "get_blackboard_lti_service",
    "BlackboardLaunchData",
]
