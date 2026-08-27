"""Deprecated adapter for the retired department compliance report.

The public report surface now emits only the canonical bounded accessibility
evidence artifact. Legacy method names remain importable for downstream users.
"""

from __future__ import annotations

from typing import Any

from .compliance_certificate import ComplianceCertificate


class ComplianceReportGenerator:
    """Compatibility facade for legacy report-generator imports."""

    @staticmethod
    async def generate_ai_recommendations(
        stats: dict[str, Any],
        trend_analysis: dict[str, Any] | None = None,
        issue_stats: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Return no AI prose because recommendations are not report evidence."""

        del stats, trend_analysis, issue_stats
        return []

    @staticmethod
    def generate_department_report(
        stats: dict[str, Any],
        issues: list[Any] | None = None,
        leaderboard: list[Any] | None = None,
        trend_analysis: dict[str, Any] | None = None,
        issue_stats: dict[str, Any] | None = None,
        ai_recommendations: list[dict[str, Any]] | None = None,
    ) -> bytes:
        """Render the bounded compatibility artifact from legacy aggregates."""

        del issues, leaderboard, trend_analysis, issue_stats, ai_recommendations
        return ComplianceCertificate.generate_certificate_from_stats(stats) or b""
