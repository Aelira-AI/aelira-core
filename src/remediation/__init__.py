"""
Remediation Module Facade

Re-exports remediation classes from src.education.remediation for backwards compatibility.
"""

from src.education.remediation import (
    BaseRemediator,
    RemediationResult,
    RemediationIssue,
    FixedIssue,
    ManualIssue,
    RemediationConfig,
    IssueCategory,
    IssueSeverity,
    FixStatus,
    DocxRemediator,
    PptxRemediator,
    PdfRemediator,
    XlsxRemediator,
    get_remediator_for_file,
)

__all__ = [
    "BaseRemediator",
    "RemediationResult",
    "RemediationIssue",
    "FixedIssue",
    "ManualIssue",
    "RemediationConfig",
    "IssueCategory",
    "IssueSeverity",
    "FixStatus",
    "DocxRemediator",
    "PptxRemediator",
    "PdfRemediator",
    "XlsxRemediator",
    "get_remediator_for_file",
]
