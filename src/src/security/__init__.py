"""
Security module for document validation and threat detection.

Provides:
- File type validation (magic bytes)
- Office document macro detection (oletools)
- PDF security analysis
- Document sanitization
- Abuse detection
"""

from .document_validator import (
    DocumentValidator,
    ValidationResult,
    ThreatLevel,
    validate_document,
    sanitize_document,
)
from .abuse_detector import (
    AbuseDetector,
    check_signup_abuse,
    check_usage_abuse,
)

__all__ = [
    "DocumentValidator",
    "ValidationResult",
    "ThreatLevel",
    "validate_document",
    "sanitize_document",
    "AbuseDetector",
    "check_signup_abuse",
    "check_usage_abuse",
]
