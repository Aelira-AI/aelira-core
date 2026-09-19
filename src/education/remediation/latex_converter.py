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
from types import SimpleNamespace
from ..latex_runtime import tex_environment, version_command
from ..latex_metadata import extract_metadata, save_html_metadata, save_pdf_metadata
from ..latex_semantics import extract_semantics, save_html_semantics

from ..latex_diagnostics import (
    ConversionDiagnostics,
    ConversionStage,
    classify,
    conversion_session,
    diagnostic,
    has_loss,
    inspect_candidate,
    inspect_source,
    record,
    sha,
)

from .latex_pdf_validation import (
    LatexPDFValidation,
    inspect_pdf_structure,
    validate_pdf_candidate,
)

logger = logging.getLogger(__name__)

# Import through LaTeXML's local XML catalog, independent of its install path.
# Override only generated branding; authored footer/navigation content is retained.
LATEXML_HTML_STYLESHEET = """<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
  <xsl:import href="urn:x-LaTeXML:XSLT:LaTeXML-html5.xsl"/>
  <xsl:template match="/" mode="footer-generator-identifier"/>
</xsl:stylesheet>
"""


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
<html>
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
            logger.info(
                "LuaLaTeX executable found; functional readiness requires the runtime probe"
            )
        elif self.latexml_available:
            logger.info(
                "LaTeXML executable found; functional readiness requires the runtime probe"
            )
        else:
            logger.warning(
                "LuaLaTeX/LaTeXML not available - falling back to pandoc/pdflatex"
            )

    def _run_stage(
        self,
        args,
        *,
        source,
        candidate,
        phase,
        pass_number=1,
        final_pass=True,
        **kwargs,
    ):
        """Keep bounded structured evidence on every exit, including zero."""
        tool = args[0]
        input_hash = sha(source.read_bytes())
        version = "unknown"
        version_args = version_command(tool)
        try:
            probe = subprocess.run(
                version_args,
                capture_output=True,
                text=True,
                timeout=5,
                cwd=kwargs.get("cwd"),
            )
            match = re.search(
                r"\b([0-9]{1,4}(?:\.[0-9]{1,4}){1,3})\b",
                (probe.stdout or "")[:4096] + (probe.stderr or "")[:4096],
            )
            if match:
                version = match[1]
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            if tool in {"lualatex", "pdflatex"}:
                kwargs["env"] = tex_environment(candidate.parent)
                # Compile in owned scratch. -output-directory leaks into the
                # format-building child and can leave its .fmt in the wrong
                # directory on a cold installation. Keep source lookup explicit.
                kwargs["env"]["TEXINPUTS"] = (
                    str(source.parent.resolve())
                    + os.pathsep
                    + kwargs["env"].get("TEXINPUTS", "")
                )
            result = subprocess.run(args, **kwargs)
            findings = classify(
                result.stdout,
                result.stderr,
                exit_code=result.returncode,
                final_pass=final_pass,
            )
        except subprocess.TimeoutExpired:
            result = SimpleNamespace(returncode=1)
            findings = [diagnostic("timeout")]
        except OSError:
            result = SimpleNamespace(returncode=1)
            findings = [diagnostic("tool_unavailable")]
        try:
            candidate_hash = (
                sha(candidate.read_bytes()) if candidate.is_file() else None
            )
        except OSError:
            candidate_hash = None
        if not candidate_hash and not findings:
            findings.append(diagnostic("candidate_missing"))
        stage = record(
            ConversionStage(
                tool=tool,
                version=version,
                phase=phase,
                pass_number=pass_number,
                input_sha256=input_hash,
                candidate_sha256=candidate_hash,
                exit_code=result.returncode,
                diagnostics=findings,
            )
        )
        audit = candidate.parent / f"{tool}-{phase}-{pass_number}.diagnostics.json"
        audit.write_text(stage.model_dump_json(indent=2), encoding="utf-8")
        audit.chmod(0o600)
        # Callers cannot accidentally accept known loss on an exit-zero candidate.
        return SimpleNamespace(
            returncode=1 if stage.blocked else result.returncode, stdout="", stderr=""
        )

    def _finish_diagnostics(self, tex_path, candidate, stages, receipts, kind):
        try:
            report = ConversionDiagnostics(
                source_sha256=sha(Path(tex_path).read_bytes()),
                candidate_sha256=(
                    sha(Path(candidate).read_bytes()) if candidate else None
                ),
                status=(
                    "accepted" if candidate and stages and not has_loss() else "refused"
                ),
                stages=stages,
            )
            if receipts is not None:
                receipts[kind] = report
        except OSError:
            return

    def _check_command(self, cmd: str) -> bool:
        """Check if a command is available in PATH."""
        return shutil.which(cmd) is not None

    def _preserve_metadata(self, source: Path, candidate: Path, kind: str) -> bool:
        """Bind the saved metadata check to authored source and final bytes."""
        metadata = extract_metadata(source.read_text(encoding="utf-8"))
        reasons = sorted(metadata.issues)
        if kind == "pdf" and metadata.spans:
            reasons.append("metadata_unsupported")
        try:
            saved = not reasons and (
                save_html_metadata(candidate, metadata)
                if kind == "html"
                else save_pdf_metadata(candidate, metadata)
            )
        except Exception:
            saved = False
        if not saved and not reasons:
            reasons.append("metadata_not_preserved")
        record(
            ConversionStage(
                tool="inspection",
                phase="inspect",
                metadata_profile="literal-authored-v1",
                input_sha256=sha(source.read_bytes()),
                candidate_sha256=(
                    sha(candidate.read_bytes()) if candidate.is_file() else None
                ),
                diagnostics=[diagnostic(reason) for reason in reasons],
            )
        )
        return bool(saved)

    def _preserve_semantics(self, source: Path, candidate: Path, kind: str) -> bool:
        """Require source-bound saved relationships; never infer author intent."""
        reasons = set()
        try:
            contract = extract_semantics(source.read_text(encoding="utf-8"))
            reasons.update(contract.issues)
            if not reasons:
                if kind == "html":
                    if not save_html_semantics(source, candidate):
                        reasons.add("semantics_not_preserved")
                elif contract.graphics or contract.tables:
                    # PDF structure checks alone cannot associate source assets
                    # and table cells with marked content. Do not credit them.
                    reasons.add("semantics_unsupported")
        except (OSError, UnicodeError, ValueError):
            reasons.add("semantics_not_preserved")
        record(
            ConversionStage(
                tool="inspection",
                phase="inspect",
                semantics_profile="literal-relationships-v1",
                input_sha256=sha(source.read_bytes()),
                candidate_sha256=(
                    sha(candidate.read_bytes()) if candidate.is_file() else None
                ),
                diagnostics=[diagnostic(reason) for reason in sorted(reasons)],
            )
        )
        return not reasons

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

        # LaTeXML's installed Babel binding cannot load current language files.
        # For a document-only declaration, the source-bound metadata check
        # restores its language after conversion. Keep span commands intact;
        # their scope must survive conversion or the export is refused.
        processed = re.sub(
            r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{babel\}",
            lambda match: "% " + match[0] + " (retained in source metadata)\n",
            processed,
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
            result = self._run_stage(
                [
                    "latexml",
                    "--dest=" + str(xml_path),
                    "--log=" + str(output_dir / "parse.log"),
                    str(processed_tex),
                ],
                source=processed_tex,
                candidate=xml_path,
                phase="parse",
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(output_dir),
            )

            processed_tex.chmod(0o600)
            if xml_path.exists():
                xml_path.chmod(0o600)
            if result.returncode != 0 or not xml_path.exists():
                return None
            if inspect_candidate(processed_tex, xml_path, xml=True).blocked:
                return None

            # Step 2: XML → HTML5 with MathML
            # Note: LaTeXML 0.8.x automatically generates MathML with --format=html5
            # Its default footer adds a mascot image absent from the source.
            # Suppress that template before conversion, not images at validation.
            stylesheet = output_dir / "aelira-html5.xsl"
            stylesheet.write_text(LATEXML_HTML_STYLESHEET, encoding="utf-8")
            logger.info("Converting XML to accessible HTML5...")
            result = self._run_stage(
                [
                    "latexmlpost",
                    "--dest=" + str(html_path),
                    "--format=html5",
                    "--stylesheet=" + str(stylesheet),
                    "--log=" + str(output_dir / "postprocess.log"),
                    str(xml_path),
                ],
                source=xml_path,
                candidate=html_path,
                phase="postprocess",
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(output_dir),
            )

            if result.returncode != 0:
                return None
            if html_path.exists():
                self._enhance_html_accessibility(html_path)
                if not self._preserve_metadata(tex_file, html_path, "html"):
                    return None
                preserved = self._preserve_semantics(tex_file, html_path, "html")
                inspected = inspect_candidate(tex_file, html_path)
                if preserved and not inspected.blocked:
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

            # Add skip link for keyboard navigation
            if "<body>" in content and "skip-link" not in content:
                skip_link = '<a href="#main-content" class="skip-link" style="position:absolute;left:-9999px;focus:position:static;">Skip to main content</a>'
                content = content.replace("<body>", f"<body>\n{skip_link}")

            # Wrap main content with landmark
            if "<main" not in content:
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

            # Authored metadata is verified against the source before validation.

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
        self, tex_path, output_dir=None, *, conversion_receipts=None
    ):
        with conversion_session() as stages:
            candidate, validation = self._convert_to_pdf_with_validation(
                tex_path, output_dir
            )
            self._finish_diagnostics(
                tex_path, candidate, stages, conversion_receipts, "pdf"
            )
            return candidate, validation

    def _convert_to_pdf_with_validation(
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
            if inspect_source(tex_file).blocked:
                return None, failure
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
                    rendered = bool(
                        html and self._html_to_pdf_playwright_sync(html, str(pdf))
                    )
                    if html:
                        record(
                            ConversionStage(
                                tool="html-renderer",
                                phase="render",
                                input_sha256=sha(Path(html).read_bytes()),
                                candidate_sha256=(
                                    sha(pdf.read_bytes()) if pdf.is_file() else None
                                ),
                                diagnostics=(
                                    []
                                    if rendered and pdf.is_file()
                                    else [diagnostic("process_failed")]
                                ),
                            )
                        )
                    candidate = str(pdf) if rendered and pdf.is_file() else None
                else:
                    candidate = getattr(self, f"_convert_with_{exporter}")(
                        str(tex_file), attempt
                    )
                if has_loss():
                    return None, failure
                if candidate:
                    if not self._preserve_metadata(tex_file, Path(candidate), "pdf"):
                        return None, failure
                    if not self._preserve_semantics(tex_file, Path(candidate), "pdf"):
                        return None, failure
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
            for run in range(2):
                result = self._run_stage(
                    [
                        "pdflatex",
                        "-interaction=nonstopmode",
                        "-no-shell-escape",  # SECURITY: never enable shell escape (RCE)
                        str(tex_file),
                    ],
                    source=tex_file,
                    candidate=pdf_path,
                    phase="compile",
                    pass_number=run + 1,
                    final_pass=run == 1,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(output_dir),
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
                result = self._run_stage(
                    [
                        "lualatex",
                        "-interaction=nonstopmode",
                        "-no-shell-escape",  # SECURITY: never enable shell escape
                        str(tex_file),
                    ],
                    source=tex_file,
                    candidate=pdf_path,
                    phase="compile",
                    pass_number=run + 1,
                    final_pass=run == 1,
                    capture_output=True,
                    text=True,
                    timeout=180,  # LuaLaTeX can be slower than pdflatex
                    cwd=str(output_dir),
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

    def convert_to_html(self, tex_path, output_dir=None, *, conversion_receipts=None):
        with conversion_session() as stages:
            candidate = self._convert_to_html(tex_path, output_dir)
            self._finish_diagnostics(
                tex_path, candidate, stages, conversion_receipts, "html"
            )
            return candidate

    def _convert_to_html(
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

        if inspect_source(tex_file).blocked:
            return None

        # Choose supported authored relationship/language routes before conversion;
        # known loss in another converter must never trigger a silent retry.
        authored = extract_semantics(tex_file.read_text(encoding="utf-8"))
        if self.pandoc_available and (
            extract_metadata(tex_file.read_text(encoding="utf-8")).spans
            or authored.graphics
            or authored.tables
        ):
            attempt = Path(tempfile.mkdtemp(prefix="html-pandoc-", dir=out_dir))
            return self._convert_with_pandoc(str(tex_file), attempt)

        # Primary: LaTeXML (best MathML support)
        if self.latexml_available:
            attempt = Path(tempfile.mkdtemp(prefix="html-latexml-", dir=out_dir))
            html_path = self._convert_with_latexml(tex_path, attempt)
            if has_loss():
                return None
            if html_path:
                return html_path

        # Fallback: pandoc with MathML
        if self.pandoc_available:
            logger.warning("Using pandoc fallback for HTML")
            attempt = Path(tempfile.mkdtemp(prefix="html-pandoc-", dir=out_dir))
            return self._convert_with_pandoc(tex_path, attempt)

        logger.error("No HTML conversion tools available")
        return None

    def _convert_with_pandoc(self, tex_path: str, output_dir: Path) -> Optional[str]:
        """Fallback HTML conversion using pandoc."""
        tex_file = Path(tex_path)
        html_path = output_dir / (tex_file.stem + ".html")

        try:
            options = []
            semantics = extract_semantics(tex_file.read_text(encoding="utf-8"))
            if semantics.graphics or semantics.tables:
                # The bounded relationship checker cannot evaluate arbitrary
                # CSS. Use a minimal template for this route; metadata is copied
                # from the original source by the separate metadata gate.
                template = output_dir / "relationships.html.template"
                template.write_text(
                    '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>$body$</body></html>',
                    encoding="utf-8",
                )
                options = ["--template", str(template)]
            result = self._run_stage(
                [
                    "pandoc",
                    str(tex_file),
                    "-o",
                    str(html_path),
                    "--standalone",
                    "--mathml",
                    "--toc",
                    "--section-divs",
                    *options,
                ],
                source=tex_file,
                candidate=html_path,
                phase="parse",
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
                if not self._preserve_metadata(tex_file, html_path, "html"):
                    return None
                preserved = self._preserve_semantics(tex_file, html_path, "html")
                inspected = inspect_candidate(tex_file, html_path)
                if preserved and not inspected.blocked:
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
        conversion_receipts: Optional[dict[str, ConversionDiagnostics]] = None,
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
                    tex_path, output_dir, conversion_receipts=conversion_receipts
                )
                if validation_receipts is not None:
                    validation_receipts["pdf"] = receipt
            elif fmt_lower == "html":
                results["html"] = self.convert_to_html(
                    tex_path, output_dir, conversion_receipts=conversion_receipts
                )
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
