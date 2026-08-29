"""Request-safe accessibility analysis endpoints."""

import logging
import time
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Color Vision Deficiency Analysis ====================


class CVDColorPair(BaseModel):
    """A foreground/background color pair to analyze"""

    foreground: str  # Hex color (e.g., "#000000")
    background: str  # Hex color (e.g., "#ffffff")
    label: Optional[str] = None  # Optional label (e.g., "header text")


class CVDAnalysisRequest(BaseModel):
    """Request model for CVD analysis"""

    color_pairs: List[CVDColorPair]
    cvd_types: Optional[List[str]] = None  # If None, test all common types


class CVDIssue(BaseModel):
    """A single CVD accessibility issue"""

    color_blindness_type: str
    original_contrast: float
    simulated_contrast: float
    passes_wcag_aa: bool
    passes_wcag_aaa: bool
    severity: str
    description: str
    suggested_fix: Optional[str] = None


class CVDColorPairResult(BaseModel):
    """Result for a single color pair"""

    foreground: str
    background: str
    label: Optional[str]
    original_contrast: float
    issues: List[CVDIssue]
    accessible_for_all: bool
    affected_population_percentage: float


class CVDAnalysisResponse(BaseModel):
    """Response model for CVD analysis"""

    success: bool
    total_pairs_analyzed: int
    pairs_with_issues: int
    results: List[CVDColorPairResult]
    summary: dict
    processing_time_ms: int
    error: Optional[str] = None


@router.post("/cvd/analyze", response_model=CVDAnalysisResponse)
async def analyze_color_blindness(request: CVDAnalysisRequest):
    """
    Analyze color pairs for color vision deficiency (CVD) accessibility.

    Tests color combinations against 7 types of color blindness:
    - Protanopia (red-blind, 1% of males)
    - Deuteranopia (green-blind, 1% of males)
    - Tritanopia (blue-blind, very rare)
    - Protanomaly (red-weak, 1% of males)
    - Deuteranomaly (green-weak, 5% of males - MOST COMMON)
    - Tritanomaly (blue-weak, very rare)
    - Achromatopsia (complete color blindness, very rare)

    For each color pair, calculates:
    - Original contrast ratio
    - Simulated contrast ratio for each CVD type
    - WCAG AA/AAA compliance
    - Affected population percentage
    - Suggested fixes

    Args:
        color_pairs: List of foreground/background color pairs to analyze
        cvd_types: Optional list of specific CVD types to test (default: all)

    Returns:
        CVD analysis with issues, compliance status, and recommendations
    """
    from ...education.color_blindness_simulator import ColorBlindnessSimulator

    start_time = time.time()

    try:
        simulator = ColorBlindnessSimulator()
        results = []
        total_with_issues = 0

        for pair in request.color_pairs:
            analysis = simulator.analyze_color_accessibility(
                foreground=pair.foreground,
                background=pair.background,
                cvd_types=request.cvd_types,
            )

            # Convert issues to response format
            issues = [
                CVDIssue(
                    color_blindness_type=issue.color_blindness_type,
                    original_contrast=issue.original_contrast,
                    simulated_contrast=issue.simulated_contrast,
                    passes_wcag_aa=issue.passes_wcag_aa,
                    passes_wcag_aaa=issue.passes_wcag_aaa,
                    severity=issue.severity,
                    description=issue.description,
                    suggested_fix=issue.suggested_fix,
                )
                for issue in analysis.issues
            ]

            result = CVDColorPairResult(
                foreground=analysis.foreground_color,
                background=analysis.background_color,
                label=pair.label,
                original_contrast=analysis.original_contrast,
                issues=issues,
                accessible_for_all=analysis.accessible_for_all,
                affected_population_percentage=analysis.affected_population_percentage,
            )

            results.append(result)

            if not analysis.accessible_for_all:
                total_with_issues += 1

        processing_time_ms = int((time.time() - start_time) * 1000)

        # Calculate summary statistics
        total_issues = sum(len(r.issues) for r in results)
        critical_issues = sum(
            1 for r in results for i in r.issues if i.severity == "critical"
        )
        serious_issues = sum(
            1 for r in results for i in r.issues if i.severity == "serious"
        )

        return CVDAnalysisResponse(
            success=True,
            total_pairs_analyzed=len(request.color_pairs),
            pairs_with_issues=total_with_issues,
            results=results,
            summary={
                "total_issues": total_issues,
                "critical_issues": critical_issues,
                "serious_issues": serious_issues,
                "all_accessible": total_with_issues == 0,
                "cvd_types_tested": request.cvd_types
                or [
                    "protanopia",
                    "deuteranopia",
                    "tritanopia",
                    "protanomaly",
                    "deuteranomaly",
                    "tritanomaly",
                    "achromatopsia",
                ],
            },
            processing_time_ms=processing_time_ms,
        )

    except Exception as e:
        processing_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"CVD analysis failed: {e}", exc_info=True)
        return CVDAnalysisResponse(
            success=False,
            total_pairs_analyzed=0,
            pairs_with_issues=0,
            results=[],
            summary={},
            processing_time_ms=processing_time_ms,
            error=str(e),
        )


class CVDSimulateRequest(BaseModel):
    """Request model for color simulation"""

    color: str  # Hex color (e.g., "#ff0000")
    cvd_type: str  # CVD type to simulate


class CVDSimulateResponse(BaseModel):
    """Response model for color simulation"""

    success: bool
    original_color: str
    cvd_type: str
    simulated_color: str
    error: Optional[str] = None


@router.post("/cvd/simulate", response_model=CVDSimulateResponse)
async def simulate_cvd_color(request: CVDSimulateRequest):
    """
    Simulate how a color appears to users with a specific color vision deficiency.

    Useful for generating CVD-aware color palettes or previewing
    how content appears to color-blind users.

    Args:
        color: Hex color to simulate (e.g., "#ff0000")
        cvd_type: Type of color blindness to simulate

    Returns:
        Simulated color as hex string
    """
    from ...education.color_blindness_simulator import ColorBlindnessSimulator

    try:
        simulator = ColorBlindnessSimulator()
        simulated = simulator.simulate_color_blindness(
            color=request.color, cvd_type=request.cvd_type
        )

        return CVDSimulateResponse(
            success=True,
            original_color=request.color,
            cvd_type=request.cvd_type,
            simulated_color=simulated,
        )

    except Exception as e:
        logger.error(f"CVD simulation failed: {e}", exc_info=True)
        return CVDSimulateResponse(
            success=False,
            original_color=request.color,
            cvd_type=request.cvd_type,
            simulated_color="",
            error=str(e),
        )


@router.get("/cvd/statistics")
async def get_cvd_statistics():
    """
    Get population statistics for color vision deficiencies.

    Returns approximate percentages of population affected by each CVD type.
    Useful for understanding the impact of color accessibility issues.
    """
    from ...education.color_blindness_simulator import ColorBlindnessSimulator

    simulator = ColorBlindnessSimulator()
    return {
        "success": True,
        "statistics": simulator.get_cvd_statistics(),
        "note": "Percentages are approximate. Male population is more commonly affected than female.",
    }
