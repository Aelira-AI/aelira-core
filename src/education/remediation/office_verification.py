"""Compare saved Office documents using their ordinary deterministic scanners.

Scanner issue IDs contain list offsets and change after fixes. Verification uses
category and document location instead, conservatively withholding attribution
while another finding remains at the same location in that category.
"""

import logging
import math
import re
from collections import Counter
from pathlib import Path

from .base import IssueCategory, VerificationResult
from .score_measurement import MeasurementError, begin_measurement, finish_measurement

logger = logging.getLogger(__name__)


def require_separate_office_output(source_path, output_path):
    """The before measurement must still refer to the unchanged source file."""
    source, output = Path(source_path), Path(output_path)
    if source.resolve() == output.resolve() or (
        output.exists() and source.samefile(output)
    ):
        raise ValueError("Remediated output must not overwrite the original document")


def scan_office(document_type, path):
    """Use the same processor/settings on both files, without ambient AI calls."""
    if document_type == "word":
        from ..docx_processor import DocxProcessor

        return DocxProcessor(require_complete_scan=True).process_docx(path)
    if document_type == "powerpoint":
        from ..pptx_processor import PowerPointProcessor

        return PowerPointProcessor(require_complete_scan=True).process_pptx(path)
    if document_type == "excel":
        from ..xlsx_processor import XlsxProcessor

        return XlsxProcessor(require_complete_scan=True).process_xlsx(path)
    raise ValueError("Unsupported Office document type")


def _key(remediator, issue, scan):
    category = issue.category.value
    location = issue.location or "Document"
    # Titles and sheet names can legitimately change during remediation.
    location = re.sub(r"^(Slide \d+) \([^)]*\)", r"\1", location)
    for index, sheet in enumerate(getattr(scan, "sheets", ())):
        prefix = f"Sheet '{sheet.sheet_name}'"
        if location.startswith(prefix):
            location = f"Sheet {index}" + location[len(prefix) :]
            break
    return category, location


def _needs_semantic_verifier(issue):
    """Presence checks cannot close findings about meaning or image content."""
    if issue.category not in {IssueCategory.ALT_TEXT, IssueCategory.CHART}:
        return False
    metadata = issue.metadata
    if metadata.get("alt_text_validated") or metadata.get("has_alt_text"):
        return True
    text = " ".join(
        str(value or "")
        for value in (
            issue.description,
            metadata.get("title"),
            metadata.get("issue_type"),
        )
    ).lower()
    return any(
        word in text for word in ("inaccurate", "accuracy", "review", "incorrect")
    )


def _move_to_review(remediator, fixes, reason):
    existing = {issue.issue_id for issue in remediator.result.manual_issues}
    originals = {issue.id: issue for issue in remediator.issues}
    for fixed in fixes:
        fixed.verification_passed = False
        if fixed.issue_id not in existing:
            issue = originals.get(fixed.issue_id)
            if issue is not None:
                remediator._add_manual_issue(
                    issue,
                    reason=reason,
                    recommendation="Review the saved document and rescan after correcting the finding.",
                )
                existing.add(fixed.issue_id)


def unavailable_office_verification(remediator, reason, code="incomplete_comparison"):
    result = remediator.result
    _move_to_review(remediator, result.fixed_issues, reason)
    result.fixed_issues = []
    result.fixed_count = 0
    result.remediated_compliance_score = None
    result.improvement = None
    result.score_provenance = None
    result.score_measurement = None
    result.score_verification_reason = code
    result.verification_passed = False
    result.verification_result = VerificationResult(
        issues_before=result.total_issues,
        issues_remaining=[issue.id for issue in remediator.issues],
    )
    result.warnings.append(reason)
    return result.verification_result


def verify_office_output(remediator, output_path):
    """Measure both files and retain only fixes evidenced in saved output."""
    result = remediator.result
    stage = "original_scan_failed"
    try:
        measurement = begin_measurement(remediator.file_path, output_path)
        before = scan_office(remediator.DOCUMENT_TYPE, remediator.file_path)
        before_score = float(before.compliance_score)
        if not math.isfinite(before_score) or not 0 <= before_score <= 100:
            raise ValueError("Invalid source scanner score")
        result.original_compliance_score = before_score
        stage = "output_scan_failed"
        after = scan_office(remediator.DOCUMENT_TYPE, output_path)
        after_score = float(after.compliance_score)
        if not math.isfinite(after_score) or not 0 <= after_score <= 100:
            raise ValueError("Invalid output scanner score")
        stage = "incomplete_comparison"
        before_issues = remediator._normalize_issues(before.issues)
        after_issues = remediator._normalize_issues(after.issues)
        before_keys = Counter(
            _key(remediator, issue, before) for issue in before_issues
        )
        after_keys = Counter(_key(remediator, issue, after) for issue in after_issues)
        measurement = finish_measurement(
            measurement,
            remediator.file_path,
            output_path,
            before_score,
            after_score,
            f"office-{remediator.DOCUMENT_TYPE}-strict-v1",
        )
    except Exception as error:
        logger.warning("Office saved-file verification unavailable", exc_info=True)
        return unavailable_office_verification(
            remediator,
            "Saved-file scanner verification unavailable; manual review required.",
            error.code if isinstance(error, MeasurementError) else stage,
        )

    if any(_needs_semantic_verifier(issue) for issue in remediator.issues):
        return unavailable_office_verification(
            remediator,
            "Semantic image-description findings require a matching content verifier; "
            "a deterministic rescan cannot provide a comparable after score.",
        )

    if any(
        not before_keys[_key(remediator, issue, before)] for issue in remediator.issues
    ):
        return unavailable_office_verification(
            remediator,
            "The verification scanner could not reproduce every original finding; "
            "a comparable after score is unavailable.",
        )

    originals = {issue.id: issue for issue in remediator.issues}
    verified, unverified = [], []
    claimed_keys = set()
    for fixed in result.fixed_issues:
        original = originals.get(fixed.issue_id)
        key = _key(remediator, original, before) if original is not None else None
        # Duplicated/ambiguous findings cannot each claim the same disappearance.
        if (
            key is not None
            and before_keys[key] == 1
            and not after_keys[key]
            and key not in claimed_keys
        ):
            fixed.verification_passed = True
            if fixed.category in {IssueCategory.ALT_TEXT, IssueCategory.CHART}:
                fixed.needs_review = True
                fixed.notes = "Saved-file presence verified; description accuracy requires human review."
            verified.append(fixed)
            claimed_keys.add(key)
        else:
            unverified.append(fixed)
    _move_to_review(
        remediator,
        unverified,
        "The saved-file rescan did not verify that this finding was resolved.",
    )
    result.fixed_issues = verified
    result.fixed_count = len(verified)
    result.remediated_compliance_score = after_score
    result.score_provenance = "scanner_rescan"
    result.score_measurement = measurement
    result.score_verification_reason = None
    result.improvement = after_score - before_score
    regressions = [
        f"{category}: {location}"
        for (category, location), count in (after_keys - before_keys).items()
        for _ in range(count)
    ]
    passed = not regressions and not unverified and after_score >= before_score
    result.verification_passed = passed
    result.verification_result = VerificationResult(
        passed=passed,
        issues_before=len(before_issues),
        issues_after=len(after_issues),
        issues_fixed=[fixed.issue_id for fixed in verified],
        issues_remaining=[
            f"{category}: {location}" for category, location in after_keys
        ],
        regressions=regressions,
        verification_score=max(
            0.0,
            100 * (len(before_issues) - len(after_issues)) / max(1, len(before_issues)),
        ),
    )
    if regressions or after_score < before_score:
        result.warnings.append(
            "The saved-file rescan found a regression; review before use."
        )
    return result.verification_result


def measured_office_scores(remediator):
    """Scores are recorded by verification; disabling it cannot restore estimates."""
    if remediator.result.verification_result is None:
        unavailable_office_verification(
            remediator, "Saved-file verification was disabled; manual review required."
        )
