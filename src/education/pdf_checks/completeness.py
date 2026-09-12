"""PDF names for the shared document-scan completeness contract."""

from ..scan_completeness import (
    IncompleteScanError as IncompletePDFScanError,
    complete_scan_requested,
    record_incomplete_check,
    require_complete_scan as require_complete_pdf_scan,
)

__all__ = [
    "IncompletePDFScanError",
    "complete_scan_requested",
    "record_incomplete_check",
    "require_complete_pdf_scan",
]
