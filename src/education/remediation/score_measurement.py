"""Bind paired deterministic scanner measurements to unchanged artifact bytes."""

import hashlib
import math
import re
from collections.abc import Mapping
from pathlib import Path

METHOD_VERSIONS = frozenset(
    {
        "office-word-strict-v1",
        "office-powerpoint-strict-v1",
        "office-excel-strict-v1",
        "pdf-strict-v1",
        "code-static-v1",
        "latex-source-v1",
        "canvas-axe-v1",
    }
)
REASON_CODES = frozenset(
    {
        "original_file_missing",
        "original_scan_failed",
        "output_file_missing",
        "output_scan_failed",
        "incomplete_comparison",
        "baseline_mismatch",
        "unsupported_scan_type",
        "legacy_unverified",
        "artifact_mismatch",
    }
)


class MeasurementError(ValueError):
    """An allowlisted diagnostic, never file paths or exception text."""

    def __init__(self, code):
        self.code = (
            code
            if isinstance(code, str) and code in REASON_CODES
            else "incomplete_comparison"
        )
        super().__init__(self.code)


def _digest(path, missing_code):
    try:
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except (OSError, TypeError, ValueError):
        raise MeasurementError(missing_code) from None


def begin_measurement(source_path, output_path):
    return {
        "source_sha256": _digest(source_path, "original_file_missing"),
        "output_sha256": _digest(output_path, "output_file_missing"),
    }


def valid_measurement(value):
    """Project only known method identifiers, hashes and finite score values."""
    if (
        not isinstance(value, Mapping)
        or not isinstance(value.get("method_version"), str)
        or value["method_version"] not in METHOD_VERSIONS
    ):
        return None
    for key in ("source_sha256", "output_sha256"):
        if not isinstance(value.get(key), str) or not re.fullmatch(
            r"[0-9a-f]{64}", value[key]
        ):
            return None
    for key in ("source_score", "output_score"):
        number = value.get(key)
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not 0 <= number <= 100
            or not math.isfinite(number)
        ):
            return None
    return {
        key: value[key]
        for key in (
            "method_version",
            "source_sha256",
            "output_sha256",
            "source_score",
            "output_score",
        )
    }


def finish_measurement(
    snapshot, source_path, output_path, before_score, after_score, method_version
):
    if snapshot != begin_measurement(source_path, output_path):
        raise MeasurementError("artifact_mismatch")
    measurement = valid_measurement(
        {
            **snapshot,
            "source_score": before_score,
            "output_score": after_score,
            "method_version": method_version,
        }
    )
    if measurement is None:
        raise MeasurementError("incomplete_comparison")
    return measurement
