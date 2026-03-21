"""
End-to-end tests for LaTeX processor.

Tests LaTeX equation parsing, MathML conversion, and accessibility
using comprehensive test fixtures covering 20+ equation types.
"""

import os

import pytest
from pathlib import Path

# Add backend to path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.education.latex_processor import LaTeXProcessor

# Skip all tests in this module unless RUN_E2E_TESTS is set
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_E2E_TESTS"),
    reason="E2E test requires running infrastructure (set RUN_E2E_TESTS=1 to enable)",
)

# Fixture paths
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "latex"
COMPREHENSIVE_EQUATIONS = FIXTURES_DIR / "equations_comprehensive.tex"
AMSMATH_EQUATIONS = FIXTURES_DIR / "equations_amsmath.tex"


@pytest.fixture
def latex_processor():
    """Create LaTeX processor instance."""
    return LaTeXProcessor()


@pytest.mark.asyncio
class TestLaTeXProcessingWorkflow:
    """Test complete LaTeX processing workflow."""

    async def test_comprehensive_equations_processing(self, latex_processor):
        """Test processing of comprehensive equation suite."""
        assert (
            COMPREHENSIVE_EQUATIONS.exists()
        ), f"Test fixture not found: {COMPREHENSIVE_EQUATIONS}"

        # Read LaTeX file
        latex_content = COMPREHENSIVE_EQUATIONS.read_text()

        # Process LaTeX
        result = await latex_processor.process_latex(latex_content)

        # Verify basic structure
        assert result is not None, "LaTeX processing returned None"
        assert "equations" in result
        assert "metadata" in result
        assert "compliance" in result

        # Should find multiple equations
        equations = result["equations"]
        assert (
            len(equations) >= 15
        ), f"Expected at least 15 equations, found {len(equations)}"

        print(f"\n📊 Found {len(equations)} equations in comprehensive suite")

    async def test_amsmath_environments_processing(self, latex_processor):
        """Test processing of AMS math environments."""
        assert (
            AMSMATH_EQUATIONS.exists()
        ), f"Test fixture not found: {AMSMATH_EQUATIONS}"

        latex_content = AMSMATH_EQUATIONS.read_text()
        result = await latex_processor.process_latex(latex_content)

        # Should detect align, gather, multline, split, cases environments
        equations = result["equations"]
        assert (
            len(equations) >= 5
        ), f"Expected at least 5 equation environments, found {len(equations)}"

        # Check for specific environments
        latex_envs = ["align", "gather", "multline", "split", "cases"]
        found_envs = set()

        for eq in equations:
            latex_source = eq.get("latex", "")
            for env in latex_envs:
                if f"\\begin{{{env}}}" in latex_source:
                    found_envs.add(env)

        print(f"\n📊 Found AMS math environments: {', '.join(found_envs)}")


@pytest.mark.asyncio
class TestLaTeXEquationDetection:
    """Test LaTeX equation detection and extraction."""

    async def test_inline_equation_detection(self, latex_processor):
        """Test detection of inline equations ($...$)."""
        latex = r"The quadratic formula $x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}$ is well-known."

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1, "Should detect inline equation"

        eq = equations[0]
        assert "latex" in eq
        assert "frac" in eq["latex"]  # Should contain the fraction

    async def test_display_equation_detection(self, latex_processor):
        """Test detection of display equations ($$...$$)."""
        latex = r"$$\int_{0}^{\infty} e^{-x} dx = 1$$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1, "Should detect display equation"

        eq = equations[0]
        assert "int" in eq["latex"]  # Should contain integral
        assert "infty" in eq["latex"]  # Should contain infinity

    async def test_equation_environment_detection(self, latex_processor):
        """Test detection of equation environments."""
        latex = r"""
\begin{equation}
E = mc^2
\end{equation}
"""

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1, "Should detect equation environment"

    async def test_multiple_equation_detection(self, latex_processor):
        """Test detection of multiple equations in one document."""
        latex_content = COMPREHENSIVE_EQUATIONS.read_text()
        result = await latex_processor.process_latex(latex_content)

        equations = result["equations"]

        # Should detect all major equation types
        # Comprehensive suite has 20 equations
        assert (
            len(equations) >= 15
        ), f"Should detect at least 15 equations, found {len(equations)}"


@pytest.mark.asyncio
class TestMathMLConversion:
    """Test LaTeX to MathML conversion."""

    async def test_simple_fraction_conversion(self, latex_processor):
        """Test conversion of simple fraction."""
        latex = r"$\frac{a}{b}$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1
        eq = equations[0]

        # Should have MathML output
        assert "mathml" in eq, "MathML conversion missing"
        mathml = eq["mathml"]

        # MathML should contain mfrac element
        assert (
            "<mfrac>" in mathml or "frac" in mathml.lower()
        ), "MathML should contain fraction element"

    async def test_square_root_conversion(self, latex_processor):
        """Test conversion of square root."""
        latex = r"$\sqrt{x^2 + y^2}$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1
        eq = equations[0]

        mathml = eq["mathml"]
        # MathML should contain sqrt element
        assert (
            "<msqrt>" in mathml or "sqrt" in mathml.lower()
        ), "MathML should contain square root element"

    async def test_integral_conversion(self, latex_processor):
        """Test conversion of integral."""
        latex = r"$$\int_{0}^{\infty} e^{-x} dx$$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1
        eq = equations[0]

        mathml = eq["mathml"]
        # MathML should contain integral symbol (Unicode, entity, or hex code)
        assert (
            "∫" in mathml
            or "&int;" in mathml
            or "<mo>∫</mo>" in mathml
            or "&#x0222B;" in mathml
            or "&#8747;" in mathml
        ), "MathML should contain integral symbol"

    async def test_summation_conversion(self, latex_processor):
        """Test conversion of summation."""
        latex = r"$$\sum_{i=1}^{n} i^2$$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1
        eq = equations[0]

        mathml = eq["mathml"]
        # MathML should contain summation symbol (Unicode, entity, or hex code)
        assert (
            "∑" in mathml
            or "&sum;" in mathml
            or "<mo>∑</mo>" in mathml
            or "&#x02211;" in mathml
            or "&#8721;" in mathml
        ), "MathML should contain summation symbol"

    async def test_matrix_conversion(self, latex_processor):
        """Test conversion of matrix."""
        latex = r"""$$\begin{bmatrix}
a & b \\
c & d
\end{bmatrix}$$"""

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1
        eq = equations[0]

        mathml = eq["mathml"]
        # MathML should contain table structure for matrix
        assert (
            "<mtable>" in mathml or "table" in mathml.lower()
        ), "MathML should contain matrix/table structure"

    async def test_greek_letters_conversion(self, latex_processor):
        """Test conversion of Greek letters."""
        latex = r"$\alpha, \beta, \gamma, \Delta, \Theta$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1
        eq = equations[0]

        mathml = eq["mathml"]
        # MathML should contain Greek letters
        # (Either as Unicode, entity references, or hex codes)
        has_greek = (
            any(c in mathml for c in ["α", "β", "γ", "Δ", "Θ"])
            or any(
                entity in mathml
                for entity in ["&alpha;", "&beta;", "&gamma;", "&Delta;", "&Theta;"]
            )
            or any(
                hex_code in mathml
                for hex_code in [
                    "&#x003B1;",
                    "&#x003B2;",
                    "&#x003B3;",
                    "&#x00394;",
                    "&#x00398;",
                    "&#945;",
                    "&#946;",
                    "&#947;",
                    "&#916;",
                    "&#920;",
                ]
            )
        )

        assert has_greek, "MathML should contain Greek letters"

    async def test_subscript_superscript_conversion(self, latex_processor):
        """Test conversion of subscripts and superscripts."""
        latex = r"$x_{i}^{2}$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1
        eq = equations[0]

        mathml = eq["mathml"]
        # MathML should have sub and sup elements (or combined msubsup)
        assert (
            "<msub>" in mathml or "<msup>" in mathml or "<msubsup>" in mathml
        ), "MathML should contain subscript/superscript elements"


@pytest.mark.asyncio
class TestLaTeXARIALabels:
    """Test ARIA label generation for equations."""

    async def test_aria_label_generation(self, latex_processor):
        """Test that ARIA labels are generated."""
        latex = r"$E = mc^2$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1
        eq = equations[0]

        # Should have ARIA label
        assert "aria_label" in eq, "ARIA label missing"
        aria = eq["aria_label"]

        assert len(aria) > 0, "ARIA label is empty"
        assert len(aria) >= 10, f"ARIA label too short: '{aria}'"

    async def test_aria_label_quality(self, latex_processor):
        """Test quality of generated ARIA labels."""
        latex = r"$\frac{a}{b}$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        eq = equations[0]
        aria = eq["aria_label"].lower()

        # ARIA label should describe the fraction
        # May contain words like "fraction", "divided", "over", etc.
        descriptive_words = ["fraction", "divided", "over", "a", "b"]
        has_description = any(word in aria for word in descriptive_words)

        assert has_description, f"ARIA label not descriptive enough: '{aria}'"

    async def test_complex_equation_aria(self, latex_processor):
        """Test ARIA label for complex equation."""
        latex = r"$$\int_{0}^{\infty} e^{-x^2} dx = \frac{\sqrt{\pi}}{2}$$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        eq = equations[0]
        aria = eq["aria_label"]

        # Should be longer for complex equation
        assert len(aria) >= 20, f"ARIA label for complex equation too short: '{aria}'"


@pytest.mark.asyncio
class TestLaTeXCompliance:
    """Test LaTeX accessibility compliance checking."""

    async def test_compliance_score_calculation(self, latex_processor):
        """Test compliance score calculation."""
        latex_content = COMPREHENSIVE_EQUATIONS.read_text()
        result = await latex_processor.process_latex(latex_content)

        compliance = result["compliance"]

        assert "score" in compliance
        assert "issues" in compliance
        assert 0 <= compliance["score"] <= 100

    async def test_missing_mathml_detection(self, latex_processor):
        """Test detection of equations without MathML."""
        # If conversion fails, should be flagged as issue
        latex = r"$x = y$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        for eq in equations:
            if not eq.get("mathml"):
                # Should be flagged in compliance issues
                compliance = result["compliance"]
                issues = compliance["issues"]
                assert any(
                    "mathml" in issue.get("description", "").lower() for issue in issues
                ), "Missing MathML should be flagged"

    async def test_missing_aria_detection(self, latex_processor):
        """Test detection of equations without ARIA labels."""
        latex = r"$E = mc^2$"

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        for eq in equations:
            if not eq.get("aria_label"):
                # Should be flagged in compliance issues
                compliance = result["compliance"]
                issues = compliance["issues"]
                assert any(
                    "aria" in issue.get("description", "").lower() for issue in issues
                ), "Missing ARIA label should be flagged"


@pytest.mark.asyncio
class TestLaTeXPackageSupport:
    """Test support for various LaTeX packages."""

    async def test_amsmath_align_environment(self, latex_processor):
        """Test support for amsmath align environment."""
        latex = r"""
\begin{align}
f(x) &= x^2 + 2x + 1 \\
     &= (x + 1)^2
\end{align}
"""

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1, "Should detect align environment"

    async def test_amsmath_cases_environment(self, latex_processor):
        """Test support for piecewise functions (cases)."""
        latex = r"""
\begin{equation}
f(x) = \begin{cases}
x^2 & \text{if } x \geq 0 \\
-x^2 & \text{if } x < 0
\end{cases}
\end{equation}
"""

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        assert len(equations) >= 1, "Should detect cases environment"

    async def test_matrices(self, latex_processor):
        """Test support for bmatrix, pmatrix, vmatrix."""
        latex = r"""
$$\begin{bmatrix} a & b \\ c & d \end{bmatrix}$$
$$\begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}$$
$$\begin{vmatrix} x & y \\ z & w \end{vmatrix}$$
"""

        result = await latex_processor.process_latex(latex)
        equations = result["equations"]

        # Should detect all three matrix types
        assert len(equations) >= 3, f"Should detect 3 matrices, found {len(equations)}"


@pytest.mark.asyncio
class TestLaTeXEdgeCases:
    """Test LaTeX processing edge cases."""

    async def test_empty_input(self, latex_processor):
        """Test handling of empty input."""
        result = await latex_processor.process_latex("")

        assert result is not None
        assert result["equations"] == []

    async def test_no_equations(self, latex_processor):
        """Test handling of LaTeX with no equations."""
        latex = r"""
\documentclass{article}
\begin{document}
This is plain text with no equations.
\end{document}
"""

        result = await latex_processor.process_latex(latex)

        assert result is not None
        assert len(result["equations"]) == 0

    async def test_malformed_equation(self, latex_processor):
        """Test handling of malformed equations."""
        # Missing closing brace
        latex = r"$\frac{a{b}$"

        # Should handle gracefully (may skip or report error)
        result = await latex_processor.process_latex(latex)

        assert result is not None
        # May have error in compliance issues

    async def test_nested_environments(self, latex_processor):
        """Test handling of nested math environments."""
        latex = r"""
\begin{equation}
\begin{split}
a &= b + c \\
  &= d + e
\end{split}
\end{equation}
"""

        result = await latex_processor.process_latex(latex)

        assert result is not None
        equations = result["equations"]
        assert len(equations) >= 1, "Should handle nested environments"

    async def test_unicode_in_latex(self, latex_processor):
        """Test handling of Unicode characters in LaTeX."""
        latex = r"$α + β = γ$"

        result = await latex_processor.process_latex(latex)

        assert result is not None
        # Should handle Unicode characters

    async def test_very_long_equation(self, latex_processor):
        """Test handling of very long equations."""
        # Create long polynomial
        terms = [f"x^{{{i}}}" for i in range(20, 0, -1)]
        latex = f"$${' + '.join(terms)} = 0$$"

        result = await latex_processor.process_latex(latex)

        assert result is not None
        equations = result["equations"]
        assert len(equations) >= 1, "Should handle long equations"


@pytest.mark.asyncio
class TestLaTeXPerformance:
    """Test LaTeX processing performance."""

    async def test_single_equation_speed(self, latex_processor):
        """Test processing speed for single equation."""
        import time

        latex = r"$x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}$"

        start_time = time.time()
        _ = await latex_processor.process_latex(latex)  # Run but don't need result
        elapsed = time.time() - start_time

        # Single equation should be fast
        assert elapsed < 2, f"Single equation processing too slow: {elapsed:.2f}s"

        print(f"\n⚡ Single equation: {elapsed:.3f}s")

    async def test_comprehensive_suite_speed(self, latex_processor):
        """Test processing speed for comprehensive equation suite."""
        import time

        latex_content = COMPREHENSIVE_EQUATIONS.read_text()

        start_time = time.time()
        result = await latex_processor.process_latex(latex_content)
        elapsed = time.time() - start_time

        equations = result["equations"]

        # Should process 20 equations reasonably fast
        # Allow AI label generation time
        assert (
            elapsed < 60
        ), f"Comprehensive suite too slow: {elapsed:.2f}s for {len(equations)} equations"

        print(
            f"\n⚡ Comprehensive suite: {elapsed:.2f}s for {len(equations)} equations"
        )
        print(f"   Average: {elapsed/len(equations):.3f}s per equation")


@pytest.mark.asyncio
class TestLaTeXHTMLExport:
    """Test LaTeX to accessible HTML export."""

    async def test_html_export_with_mathml(self, latex_processor):
        """Test HTML export includes MathML."""
        latex = r"$E = mc^2$"

        result = await latex_processor.process_latex(latex)

        # Generate HTML export
        html = await latex_processor.export_to_html(result)

        assert html is not None
        assert len(html) > 0

        # Should contain MathML
        assert (
            "<math" in html or "mathml" in html.lower()
        ), "HTML export should contain MathML"

    async def test_html_export_with_aria(self, latex_processor):
        """Test HTML export includes ARIA labels."""
        latex = r"$\frac{a}{b}$"

        result = await latex_processor.process_latex(latex)
        html = await latex_processor.export_to_html(result)

        # Should have ARIA labels
        assert (
            "aria-label=" in html or "aria-describedby=" in html
        ), "HTML export should include ARIA labels"

    async def test_html_export_structure(self, latex_processor):
        """Test HTML export has proper document structure."""
        latex_content = COMPREHENSIVE_EQUATIONS.read_text()
        result = await latex_processor.process_latex(latex_content)
        html = await latex_processor.export_to_html(result)

        # Should have HTML structure
        assert "<!DOCTYPE html>" in html or "<html" in html
        assert "<head>" in html
        assert "<body>" in html

        # Should have language attribute
        assert "lang=" in html


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
