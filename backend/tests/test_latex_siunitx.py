"""
Tests for Siunitx SI Unit Support (Task 12)

Tests cover:
- SI unit pattern detection
- Unit parsing and ARIA generation
- Number formatting
- Angle formatting
- Range expressions
- SI prefixes and modifiers
"""

import pytest
import re

from src.education.latex_processor import (
    LaTeXProcessor,
    SI_BASE_UNITS,
    SI_DERIVED_UNITS,
    SI_PREFIXES,
    SI_MODIFIERS,
)


class TestSiunitxPatterns:
    """Test siunitx pattern definitions."""

    def test_si_value_unit_pattern(self):
        """Test SI value with unit pattern matches."""
        pattern = r"\\SI\{([^}]+)\}\{([^}]+)\}"

        test_cases = [
            (
                r"\SI{9.8}{\meter\per\second\squared}",
                "9.8",
                r"\meter\per\second\squared",
            ),
            (r"\SI{100}{\kilo\meter}", "100", r"\kilo\meter"),
            (r"\SI{3.14159}{\radian}", "3.14159", r"\radian"),
        ]

        for latex, expected_value, expected_unit in test_cases:
            match = re.search(pattern, latex)
            assert match is not None, f"Pattern should match: {latex}"
            assert match.group(1) == expected_value
            assert match.group(2) == expected_unit

    def test_si_unit_only_pattern(self):
        """Test si unit-only pattern matches."""
        pattern = r"\\si\{([^}]+)\}"

        test_cases = [
            (r"\si{\kilo\gram}", r"\kilo\gram"),
            (r"\si{\meter\per\second}", r"\meter\per\second"),
        ]

        for latex, expected_unit in test_cases:
            match = re.search(pattern, latex)
            assert match is not None
            assert match.group(1) == expected_unit

    def test_num_pattern(self):
        """Test num pattern matches."""
        pattern = r"\\num\{([^}]+)\}"

        test_cases = [
            (r"\num{1.23e-4}", "1.23e-4"),
            (r"\num{12345}", "12345"),
            (r"\num{1.23(4)}", "1.23(4)"),
        ]

        for latex, expected_num in test_cases:
            match = re.search(pattern, latex)
            assert match is not None
            assert match.group(1) == expected_num

    def test_ang_pattern(self):
        """Test ang pattern matches."""
        pattern = r"\\ang\{([^}]+)\}"

        test_cases = [
            (r"\ang{45}", "45"),
            (r"\ang{45;30}", "45;30"),
            (r"\ang{45;30;0}", "45;30;0"),
        ]

        for latex, expected_angle in test_cases:
            match = re.search(pattern, latex)
            assert match is not None
            assert match.group(1) == expected_angle

    def test_sirange_pattern(self):
        """Test SIrange pattern matches."""
        pattern = r"\\SIrange\{([^}]+)\}\{([^}]+)\}\{([^}]+)\}"

        latex = r"\SIrange{1}{10}{\meter}"
        match = re.search(pattern, latex)
        assert match is not None
        assert match.group(1) == "1"
        assert match.group(2) == "10"
        assert match.group(3) == r"\meter"


class TestSIUnitMappings:
    """Test SI unit name mappings."""

    def test_base_units_defined(self):
        """Test that all SI base units are defined."""
        expected_base = [
            "meter",
            "kilogram",
            "second",
            "ampere",
            "kelvin",
            "mole",
            "candela",
        ]
        for unit in expected_base:
            assert unit in SI_BASE_UNITS.values(), f"Missing base unit: {unit}"

    def test_derived_units_defined(self):
        """Test that common derived units are defined."""
        expected_derived = ["hertz", "newton", "joule", "watt", "volt", "ohm"]
        for unit in expected_derived:
            assert unit in SI_DERIVED_UNITS.values(), f"Missing derived unit: {unit}"

    def test_prefixes_defined(self):
        """Test that SI prefixes are defined."""
        expected_prefixes = ["kilo", "mega", "giga", "milli", "micro", "nano"]
        for prefix in expected_prefixes:
            assert prefix in SI_PREFIXES.values(), f"Missing prefix: {prefix}"

    def test_modifiers_defined(self):
        """Test that unit modifiers are defined."""
        expected_modifiers = ["per", "squared", "cubed"]
        for modifier in expected_modifiers:
            assert modifier in SI_MODIFIERS.values(), f"Missing modifier: {modifier}"


class TestSiunitxAriaGeneration:
    """Test ARIA label generation for siunitx expressions."""

    @pytest.fixture
    def processor(self):
        """Create LaTeXProcessor with AI disabled for testing."""
        return LaTeXProcessor(use_ai=False)

    def test_si_value_unit_basic(self, processor):
        """Test basic SI value with unit ARIA generation."""
        latex = r"\SI{9.8}{\meter\per\second\squared}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "9.8" in aria
        assert "meter" in aria.lower()
        assert "per" in aria.lower()
        assert "second" in aria.lower()

    def test_si_unit_only(self, processor):
        """Test si unit-only ARIA generation."""
        latex = r"\si{\kilo\gram}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "kilo" in aria.lower()
        assert "gram" in aria.lower()

    def test_num_basic(self, processor):
        """Test basic num ARIA generation."""
        latex = r"\num{12345}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "12345" in aria

    def test_num_scientific(self, processor):
        """Test scientific notation num ARIA generation."""
        latex = r"\num{1.23e-4}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "1.23" in aria
        assert "10" in aria
        assert "power" in aria.lower()
        assert "-4" in aria

    def test_ang_degrees_only(self, processor):
        """Test angle with degrees only."""
        latex = r"\ang{45}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "45" in aria
        assert "degree" in aria.lower()

    def test_ang_full(self, processor):
        """Test angle with degrees, minutes, seconds."""
        latex = r"\ang{45;30;15}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "45" in aria
        assert "degree" in aria.lower()
        assert "30" in aria
        assert "minute" in aria.lower()
        assert "15" in aria
        assert "second" in aria.lower()

    def test_sirange(self, processor):
        """Test SIrange ARIA generation."""
        latex = r"\SIrange{1}{10}{\meter}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "1" in aria
        assert "10" in aria
        assert "to" in aria.lower()
        assert "meter" in aria.lower()


class TestSIUnitParsing:
    """Test SI unit string parsing."""

    @pytest.fixture
    def processor(self):
        """Create LaTeXProcessor for testing."""
        return LaTeXProcessor(use_ai=False)

    def test_parse_simple_unit(self, processor):
        """Test parsing simple unit."""
        result = processor._parse_si_unit(r"\meter")
        assert "meter" in result.lower()

    def test_parse_prefixed_unit(self, processor):
        """Test parsing unit with prefix."""
        result = processor._parse_si_unit(r"\kilo\meter")
        assert "kilo" in result.lower()
        assert "meter" in result.lower()

    def test_parse_compound_unit(self, processor):
        """Test parsing compound unit with per."""
        result = processor._parse_si_unit(r"\meter\per\second")
        assert "meter" in result.lower()
        assert "per" in result.lower()
        assert "second" in result.lower()

    def test_parse_squared_unit(self, processor):
        """Test parsing unit with squared modifier."""
        result = processor._parse_si_unit(r"\meter\squared")
        assert "meter" in result.lower()
        assert "squared" in result.lower()


class TestSINumberParsing:
    """Test SI number string parsing."""

    @pytest.fixture
    def processor(self):
        """Create LaTeXProcessor for testing."""
        return LaTeXProcessor(use_ai=False)

    def test_parse_simple_number(self, processor):
        """Test parsing simple number."""
        result = processor._parse_si_number("12345")
        assert result == "12345"

    def test_parse_scientific_notation(self, processor):
        """Test parsing scientific notation."""
        result = processor._parse_si_number("1.23e-4")
        assert "1.23" in result
        assert "10" in result
        assert "-4" in result

    def test_parse_uncertainty_parentheses(self, processor):
        """Test parsing uncertainty with parentheses."""
        result = processor._parse_si_number("1.23(4)")
        assert "1.23" in result
        assert "plus or minus" in result.lower()


class TestSIAngleParsing:
    """Test SI angle string parsing."""

    @pytest.fixture
    def processor(self):
        """Create LaTeXProcessor for testing."""
        return LaTeXProcessor(use_ai=False)

    def test_parse_degrees_only(self, processor):
        """Test parsing degrees only angle."""
        result = processor._parse_si_angle("45")
        assert "45" in result
        assert "degree" in result.lower()

    def test_parse_degrees_minutes(self, processor):
        """Test parsing degrees and minutes."""
        result = processor._parse_si_angle("45;30")
        assert "45" in result
        assert "30" in result
        assert "degree" in result.lower()
        assert "minute" in result.lower()

    def test_parse_full_angle(self, processor):
        """Test parsing full degrees;minutes;seconds."""
        result = processor._parse_si_angle("45;30;15")
        assert "45" in result
        assert "30" in result
        assert "15" in result


class TestHeuristicAriaIntegration:
    """Test siunitx integration with heuristic ARIA generation."""

    @pytest.fixture
    def processor(self):
        """Create LaTeXProcessor for testing."""
        return LaTeXProcessor(use_ai=False)

    def test_heuristic_detects_si(self, processor):
        """Test that heuristic ARIA generator detects SI expressions."""
        latex = r"\SI{9.8}{\meter\per\second\squared}"
        aria = processor._generate_heuristic_aria_label(latex)

        # Should detect as SI and generate appropriate label
        assert "9.8" in aria
        assert "meter" in aria.lower()

    def test_heuristic_detects_num(self, processor):
        """Test that heuristic ARIA generator detects num expressions."""
        latex = r"\num{1.23e-4}"
        aria = processor._generate_heuristic_aria_label(latex)

        # Should detect as SI number
        assert "1.23" in aria

    def test_heuristic_detects_ang(self, processor):
        """Test that heuristic ARIA generator detects ang expressions."""
        latex = r"\ang{45;30;0}"
        aria = processor._generate_heuristic_aria_label(latex)

        # Should detect as SI angle
        assert "45" in aria
        assert "degree" in aria.lower()


class TestSiunitxV3Syntax:
    """Test siunitx v3 syntax support (qty, unit commands)."""

    @pytest.fixture
    def processor(self):
        """Create LaTeXProcessor for testing."""
        return LaTeXProcessor(use_ai=False)

    def test_qty_value_unit(self, processor):
        """Test qty value with unit (siunitx v3) ARIA generation."""
        latex = r"\qty{9.8}{\meter\per\second\squared}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "9.8" in aria
        assert "meter" in aria.lower()

    def test_unit_only(self, processor):
        """Test unit command (siunitx v3) ARIA generation."""
        latex = r"\unit{\kilo\gram}"
        aria = processor._generate_siunitx_aria_label(latex)

        assert "kilo" in aria.lower()
        assert "gram" in aria.lower()


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def processor(self):
        """Create LaTeXProcessor for testing."""
        return LaTeXProcessor(use_ai=False)

    def test_unknown_unit(self, processor):
        """Test handling of unknown unit."""
        latex = r"\SI{5}{\unknownunit}"
        aria = processor._generate_siunitx_aria_label(latex)

        # Should still produce output with value
        assert "5" in aria

    def test_empty_unit(self, processor):
        """Test handling of empty unit."""
        result = processor._parse_si_unit("")
        # Should return empty or the original
        assert result == ""

    def test_nested_braces(self, processor):
        """Test handling of expressions that might have nested braces."""
        # This is a simplified test - real nested braces are complex
        latex = r"\SI{1.23}{\meter}"
        aria = processor._generate_siunitx_aria_label(latex)
        assert "1.23" in aria


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
