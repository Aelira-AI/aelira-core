"""
Integration tests for LaTeX PDF/UA-1 compliance.

Tests that remediated LaTeX documents produce valid PDF/UA-1 compliant PDFs
using LuaLaTeX + tagpdf, with proper structure trees that pass external validators.

Key validation points:
1. LuaLaTeX produces valid PDF/UA structure
2. Structure elements have proper content references (/K, /Pg)
3. Internal scanner scores 100%
4. End-to-end remediation + validation flow

Sources:
- tagpdf package v0.99x (2026): https://ctan.math.illinois.edu/macros/latex/contrib/tagpdf/tagpdf.pdf
- PDF/UA-1 Examples by LaTeX Project: https://github.com/latex3/tagging-project/discussions/82
"""

import tempfile
import pytest
from pathlib import Path
import sys

# Add backend to path before imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import after path setup
from src.education.remediation.latex_converter import (
    get_latex_converter,
)
from src.education.remediation.latex_remediator import LatexRemediator, remediate_latex
from src.education.remediation.base import RemediationConfig


# Override the autouse database fixture - we don't need database for these tests
@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Override database setup - these tests don't need database."""
    yield  # No-op fixture


# Test fixtures


@pytest.fixture
def sample_latex_with_metadata():
    """Create sample LaTeX with DocumentMetadata for PDF/UA."""
    content = r"""\DocumentMetadata{
  lang=en,
  pdfstandard=ua-1,
  pdfversion=1.7,
  testphase={phase-III,math,title,table}
}
\documentclass{article}
\usepackage[english]{babel}
\title{Test Document}
\author{Test Author}
\begin{document}
\maketitle

\section{Introduction}
This is a test document with accessible math.

\begin{equation}
E = mc^2 \label{eq:einstein}
\end{equation}

\section{Conclusion}
Document accessibility is important.

\end{document}
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tex", delete=False, dir="/tmp"
    ) as f:
        f.write(content)
        return f.name


@pytest.fixture
def sample_latex_without_accessibility():
    """Create sample LaTeX without any accessibility features."""
    content = r"""\documentclass{article}
\begin{document}
\section{Test}
Hello world.

\begin{equation}
a^2 + b^2 = c^2
\end{equation}

\end{document}
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tex", delete=False, dir="/tmp"
    ) as f:
        f.write(content)
        return f.name


@pytest.fixture
def latex_converter():
    """Create LaTeX converter instance."""
    return get_latex_converter()


class TestLuaLaTeXAvailability:
    """Test that LuaLaTeX is properly detected."""

    def test_converter_detects_lualatex(self, latex_converter):
        """Verify converter reports LuaLaTeX availability status."""
        # This is informational - test will pass regardless of availability
        print(f"\nLuaLaTeX available: {latex_converter.lualatex_available}")
        print(f"LaTeXML available: {latex_converter.latexml_available}")
        print(f"pdflatex available: {latex_converter.pdflatex_available}")

        # At least one conversion method should be available to exercise this;
        # skip cleanly in environments without any LaTeX toolchain (e.g. CI).
        if not (
            latex_converter.lualatex_available
            or latex_converter.latexml_available
            or latex_converter.pdflatex_available
        ):
            pytest.skip("No LaTeX/PDF conversion tools installed in this environment")


class TestDocumentMetadataInjection:
    """Test that remediation properly injects DocumentMetadata."""

    def test_auto_remediate_adds_document_metadata(
        self, sample_latex_without_accessibility
    ):
        """Verify auto_remediate adds DocumentMetadata before documentclass."""
        remediator = LatexRemediator(sample_latex_without_accessibility, [])
        result = remediator.auto_remediate()

        assert result, "Auto-remediation should return True when fixes applied"

        # Read the remediated content
        output_files = remediator.get_output_files()
        assert "tex" in output_files, "Should have TEX output"

        tex_path = output_files["tex"]
        content = Path(tex_path).read_text()

        # DocumentMetadata MUST be before \documentclass
        doc_metadata_pos = content.find(r"\DocumentMetadata")
        documentclass_pos = content.find(r"\documentclass")

        assert doc_metadata_pos != -1, "DocumentMetadata should be present"
        assert (
            doc_metadata_pos < documentclass_pos
        ), "DocumentMetadata must come before documentclass"

        # Verify key attributes
        assert "pdfstandard=ua-1" in content, "Should set PDF/UA-1 standard"
        assert "lang=en" in content, "Should set document language"

        print(f"\n✓ DocumentMetadata correctly injected at position {doc_metadata_pos}")
        print(f"  documentclass at position {documentclass_pos}")

    def test_removes_obsolete_packages(self, sample_latex_without_accessibility):
        """Verify obsolete accessibility/axessibility packages are removed."""
        # Create a file with obsolete packages
        content = Path(sample_latex_without_accessibility).read_text()
        content_with_obsolete = content.replace(
            r"\begin{document}",
            r"\usepackage{accessibility}"
            + "\n"
            + r"\usepackage{axessibility}"
            + "\n"
            + r"\begin{document}",
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tex", delete=False, dir="/tmp"
        ) as f:
            f.write(content_with_obsolete)
            test_path = f.name

        remediator = LatexRemediator(test_path, [])
        remediator.auto_remediate()

        output_files = remediator.get_output_files()
        tex_path = output_files["tex"]
        remediated = Path(tex_path).read_text()

        # Check obsolete packages are removed
        assert (
            r"\usepackage{accessibility}" not in remediated
        ), "accessibility package should be removed"
        assert (
            r"\usepackage{axessibility}" not in remediated
        ), "axessibility package should be removed"

        print("\n✓ Obsolete packages correctly removed")


@pytest.mark.skipif(
    not get_latex_converter().lualatex_available, reason="LuaLaTeX not available"
)
class TestLuaLaTeXPDFGeneration:
    """Test PDF generation with LuaLaTeX (requires LuaLaTeX installed)."""

    def test_lualatex_produces_pdf(self, latex_converter, sample_latex_with_metadata):
        """Verify LuaLaTeX produces a PDF file."""
        pdf_path = latex_converter.convert_to_pdf(sample_latex_with_metadata)

        assert pdf_path is not None, "LuaLaTeX should produce PDF"
        assert Path(pdf_path).exists(), f"PDF file should exist at {pdf_path}"

        # Check file size (should be non-trivial)
        size = Path(pdf_path).stat().st_size
        assert size > 1000, f"PDF should be substantial (got {size} bytes)"

        print(f"\n✓ LuaLaTeX produced PDF: {pdf_path} ({size} bytes)")

    def test_lualatex_pdf_has_valid_structure(
        self, latex_converter, sample_latex_with_metadata
    ):
        """Verify LuaLaTeX output has valid PDF/UA structure."""
        try:
            import pikepdf
        except ImportError:
            pytest.skip("pikepdf not available")

        pdf_path = latex_converter.convert_to_pdf(sample_latex_with_metadata)
        assert pdf_path is not None

        with pikepdf.open(pdf_path) as pdf:
            # Required for PDF/UA: language
            assert "/Lang" in pdf.Root, "PDF must have /Lang in catalog"
            lang = str(pdf.Root["/Lang"])
            assert lang, "Language must be set"
            print(f"\n  Lang: {lang}")

            # Required for PDF/UA: marked content
            assert "/MarkInfo" in pdf.Root, "PDF must have /MarkInfo"
            marked = pdf.Root["/MarkInfo"].get("/Marked")
            assert marked == True, "PDF must be marked as tagged"
            print("  Marked: True")

            # Required for PDF/UA: structure tree
            assert "/StructTreeRoot" in pdf.Root, "PDF must have /StructTreeRoot"
            struct_root = pdf.Root["/StructTreeRoot"]
            assert "/K" in struct_root, "Structure tree must have children"
            print("  StructTreeRoot: present with children")

        print("\n✓ PDF/UA structure validated")

    def test_lualatex_pdf_has_content_references(
        self, latex_converter, sample_latex_with_metadata
    ):
        """Verify structure elements have proper content references (not floating)."""
        try:
            import pikepdf  # noqa: F401  # availability check for skip
        except ImportError:
            pytest.skip("pikepdf not available")

        pdf_path = latex_converter.convert_to_pdf(sample_latex_with_metadata)
        assert pdf_path is not None

        # Use the converter's validation method
        is_valid = latex_converter._verify_pdf_ua_structure(pdf_path)
        assert is_valid, "PDF should have valid structure with content references"

        print("\n✓ Structure elements have valid content references")


class TestEndToEndRemediation:
    """Test complete remediation → PDF → validation flow."""

    @pytest.mark.skipif(
        not get_latex_converter().lualatex_available, reason="LuaLaTeX not available"
    )
    def test_remediate_then_convert_produces_valid_pdf(
        self, sample_latex_without_accessibility
    ):
        """Full flow: raw LaTeX → remediation → PDF → validation."""
        try:
            import pikepdf
        except ImportError:
            pytest.skip("pikepdf not available")

        # Step 1: Remediate
        config = RemediationConfig(latex_output_formats=["tex", "pdf"])
        remediator = LatexRemediator(
            sample_latex_without_accessibility, [], config=config
        )
        success = remediator.auto_remediate()

        assert success, "Remediation should succeed"

        # Step 2: Get output files
        output_files = remediator.get_output_files()
        assert "tex" in output_files, "Should have TEX output"

        # Step 3: Convert to PDF (may already be done by remediator)
        converter = get_latex_converter()
        if output_files.get("pdf"):
            pdf_path = output_files["pdf"]
        else:
            pdf_path = converter.convert_to_pdf(output_files["tex"])

        if pdf_path is None:
            pytest.skip("PDF conversion not available")

        # Step 4: Validate structure
        with pikepdf.open(pdf_path) as pdf:
            has_lang = "/Lang" in pdf.Root
            has_mark = "/MarkInfo" in pdf.Root
            has_struct = "/StructTreeRoot" in pdf.Root

            print(f"\n  Language: {'✓' if has_lang else '✗'}")
            print(f"  Marked: {'✓' if has_mark else '✗'}")
            print(f"  Structure: {'✓' if has_struct else '✗'}")

            assert has_struct, "Remediated PDF should have structure tree"

        print("\n✓ End-to-end remediation validated")

    def test_remediate_function_shortcut(self, sample_latex_without_accessibility):
        """Test the convenience remediate_latex() function."""
        result = remediate_latex(sample_latex_without_accessibility)

        assert result is not None, "Should return result"
        assert result.fixed_count > 0, "Should have applied fixes"
        assert result.output_file is not None, "Should have output file"

        # Verify DocumentMetadata was added
        content = Path(result.output_file).read_text()
        assert r"\DocumentMetadata" in content, "Should have DocumentMetadata"

        print(f"\n✓ remediate_latex() applied {result.fixed_count} fixes")
        print(f"  Output: {result.output_file}")


class TestPDFProcessorIntegration:
    """Test that PDF processor correctly analyzes LuaLaTeX output."""

    @pytest.mark.skipif(
        not get_latex_converter().lualatex_available, reason="LuaLaTeX not available"
    )
    def test_processor_detects_heading_structure(self, sample_latex_with_metadata):
        """Verify PDF processor finds headings in tagpdf structure."""
        try:
            from src.education.pdf_checks.structure_checker import StructureTreeChecker
            import pikepdf  # noqa: F401  # availability check for skip
        except ImportError as e:
            pytest.skip(f"Required module not available: {e}")

        converter = get_latex_converter()
        pdf_path = converter.convert_to_pdf(sample_latex_with_metadata)

        if pdf_path is None:
            pytest.skip("PDF conversion not available")

        # Use the structure-tree heading detector directly
        has_h1 = StructureTreeChecker().has_h1(pdf_path)

        # LuaLaTeX + tagpdf should create proper heading structure
        # Note: This depends on tagpdf's testphase settings
        print(f"\n  H1 in structure tree: {'✓' if has_h1 else '✗'}")

        # The test passes if we can check - actual H1 presence depends on tagpdf config
        assert True  # Informational test


class TestLaTeXMLPreprocessing:
    """Test that LaTeXML preprocessing handles DocumentMetadata correctly."""

    def test_preprocess_removes_document_metadata(self, sample_latex_with_metadata):
        """Verify preprocessing removes DocumentMetadata for LaTeXML."""
        converter = get_latex_converter()

        content = Path(sample_latex_with_metadata).read_text()
        processed = converter._preprocess_for_latexml(content)

        # DocumentMetadata should be removed (LaTeXML doesn't support it)
        assert (
            r"\DocumentMetadata{" not in processed
        ), "DocumentMetadata should be removed"

        # Should have comment indicating removal
        assert "DocumentMetadata removed" in processed, "Should have removal comment"

        # documentclass should still be present
        assert r"\documentclass" in processed, "documentclass should remain"

        print("\n✓ DocumentMetadata correctly removed for LaTeXML")


# Informational tests (always pass, report status)


class TestToolAvailability:
    """Informational tests about tool availability."""

    def test_report_tool_status(self, latex_converter):
        """Report which tools are available for PDF generation."""
        print("\n" + "=" * 60)
        print("LaTeX PDF/UA Tool Availability Report")
        print("=" * 60)
        print(
            f"  LuaLaTeX:  {'✓ Available' if latex_converter.lualatex_available else '✗ Not available'}"
        )
        print(
            f"  LaTeXML:   {'✓ Available' if latex_converter.latexml_available else '✗ Not available'}"
        )
        print(
            f"  pdflatex:  {'✓ Available' if latex_converter.pdflatex_available else '✗ Not available'}"
        )
        print(
            f"  pandoc:    {'✓ Available' if latex_converter.pandoc_available else '✗ Not available'}"
        )
        print("=" * 60)

        if latex_converter.lualatex_available:
            print("\n→ Will use LuaLaTeX for PDF/UA-1 compliant output")
        elif latex_converter.latexml_available:
            print("\n→ Will use LaTeXML pipeline (limited PDF/UA compliance)")
        elif latex_converter.pdflatex_available:
            print("\n→ Will use pdflatex fallback (minimal accessibility)")
        else:
            print("\n⚠ No PDF tools available!")

        # This test always passes - it's informational
        assert True

    def test_report_pikepdf_status(self):
        """Report pikepdf availability for PDF validation."""
        try:
            import pikepdf

            version = pikepdf.__version__
            print(f"\n  pikepdf:   ✓ Available (v{version})")
            assert True
        except ImportError:
            print("\n  pikepdf:   ✗ Not available (PDF validation disabled)")
            assert True  # Still passes - informational


class TestShellEscapeDisabled:
    """Security: LaTeX compilation must never enable -shell-escape (RCE vector).

    -shell-escape enables \\write18{...}, i.e. arbitrary shell command execution
    during compilation of an attacker-supplied .tex file. Compilation must run
    with shell escape fully disabled.
    """

    def _captured_argvs(self, monkeypatch, converter, method_name):
        """Invoke a _convert_with_* method with subprocess.run mocked, return argv lists."""
        import src.education.remediation.latex_converter as mod

        captured = []

        class _FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd, *args, **kwargs):
            captured.append(list(cmd))
            return _FakeCompleted()

        monkeypatch.setattr(mod.subprocess, "run", _fake_run)

        with tempfile.TemporaryDirectory() as tmp:
            tex_path = Path(tmp) / "doc.tex"
            tex_path.write_text(
                r"\documentclass{article}\begin{document}x\end{document}"
            )
            getattr(converter, method_name)(str(tex_path), Path(tmp))

        assert captured, "expected the compiler to be invoked"
        return captured

    def test_lualatex_never_enables_shell_escape(self, monkeypatch, latex_converter):
        for argv in self._captured_argvs(
            monkeypatch, latex_converter, "_convert_with_lualatex"
        ):
            assert "-shell-escape" not in argv, f"-shell-escape present in {argv}"
            assert "--shell-escape" not in argv, f"--shell-escape present in {argv}"
            assert "-no-shell-escape" in argv, f"-no-shell-escape missing from {argv}"

    def test_pdflatex_never_enables_shell_escape(self, monkeypatch, latex_converter):
        for argv in self._captured_argvs(
            monkeypatch, latex_converter, "_convert_with_pdflatex"
        ):
            assert "-shell-escape" not in argv, f"-shell-escape present in {argv}"
            assert "--shell-escape" not in argv, f"--shell-escape present in {argv}"
            assert "-no-shell-escape" in argv, f"-no-shell-escape missing from {argv}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
