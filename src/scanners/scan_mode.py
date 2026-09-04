"""
Scan modes for accessibility testing.

Defines the different scanning thoroughness levels available in Aelira.
"""

from enum import Enum


class ScanMode(str, Enum):
    """
    Accessibility scan mode - determines which engines run and scan depth.

    QUICK: Requests a fast axe-core-only scan (5-10s)
    COMPREHENSIVE: Requests axe-core and Pa11y (15-25s)
    DEEP: Requests all configured engines and AI vision analysis (30-60s)
    """

    QUICK = "quick"
    COMPREHENSIVE = "comprehensive"
    DEEP = "deep"

    @property
    def description(self) -> str:
        """Human-readable description of scan mode"""
        descriptions = {
            ScanMode.QUICK: "Quick Scan - requests axe-core only (fast)",
            ScanMode.COMPREHENSIVE: "Comprehensive Scan - requests axe-core + Pa11y",
            ScanMode.DEEP: "Deep Scan - requests all configured engines + AI vision",
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
