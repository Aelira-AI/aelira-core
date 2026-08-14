"""Accessibility analysis endpoints — focus order, color vision deficiency."""

import logging
import time
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ==================== Focus Order Analysis ====================


class FocusOrderRequest(BaseModel):
    """Request model for focus order analysis"""

    url: str
    max_tabs: int = 100


class FocusOrderAnalysisResponse(BaseModel):
    """Response model for focus order analysis"""

    success: bool
    url: str
    total_focusable_elements: int
    focus_sequence: List[dict]
    issues: List[dict]
    compliance_score: float
    wcag_compliant: bool
    processing_time_ms: int
    error: Optional[str] = None


@router.post("/focus-order/analyze", response_model=FocusOrderAnalysisResponse)
async def analyze_focus_order(request: FocusOrderRequest):
    """
    Analyze keyboard focus order for WCAG 2.4.3 compliance.

    This endpoint simulates TAB key navigation and detects:
    - Focus traps (keyboard users can't escape)
    - Invisible elements in focus order
    - Illogical focus order (large visual jumps)
    - Missing focus indicators (WCAG 2.4.7)
    - Skip link issues

    Args:
        url: URL to analyze
        max_tabs: Maximum TAB key presses to simulate (default: 100)

    Returns:
        Focus order analysis with issues and compliance score
    """
    from ...education.focus_order_analyzer import FocusOrderAnalyzer

    start_time = time.time()

    try:
        analyzer = FocusOrderAnalyzer()
        result = await analyzer.analyze_focus_order(
            url=request.url, max_tabs=request.max_tabs
        )

        processing_time_ms = int((time.time() - start_time) * 1000)

        return FocusOrderAnalysisResponse(
            success=True,
            url=result.url,
            total_focusable_elements=result.total_focusable_elements,
            focus_sequence=[el.model_dump() for el in result.focus_sequence],
            issues=[issue.model_dump() for issue in result.issues],
            compliance_score=result.compliance_score,
            wcag_compliant=result.wcag_compliant,
            processing_time_ms=processing_time_ms,
        )

    except Exception as e:
        processing_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Focus order analysis failed: {e}", exc_info=True)
        return FocusOrderAnalysisResponse(
            success=False,
            url=request.url,
            total_focusable_elements=0,
            focus_sequence=[],
            issues=[],
            compliance_score=0.0,
            wcag_compliant=False,
            processing_time_ms=processing_time_ms,
            error=str(e),
        )


class FocusOrderHTMLRequest(BaseModel):
    """Request model for focus order analysis of HTML content"""

    html_content: str
    base_url: str = "http://localhost"
    max_tabs: int = 100


@router.post("/focus-order/analyze-html", response_model=FocusOrderAnalysisResponse)
async def analyze_focus_order_html(request: FocusOrderHTMLRequest):
    """
    Analyze keyboard focus order for raw HTML content.

    Use this endpoint when you have HTML content (e.g., from a document processor)
    rather than a live URL.

    Args:
        html_content: HTML content to analyze
        base_url: Base URL for the content (default: http://localhost)
        max_tabs: Maximum TAB key presses to simulate (default: 100)

    Returns:
        Focus order analysis with issues and compliance score
    """
    from ...education.focus_order_analyzer import FocusOrderAnalyzer

    start_time = time.time()

    try:
        analyzer = FocusOrderAnalyzer()
        result = await analyzer.analyze_html_content(
            html_content=request.html_content, base_url=request.base_url
        )

        processing_time_ms = int((time.time() - start_time) * 1000)

        return FocusOrderAnalysisResponse(
            success=True,
            url=result.url,
            total_focusable_elements=result.total_focusable_elements,
            focus_sequence=[el.model_dump() for el in result.focus_sequence],
            issues=[issue.model_dump() for issue in result.issues],
            compliance_score=result.compliance_score,
            wcag_compliant=result.wcag_compliant,
            processing_time_ms=processing_time_ms,
        )

    except Exception as e:
        processing_time_ms = int((time.time() - start_time) * 1000)
        logger.error(f"Focus order HTML analysis failed: {e}", exc_info=True)
        return FocusOrderAnalysisResponse(
            success=False,
            url=request.base_url,
            total_focusable_elements=0,
            focus_sequence=[],
            issues=[],
            compliance_score=0.0,
            wcag_compliant=False,
            processing_time_ms=processing_time_ms,
            error=str(e),
        )


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
