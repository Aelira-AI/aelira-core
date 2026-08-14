"""
Scan modes for accessibility testing.

Defines the different scanning thoroughness levels available in Aelira.
"""

from enum import Enum


class ScanMode(str, Enum):
    """
    Accessibility scan mode - determines which engines run and scan depth.

    QUICK: Fast scan using axe-core only (~90% coverage, 5-10s)
    COMPREHENSIVE: Multi-engine scan with axe-core + Pa11y (~95%+ coverage, 15-25s)
    DEEP: All engines + AI vision analysis (maximum confidence, 30-60s)
    """

    QUICK = "quick"
    COMPREHENSIVE = "comprehensive"
    DEEP = "deep"

    @property
    def description(self) -> str:
        """Human-readable description of scan mode"""
        descriptions = {
            ScanMode.QUICK: "Quick Scan - axe-core only (fast, ~90% coverage)",
            ScanMode.COMPREHENSIVE: "Comprehensive Scan - axe-core + Pa11y (slower, ~95%+ coverage)",
            ScanMode.DEEP: "Deep Scan - All engines + AI vision (maximum confidence)",
        }
        return descriptions[self]

    @property
    def engines(self) -> list:
        """List of engines used in this scan mode"""
        engines_map = {
            ScanMode.QUICK: ["axe-core"],
            ScanMode.COMPREHENSIVE: ["axe-core", "pa11y"],
            ScanMode.DEEP: ["axe-core", "pa11y", "ai-vision"],
        }
        return engines_map[self]

    @property
    def estimated_duration_range(self) -> tuple:
        """Estimated duration range in seconds (min, max)"""
        durations = {
            ScanMode.QUICK: (5, 10),
            ScanMode.COMPREHENSIVE: (15, 25),
            ScanMode.DEEP: (30, 60),
        }
        return durations[self]
