"""
Color Blindness Simulation Module (RGBlind Integration)

This module provides functionality to:
1. Simulate 8 types of color blindness (protanopia, deuteranopia, tritanopia, etc.)
2. Validate contrast ratios for color-blind users
3. Detect color-coded content that may be inaccessible
4. Integrate with PowerPoint and web scanners for enhanced accessibility checking

Based on RGBlind: Python library for color blindness simulation
Algorithms based on: Brettel, H., Viénot, F., & Mollon, J. D. (1997)
"Computerized simulation of color appearance for dichromats"
"""

from typing import Tuple, Dict, List, Optional
from pydantic import BaseModel
import numpy as np


class ColorBlindnessType:
    """Supported color blindness types"""

    PROTANOPIA = "protanopia"  # Red-blind (1% of males)
    DEUTERANOPIA = "deuteranopia"  # Green-blind (1% of males)
    TRITANOPIA = "tritanopia"  # Blue-blind (very rare)
    PROTANOMALY = "protanomaly"  # Red-weak (1% of males)
    DEUTERANOMALY = "deuteranomaly"  # Green-weak (5% of males, most common)
    TRITANOMALY = "tritanomaly"  # Blue-weak (very rare)
    ACHROMATOPSIA = "achromatopsia"  # Complete color blindness (very rare)
    ACHROMATOMALY = "achromatomaly"  # Blue cone monochromacy (very rare)


class ColorBlindnessIssue(BaseModel):
    """Issue detected for color-blind users"""

    color_blindness_type: str
    original_contrast: float
    simulated_contrast: float
    passes_wcag_aa: bool  # 4.5:1 threshold
    passes_wcag_aaa: bool  # 7:1 threshold
    severity: str  # "critical", "serious", "moderate", "none"
    description: str
    suggested_fix: Optional[str] = None


class ColorBlindnessAnalysisResult(BaseModel):
    """Result of color blindness analysis"""

    foreground_color: str  # Hex color
    background_color: str  # Hex color
    original_contrast: float
    issues: List[ColorBlindnessIssue]
    accessible_for_all: bool  # True if passes for all CVD types
    affected_population_percentage: float  # % of population affected


class ColorBlindnessSimulator:
    """Simulate color blindness and validate accessibility"""

    def __init__(self):
        # Transformation matrices for dichromatic vision
        # Based on Brettel, Viénot, & Mollon (1997)
        # http://www.inf.ufrgs.br/~oliveira/pubs_files/CVD_Simulation/CVD_Simulation.html

        # Protanopia (red-blind) - LMS to RGB transformation
        self.protanopia_matrix = np.array(
            [
                [0.152286, 1.052583, -0.204868],
                [0.114503, 0.786281, 0.099216],
                [-0.003882, -0.048116, 1.051998],
            ]
        )

        # Deuteranopia (green-blind)
        self.deuteranopia_matrix = np.array(
            [
                [0.367322, 0.860646, -0.227968],
                [0.280085, 0.672501, 0.047413],
                [-0.011820, 0.042940, 0.968881],
            ]
        )

        # Tritanopia (blue-blind)
        self.tritanopia_matrix = np.array(
            [
                [1.255528, -0.076749, -0.178779],
                [-0.078411, 0.930809, 0.147602],
                [0.004733, 0.691367, 0.303900],
            ]
        )

        # Achromatopsia (complete color blindness - grayscale)
        self.achromatopsia_matrix = np.array(
            [[0.299, 0.587, 0.114], [0.299, 0.587, 0.114], [0.299, 0.587, 0.114]]
        )

    def hex_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        """Convert hex color to RGB tuple"""
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))

    def rgb_to_hex(self, rgb: Tuple[int, int, int]) -> str:
        """Convert RGB tuple to hex string"""
        r, g, b = [max(0, min(255, int(c))) for c in rgb]
        return f"#{r:02x}{g:02x}{b:02x}"

    def simulate_color_blindness(self, color: str, cvd_type: str) -> str:
        """
        Simulate how a color appears to color-blind users

        Args:
            color: Hex color string (e.g., "#ff0000")
            cvd_type: Type of color vision deficiency (see ColorBlindnessType)

        Returns:
            Simulated color as hex string
        """
        # Convert to RGB
        rgb = np.array(self.hex_to_rgb(color), dtype=float)

        # Normalize to 0-1 range
        rgb_normalized = rgb / 255.0

        # Select transformation matrix
        if cvd_type == ColorBlindnessType.PROTANOPIA:
            matrix = self.protanopia_matrix
        elif cvd_type == ColorBlindnessType.DEUTERANOPIA:
            matrix = self.deuteranopia_matrix
        elif cvd_type == ColorBlindnessType.TRITANOPIA:
            matrix = self.tritanopia_matrix
        elif cvd_type == ColorBlindnessType.ACHROMATOPSIA:
            matrix = self.achromatopsia_matrix
        elif cvd_type == ColorBlindnessType.PROTANOMALY:
            # Protanomaly is a blend of normal and protanopia (50%)
            matrix = 0.5 * np.eye(3) + 0.5 * self.protanopia_matrix
        elif cvd_type == ColorBlindnessType.DEUTERANOMALY:
            # Deuteranomaly is a blend of normal and deuteranopia (60%)
            matrix = 0.4 * np.eye(3) + 0.6 * self.deuteranopia_matrix
        elif cvd_type == ColorBlindnessType.TRITANOMALY:
            # Tritanomaly is a blend of normal and tritanopia (50%)
            matrix = 0.5 * np.eye(3) + 0.5 * self.tritanopia_matrix
        elif cvd_type == ColorBlindnessType.ACHROMATOMALY:
            # Blue cone monochromacy (mostly grayscale with some blue)
            matrix = 0.2 * np.eye(3) + 0.8 * self.achromatopsia_matrix
        else:
            # Unknown type, return original
            return color

        # Apply transformation
        simulated_rgb = np.dot(matrix, rgb_normalized)

        # Denormalize to 0-255 range
        simulated_rgb = np.clip(simulated_rgb * 255, 0, 255)

        return self.rgb_to_hex(tuple(simulated_rgb))

    def calculate_relative_luminance(self, rgb: Tuple[int, int, int]) -> float:
        """Calculate relative luminance (WCAG formula)"""

        def adjust(color_value):
            color_value = color_value / 255.0
            if color_value <= 0.03928:
                return color_value / 12.92
            return ((color_value + 0.055) / 1.055) ** 2.4

        r, g, b = rgb
        r = adjust(r)
        g = adjust(g)
        b = adjust(b)

        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def calculate_contrast_ratio(self, color1: str, color2: str) -> float:
        """
        Calculate contrast ratio between two colors (WCAG formula)
        https://www.w3.org/TR/WCAG21/#dfn-contrast-ratio
        """
        rgb1 = self.hex_to_rgb(color1)
        rgb2 = self.hex_to_rgb(color2)

        l1 = self.calculate_relative_luminance(rgb1)
        l2 = self.calculate_relative_luminance(rgb2)

        lighter = max(l1, l2)
        darker = min(l1, l2)

        return (lighter + 0.05) / (darker + 0.05)

    def analyze_color_accessibility(
        self, foreground: str, background: str, cvd_types: Optional[List[str]] = None
    ) -> ColorBlindnessAnalysisResult:
        """
        Analyze color contrast for all color vision deficiencies

        Args:
            foreground: Foreground hex color (e.g., "#000000")
            background: Background hex color (e.g., "#ffffff")
            cvd_types: List of CVD types to test (default: all common types)

        Returns:
            ColorBlindnessAnalysisResult with issues and recommendations
        """
        if cvd_types is None:
            # Test most common types (affects ~8% of males, ~0.5% of females)
            cvd_types = [
                ColorBlindnessType.PROTANOPIA,  # 1% males
                ColorBlindnessType.DEUTERANOPIA,  # 1% males
                ColorBlindnessType.TRITANOPIA,  # 0.01% population
                ColorBlindnessType.PROTANOMALY,  # 1% males
                ColorBlindnessType.DEUTERANOMALY,  # 5% males (MOST COMMON)
                ColorBlindnessType.TRITANOMALY,  # <0.01% population
                ColorBlindnessType.ACHROMATOPSIA,  # 0.003% population
            ]

        # Calculate original contrast
        original_contrast = self.calculate_contrast_ratio(foreground, background)

        issues = []
        total_affected_population = 0.0

        # Population percentages (approximate)
        cvd_population = {
            ColorBlindnessType.PROTANOPIA: 1.0,  # 1% of males
            ColorBlindnessType.DEUTERANOPIA: 1.0,  # 1% of males
            ColorBlindnessType.TRITANOPIA: 0.01,  # Very rare
            ColorBlindnessType.PROTANOMALY: 1.0,  # 1% of males
            ColorBlindnessType.DEUTERANOMALY: 5.0,  # 5% of males (MOST COMMON)
            ColorBlindnessType.TRITANOMALY: 0.01,  # Very rare
            ColorBlindnessType.ACHROMATOPSIA: 0.003,  # Extremely rare
        }

        for cvd_type in cvd_types:
            # Simulate how colors appear to this CVD type
            simulated_fg = self.simulate_color_blindness(foreground, cvd_type)
            simulated_bg = self.simulate_color_blindness(background, cvd_type)

            # Calculate simulated contrast
            simulated_contrast = self.calculate_contrast_ratio(
                simulated_fg, simulated_bg
            )

            # Check WCAG compliance
            passes_aa = simulated_contrast >= 4.5
            passes_aaa = simulated_contrast >= 7.0

            # Determine severity
            if not passes_aa:
                severity = "critical" if simulated_contrast < 3.0 else "serious"

                # Create issue
                issue = ColorBlindnessIssue(
                    color_blindness_type=cvd_type,
                    original_contrast=round(original_contrast, 2),
                    simulated_contrast=round(simulated_contrast, 2),
                    passes_wcag_aa=passes_aa,
                    passes_wcag_aaa=passes_aaa,
                    severity=severity,
                    description=f"Color combination fails WCAG AA for {cvd_type.replace('_', ' ')} users (contrast: {simulated_contrast:.2f}:1, need: 4.5:1)",
                    suggested_fix=self._suggest_color_fix(
                        foreground, background, cvd_type, simulated_contrast
                    ),
                )
                issues.append(issue)

                # Add to affected population
                total_affected_population += cvd_population.get(cvd_type, 0.0)

        # Check if accessible for all
        accessible_for_all = len(issues) == 0

        return ColorBlindnessAnalysisResult(
            foreground_color=foreground,
            background_color=background,
            original_contrast=round(original_contrast, 2),
            issues=issues,
            accessible_for_all=accessible_for_all,
            affected_population_percentage=round(
                min(total_affected_population, 100), 2
            ),
        )

    def _suggest_color_fix(
        self, foreground: str, background: str, cvd_type: str, current_contrast: float
    ) -> str:
        """Generate suggested fix for color blindness issue"""
        # Get RGB values
        fg_rgb = self.hex_to_rgb(foreground)
        bg_rgb = self.hex_to_rgb(background)

        # Calculate average brightness
        fg_brightness = sum(fg_rgb) / 3
        bg_brightness = sum(bg_rgb) / 3

        suggestions = []

        # Suggest increasing contrast
        if fg_brightness > bg_brightness:
            suggestions.append("Darken foreground color or lighten background color")
        else:
            suggestions.append("Lighten foreground color or darken background color")

        # Add pattern/texture suggestion for critical cases
        if current_contrast < 3.0:
            suggestions.append(
                "Consider adding patterns, borders, or text labels (not just color) to convey information"
            )

        # Specific CVD type recommendations
        if cvd_type in [
            ColorBlindnessType.PROTANOPIA,
            ColorBlindnessType.DEUTERANOPIA,
            ColorBlindnessType.DEUTERANOMALY,
        ]:
            suggestions.append(
                "Avoid red/green color combinations - use blue/yellow instead"
            )
        elif cvd_type == ColorBlindnessType.TRITANOPIA:
            suggestions.append(
                "Avoid blue/yellow color combinations - use red/green instead"
            )

        return "; ".join(suggestions)

    def get_cvd_statistics(self) -> Dict[str, float]:
        """
        Get population statistics for color vision deficiencies

        Returns:
            Dictionary mapping CVD type to percentage of population affected
        """
        return {
            "Total color blind (male)": 8.0,
            "Total color blind (female)": 0.5,
            ColorBlindnessType.DEUTERANOMALY: 5.0,  # Most common
            ColorBlindnessType.PROTANOPIA: 1.0,
            ColorBlindnessType.DEUTERANOPIA: 1.0,
            ColorBlindnessType.PROTANOMALY: 1.0,
            ColorBlindnessType.TRITANOPIA: 0.01,
            ColorBlindnessType.TRITANOMALY: 0.01,
            ColorBlindnessType.ACHROMATOPSIA: 0.003,
        }
