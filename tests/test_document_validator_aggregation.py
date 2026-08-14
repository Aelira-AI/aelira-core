"""DocumentValidator must aggregate finding severities correctly.

Regression for a fail-open bug: the aggregation loop compared enum *string*
values lexicographically ("critical" < "safe" alphabetically), so any set of
findings — including CRITICAL ones — aggregated to an overall SAFE result and
is_safe=True. A synthetic PDF with /JavaScript, /OpenAction, and /Launch
sailed through the upload gate.
"""

import pytest

from src.security.document_validator import (
    DocumentValidator,
    ThreatLevel,
)

MALICIOUS_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj\n<< /Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>\nendobj\n"
    b"2 0 obj\n<< /S /Launch /F (cmd.exe) >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
)


class TestThreatRankOrdering:
    def test_rank_is_strictly_increasing(self):
        order = [
            ThreatLevel.SAFE,
            ThreatLevel.LOW,
            ThreatLevel.MEDIUM,
            ThreatLevel.HIGH,
            ThreatLevel.CRITICAL,
        ]
        ranks = [level.rank for level in order]
        assert ranks == sorted(ranks)
        assert len(set(ranks)) == len(ranks)

    def test_every_level_outranks_safe(self):
        # The original bug: lexicographic value comparison put every level
        # below "safe". Rank comparison must invert that for all of them.
        for level in ThreatLevel:
            if level is not ThreatLevel.SAFE:
                assert level.rank > ThreatLevel.SAFE.rank, level


class TestAggregation:
    @pytest.fixture
    def validator(self):
        return DocumentValidator()

    @pytest.mark.asyncio
    async def test_malicious_pdf_is_not_safe(self, validator):
        result = await validator.validate("evil.pdf", MALICIOUS_PDF)
        assert result.findings, "expected findings for JS/OpenAction/Launch PDF"
        max_rank = max(f.threat_level.rank for f in result.findings)
        assert result.threat_level.rank == max_rank
        if max_rank > ThreatLevel.LOW.rank:
            assert result.is_safe is False

    @pytest.mark.asyncio
    async def test_high_finding_never_aggregates_to_safe(self, validator):
        result = await validator.validate("evil.pdf", MALICIOUS_PDF)
        high_or_worse = [
            f for f in result.findings if f.threat_level.rank >= ThreatLevel.HIGH.rank
        ]
        if high_or_worse:
            assert result.threat_level.rank >= ThreatLevel.HIGH.rank
            assert result.is_safe is False
