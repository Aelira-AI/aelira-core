"""Score provenance at the boundary between remediation and its consumers."""

import math
from collections.abc import Mapping
from typing import Any

from .score_measurement import REASON_CODES, valid_measurement


def measured_score(value: Any) -> float | None:
    """Accept finite scanner measurements only, including a genuine zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= 100 and math.isfinite(value) else None


def score_fields(
    result: Mapping[str, Any],
    *,
    original_score: Any,
    source_scan_type: Any = None,
    source_sha256: Any = None,
    output_sha256: Any = None,
) -> dict[str, Any]:
    """Keep the recorded baseline; reject estimates and incomparable rescans.

    Legacy scores without provenance cannot be retroactively certified. A
    measured regression is still a measurement: verification success is not
    a requirement for reporting a lower score.
    """
    original = measured_score(original_score)
    rescanned_original = measured_score(result.get("original_compliance_score"))
    after = measured_score(result.get("remediated_compliance_score"))
    scan_type = str(getattr(source_scan_type, "value", source_scan_type)).upper()
    measurement = valid_measurement(result.get("score_measurement"))
    methods_by_type = {
        "WORD": {"office-word-strict-v1"},
        "DOCX": {"office-word-strict-v1"},
        "POWERPOINT": {"office-powerpoint-strict-v1"},
        "PPTX": {"office-powerpoint-strict-v1"},
        "EXCEL": {"office-excel-strict-v1"},
        "XLSX": {"office-excel-strict-v1"},
        "PDF": {"pdf-strict-v1"},
        "CODE": {"code-static-v1"},
        "HTML": {"code-static-v1"},
        "LATEX": {"latex-source-v1", "pdf-strict-v1"},
    }
    reason = result.get("score_verification_reason")
    reason = reason if isinstance(reason, str) and reason in REASON_CODES else None
    if scan_type in {"WEBSITE", "CANVAS_CONTENT"}:
        reason = "unsupported_scan_type"
    elif (
        measurement
        and scan_type not in {"NONE", ""}
        and measurement["method_version"] not in methods_by_type.get(scan_type, set())
    ):
        reason = "unsupported_scan_type"
    elif result.get("score_provenance") != "scanner_rescan":
        reason = reason or "legacy_unverified"
    elif measurement is None:
        reason = reason or "incomplete_comparison"
    elif result.get("score_verified") is False:
        reason = reason or "incomplete_comparison"
    elif original is None or rescanned_original is None or after is None:
        reason = reason or "incomplete_comparison"
    elif round(original, 1) != round(rescanned_original, 1):
        reason = "baseline_mismatch"
    elif (
        measurement["source_score"] != rescanned_original
        or measurement["output_score"] != after
    ):
        reason = "incomplete_comparison"
    elif reason is None and any(
        isinstance(expected, str) and expected != measurement[key]
        for key, expected in (
            ("source_sha256", source_sha256),
            ("output_sha256", output_sha256),
        )
    ):
        reason = "artifact_mismatch"
    comparable = (
        result.get("score_provenance") == "scanner_rescan"
        # Static source-code scans do not verify browser/axe scan outcomes,
        # even when their numeric source scores happen to coincide.
        and scan_type not in {"WEBSITE", "CANVAS_CONTENT"}
        and original is not None
        and rescanned_original is not None
        and round(original, 1) == round(rescanned_original, 1)
        and after is not None
        and measurement is not None
        and reason is None
    )
    return {
        "original_compliance_score": original,
        "remediated_compliance_score": after if comparable else None,
        "compliance_improvement": round(after - original, 1) if comparable else None,
        "score_verified": comparable,
        "score_provenance": "scanner_rescan" if comparable else None,
        "score_measurement": measurement if comparable else None,
        "score_verification_reason": (
            None if comparable else reason or "incomplete_comparison"
        ),
    }
