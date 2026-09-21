r"""LaTeX source remediation with manual review for unsupported semantics.

Authored metadata and source reference labels can be preserved or repaired.
Figure alternatives and table header relationships require author context and
independent exported-structure checks; generated captions or rules cannot repair
these semantics.
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (
    BaseRemediator,
    IssueCategory,
    OutputFormat,
    RemediationConfig,
    RemediationIssue,
    RemediationResult,
)
from .latex_converter import get_latex_converter
from ..latex_metadata import LANGUAGES, extract_metadata, language_tag
from ..latex_evidence import (
    LatexCheck,
    LatexRepresentationEvidence,
    conversion_evidence,
    digest,
)
from .score_measurement import valid_measurement

logger = logging.getLogger(__name__)


class LatexRemediator(BaseRemediator):
    """Auto-remediate supported LaTeX source findings.

    Figures without alternatives and tables without authored header relationships
    remain manual review findings.
    """

    DOCUMENT_TYPE = "latex"
    SUPPORTED_EXTENSIONS = [".tex"]

    AUTO_FIXABLE_CATEGORIES = [
        IssueCategory.LANGUAGE,
        IssueCategory.ALT_TEXT,
        IssueCategory.HEADING,
        IssueCategory.TABLE,
        IssueCategory.STRUCTURE,
        IssueCategory.ARIA,
        IssueCategory.TITLE,
        IssueCategory.LINK,
        IssueCategory.COLOR,
        IssueCategory.LIST,
    ]

    # Map our issue_type strings to IssueCategory
    ISSUE_TYPE_TO_CATEGORY = {
        "missing_title": IssueCategory.TITLE,
        "missing_author": IssueCategory.TITLE,
        "missing_lang": IssueCategory.LANGUAGE,
        "missing_alt_text": IssueCategory.ALT_TEXT,
        "missing_figure_caption": IssueCategory.ALT_TEXT,
        "missing_table_caption": IssueCategory.TABLE,
        "complex_table_no_header": IssueCategory.TABLE,
        "equation_no_label": IssueCategory.ARIA,
        "color_only_emphasis": IssueCategory.COLOR,
        "low_contrast_potential": IssueCategory.COLOR,
        "unlabeled_hyperlink": IssueCategory.LINK,
        "missing_list_structure": IssueCategory.LIST,
        "conversion_failed": IssueCategory.ARIA,
        "wcag_noncompliant": IssueCategory.ARIA,
    }

    # Note: We no longer inject accessibility/axessibility packages.
    # They are obsolete and don't create valid PDF/UA structure.
    # Instead, we use \DocumentMetadata with LuaLaTeX + tagpdf for PDF/UA-1 compliance.

    def __init__(
        self,
        file_path: str,
        issues: List[Dict[str, Any]],
        config: Optional[RemediationConfig] = None,
        ai_client: Optional[Any] = None,
        *,
        alt_text_client: Optional[Any] = None,
    ):
        """Initialize LaTeX remediator."""
        super().__init__(
            file_path,
            issues,
            config,
            ai_client,
            alt_text_client=alt_text_client,
        )

        # Store original content for modifications
        self._original_content: Optional[str] = None
        self._modified_content: Optional[str] = None

        # Track modifications made
        self._modifications: List[str] = []

    def _load_document(self) -> str:
        """Load the LaTeX document as text."""
        with open(self.file_path, "r", encoding="utf-8") as f:
            self._original_content = f.read()
            self._modified_content = self._original_content
        return self._modified_content

    def _save_document(self, document: str) -> str:
        """
        Save the remediated LaTeX document and convert to requested formats.

        Returns the primary output file path (TEX by default).
        """
        output_path = self._get_output_path()
        from .source_verification import require_separate_source_output

        require_separate_source_output(self.file_path, output_path)

        # Ensure directory exists
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save the .tex file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self._modified_content)

        logger.info(f"Saved remediated LaTeX to: {output_path}")

        # Store all output files
        self._output_files = {"tex": output_path}

        # Convert to additional formats if requested
        requested_formats = getattr(
            self.config, "latex_output_formats", [OutputFormat.TEX]
        )
        format_values = [
            f.value if hasattr(f, "value") else f for f in requested_formats
        ]

        if len(format_values) > 1 or "tex" not in format_values:
            converter = get_latex_converter()
            receipts = {}
            self._conversion_receipts = {}
            results = converter.convert_all_formats(
                output_path,
                format_values,
                str(output_dir),
                validation_receipts=receipts,
                conversion_receipts=self._conversion_receipts,
            )
            self.result.latex_pdf_validation = receipts.get("pdf")
            self._requested_formats = format_values
            self._output_files.update(results)

            # Log conversion results
            for fmt, path in results.items():
                if path and fmt != "tex":
                    self._modifications.append(f"Converted to {fmt.upper()}: {path}")
                    logger.info(f"Generated {fmt.upper()}: {path}")

        self._record_evidence()

        # Return primary output (prefer PDF if available, then HTML, then TEX)
        if "pdf" in self._output_files and self._output_files["pdf"]:
            return self._output_files["pdf"]
        elif "html" in self._output_files and self._output_files["html"]:
            return self._output_files["html"]
        if "tex" not in format_values:
            self.result.success = False
            self.result.error_message = "requested_export_unavailable"
        return output_path

    def get_output_files(self) -> Dict[str, Optional[str]]:
        """Get all generated output files (some may be None if conversion failed)."""
        return getattr(self, "_output_files", {"tex": self._get_output_path()})

    def can_auto_fix(self, issue: RemediationIssue) -> bool:
        """
        Determine if a LaTeX issue can be automatically fixed.

        Args:
            issue: The issue to check

        Returns:
            True if the issue can be auto-fixed
        """
        # Categories we can auto-fix
        if issue.category in self.AUTO_FIXABLE_CATEGORIES:
            # Check specific issue types
            if issue.category == IssueCategory.ALT_TEXT:
                # Source text alone cannot supply trustworthy visual meaning.
                return False

            elif issue.category == IssueCategory.LANGUAGE:
                metadata = extract_metadata(self._modified_content)
                return bool(metadata.language and not metadata.issues)

            elif issue.category == IssueCategory.STRUCTURE:
                # Can add accessibility packages
                return (
                    "package" in issue.description.lower()
                    or "accessibility" in issue.description.lower()
                )

            elif issue.category == IssueCategory.HEADING:
                # Can fix heading hierarchy in some cases
                return True

            elif issue.category == IssueCategory.TABLE:
                # Header relationships and captions require author context.
                return False

            elif issue.category == IssueCategory.ARIA:
                return self._is_reference_label_issue(issue)

            elif issue.category == IssueCategory.TITLE:
                return self._get_rule_based_fix(issue, None) is not None

            elif issue.category == IssueCategory.LINK:
                # Can fix bare URLs
                return True

            elif issue.category == IssueCategory.COLOR:
                # Can add additional emphasis to color-only text
                return True

            elif issue.category == IssueCategory.LIST:
                # List structure issues need manual review
                return False

        return False

    def apply_fix(
        self, issue: RemediationIssue, document: str, fix_content: str
    ) -> bool:
        """
        Apply a fix to the LaTeX document.

        Args:
            issue: The issue being fixed
            document: The document content (not used, we use self._modified_content)
            fix_content: The fix to apply

        Returns:
            True if fix was applied successfully
        """
        try:
            if issue.category == IssueCategory.LANGUAGE:
                return self._apply_language_fix(fix_content)

            elif issue.category == IssueCategory.STRUCTURE:
                return self._apply_structure_fix(fix_content)

            elif issue.category == IssueCategory.ALT_TEXT:
                return self._apply_alt_text_fix(issue, fix_content)

            elif issue.category == IssueCategory.HEADING:
                return self._apply_heading_fix(issue, fix_content)

            elif issue.category == IssueCategory.TABLE:
                return self._apply_table_fix(issue, fix_content)

            elif issue.category == IssueCategory.ARIA:
                return self._apply_aria_fix(issue, fix_content)

            elif issue.category == IssueCategory.TITLE:
                return self._apply_title_fix(issue, fix_content)

            elif issue.category == IssueCategory.LINK:
                return self._apply_link_fix(issue, fix_content)

            elif issue.category == IssueCategory.COLOR:
                return self._apply_color_fix(issue, fix_content)

            else:
                logger.warning(f"No handler for category: {issue.category}")
                return False

        except Exception as e:
            logger.error(f"Failed to apply fix: {e}")
            return False

    def _apply_title_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Add or fix document title/author."""
        if fix_content.startswith("title:"):
            title = fix_content[6:]
            if r"\title{" not in self._modified_content:
                if r"\begin{document}" in self._modified_content:
                    self._modified_content = self._modified_content.replace(
                        r"\begin{document}",
                        f"\\title{{{title}}}\n\\begin{{document}}\n\\maketitle\n",
                    )
                    self._modifications.append(f"Added document title: {title}")
                    return True
        elif fix_content.startswith("author:"):
            author = fix_content[7:]
            if r"\author{" not in self._modified_content:
                # Insert after \title if present, otherwise before \begin{document}
                if r"\title{" in self._modified_content:
                    self._modified_content = re.sub(
                        r"(\\title\{[^}]+\})",
                        f"\\1\n\\\\author{{{author}}}",
                        self._modified_content,
                        count=1,
                    )
                elif r"\begin{document}" in self._modified_content:
                    self._modified_content = self._modified_content.replace(
                        r"\begin{document}",
                        f"\\author{{{author}}}\n\\begin{{document}}",
                    )
                self._modifications.append(f"Added document author: {author}")
                return True
        return False

    def _apply_link_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Fix bare URLs with descriptive text."""
        # Find bare \url{} commands and convert to \href with descriptive text
        url_pattern = r"\\url\{([^}]+)\}"
        match = re.search(url_pattern, self._modified_content)
        if match:
            url = match.group(1)
            # Generate a description from the URL
            description = fix_content if fix_content else self._url_to_description(url)
            replacement = f"\\href{{{url}}}{{{description}}}"
            self._modified_content = self._modified_content.replace(
                match.group(0), replacement, 1
            )
            self._modifications.append(
                f"Converted bare URL to descriptive link: {url[:30]}..."
            )
            return True
        return False

    def _url_to_description(self, url: str) -> str:
        """Generate a description from a URL."""
        # Extract domain and path
        import urllib.parse

        try:
            parsed = urllib.parse.urlparse(url)
            domain = parsed.netloc.replace("www.", "")
            if parsed.path and parsed.path != "/":
                path = parsed.path.strip("/").split("/")[-1]
                path = path.replace("-", " ").replace("_", " ").title()
                return f"{path} on {domain}"
            return f"Link to {domain}"
        except Exception:
            return "External link"

    def _apply_color_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Fix color-only emphasis by adding additional visual indicators."""
        # Find \textcolor{} without other emphasis and add \textbf{}
        color_pattern = r"\\textcolor\{([^}]+)\}\{([^}]+)\}"
        for match in re.finditer(color_pattern, self._modified_content):
            color = match.group(1)
            text = match.group(2)
            # Check if already has emphasis
            if not any(
                cmd in text for cmd in [r"\textbf", r"\emph", r"\underline", r"\textit"]
            ):
                # Add bold emphasis alongside color
                replacement = f"\\textcolor{{{color}}}{{\\textbf{{{text}}}}}"
                self._modified_content = self._modified_content.replace(
                    match.group(0), replacement, 1
                )
                self._modifications.append(
                    f"Added bold emphasis to colored text: {text[:20]}..."
                )
                return True
        return False

    def _apply_language_fix(self, lang: str) -> bool:
        """Add or fix document language settings."""
        metadata = extract_metadata(self._modified_content)
        tag = language_tag(lang)
        if (
            not tag
            or metadata.issues
            or (metadata.language and metadata.language != tag)
        ):
            return False
        babel_name = next(
            (name for name, value in LANGUAGES.items() if value == tag), None
        )
        # Check if babel is already loaded
        if re.search(
            r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{[^}]*\bbabel\b[^}]*\}",
            self._modified_content,
        ):
            # Existing main/secondary languages and options are authored content.
            pass
        else:
            if not babel_name:
                return False
            # Add babel package after documentclass
            if r"\documentclass" in self._modified_content:
                self._modified_content = re.sub(
                    r"(\\documentclass[^\n]*\n)",
                    r"\1\\usepackage[" + babel_name + r"]{babel}" + "\n",
                    self._modified_content,
                    count=1,
                )
                self._modifications.append(f"Added babel package with language {lang}")

        # Also add pdfinfo if not present
        if r"\hypersetup" not in self._modified_content:
            # Add hypersetup with language
            hypersetup = f"\\hypersetup{{pdflang={{{tag}}}}}\n"
            # Insert before \begin{document}
            if r"\begin{document}" in self._modified_content:
                # hypersetup belongs to hyperref. A source-only repair must load
                # it too; an optional later conversion cannot supply this dependency.
                preamble = self._modified_content.split(r"\begin{document}", 1)[0]
                preamble = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
                package_lists = re.findall(
                    r"\\(?:usepackage|RequirePackage)\s*"
                    r"(?:\[[^\]]*\]\s*)?\{([^}]+)\}",
                    preamble,
                )
                if not any(
                    "hyperref" in [name.strip() for name in packages.split(",")]
                    for packages in package_lists
                ):
                    hypersetup = "\\usepackage{hyperref}\n" + hypersetup
                    self._modifications.append(
                        "Added hyperref package for PDF language"
                    )
                self._modified_content = self._modified_content.replace(
                    r"\begin{document}", f"{hypersetup}\\begin{{document}}"
                )
                self._modifications.append("Added hypersetup with PDF language")

        return True

    def _apply_structure_fix(self, fix_content: str) -> bool:
        """Add accessibility structure improvements."""
        if "accessibility" in fix_content.lower():
            metadata = extract_metadata(self._modified_content)
            if metadata.issues:
                return False
            pdf_metadata = ""
            # Add DocumentMetadata for PDF/UA tagging (replaces obsolete accessibility package)
            if r"\DocumentMetadata" not in self._modified_content:
                if r"\documentclass" in self._modified_content:
                    authored = (
                        f"  lang={{{metadata.language}}},\n"
                        if metadata.language
                        else ""
                    )
                    # Title and author are hyperref keys, not DocumentMetadata
                    # keys. Keep their authored values in the supported interface.
                    pdf_metadata = ",".join(
                        f"{key}={{{value}}}"
                        for key, value in (
                            ("pdfauthor", metadata.author),
                            ("pdftitle", metadata.title),
                        )
                        if value
                    )
                    document_metadata = f"""\\DocumentMetadata{{
{authored}\
  pdfstandard=ua-1,
  pdfversion=1.7,
  testphase={{phase-III,math,title,table,firstaid}}
}}
"""
                    self._modified_content = document_metadata + self._modified_content
                    self._modifications.append(
                        "Added DocumentMetadata for PDF/UA-1 tagging"
                    )

            # Also ensure hyperref is loaded for PDF metadata
            preamble = self._modified_content.split(r"\begin{document}", 1)[0]
            preamble = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
            hyperref_loaded = any(
                "hyperref" in [name.strip() for name in packages.split(",")]
                for packages in re.findall(
                    r"\\(?:usepackage|RequirePackage)\s*"
                    r"(?:\[[^\]]*\]\s*)?\{([^}]+)\}",
                    preamble,
                )
            )
            if not hyperref_loaded:
                # Add hyperref before \begin{document}
                if r"\begin{document}" in self._modified_content:
                    self._modified_content = self._modified_content.replace(
                        r"\begin{document}", "\\usepackage{hyperref}\n\\begin{document}"
                    )
                    self._modifications.append("Added hyperref package")

            if pdf_metadata and r"\begin{document}" in self._modified_content:
                self._modified_content = self._modified_content.replace(
                    r"\begin{document}",
                    f"\\hypersetup{{{pdf_metadata}}}\n\\begin{{document}}",
                    1,
                )
                self._modifications.append("Preserved authored PDF title and author")

        return True

    def _apply_alt_text_fix(self, issue: RemediationIssue, alt_text: str) -> bool:
        """Leave visual meaning to author review; comments/captions are not alt text."""
        return False

    def _apply_heading_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Fix heading hierarchy issues."""
        # Heading fixes might involve restructuring
        # For now, we add structure comments
        if (
            "skip" in issue.description.lower()
            or "hierarchy" in issue.description.lower()
        ):
            # Add a comment noting the issue (not a structural fix)
            self._modified_content = f"% ACCESSIBILITY NOTE: {issue.description}\n% Suggestion: {fix_content}\n{self._modified_content}"
            self._modifications.append("Added heading hierarchy note")
            return False  # Comment does not fix the heading hierarchy

        return False

    def _apply_table_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Never infer table headers or manufacture a caption from source layout."""
        return False

    @staticmethod
    def _is_reference_label_issue(issue: RemediationIssue) -> bool:
        """A source cross-reference finding is not a mathematical description."""
        return (
            issue.category == IssueCategory.ARIA
            and "without \\label{} for cross-referencing" in issue.description
        )

    def _get_manual_reason(self, issue: RemediationIssue) -> str:
        if issue.category == IssueCategory.ALT_TEXT:
            return "Figure alternatives and decorative intent require author review"
        if issue.category == IssueCategory.TABLE:
            return "Table meaning and header relationships require author review"
        if issue.category in {IssueCategory.LANGUAGE, IssueCategory.TITLE}:
            return "Authored document metadata is unknown, ambiguous or unsupported"
        if issue.category == IssueCategory.ARIA and not self._is_reference_label_issue(
            issue
        ):
            return "Mathematical meaning has no independent semantic verification"
        return super()._get_manual_reason(issue)

    def _get_manual_recommendation(self, issue: RemediationIssue) -> str:
        if issue.category == IssueCategory.ALT_TEXT:
            return "Provide an authored alternative or intentional artifact declaration for each image, then verify the exported structure; a caption or source comment does not establish equivalence."
        if issue.category == IssueCategory.TABLE:
            return "Confirm table purpose and explicit row/column header relationships with the author, then verify them in the export; visual rules do not identify headers."
        if issue.category in {IssueCategory.LANGUAGE, IssueCategory.TITLE}:
            return "Confirm the document language, title and author in the source; automated remediation will not infer them."
        if issue.category == IssueCategory.ARIA and not self._is_reference_label_issue(
            issue
        ):
            return "Review the complete source and structured mathematics with author context; a reference label or generated summary is not an equivalent description."
        return super()._get_manual_recommendation(issue)

    def _apply_aria_fix(self, issue: RemediationIssue, aria_label: str) -> bool:
        """Add source reference labels only, never a generated description."""
        if not self._is_reference_label_issue(issue):
            return False
        # For LaTeX, we add labels to equations for cross-referencing
        if (
            "equation" in issue.description.lower()
            or "label" in issue.description.lower()
        ):
            # Find equation environments without labels and add them
            eq_pattern = r"(\\begin\{equation\})(.*?)(\\end\{equation\})"
            eq_count = 0
            modified = False

            for match in re.finditer(eq_pattern, self._modified_content, re.DOTALL):
                eq_content = match.group(2)
                if r"\label{" not in eq_content:
                    eq_count += 1
                    label = f"eq:equation{eq_count}"

                    new_content = eq_content.rstrip() + f"\n\\label{{{label}}}\n"
                    self._modified_content = self._modified_content.replace(
                        match.group(0), match.group(1) + new_content + match.group(3), 1
                    )
                    self._modifications.append(f"Added label to equation: {label}")
                    modified = True
                    break  # Fix one at a time

            if modified:
                return True

            # Also check align environments
            align_pattern = r"(\\begin\{align\})(.*?)(\\end\{align\})"
            for match in re.finditer(align_pattern, self._modified_content, re.DOTALL):
                align_content = match.group(2)
                if r"\label{" not in align_content:
                    eq_count += 1
                    label = f"eq:align{eq_count}"
                    new_content = align_content.rstrip() + f"\n\\label{{{label}}}\n"
                    self._modified_content = self._modified_content.replace(
                        match.group(0), match.group(1) + new_content + match.group(3), 1
                    )
                    self._modifications.append(
                        f"Added label to align environment: {label}"
                    )
                    return True

        return False

    def _get_rule_based_fix(
        self, issue: RemediationIssue, document: Any
    ) -> Optional[str]:
        """Get rule-based fixes for LaTeX issues."""
        if issue.category == IssueCategory.LANGUAGE:
            metadata = extract_metadata(self._modified_content)
            return metadata.language if not metadata.issues else None

        elif issue.category == IssueCategory.STRUCTURE:
            if "accessibility" in issue.description.lower():
                return "add accessibility package"

        elif issue.category == IssueCategory.TITLE:
            metadata = extract_metadata(self._modified_content)
            if metadata.issues:
                return None
            if "title" in issue.description.lower():
                return f"title:{metadata.title}" if metadata.title else None
            elif "author" in issue.description.lower():
                return f"author:{metadata.author}" if metadata.author else None

        elif issue.category in {IssueCategory.ALT_TEXT, IssueCategory.TABLE}:
            return None

        elif issue.category == IssueCategory.ARIA:
            if self._is_reference_label_issue(issue):
                return "equation_label:auto"

        elif issue.category == IssueCategory.LINK:
            # URLs need descriptive text - use AI
            return None

        elif issue.category == IssueCategory.COLOR:
            # Color issues need manual review or AI
            return None

        return None

    def _get_template_fix(self, issue: RemediationIssue) -> Optional[str]:
        """Get template-based fixes for LaTeX issues."""
        templates = {
            IssueCategory.STRUCTURE: "add accessibility package",
        }
        return templates.get(issue.category)

    def _get_ai_generated_fix(
        self, issue: RemediationIssue, document: Any, *, client: Any
    ) -> Optional[str]:
        """Generate fix using AI."""

        # Unverified prose cannot establish visual, tabular or math semantics.
        if issue.category in {
            IssueCategory.ALT_TEXT,
            IssueCategory.TABLE,
            IssueCategory.ARIA,
            IssueCategory.LANGUAGE,
            IssueCategory.TITLE,
        }:
            return None

        self.result.ai_calls_made += 1

        try:
            from ...utils.security import sanitize_for_prompt

            safe_desc = sanitize_for_prompt(issue.description or "", max_length=300)
            safe_loc = sanitize_for_prompt(issue.location or "Unknown", max_length=100)
            prompt = f"""Suggest a fix for this LaTeX accessibility issue:
Issue: {safe_desc}
Category: {issue.category.value}
Location: {safe_loc}

Provide ONLY the fix content, no explanation."""

            # Use the AI client (with hasattr guard matching other remediators)
            if not hasattr(client, "generate_text_sync"):
                logger.warning("AI client does not support generate_text_sync")
                return None
            result = client.generate_text_sync(
                prompt=prompt, max_tokens=200, temperature=0.3
            )

            if result.get("success"):
                return result.get("content", "").strip()

        except Exception as e:
            logger.warning(f"AI fix generation failed: {e}")

        return None

    def _record_evidence(self, *, source_checked=False):
        """Bind each observation to its own representation; never transfer scores."""
        try:
            source = Path(self.file_path).read_bytes()
        except OSError:
            self.result.latex_evidence = {}
            return
        evidence = {}
        for kind, path in getattr(self, "_output_files", {}).items():
            if kind not in {"tex", "html", "pdf", "docx"}:
                continue
            try:
                candidate = Path(path).read_bytes() if path else None
            except OSError:
                candidate = None
            receipt = conversion_evidence(source, candidate, kind)
            if kind == "tex":
                fields = receipt.model_dump()
                fields["conversion"] = LatexCheck().model_dump()
                fields["source_check"] = LatexCheck(
                    status="unavailable" if source_checked else "not_assessed",
                    method="latex-source-v1" if source_checked else "none",
                ).model_dump()
                measured = valid_measurement(self.result.score_measurement)
                verified = self.result.verification_result
                if (
                    measured
                    and verified
                    and candidate is not None
                    and measured["method_version"] == "latex-source-v1"
                    and measured["source_sha256"] == digest(source)
                    and measured["output_sha256"] == digest(candidate)
                ):
                    fields["source_check"] = LatexCheck(
                        status="completed",
                        method="latex-source-v1",
                        findings_count=verified.issues_after,
                    ).model_dump()
                receipt = LatexRepresentationEvidence.model_validate(fields)
            evidence[kind] = receipt
        pdf = self.result.latex_pdf_validation
        if pdf is not None:
            # A refused candidate may be retained only as a digest-bound observation.
            evidence["pdf"] = LatexRepresentationEvidence(
                representation="pdf",
                source_sha256=digest(source),
                candidate_sha256=pdf.candidate_sha256,
                conversion=LatexCheck(
                    status=(
                        "completed"
                        if pdf.candidate_sha256
                        else (
                            "failed"
                            if pdf.reason == "conversion_failed"
                            else "unavailable"
                        )
                    ),
                    method="latex-export-v1",
                ),
                structural_validation=LatexCheck(
                    status=(
                        "not_assessed"
                        if pdf.reason in {"conversion_failed", "no_converter"}
                        else pdf.status
                        if pdf.candidate_sha256
                        else "unavailable"
                    ),
                    method="pikepdf+veraPDF/ua1",
                ),
            )
        for kind, diagnostics in getattr(self, "_conversion_receipts", {}).items():
            if kind in evidence:
                fields = evidence[kind].model_dump()
                fields["conversion_diagnostics"] = diagnostics.model_dump()
                if any(stage.blocked for stage in diagnostics.stages):
                    fields["conversion"] = LatexCheck(
                        status="failed", method="latex-export-v1"
                    ).model_dump()
                evidence[kind] = LatexRepresentationEvidence.model_validate(fields)
        self.result.latex_evidence = evidence

    def _verify_fixes(self, output_path: str):
        kind = Path(output_path).suffix.lower().lstrip(".")
        self._output_files = {**getattr(self, "_output_files", {}), kind: output_path}
        try:
            return self._verify_source_fixes(output_path)
        finally:
            self._record_evidence(source_checked=True)

    def _verify_source_fixes(self, output_path: str):
        """Check syntax and rescan source findings on saved TEX output."""
        from .source_verification import scan_latex_source, verify_source_output

        if Path(output_path).suffix.lower() != ".tex":
            self.result.warnings.append(
                "PDF/HTML exports require their own accessibility verifier; source checks are not comparable."
            )
            return super()._verify_fixes(output_path)
        if "tex" not in getattr(self, "_requested_formats", ["tex"]):
            self.result.warnings.append(
                "The requested export was unavailable; TEX is retained for review."
            )
            return super()._verify_fixes(output_path)
        # Read the output file
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Basic syntax checks
            issues = []

            # Check for balanced braces
            open_braces = content.count("{")
            close_braces = content.count("}")
            if open_braces != close_braces:
                issues.append(
                    f"Unbalanced braces: {open_braces} open, {close_braces} close"
                )

            # Check for \begin/\end matching
            begins = len(re.findall(r"\\begin\{", content))
            ends = len(re.findall(r"\\end\{", content))
            if begins != ends:
                issues.append(f"Unbalanced environments: {begins} begin, {ends} end")

            if issues:
                self.result.warnings.extend(issues)
                logger.warning(f"LaTeX verification issues: {issues}")
                return super()._verify_fixes(output_path)

        except Exception:
            self.result.warnings.append("Saved LaTeX syntax could not be checked.")
            return super()._verify_fixes(output_path)

        return verify_source_output(self, output_path, scan_latex_source)

    def auto_remediate(self) -> bool:
        """
        Perform automatic remediation without pre-scanned issues.

        This method scans for common LaTeX accessibility issues and fixes them:
        1. DocumentMetadata for PDF/UA tagging (LuaLaTeX + tagpdf)
        2. Missing document language
        3. Missing document title/author
        4. Missing PDF metadata
        5. Equations without labels

        Figure alternatives and table header relationships remain manual findings.

        Returns:
            True if any fixes were applied
        """
        try:
            # Load document
            self._load_document()

            fixes_applied = 0

            # Reuse the authored-metadata path; never infer language or identity.
            before = self._modified_content
            self._apply_structure_fix("add accessibility package")
            if self._modified_content != before:
                fixes_applied += 1

            # 1. Remove obsolete accessibility packages (tagpdf replaces them)
            # These packages don't create valid PDF/UA structure with content references
            obsolete_packages = [
                (
                    r"\\usepackage(\[[^\]]*\])?\{accessibility\}[^\n]*\n?",
                    "accessibility",
                ),
                (r"\\usepackage(\[[^\]]*\])?\{axessibility\}[^\n]*\n?", "axessibility"),
            ]
            for pattern, pkg_name in obsolete_packages:
                if re.search(pattern, self._modified_content):
                    self._modified_content = re.sub(pattern, "", self._modified_content)
                    self._modifications.append(
                        f"Removed obsolete {pkg_name} package (tagpdf replaces it)"
                    )
                    logger.info(f"Removed obsolete {pkg_name} package")

            # 2. Ensure hyperref is present for PDF metadata
            # Note: With DocumentMetadata/tagpdf, hyperref is loaded automatically
            # but we add it explicitly for compatibility with non-LuaLaTeX pipelines
            hyperref_loaded = (
                r"\usepackage{hyperref}" in self._modified_content
                or re.search(
                    r"\\usepackage\[[^\]]*\]\{hyperref\}", self._modified_content
                )
            )
            if not hyperref_loaded:
                if r"\begin{document}" in self._modified_content:
                    self._modified_content = self._modified_content.replace(
                        r"\begin{document}",
                        r"\usepackage{hyperref}  % PDF metadata and links"
                        + "\n"
                        + r"\begin{document}",
                    )
                    fixes_applied += 1
                    self._modifications.append("Added hyperref package")
                    logger.info("Added hyperref package")

            # Missing language/title/author require an explicit authored value.
            metadata = extract_metadata(self._modified_content)
            if not metadata.language or not metadata.title or not metadata.author:
                self.result.warnings.append(
                    "Unknown document metadata requires author review."
                )

            # 6. Add PDF metadata if title exists but no hypersetup
            # Note: With DocumentMetadata, much of this is handled automatically,
            # but we add hypersetup for fallback pipelines and explicit metadata
            if (
                metadata.title
                and not metadata.issues
                and r"\hypersetup" not in self._modified_content
            ):
                title = metadata.title
                # Note: pdfaccessible is NOT a valid hyperref option - removed
                hypersetup = "\\hypersetup{pdftitle={" + title + "}}\n"
                if r"\begin{document}" in self._modified_content:
                    self._modified_content = self._modified_content.replace(
                        r"\begin{document}", hypersetup + r"\begin{document}"
                    )
                    fixes_applied += 1
                    self._modifications.append(
                        f"Added PDF metadata with title: {title}"
                    )
                    logger.info(f"Added PDF metadata with title: {title}")

            # Keep unsupported figure/table findings unresolved, including the
            # no-pre-scanned-issues entry point. Never guess from filenames or rules.
            from ..latex_processor import LaTeXProcessor

            source_findings = LaTeXProcessor(
                use_ai=False, llm_client=False
            ).detect_accessibility_issues(self._original_content)
            semantic_findings = [
                {
                    "id": f"latex-semantic-{index}",
                    "category": self.ISSUE_TYPE_TO_CATEGORY[finding.issue_type].value,
                    "severity": finding.severity,
                    "description": finding.description,
                    "location": str(finding.line_number),
                    "wcag_criteria": finding.wcag_criterion,
                }
                for index, finding in enumerate(source_findings)
                if self.ISSUE_TYPE_TO_CATEGORY.get(finding.issue_type)
                in {IssueCategory.ALT_TEXT, IssueCategory.TABLE}
            ]
            for issue in self._normalize_issues(semantic_findings):
                self._add_manual_issue(
                    issue,
                    reason=self._get_manual_reason(issue),
                    recommendation=self._get_manual_recommendation(issue),
                )
            self.result.total_issues = max(
                self.result.total_issues, self.result.manual_count
            )

            # 8. Add labels to equations without them
            equation_pattern = r"(\\begin\{equation\})(.*?)(\\end\{equation\})"
            eq_count = 0
            for match in re.finditer(
                equation_pattern, self._modified_content, re.DOTALL
            ):
                eq_content = match.group(2)
                if r"\label{" not in eq_content:
                    eq_count += 1
                    new_content = (
                        eq_content.rstrip() + f"\n\\label{{eq:equation{eq_count}}}\n"
                    )
                    self._modified_content = self._modified_content.replace(
                        match.group(0), match.group(1) + new_content + match.group(3)
                    )
                    fixes_applied += 1
                    self._modifications.append(f"Added label to equation {eq_count}")

            # Save if any fixes were applied
            if fixes_applied > 0:
                # Use _save_document to trigger format conversion (PDF, HTML)
                output_path = self._save_document(self._modified_content)

                self.result.output_file = output_path
                self.result.fixed_count = fixes_applied
                logger.info(f"Applied {fixes_applied} automatic fixes to {output_path}")
                logger.debug(f"Modifications: {self._modifications}")
                return True

            return False

        except Exception as e:
            logger.error(f"Auto-remediation failed: {e}")
            self.result.error_message = str(e)
            return False


# Convenience function for direct remediation
def remediate_latex(
    file_path: str,
    issues: Optional[List[Dict[str, Any]]] = None,
    config: Optional[RemediationConfig] = None,
    ai_client: Optional[Any] = None,
) -> "RemediationResult":
    """
    Remediate a LaTeX file.

    Args:
        file_path: Path to the .tex file
        issues: List of issues from scanning (optional for auto-remediation)
        config: Remediation configuration
        ai_client: AI client for generating fixes

    Returns:
        RemediationResult with fixed and manual issues
    """

    remediator = LatexRemediator(
        file_path=file_path,
        issues=issues or [],
        config=config,
        ai_client=ai_client,
    )

    # If no issues provided, run auto-remediation
    if not issues:
        remediator.auto_remediate()
        remediator.result.complete()
        return remediator.result

    return remediator.remediate()
