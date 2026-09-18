"""LaTeX exports with explicit, fail-closed PDF machine validation.

A generated file is not an accessibility or PDF/UA conformance certificate.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .latex_pdf_validation import (
    LatexPDFValidation,
    inspect_pdf_structure,
    validate_pdf_candidate,
)

logger = logging.getLogger(__name__)


class LaTeXConverter:
    """Convert LaTeX files to PDF and HTML candidates."""

    # Allowed base directories for file operations (configurable via env)
    ALLOWED_DIRS = [
        "/tmp",
        "/var/tmp",
        "/app",  # Docker container working directory
        "/app/data",
        os.environ.get("AELIRA_UPLOAD_DIR", "/app/uploads"),
        os.environ.get("AELIRA_REMEDIATION_DIR", "/app/remediated"),
    ]

    # Accessible HTML template with MathML support
    HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            max-width: 800px;
            margin: 2rem auto;
            padding: 1rem;
            line-height: 1.6;
            color: #333;
        }}
        h1, h2, h3 {{ color: #1a1a2e; }}
        math {{ font-size: 1.1em; }}
        .equation {{
            display: block;
            margin: 1.5rem 0;
            text-align: center;
        }}
        figure {{
            margin: 1.5rem 0;
            text-align: center;
        }}
        figcaption {{
            font-style: italic;
            margin-top: 0.5rem;
        }}
        table {{
            border-collapse: collapse;
            margin: 1rem auto;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 0.5rem;
        }}
        th {{
            background: #f5f5f5;
        }}
        @media print {{
            body {{ max-width: none; margin: 0; }}
        }}
    </style>
</head>
<body>
{content}
</body>
</html>
"""

    def __init__(self):
        """Initialize converter and check for required tools."""
        self.latexml_available = self._check_command("latexml")
        self.latexmlpost_available = self._check_command("latexmlpost")
        self.pandoc_available = self._check_command("pandoc")
        self.pdflatex_available = self._check_command("pdflatex")
        self.lualatex_available = self._check_command("lualatex")
        self._playwright_browser = None

        if self.lualatex_available:
            logger.info("LuaLaTeX available - PDF candidates require validation")
        elif self.latexml_available:
            logger.info(
                "LaTeXML available - using MathML pipeline (PDF less accessible)"
            )
        else:
            logger.warning(
                "LuaLaTeX/LaTeXML not available - falling back to pandoc/pdflatex"
            )

    def _check_command(self, cmd: str) -> bool:
        """Check if a command is available in PATH."""
        return shutil.which(cmd) is not None

    def _validate_path(self, path: Path) -> Path:
        """
        Validate path is safe and within allowed directories.

        Prevents path traversal attacks by ensuring:
        1. Path is resolved (no .. or symlinks)
        2. Path is within an allowed base directory

        Args:
            path: Path to validate

        Returns:
            Resolved, validated path

        Raises:
            ValueError: If path is outside allowed directories
        """
        resolved = path.resolve()

        # Check if path is within any allowed directory
        for allowed_dir in self.ALLOWED_DIRS:
            if allowed_dir and str(resolved).startswith(
                str(Path(allowed_dir).resolve())
            ):
                return resolved

        raise ValueError(
            f"Path '{path}' is outside allowed directories. "
            f"Allowed: {[d for d in self.ALLOWED_DIRS if d]}"
        )

    def _preprocess_for_latexml(self, tex_content: str) -> str:
        r"""
        Preprocess LaTeX content for LaTeXML compatibility.

        LaTeXML doesn't support some packages (hyperref, accessibility, etc.)
        and doesn't support \DocumentMetadata (only used by LuaLaTeX + tagpdf).
        This creates a simplified version that can be converted to HTML/MathML.
        """
        processed = tex_content

        # Remove \DocumentMetadata (not supported by LaTeXML)
        # This is only used for LuaLaTeX PDF/UA output
        # Handle multi-line DocumentMetadata with nested braces
        def remove_document_metadata(content: str) -> str:
            while r"\DocumentMetadata{" in content:
                start = content.find(r"\DocumentMetadata{")
                if start == -1:
                    break
                # Find matching closing brace
                brace_count = 0
                end = start + len(r"\DocumentMetadata")
                for i, char in enumerate(content[end:], end):
                    if char == "{":
                        brace_count += 1
                    elif char == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            # Found the matching close brace
                            # Also remove trailing newlines
                            end_pos = i + 1
                            while end_pos < len(content) and content[end_pos] in "\n\r":
                                end_pos += 1
                            content = (
                                content[:start]
                                + "% DocumentMetadata removed for LaTeXML processing\n"
                                + content[end_pos:]
                            )
                            break
                else:
                    # Couldn't find matching brace, break to avoid infinite loop
                    break
            return content

        processed = remove_document_metadata(processed)

        # Remove packages unsupported by LaTeXML
        unsupported_packages = [
            r"\\usepackage(\[[^\]]*\])?\{hyperref\}[^\n]*\n?",
            r"\\usepackage(\[[^\]]*\])?\{accessibility\}[^\n]*\n?",
            r"\\usepackage(\[[^\]]*\])?\{axessibility\}[^\n]*\n?",
            r"\\usepackage(\[[^\]]*\])?\{tagpdf\}[^\n]*\n?",
            r"\\usepackage(\[[^\]]*\])?\{pdfcomment\}[^\n]*\n?",
        ]

        for pattern in unsupported_packages:
            processed = re.sub(pattern, "", processed)

        # Remove hypersetup commands (handles nested braces like {pdflang={english}})
        # Match \hypersetup{ then find balanced closing brace
        def remove_hypersetup(content: str) -> str:
            result = content
            while r"\hypersetup{" in result:
                start = result.find(r"\hypersetup{")
                if start == -1:
                    break
                # Find matching closing brace
                brace_count = 0
                end = start + len(r"\hypersetup")
                for i, char in enumerate(result[end:], end):
                    if char == "{":
                        brace_count += 1
                    elif char == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            # Found the matching close brace
                            # Also remove trailing newline if present
                            if i + 1 < len(result) and result[i + 1] == "\n":
                                result = result[:start] + result[i + 2 :]
                            else:
                                result = result[:start] + result[i + 1 :]
                            break
                else:
                    # Couldn't find matching brace, remove line as fallback
                    line_end = result.find("\n", start)
                    if line_end > 0:
                        result = result[:start] + result[line_end + 1 :]
                    else:
                        break
            return result

        processed = remove_hypersetup(processed)

        # Remove \pdftooltip commands (from pdfcomment) - handles nested braces
        # Simplified: just remove the whole command and keep first argument
        processed = re.sub(
            r"\\pdftooltip\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}",
            r"\1",
            processed,
        )

        # Remove babel entirely (LaTeXML has compatibility issues)
        # We add lang="en" to HTML output anyway
        processed = re.sub(
            r"\\usepackage(\[[^\]]*\])?\{babel\}[^\n]*\n?", "", processed
        )

        logger.info("Preprocessed LaTeX for LaTeXML compatibility")
        return processed

    def _convert_with_latexml(self, tex_path: str, output_dir: Path) -> Optional[str]:
        """
        Convert LaTeX to accessible HTML using LaTeXML.

        LaTeXML properly converts math to MathML with semantic markup.

        Args:
            tex_path: Path to .tex file
            output_dir: Output directory

        Returns:
            Path to HTML file or None if failed
        """
        tex_file = Path(tex_path)
        xml_path = output_dir / (tex_file.stem + ".xml")
        html_path = output_dir / (tex_file.stem + ".html")

        try:
            # Preprocess the LaTeX for LaTeXML compatibility
            original_content = tex_file.read_text(encoding="utf-8")
            processed_content = self._preprocess_for_latexml(original_content)

            # Write preprocessed content to temp file
            processed_tex = output_dir / (tex_file.stem + "_latexml.tex")
            processed_tex.write_text(processed_content, encoding="utf-8")

            # Step 1: LaTeX → XML with MathML
            logger.info(f"Converting {tex_file.name} to XML with LaTeXML...")
            result = subprocess.run(
                [
                    "latexml",
                    "--dest=" + str(xml_path),
                    "--quiet",
                    str(processed_tex),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(output_dir),
            )

            # Clean up temp file
            if processed_tex.exists():
                processed_tex.unlink()

            if result.returncode != 0:
                logger.warning(f"LaTeXML warning: {result.stderr[:500]}")
                # LaTeXML often returns non-zero but still produces output
                if not xml_path.exists():
                    logger.error("LaTeXML failed to produce XML output")
                    return None

            # Step 2: XML → HTML5 with MathML
            # Note: LaTeXML 0.8.x automatically generates MathML with --format=html5
            logger.info("Converting XML to accessible HTML5...")
            result = subprocess.run(
                [
                    "latexmlpost",
                    "--dest=" + str(html_path),
                    "--format=html5",
                    str(xml_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(output_dir),
            )

            if result.returncode != 0:
                logger.warning(f"latexmlpost warning: {result.stderr[:500]}")

            # Clean up intermediate XML
            if xml_path.exists():
                xml_path.unlink()

            if html_path.exists():
                # Enhance HTML with accessibility features
                self._enhance_html_accessibility(html_path)
                logger.info(f"Generated accessible HTML: {html_path}")
                return str(html_path)

            return None

        except subprocess.TimeoutExpired:
            logger.error("LaTeXML conversion timed out")
            return None
        except Exception as e:
            logger.error(f"LaTeXML conversion failed: {e}")
            return None

    def _enhance_html_accessibility(self, html_path: Path):
        """Add additional accessibility enhancements to HTML."""
        try:
            content = html_path.read_text(encoding="utf-8")

            # Ensure lang attribute on html element
            if "<html>" in content:
                content = content.replace("<html>", '<html lang="en">')

            # Add skip link for keyboard navigation
            if "<body>" in content and "skip-link" not in content:
                skip_link = '<a href="#main-content" class="skip-link" style="position:absolute;left:-9999px;focus:position:static;">Skip to main content</a>'
                content = content.replace("<body>", f"<body>\n{skip_link}")

            # Wrap main content with landmark
            if "<main" not in content and "<article" not in content:
                # Find body content and wrap in main
                body_start = content.find("<body")
                body_end = content.find(">", body_start)
                close_body = content.find("</body>")
                if body_end > 0 and close_body > 0:
                    before = content[: body_end + 1]
                    main_content = content[body_end + 1 : close_body]
                    after = content[close_body:]
                    content = f'{before}\n<main id="main-content" role="main">\n{main_content}\n</main>\n{after}'

            html_path.write_text(content, encoding="utf-8")

        except Exception as e:
            logger.warning(f"Could not enhance HTML accessibility: {e}")

    def _extract_title_from_html(self, html_path: str) -> str:
        """Extract title from HTML file."""
        try:
            content = Path(html_path).read_text(encoding="utf-8")
            # Try <title> tag first
            match = re.search(r"<title>([^<]+)</title>", content)
            if match:
                return match.group(1).strip()
            # Try H1 tag
            match = re.search(r"<h1[^>]*>([^<]+)</h1>", content)
            if match:
                return match.group(1).strip()
            return "Untitled Document"
        except Exception:
            return "Untitled Document"

    def _set_pdf_metadata(self, pdf_path: str, title: str) -> bool:
        """Set PDF metadata (title, language) for accessibility."""
        try:
            import pikepdf

            with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
                # Set document info
                with pdf.open_metadata() as meta:
                    meta["dc:title"] = title
                    meta["dc:language"] = "en"
                    meta["pdf:Producer"] = "Aelira Accessibility Platform"

                # Set title in docinfo for broader compatibility
                pdf.docinfo["/Title"] = title

                # Set language in document catalog (Root) - required for PDF/UA
                # This is where screen readers look for the document language
                pdf.Root["/Lang"] = "en"

                # Enable marking for accessibility
                if "/MarkInfo" not in pdf.Root:
                    pdf.Root["/MarkInfo"] = pikepdf.Dictionary({"/Marked": True})
                else:
                    pdf.Root["/MarkInfo"]["/Marked"] = True

                pdf.save(pdf_path)

            logger.info(f"Set PDF metadata: title='{title}', lang='en' (in Root)")
            return True
        except Exception as e:
            logger.warning(f"Could not set PDF metadata: {e}")
            return False

    # Note: _enhance_pdf_structure() was REMOVED because it creates invalid PDF/UA.
    # Playwright's tagged PDF doesn't expose content stream markers needed to link
    # structure elements to actual content. Adding structure elements without /K
    # (content) and /Pg (page) references creates "floating" elements that fail
    # external validators like PAC3 and axesCheck.
    #
    # For valid PDF/UA-1 output, use LuaLaTeX + tagpdf via convert_to_pdf().

    async def _html_to_pdf_playwright(self, html_path: str, pdf_path: str) -> bool:
        """
        Convert HTML to PDF using Playwright (preserves accessibility).

        Playwright's PDF generation maintains document structure and
        MathML rendering better than traditional tools.
        """
        try:
            from playwright.async_api import async_playwright

            # Extract title from HTML for PDF metadata
            title = self._extract_title_from_html(html_path)

            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()

                # Load HTML file
                await page.goto(f"file://{html_path}", wait_until="networkidle")

                # Generate PDF with accessibility options
                await page.pdf(
                    path=pdf_path,
                    format="A4",
                    margin={
                        "top": "1in",
                        "bottom": "1in",
                        "left": "1in",
                        "right": "1in",
                    },
                    print_background=True,
                    tagged=True,  # Enable PDF tagging for accessibility
                )

                await browser.close()

            # Post-process: Set PDF metadata for accessibility
            self._set_pdf_metadata(pdf_path, title)

            # Note: We don't add H1 structure tags here because Playwright's
            # tagged PDF doesn't provide proper content references (/K, /Pg).
            # Adding floating structure elements creates invalid PDF/UA.
            # For full PDF/UA compliance, use the HTML output which has
            # proper semantic structure with MathML.

            logger.info(f"Generated accessible PDF: {pdf_path}")
            return True

        except Exception as e:
            logger.error(f"Playwright PDF generation failed: {e}")
            return False

    def _html_to_pdf_playwright_sync(self, html_path: str, pdf_path: str) -> bool:
        """Synchronous wrapper for Playwright PDF generation."""
        try:
            # Try to get existing event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're in an async context, use run_coroutine_threadsafe
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            asyncio.run,
                            self._html_to_pdf_playwright(html_path, pdf_path),
                        )
                        return future.result(timeout=120)
                else:
                    return loop.run_until_complete(
                        self._html_to_pdf_playwright(html_path, pdf_path)
                    )
            except RuntimeError:
                # No event loop, create one
                return asyncio.run(self._html_to_pdf_playwright(html_path, pdf_path))
        except Exception as e:
            logger.error(f"Sync Playwright wrapper failed: {e}")
            return False

    def convert_to_pdf(
        self, tex_path: str, output_dir: Optional[str] = None
    ) -> Optional[str]:
        """Return a PDF only when both machine validators pass on its bytes."""
        return self.convert_to_pdf_with_validation(tex_path, output_dir)[0]

    def convert_to_pdf_with_validation(
        self, tex_path: str, output_dir: Optional[str] = None
    ) -> tuple[Optional[str], LatexPDFValidation]:
        """All exporters share one gate; each attempt owns a fresh directory.

        Failed candidates may remain as diagnostics. They are never returned as
        output files. Receipts belong to this call, not the shared converter.
        """
        failure = LatexPDFValidation(status="failed", reason="conversion_failed")
        try:
            tex_file = self._validate_path(Path(tex_path))
            out_dir = self._validate_path(
                Path(output_dir) if output_dir else tex_file.parent
            )
            if not tex_file.is_file():
                return None, failure
            out_dir.mkdir(parents=True, exist_ok=True)
            available = False
            for exporter, enabled in (
                ("lualatex", self.lualatex_available),
                ("latexml", self.latexml_available),
                ("pdflatex", self.pdflatex_available),
            ):
                if not enabled:
                    continue
                available = True
                attempt = Path(tempfile.mkdtemp(prefix=f"pdf-{exporter}-", dir=out_dir))
                if exporter == "latexml":
                    html = self._convert_with_latexml(str(tex_file), attempt)
                    pdf = attempt / (tex_file.stem + ".pdf")
                    candidate = (
                        str(pdf)
                        if html and self._html_to_pdf_playwright_sync(html, str(pdf))
                        else None
                    )
                else:
                    candidate = getattr(self, f"_convert_with_{exporter}")(
                        str(tex_file), attempt
                    )
                if candidate:
                    receipt = validate_pdf_candidate(candidate)
                    # A failed validation is terminal, not an invitation to try
                    # another exporter whose output might hide the same defect.
                    return (candidate if receipt.accepted else None), receipt
            if not available:
                return None, LatexPDFValidation(
                    status="unavailable", reason="no_converter"
                )
        except (OSError, ValueError):
            logger.warning("PDF export could not prepare its input or output")
        return None, failure

    def _convert_with_pdflatex(self, tex_path: str, output_dir: Path) -> Optional[str]:
        """Fallback PDF conversion using pdflatex."""
        tex_file = Path(tex_path)
        pdf_path = output_dir / (tex_file.stem + ".pdf")

        try:
            # Run pdflatex twice for references
            for _ in range(2):
                result = subprocess.run(
                    [
                        "pdflatex",
                        "-interaction=nonstopmode",
                        "-no-shell-escape",  # SECURITY: never enable shell escape (RCE)
                        "-output-directory",
                        str(output_dir),
                        str(tex_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(tex_file.parent),
                )

                if result.returncode != 0:
                    logger.warning("pdflatex compilation failed")
                    return None

            if pdf_path.exists():
                logger.info(f"Generated PDF (fallback): {pdf_path}")
                return str(pdf_path)

            return None

        except Exception as e:
            logger.error(f"pdflatex conversion failed: {e}")
            return None

    def _convert_with_lualatex(self, tex_path: str, output_dir: Path) -> Optional[str]:
        """Generate a PDF candidate; the caller owns machine validation."""
        tex_file = Path(tex_path)
        pdf_path = output_dir / (tex_file.stem + ".pdf")

        try:
            # SECURITY: -no-shell-escape disables \write18 shell execution.
            # tex files here are attacker-supplied (user uploads); -shell-escape
            # would allow arbitrary command execution during compilation (RCE).
            # tagpdf's tagging works without shell escape.
            # Two passes for references and structure finalization
            for run in range(2):
                result = subprocess.run(
                    [
                        "lualatex",
                        "-interaction=nonstopmode",
                        "-no-shell-escape",  # SECURITY: never enable shell escape
                        f"-output-directory={output_dir}",
                        str(tex_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,  # LuaLaTeX can be slower than pdflatex
                    cwd=str(tex_file.parent),
                )

                if result.returncode != 0:
                    logger.warning("LuaLaTeX compilation failed on pass %s", run + 1)
                    return None

            if pdf_path.exists():
                return str(pdf_path)

            logger.error("LuaLaTeX did not produce PDF output")
            return None

        except subprocess.TimeoutExpired:
            logger.error("LuaLaTeX timed out")
            return None
        except Exception as e:
            logger.error(f"LuaLaTeX conversion failed: {e}")
            return None

    def _verify_pdf_ua_structure(self, pdf_path: str) -> bool:
        """Compatibility predicate for the bounded structural check only."""
        try:
            return inspect_pdf_structure(Path(pdf_path).read_bytes()) == "passed"
        except OSError:
            return False

    def convert_to_html(
        self, tex_path: str, output_dir: Optional[str] = None
    ) -> Optional[str]:
        """
        Convert LaTeX file to accessible HTML with MathML.

        Uses LaTeXML for proper MathML conversion with both
        presentation and content MathML for maximum accessibility.

        Args:
            tex_path: Path to .tex file
            output_dir: Output directory (defaults to same as input)

        Returns:
            Path to generated HTML or None if failed
        """
        tex_file = Path(tex_path)
        if not tex_file.exists():
            logger.error(f"LaTeX file not found: {tex_file}")
            return None

        # Validate paths
        try:
            tex_file = self._validate_path(tex_file)
        except ValueError as e:
            logger.error(f"Path validation failed: {e}")
            return None

        out_dir = Path(output_dir) if output_dir else tex_file.parent
        try:
            out_dir = self._validate_path(out_dir)
        except ValueError as e:
            logger.error(f"Output path validation failed: {e}")
            return None

        out_dir.mkdir(parents=True, exist_ok=True)

        # Primary: LaTeXML (best MathML support)
        if self.latexml_available:
            html_path = self._convert_with_latexml(tex_path, out_dir)
            if html_path:
                return html_path

        # Fallback: pandoc with MathML
        if self.pandoc_available:
            logger.warning("Using pandoc fallback for HTML")
            return self._convert_with_pandoc(tex_path, out_dir)

        logger.error("No HTML conversion tools available")
        return None

    def _convert_with_pandoc(self, tex_path: str, output_dir: Path) -> Optional[str]:
        """Fallback HTML conversion using pandoc."""
        tex_file = Path(tex_path)
        html_path = output_dir / (tex_file.stem + ".html")

        try:
            result = subprocess.run(
                [
                    "pandoc",
                    str(tex_file),
                    "-o",
                    str(html_path),
                    "--standalone",
                    "--mathml",
                    "--metadata",
                    "lang=en",
                    "--toc",
                    "--section-divs",
                ],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(tex_file.parent),
            )

            if result.returncode != 0:
                logger.error(f"pandoc error: {result.stderr}")
                return None

            if html_path.exists():
                self._enhance_html_accessibility(html_path)
                logger.info(f"Generated HTML (pandoc): {html_path}")
                return str(html_path)

            return None

        except Exception as e:
            logger.error(f"pandoc conversion failed: {e}")
            return None

    def convert_all_formats(
        self,
        tex_path: str,
        formats: list[str],
        output_dir: Optional[str] = None,
        *,
        validation_receipts: Optional[dict[str, LatexPDFValidation]] = None,
    ) -> dict[str, Optional[str]]:
        """
        Convert LaTeX to multiple accessible formats.

        Args:
            tex_path: Path to .tex file
            formats: List of formats ('tex', 'pdf', 'html')
            output_dir: Output directory

        Returns:
            Dict mapping format to output path (or None if failed)
        """
        results: dict[str, Optional[str]] = {}

        for fmt in formats:
            fmt_lower = fmt.lower()

            if fmt_lower == "tex":
                results["tex"] = tex_path
            elif fmt_lower == "pdf":
                results["pdf"], receipt = self.convert_to_pdf_with_validation(
                    tex_path, output_dir
                )
                if validation_receipts is not None:
                    validation_receipts["pdf"] = receipt
            elif fmt_lower == "html":
                results["html"] = self.convert_to_html(tex_path, output_dir)
            else:
                logger.warning(f"Unknown format: {fmt}")
                results[fmt_lower] = None

        return results


# Singleton instance
_converter: Optional[LaTeXConverter] = None


def get_latex_converter() -> LaTeXConverter:
    """Get or create the LaTeX converter singleton."""
    global _converter
    if _converter is None:
        _converter = LaTeXConverter()
    return _converter
