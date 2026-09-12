"""Saved-file scoring for deterministic source-code accessibility checks."""

from collections import Counter
from pathlib import Path

from .base import BaseRemediator, VerificationResult
from .score_measurement import MeasurementError, begin_measurement, finish_measurement


def require_separate_source_output(source_path, output_path):
    """Keep the input intact so before/after scans measure different artifacts."""
    source, output = Path(source_path), Path(output_path)
    if source.resolve() == output.resolve() or (
        output.exists() and source.samefile(output)
    ):
        raise ValueError("Remediated output must not overwrite the original document")


def verify_source_output(remediator, output_path, scanner, approved_verifier=None):
    """Compare full scans; attribute only uniquely disappearing source findings."""
    result = remediator.result
    stage = "original_scan_failed"
    try:
        snapshot = begin_measurement(remediator.file_path, output_path)
        before = scanner(remediator.file_path)
        result.original_compliance_score = before["compliance_score"]
        stage = "output_scan_failed"
        after = scanner(output_path)
        stage = "incomplete_comparison"
        before_keys = Counter(issue["description"] for issue in before["issues"])
        after_keys = Counter(issue["description"] for issue in after["issues"])
        measurement = finish_measurement(
            snapshot,
            remediator.file_path,
            output_path,
            before["compliance_score"],
            after["compliance_score"],
            (
                "latex-source-v1"
                if Path(remediator.file_path).suffix.lower() == ".tex"
                else "code-static-v1"
            ),
        )
    except Exception as exc:
        result.score_verification_reason = (
            exc.code if isinstance(exc, MeasurementError) else stage
        )
        result.warnings.append(
            "Saved-file accessibility rescan unavailable; review required."
        )
        return BaseRemediator._verify_fixes(remediator, output_path)

    originals = {issue.id: issue for issue in remediator.issues}
    manual_ids = {issue.issue_id for issue in result.manual_issues}
    verified = []
    claimed = set()
    approved_claimed = set()
    unverified = False
    for fixed in result.fixed_issues:
        original = originals.get(fixed.issue_id)
        key = original.description if original else None
        if key and before_keys[key] == 1 and not after_keys[key] and key not in claimed:
            fixed.verification_passed = True
            verified.append(fixed)
            claimed.add(key)
        else:
            # A human-approved edit can be proven applied even when the static
            # scanner cannot judge its semantics (e.g. changing an incorrect lang).
            approved_key = (
                approved_verifier(fixed, original, output_path)
                if approved_verifier and original is not None
                else None
            )
            if approved_key and approved_key not in approved_claimed:
                fixed.verification_passed = True
                fixed.needs_review = True
                fixed.notes = (
                    "Approved change verified in the saved output; the source scanner "
                    "does not establish its semantic accessibility correctness."
                )
                verified.append(fixed)
                approved_claimed.add(approved_key)
                continue
            unverified = True
            fixed.verification_passed = False
            if original and original.id not in manual_ids:
                remediator._add_manual_issue(
                    original,
                    reason="The saved-file scan did not verify this finding was resolved.",
                    recommendation="Review the saved output and rescan after correction.",
                )
                manual_ids.add(original.id)
    result.fixed_issues = verified
    result.fixed_count = len(verified)
    result.remediated_compliance_score = after["compliance_score"]
    result.improvement = after["compliance_score"] - before["compliance_score"]
    result.score_provenance = "scanner_rescan"
    result.score_measurement = measurement
    result.score_verification_reason = None
    regressions = list((after_keys - before_keys).elements())
    passed = not regressions and not unverified and result.improvement >= 0
    result.verification_passed = passed
    result.verification_result = VerificationResult(
        passed=passed,
        issues_before=len(before["issues"]),
        issues_after=len(after["issues"]),
        issues_fixed=[fixed.issue_id for fixed in verified],
        issues_remaining=list(after_keys.elements()),
        regressions=regressions,
    )
    if regressions or result.improvement < 0:
        result.warnings.append(
            "The saved-file scan found a regression; review before use."
        )
    return result.verification_result


def scan_code_source(path):
    from ..code_scanner import CodeScanner

    scanner = CodeScanner(
        scan_images=False,
        generate_fixes=False,
        validate_alt_text=False,
        scan_cvd=False,
        llm_client=False,
    )
    return scanner.scan_uploaded_code(path).model_dump()


def scan_latex_source(path):
    from ..latex_processor import LaTeXProcessor

    if Path(path).suffix.lower() != ".tex":
        raise ValueError("Source LaTeX checks cannot grade PDF or HTML exports")
    processor = LaTeXProcessor(use_ai=False, llm_client=False)
    return processor.scan_source(Path(path).read_text(encoding="utf-8"))
