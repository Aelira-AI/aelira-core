"""Deprecated compatibility wrapper for the retired score-award PDF surface.

New code must use :mod:`accessibility_evidence_report`. The legacy class and
method names remain importable for downstream installations, but they can only
produce the same bounded evidence artifact and never return an award level.
"""

from __future__ import annotations

from typing import Any, Optional

from .accessibility_evidence_report import AccessibilityEvidenceReport


class ComplianceCertificate:
    """Compatibility facade; score-based awards were retired in v0.9.7."""

    @staticmethod
    def get_certificate_level(compliance_score: float) -> None:
        """Return no award level for every score."""

        del compliance_score
        return None

    @staticmethod
    def generate_certificate(
        department_name: str,
        institution: str,
        compliance_score: float,
        total_scans: int = 0,
        files_analyzed: int = 0,
        issued_by: str = "",
        valid_months: int = 0,
        include_qr: bool = False,
    ) -> bytes:
        """Render a limited evidence report from legacy aggregate arguments."""

        del issued_by, valid_months, include_qr
        report = {
            "schema_version": AccessibilityEvidenceReport.SCHEMA_VERSION,
            "report_kind": AccessibilityEvidenceReport.REPORT_KIND,
            "generated_at": "Unavailable from legacy aggregate input",
            "subject": {
                "department_name": department_name,
                "institution": institution,
            },
            "coverage": {
                "scope": "legacy_aggregate_input",
                "total_scans": total_scans,
                "completed_scans": "Unavailable",
                "unique_hashed_assets": files_analyzed,
                "unhashed_scan_count": "Unavailable",
                "pages_or_slides": "Unavailable",
                "content_types": {},
                "earliest_scan_at": None,
                "latest_scan_at": None,
                "corpus_denominator": None,
            },
            "methodology": {
                "scan_modes": [],
                "engines_used": [],
                "engine_versions": [],
                "ruleset_version": None,
                "recorded_wcag_levels": [],
                "automated_checks_present": total_scans > 0,
                "validator_evidence_present": False,
                "human_review_present": False,
            },
            "score": {
                "label": "automated_scan_score",
                "average": compliance_score,
                "minimum": None,
                "maximum": None,
                "is_conformance_determination": False,
            },
            "unresolved_findings": {
                "total": "Unavailable",
                "by_severity": {},
                "by_status": {},
                "tracker_coverage": "unavailable",
                "representative_findings": [],
            },
            "verification": {
                "status": "not_assessed",
                "automated_scan_count": total_scans,
                "validator_checkpoint_counts": {},
                "pending_manual_reviews": "Unavailable",
                "reviewed_fixes": "Unavailable",
                "statement": "Legacy aggregate input does not contain verification evidence.",
            },
            "standard": {
                "framework_code": "Unavailable",
                "framework_name": "Unavailable",
                "target_standard": "Unavailable",
                "deadline_date": None,
                "has_deadline": "Unavailable",
                "applicability_source": "unavailable",
            },
            "limitations": [
                "This compatibility path received only aggregate values; scan methodology, findings, validator evidence, and review evidence are unavailable.",
                "Automated scan scores summarize configured checks and do not determine whether content meets an accessibility standard or legal requirement.",
            ],
            "support": {},
        }
        return AccessibilityEvidenceReport.render(report)

    @staticmethod
    def generate_certificate_from_stats(stats: dict[str, Any]) -> Optional[bytes]:
        """Render the bounded compatibility artifact from a legacy stats mapping."""

        return ComplianceCertificate.generate_certificate(
            department_name=stats.get("department_name", "Unknown Department"),
            institution=stats.get("institution", "Unknown Institution"),
            compliance_score=stats.get("compliance_scores", {}).get("average", 0)
            or stats.get("avg_compliance_score", 0),
            total_scans=stats.get("overview", {}).get("total_scans", 0)
            or stats.get("total_scans", 0),
            files_analyzed=stats.get("overview", {}).get("total_files_scanned", 0)
            or stats.get("total_files_scanned", 0),
        )
