"""
Unit tests for ScanMode enum.

Tests three-tier scan strategy configuration.
"""

import pytest
from src.scanners.scan_mode import ScanMode


class TestScanMode:
    """Test suite for ScanMode enum"""

    def test_scan_mode_values(self):
        """Test ScanMode enum has correct string values"""
        assert ScanMode.QUICK.value == "quick"
        assert ScanMode.COMPREHENSIVE.value == "comprehensive"
        assert ScanMode.DEEP.value == "deep"

    def test_scan_mode_is_string_enum(self):
        """Test ScanMode inherits from str for JSON serialization"""
        assert isinstance(ScanMode.QUICK, str)
        assert isinstance(ScanMode.COMPREHENSIVE, str)
        assert isinstance(ScanMode.DEEP, str)

    def test_scan_mode_from_string(self):
        """Test creating ScanMode from string values"""
        assert ScanMode("quick") == ScanMode.QUICK
        assert ScanMode("comprehensive") == ScanMode.COMPREHENSIVE
        assert ScanMode("deep") == ScanMode.DEEP

    def test_scan_mode_invalid_value(self):
        """Test creating ScanMode with invalid value raises error"""
        with pytest.raises(ValueError):
            ScanMode("invalid")

    def test_quick_mode_description(self):
        """Test QUICK mode description"""
        description = ScanMode.QUICK.description
        assert isinstance(description, str)
        assert "quick" in description.lower()
        assert "axe-core" in description.lower()
        assert "90%" in description

    def test_comprehensive_mode_description(self):
        """Test COMPREHENSIVE mode description"""
        description = ScanMode.COMPREHENSIVE.description
        assert isinstance(description, str)
        assert "comprehensive" in description.lower()
        assert "axe-core" in description.lower()
        assert "pa11y" in description.lower()
        assert "95%" in description

    def test_deep_mode_description(self):
        """Test DEEP mode description"""
        description = ScanMode.DEEP.description
        assert isinstance(description, str)
        assert "deep" in description.lower()
        assert "ai" in description.lower() or "vision" in description.lower()
        assert "maximum" in description.lower()

    def test_quick_mode_engines(self):
        """Test QUICK mode uses only axe-core"""
        engines = ScanMode.QUICK.engines
        assert isinstance(engines, list)
        assert len(engines) == 1
        assert "axe-core" in engines

    def test_comprehensive_mode_engines(self):
        """Test COMPREHENSIVE mode uses axe-core + pa11y"""
        engines = ScanMode.COMPREHENSIVE.engines
        assert isinstance(engines, list)
        assert len(engines) == 2
        assert "axe-core" in engines
        assert "pa11y" in engines

    def test_deep_mode_engines(self):
        """Test DEEP mode uses all engines including AI vision"""
        engines = ScanMode.DEEP.engines
        assert isinstance(engines, list)
        assert len(engines) == 3
        assert "axe-core" in engines
        assert "pa11y" in engines
        assert "ai-vision" in engines

    def test_quick_mode_duration(self):
        """Test QUICK mode estimated duration range"""
        min_duration, max_duration = ScanMode.QUICK.estimated_duration_range
        assert isinstance(min_duration, int)
        assert isinstance(max_duration, int)
        assert min_duration < max_duration
        assert min_duration == 5
        assert max_duration == 10

    def test_comprehensive_mode_duration(self):
        """Test COMPREHENSIVE mode estimated duration range"""
        min_duration, max_duration = ScanMode.COMPREHENSIVE.estimated_duration_range
        assert isinstance(min_duration, int)
        assert isinstance(max_duration, int)
        assert min_duration < max_duration
        assert min_duration == 15
        assert max_duration == 25

    def test_deep_mode_duration(self):
        """Test DEEP mode estimated duration range"""
        min_duration, max_duration = ScanMode.DEEP.estimated_duration_range
        assert isinstance(min_duration, int)
        assert isinstance(max_duration, int)
        assert min_duration < max_duration
        assert min_duration == 30
        assert max_duration == 60

    def test_duration_progression(self):
        """Test that duration increases from QUICK -> COMPREHENSIVE -> DEEP"""
        quick_min, quick_max = ScanMode.QUICK.estimated_duration_range
        comp_min, comp_max = ScanMode.COMPREHENSIVE.estimated_duration_range
        deep_min, deep_max = ScanMode.DEEP.estimated_duration_range

        # Each mode should take longer than the previous
        assert quick_max < comp_min  # Quick is faster than comprehensive
        assert comp_max < deep_min  # Comprehensive is faster than deep

    def test_engines_progression(self):
        """Test that engines list grows from QUICK -> COMPREHENSIVE -> DEEP"""
        quick_engines = ScanMode.QUICK.engines
        comp_engines = ScanMode.COMPREHENSIVE.engines
        deep_engines = ScanMode.DEEP.engines

        # Each mode should have more engines
        assert len(quick_engines) < len(comp_engines)
        assert len(comp_engines) < len(deep_engines)

        # Quick engines should be subset of comprehensive
        for engine in quick_engines:
            assert engine in comp_engines

        # Comprehensive engines should be subset of deep
        for engine in comp_engines:
            assert engine in deep_engines

    def test_all_modes_iterable(self):
        """Test that all ScanMode values are iterable"""
        all_modes = list(ScanMode)
        assert len(all_modes) == 3
        assert ScanMode.QUICK in all_modes
        assert ScanMode.COMPREHENSIVE in all_modes
        assert ScanMode.DEEP in all_modes

    def test_mode_comparison(self):
        """Test ScanMode enum equality and comparison"""
        assert ScanMode.QUICK == ScanMode.QUICK
        assert ScanMode.QUICK != ScanMode.COMPREHENSIVE
        assert ScanMode.QUICK != ScanMode.DEEP

        # String comparison should work (str enum)
        assert ScanMode.QUICK == "quick"
        assert ScanMode.COMPREHENSIVE == "comprehensive"
        assert ScanMode.DEEP == "deep"

    def test_mode_in_list(self):
        """Test checking if mode is in list"""
        modes = [ScanMode.QUICK, ScanMode.COMPREHENSIVE]
        assert ScanMode.QUICK in modes
        assert ScanMode.COMPREHENSIVE in modes
        assert ScanMode.DEEP not in modes

    def test_mode_json_serialization(self):
        """Test ScanMode can be JSON serialized as string"""
        import json

        # Should serialize as string value
        assert json.dumps({"mode": ScanMode.QUICK}) == '{"mode": "quick"}'
        assert (
            json.dumps({"mode": ScanMode.COMPREHENSIVE}) == '{"mode": "comprehensive"}'
        )
        assert json.dumps({"mode": ScanMode.DEEP}) == '{"mode": "deep"}'

    def test_mode_pydantic_compatibility(self):
        """Test ScanMode works with Pydantic models"""
        from pydantic import BaseModel

        class ScanRequest(BaseModel):
            mode: ScanMode

        # Should accept ScanMode enum
        request1 = ScanRequest(mode=ScanMode.QUICK)
        assert request1.mode == ScanMode.QUICK

        # Should accept string value
        request2 = ScanRequest(mode="comprehensive")
        assert request2.mode == ScanMode.COMPREHENSIVE

        # Should reject invalid values
        with pytest.raises(ValueError):
            ScanRequest(mode="invalid")

    def test_mode_default_value(self):
        """Test that QUICK is appropriate default for performance"""
        from pydantic import BaseModel

        class ScanRequest(BaseModel):
            mode: ScanMode = ScanMode.QUICK

        request = ScanRequest()
        assert request.mode == ScanMode.QUICK

        # Quick mode should be the fastest
        quick_min, _ = ScanMode.QUICK.estimated_duration_range
        comp_min, _ = ScanMode.COMPREHENSIVE.estimated_duration_range
        deep_min, _ = ScanMode.DEEP.estimated_duration_range

        assert quick_min < comp_min
        assert quick_min < deep_min

    def test_mode_display_format(self):
        """Test modes have user-friendly display format"""
        # Descriptions should be clear and helpful
        for mode in ScanMode:
            desc = mode.description
            assert len(desc) > 10  # Should have substantial description
            assert desc[0].isupper()  # Should start with capital letter
            assert any(
                word in desc.lower() for word in ["scan", "coverage", "confidence"]
            )

    def test_mode_engine_list_immutable(self):
        """Test that engines list is a new list each time (not shared reference)"""
        engines1 = ScanMode.QUICK.engines
        engines2 = ScanMode.QUICK.engines

        # Should be equal but not the same object
        assert engines1 == engines2
        # Modifying one shouldn't affect the other
        engines1.append("test")
        assert len(engines2) == 1  # Should still be original length

    def test_mode_coverage_claims(self):
        """Test that coverage claims are documented in descriptions"""
        # Quick should mention ~90% coverage
        assert "90%" in ScanMode.QUICK.description

        # Comprehensive should mention 95%+ coverage
        assert "95%" in ScanMode.COMPREHENSIVE.description

        # Deep should mention maximum confidence
        assert "maximum" in ScanMode.DEEP.description.lower()

    def test_mode_use_cases(self):
        """Test that mode descriptions suggest appropriate use cases"""
        quick_desc = ScanMode.QUICK.description.lower()
        comp_desc = ScanMode.COMPREHENSIVE.description.lower()
        deep_desc = ScanMode.DEEP.description.lower()

        # Quick should emphasize speed
        assert "fast" in quick_desc or "quick" in quick_desc

        # Comprehensive should emphasize thoroughness
        assert "slower" in comp_desc or "coverage" in comp_desc

        # Deep should emphasize confidence
        assert "maximum" in deep_desc or "confidence" in deep_desc

    def test_all_modes_have_required_properties(self):
        """Test that all modes implement required properties"""
        for mode in ScanMode:
            # Should have description
            assert hasattr(mode, "description")
            assert isinstance(mode.description, str)
            assert len(mode.description) > 0

            # Should have engines
            assert hasattr(mode, "engines")
            assert isinstance(mode.engines, list)
            assert len(mode.engines) > 0

            # Should have estimated duration
            assert hasattr(mode, "estimated_duration_range")
            duration = mode.estimated_duration_range
            assert isinstance(duration, tuple)
            assert len(duration) == 2
            assert duration[0] < duration[1]
