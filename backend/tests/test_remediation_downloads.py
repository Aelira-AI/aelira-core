"""
Integration tests for remediation downloads with multiple output formats.

Tests cover:
- LaTeX multi-format output (tex, pdf, html)
- Multimedia ZIP packaging
- Format listing endpoint
- Download endpoint format selection
"""

import pytest
from unittest.mock import patch

from src.education.remediation.base import RemediationConfig, OutputFormat
from src.education.remediation.latex_converter import (
    LaTeXConverter,
    get_latex_converter,
)


class TestOutputFormatEnum:
    """Test OutputFormat enum values."""

    def test_latex_formats(self):
        """Test LaTeX format options exist."""
        assert OutputFormat.TEX.value == "tex"
        assert OutputFormat.PDF.value == "pdf"
        assert OutputFormat.HTML.value == "html"

    def test_multimedia_formats(self):
        """Test multimedia format options exist."""
        assert OutputFormat.INDIVIDUAL.value == "individual"
        assert OutputFormat.ZIP.value == "zip"

    def test_original_format(self):
        """Test ORIGINAL format option exists."""
        assert OutputFormat.ORIGINAL.value == "original"


class TestRemediationConfig:
    """Test RemediationConfig with new format options."""

    def test_default_latex_formats(self):
        """Test default LaTeX output is TEX only."""
        config = RemediationConfig()
        assert OutputFormat.TEX in config.latex_output_formats
        assert len(config.latex_output_formats) == 1

    def test_custom_latex_formats(self):
        """Test setting multiple LaTeX formats."""
        config = RemediationConfig(
            latex_output_formats=[OutputFormat.TEX, OutputFormat.PDF, OutputFormat.HTML]
        )
        assert len(config.latex_output_formats) == 3
        assert OutputFormat.PDF in config.latex_output_formats

    def test_default_multimedia_format(self):
        """Test default multimedia output is individual files."""
        config = RemediationConfig()
        assert config.multimedia_output_format == OutputFormat.INDIVIDUAL

    def test_zip_multimedia_format(self):
        """Test ZIP multimedia option."""
        config = RemediationConfig(multimedia_output_format=OutputFormat.ZIP)
        assert config.multimedia_output_format == OutputFormat.ZIP

    def test_include_original_default(self):
        """Test include_original_in_zip default is True."""
        config = RemediationConfig()
        assert config.include_original_in_zip is True

    def test_include_original_false(self):
        """Test setting include_original_in_zip to False."""
        config = RemediationConfig(include_original_in_zip=False)
        assert config.include_original_in_zip is False


class TestLaTeXConverter:
    """Test LaTeX converter functionality."""

    def test_converter_singleton(self):
        """Test converter singleton returns same instance."""
        # Reset singleton for test
        import src.education.remediation.latex_converter as lc

        lc._converter = None

        conv1 = get_latex_converter()
        conv2 = get_latex_converter()
        assert conv1 is conv2

    def test_check_pandoc_available(self):
        """Test pandoc availability check."""
        converter = LaTeXConverter()
        assert isinstance(converter.pandoc_available, bool)

    def test_check_pdflatex_available(self):
        """Test pdflatex availability check."""
        converter = LaTeXConverter()
        assert isinstance(converter.pdflatex_available, bool)

    def test_convert_all_formats_tex_only(self):
        """Test convert_all_formats with tex only returns source path."""
        converter = LaTeXConverter()
        results = converter.convert_all_formats("/path/to/file.tex", ["tex"])
        assert results["tex"] == "/path/to/file.tex"

    def test_convert_all_formats_unknown_format(self):
        """Test unknown format returns None."""
        converter = LaTeXConverter()
        results = converter.convert_all_formats("/path/to/file.tex", ["unknown"])
        assert results["unknown"] is None


class TestMultimediaRemediatorZip:
    """Test multimedia ZIP packaging."""

    def test_generate_readme_content(self):
        """Test README generation for ZIP package."""
        from src.education.remediation.multimedia_remediator import MultimediaRemediator

        # Create mock remediator with minimal setup
        with patch.object(MultimediaRemediator, "__init__", return_value=None):
            remediator = MultimediaRemediator.__new__(MultimediaRemediator)
            remediator.file_path = "/test/video.mp4"
            remediator.file_ext = ".mp4"
            remediator._caption_file = "/test/video.vtt"
            remediator._transcript_file = None
            remediator._audio_description_file = None
            remediator._modifications = []
            remediator.config = RemediationConfig()

            readme = remediator._generate_readme()

            assert "ACCESSIBLE MEDIA PACKAGE" in readme
            assert "video.mp4" in readme
            assert "Aelira" in readme
            assert "video.vtt" in readme


class TestRemediationOptionsAPI:
    """Test remediation options in API."""

    def test_remediation_options_model(self):
        """Test RemediationOptions Pydantic model."""
        from src.api.education_routes import RemediationOptions

        opts = RemediationOptions()
        assert opts.use_ai is True
        assert opts.latex_formats == ["tex", "pdf", "html"]
        assert opts.multimedia_format == "individual"

    def test_latex_multiple_formats(self):
        """Test specifying multiple LaTeX formats."""
        from src.api.education_routes import RemediationOptions

        opts = RemediationOptions(latex_formats=["tex", "pdf", "html"])
        assert "pdf" in opts.latex_formats
        assert "html" in opts.latex_formats
        assert len(opts.latex_formats) == 3

    def test_multimedia_zip_option(self):
        """Test multimedia ZIP option."""
        from src.api.education_routes import RemediationOptions

        opts = RemediationOptions(
            multimedia_format="zip", include_original_in_zip=False
        )
        assert opts.multimedia_format == "zip"
        assert opts.include_original_in_zip is False

    def test_use_ai_option(self):
        """Test use_ai option."""
        from src.api.education_routes import RemediationOptions

        opts = RemediationOptions(use_ai=False)
        assert opts.use_ai is False


class TestOutputFormatConversion:
    """Test OutputFormat enum conversion in config."""

    def test_output_format_from_string(self):
        """Test creating OutputFormat from string."""
        fmt = OutputFormat("tex")
        assert fmt == OutputFormat.TEX

    def test_output_format_value(self):
        """Test OutputFormat value attribute."""
        assert OutputFormat.PDF.value == "pdf"
        assert OutputFormat.ZIP.value == "zip"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
