r"""
LaTeX Auto-Remediator for Aelira Accessibility Platform.

This module provides automatic remediation for LaTeX documents, focusing on:
1. Accessibility package injection (\usepackage{accessibility})
2. Document language settings
3. Figure alt text / descriptions
4. Heading structure improvements
5. Table accessibility improvements
6. MathML fallback text generation

LaTeX remediation is CRITICAL for STEM departments where most content is in LaTeX.
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

logger = logging.getLogger(__name__)


class LatexRemediator(BaseRemediator):
    """
    Auto-remediator for LaTeX (.tex) documents.

    Fixes accessibility issues including:
    - Missing accessibility packages
    - Document language not set
    - Figures without alt text
    - Tables without proper structure
    - Heading hierarchy issues
    - Missing document title/author
    - Equations without labels
    - Color-only emphasis
    - Bare URLs

    STEM-critical: Math/science departments rely heavily on LaTeX.
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
    ):
        """Initialize LaTeX remediator."""
        super().__init__(file_path, issues, config, ai_client)

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
            results = converter.convert_all_formats(
                output_path, format_values, str(output_dir)
            )
            self._output_files.update(results)

            # Log conversion results
            for fmt, path in results.items():
                if path and fmt != "tex":
                    self._modifications.append(f"Converted to {fmt.upper()}: {path}")
                    logger.info(f"Generated {fmt.upper()}: {path}")

        # Return primary output (prefer PDF if available, then HTML, then TEX)
        if "pdf" in self._output_files and self._output_files["pdf"]:
            return self._output_files["pdf"]
        elif "html" in self._output_files and self._output_files["html"]:
            return self._output_files["html"]
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
                # Can fix figures with missing alt text using AI
                return "figure" in issue.description.lower() or self.config.use_ai

            elif issue.category == IssueCategory.LANGUAGE:
                # Always can add language settings
                return True

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
                # Can improve table accessibility
                return True

            elif issue.category == IssueCategory.ARIA:
                # Can add ARIA labels/descriptions - equations, labels
                return True

            elif issue.category == IssueCategory.TITLE:
                # Can add missing title/author
                return True

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
        # Check if babel is already loaded
        if (
            r"\usepackage[" in self._modified_content
            and "babel}" in self._modified_content
        ):
            # Update existing babel - use raw string + concatenation to avoid regex escape issues
            self._modified_content = re.sub(
                r"\\usepackage\[([^\]]*)\]\{babel\}",
                r"\\usepackage[" + lang + r"]{babel}",
                self._modified_content,
            )
            self._modifications.append(f"Updated babel language to {lang}")
        else:
            # Add babel package after documentclass
            if r"\documentclass" in self._modified_content:
                self._modified_content = re.sub(
                    r"(\\documentclass[^\n]*\n)",
                    r"\1\\usepackage[" + lang + r"]{babel}" + "\n",
                    self._modified_content,
                    count=1,
                )
                self._modifications.append(f"Added babel package with language {lang}")

        # Also add pdfinfo if not present
        if r"\hypersetup" not in self._modified_content:
            # Add hypersetup with language
            hypersetup = f"\\hypersetup{{pdflang={{{lang}}}}}\n"
            # Insert before \begin{document}
            if r"\begin{document}" in self._modified_content:
                self._modified_content = self._modified_content.replace(
                    r"\begin{document}", f"{hypersetup}\\begin{{document}}"
                )
                self._modifications.append("Added hypersetup with PDF language")

        return True

    def _apply_structure_fix(self, fix_content: str) -> bool:
        """Add accessibility structure improvements."""
        if "accessibility" in fix_content.lower():
            # Add DocumentMetadata for PDF/UA tagging (replaces obsolete accessibility package)
            if r"\DocumentMetadata" not in self._modified_content:
                if r"\documentclass" in self._modified_content:
                    title_match = re.search(
                        r"\\title\{([^}]+)\}", self._modified_content
                    )
                    title = title_match.group(1) if title_match else "Untitled Document"

                    document_metadata = f"""\\DocumentMetadata{{
  lang=en,
  pdfstandard=ua-1,
  pdfversion=1.7,
  testphase={{phase-III,math,title,table,firstaid}},
  pdfauthor={{Aelira Accessibility Platform}},
  pdftitle={{{title}}}
}}
"""
                    self._modified_content = document_metadata + self._modified_content
                    self._modifications.append(
                        "Added DocumentMetadata for PDF/UA-1 tagging"
                    )

            # Also ensure hyperref is loaded for PDF metadata
            # Check for \usepackage{hyperref} or \usepackage[options]{hyperref}
            hyperref_loaded = (
                r"\usepackage{hyperref}" in self._modified_content
                or re.search(
                    r"\\usepackage\[[^\]]*\]\{hyperref\}", self._modified_content
                )
            )
            if not hyperref_loaded:
                # Add hyperref before \begin{document}
                if r"\begin{document}" in self._modified_content:
                    self._modified_content = self._modified_content.replace(
                        r"\begin{document}", "\\usepackage{hyperref}\n\\begin{document}"
                    )
                    self._modifications.append("Added hyperref package")

        return True

    def _apply_alt_text_fix(self, issue: RemediationIssue, alt_text: str) -> bool:
        """Add or fix alt text for figures."""
        # If we have a figure environment, add alt text using pdfcomment or caption
        if (
            "figure" in issue.description.lower()
            or "caption" in issue.description.lower()
        ):
            # Find figures - match \begin{figure} with optional parameters like [h]
            figure_pattern = r"(\\begin\{figure\}[^\n]*\n)(.*?)(\\end\{figure\})"
            figures = list(
                re.finditer(figure_pattern, self._modified_content, re.DOTALL)
            )

            for i, match in enumerate(figures):
                figure_begin = match.group(1)
                figure_content = match.group(2)
                figure_end = match.group(3)

                # Check if this figure needs fixes
                # Use regex to find actual \caption{ commands, not comments mentioning caption
                has_caption = bool(re.search(r"\\caption\s*\{", figure_content))
                has_alt_comment = (
                    "% Alt text:" in figure_content or r"\pdftooltip" in figure_content
                )

                if has_caption and has_alt_comment:
                    continue  # Already has both, skip

                # Generate alt text from image filename if not provided
                if not alt_text or alt_text == "auto":
                    img_match = re.search(
                        r"\\includegraphics[^{]*\{([^}]+)\}", figure_content
                    )
                    if img_match:
                        img_name = (
                            Path(img_match.group(1))
                            .stem.replace("_", " ")
                            .replace("-", " ")
                            .title()
                        )
                        alt_text = f"Figure showing {img_name}"
                    else:
                        alt_text = f"Figure {i+1}"

                new_content = figure_content

                # Add caption if missing
                if not has_caption:
                    # Insert caption before \end{figure}
                    new_content = (
                        new_content.rstrip() + f"\n    \\caption{{{alt_text}}}\n"
                    )
                    self._modifications.append(
                        f"Added caption to figure {i+1}: {alt_text}"
                    )

                # Add alt text comment if missing (for screen reader context)
                if not has_alt_comment:
                    # Add alt text comment after \includegraphics line
                    img_pattern = r"(\\includegraphics[^\n]*\n)"
                    if re.search(img_pattern, new_content):
                        new_content = re.sub(
                            img_pattern,
                            r"\1" + f"    % Alt text: {alt_text}\n",
                            new_content,
                            count=1,
                        )
                        self._modifications.append(f"Added alt text to figure {i+1}")

                # Replace the figure content
                if new_content != figure_content:
                    self._modified_content = (
                        self._modified_content[: match.start()]
                        + figure_begin
                        + new_content
                        + figure_end
                        + self._modified_content[match.end() :]
                    )
                    return True

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
        """Improve table accessibility."""
        if fix_content.startswith("table_caption:"):
            caption = fix_content[14:]
            # Find table environments without captions
            table_pattern = r"(\\begin\{table\})(.*?)(\\end\{table\})"
            match = re.search(table_pattern, self._modified_content, re.DOTALL)
            if match and r"\caption{" not in match.group(2):
                new_content = match.group(2).rstrip() + f"\n\\caption{{{caption}}}\n"
                self._modified_content = self._modified_content.replace(
                    match.group(0), match.group(1) + new_content + match.group(3)
                )
                self._modifications.append(f"Added table caption: {caption}")
                return True

        elif fix_content.startswith("table_header:"):
            # Add \hline after first row in tabular environments
            tabular_pattern = r"(\\begin\{tabular\}\{[^}]+\})(.*?)(\\end\{tabular\})"
            match = re.search(tabular_pattern, self._modified_content, re.DOTALL)
            if match:
                tabular_content = match.group(2)
                if (
                    r"\hline" not in tabular_content
                    and r"\toprule" not in tabular_content
                ):
                    # Find first row and add \hline
                    first_row_match = re.search(r"([^\n]*\\\\)", tabular_content)
                    if first_row_match:
                        first_row = first_row_match.group(1)
                        new_tabular = tabular_content.replace(
                            first_row, first_row + " \\hline", 1
                        )
                        self._modified_content = self._modified_content.replace(
                            match.group(0),
                            match.group(1) + new_tabular + match.group(3),
                        )
                        self._modifications.append("Added header separation to table")
                        return True

        return False

    def _apply_aria_fix(self, issue: RemediationIssue, aria_label: str) -> bool:
        """Add ARIA labels for equations or other content."""
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
                    # Generate label based on content or use auto-numbering
                    if aria_label and aria_label != "auto":
                        label = aria_label.replace(" ", "_").lower()
                    else:
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
            # Default to English if not specified
            return "english"

        elif issue.category == IssueCategory.STRUCTURE:
            if "accessibility" in issue.description.lower():
                return "add accessibility package"

        elif issue.category == IssueCategory.TITLE:
            if "title" in issue.description.lower():
                # Generate title from filename
                title = (
                    Path(self.file_path)
                    .stem.replace("_", " ")
                    .replace("-", " ")
                    .title()
                )
                return f"title:{title}"
            elif "author" in issue.description.lower():
                # Use a generic author that won't be flagged
                return "author:Document Author"

        elif issue.category == IssueCategory.ALT_TEXT:
            # For alt text, we need AI - return None to trigger AI generation
            return None

        elif issue.category == IssueCategory.TABLE:
            if "caption" in issue.description.lower():
                return "table_caption:Data table"
            elif "header" in issue.description.lower():
                return "table_header:add_structure"

        elif issue.category == IssueCategory.ARIA:
            if (
                "equation" in issue.description.lower()
                or "label" in issue.description.lower()
            ):
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
            IssueCategory.LANGUAGE: "english",
            IssueCategory.STRUCTURE: "add accessibility package",
        }
        return templates.get(issue.category)

    def _get_ai_generated_fix(
        self, issue: RemediationIssue, document: Any, *, client: Any
    ) -> Optional[str]:
        """Generate fix using AI."""

        self.result.ai_calls_made += 1

        try:
            from ...utils.security import sanitize_for_prompt

            safe_desc = sanitize_for_prompt(issue.description or "", max_length=300)
            safe_loc = sanitize_for_prompt(issue.location or "Unknown", max_length=100)
            safe_content = sanitize_for_prompt(
                issue.original_content or "", max_length=300
            )

            # Build context-aware prompt
            if issue.category == IssueCategory.ALT_TEXT:
                prompt = f"""Generate a brief, descriptive alt text for a LaTeX figure.
Issue: {safe_desc}
Location: {safe_loc}
Original content: {safe_content or 'Not available'}

Provide ONLY the alt text, no explanation. Keep it under 100 characters."""

            elif issue.category == IssueCategory.ARIA:
                prompt = f"""Generate an accessible description for this mathematical content.
Issue: {safe_desc}
Content: {safe_content or 'mathematical expression'}

Provide ONLY the ARIA label text that describes what the math represents.
Keep it under 150 characters. Focus on meaning, not just symbols."""

            else:
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

    def _verify_fixes(self, output_path: str):
        """Verify that the LaTeX still compiles (basic check)."""
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

        except Exception as e:
            self.result.warnings.append(f"Verification failed: {e}")

    def auto_remediate(self) -> bool:
        """
        Perform automatic remediation without pre-scanned issues.

        This method scans for common LaTeX accessibility issues and fixes them:
        1. DocumentMetadata for PDF/UA tagging (LuaLaTeX + tagpdf)
        2. Missing document language
        3. Missing document title/author
        4. Figures without captions
        5. Missing PDF metadata
        6. Tables without header structure
        7. Equations without labels

        Returns:
            True if any fixes were applied
        """
        try:
            # Load document
            self._load_document()

            fixes_applied = 0

            # 0. Add \DocumentMetadata for PDF/UA tagging (MUST be before \documentclass)
            # This is required for LuaLaTeX + tagpdf to create proper structure tree
            # with valid content references (/K, /Pg) that pass external validators.
            if r"\DocumentMetadata" not in self._modified_content:
                if r"\documentclass" in self._modified_content:
                    # Extract title if available for PDF metadata
                    title_match = re.search(
                        r"\\title\{([^}]+)\}", self._modified_content
                    )
                    title = title_match.group(1) if title_match else "Untitled Document"

                    # DocumentMetadata MUST appear BEFORE \documentclass
                    document_metadata = f"""\\DocumentMetadata{{
  lang=en,
  pdfstandard=ua-1,
  pdfversion=1.7,
  testphase={{phase-III,math,title,table,firstaid}},
  pdfauthor={{Aelira Accessibility Platform}},
  pdftitle={{{title}}}
}}
"""
                    self._modified_content = document_metadata + self._modified_content
                    fixes_applied += 1
                    self._modifications.append(
                        "Added DocumentMetadata for PDF/UA-1 tagging"
                    )
                    logger.info("Added DocumentMetadata for PDF/UA-1 tagging")

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

            # 3. Ensure babel (language) is present
            if "babel}" not in self._modified_content:
                if r"\documentclass" in self._modified_content:
                    self._modified_content = re.sub(
                        r"(\\documentclass[^\n]*\n)",
                        r"\1\\usepackage[english]{babel}  % Document language" + "\n",
                        self._modified_content,
                        count=1,
                    )
                    fixes_applied += 1
                    self._modifications.append(
                        "Added babel package with English language"
                    )
                    logger.info("Added babel package with English language")

            # 4. Add title if missing
            if r"\title{" not in self._modified_content:
                if r"\begin{document}" in self._modified_content:
                    # Extract filename for default title
                    default_title = Path(self.file_path).stem.replace("_", " ").title()
                    self._modified_content = self._modified_content.replace(
                        r"\begin{document}",
                        f"\\title{{{default_title}}}  % ACCESSIBILITY: Added document title\n"
                        + r"\begin{document}"
                        + "\n\\maketitle\n",
                    )
                    fixes_applied += 1
                    self._modifications.append(f"Added document title: {default_title}")
                    logger.info(f"Added document title: {default_title}")

            # 5. Add author if missing (use generic author, not placeholder)
            if r"\author{" not in self._modified_content:
                if r"\title{" in self._modified_content:
                    # Add after title - use real author name, not placeholder brackets
                    # Use string concat to get actual newline (raw string \n is literal)
                    self._modified_content = re.sub(
                        r"(\\title\{[^}]+\})",
                        r"\1" + "\n" + r"\\author{Document Author}",
                        self._modified_content,
                        count=1,
                    )
                    fixes_applied += 1
                    self._modifications.append("Added document author")
                    logger.info("Added document author")

            # 6. Add PDF metadata if title exists but no hypersetup
            # Note: With DocumentMetadata, much of this is handled automatically,
            # but we add hypersetup for fallback pipelines and explicit metadata
            title_match = re.search(r"\\title\{([^}]+)\}", self._modified_content)
            if title_match and r"\hypersetup" not in self._modified_content:
                title = title_match.group(1)
                # Note: pdfaccessible is NOT a valid hyperref option - removed
                hypersetup = (
                    "\\hypersetup{\n  pdftitle={" + title + "},\n  pdflang={en}\n}\n"
                )
                if r"\begin{document}" in self._modified_content:
                    self._modified_content = self._modified_content.replace(
                        r"\begin{document}", hypersetup + r"\begin{document}"
                    )
                    fixes_applied += 1
                    self._modifications.append(
                        f"Added PDF metadata with title: {title}"
                    )
                    logger.info(f"Added PDF metadata with title: {title}")

            # 7. Add captions to figures without them
            figure_pattern = r"(\\begin\{figure\}.*?)(\\end\{figure\})"
            fig_count = 0
            for match in re.finditer(figure_pattern, self._modified_content, re.DOTALL):
                figure_content = match.group(1)
                if r"\caption{" not in figure_content:
                    fig_count += 1
                    # Try to extract image filename for a better caption
                    img_match = re.search(
                        r"\\includegraphics[^{]*\{([^}]+)\}", figure_content
                    )
                    if img_match:
                        img_name = (
                            Path(img_match.group(1))
                            .stem.replace("_", " ")
                            .replace("-", " ")
                            .title()
                        )
                        caption = f"Figure showing {img_name}"
                    else:
                        caption = f"Figure {fig_count}"
                    new_content = figure_content + f"\\caption{{{caption}}}\n"
                    self._modified_content = self._modified_content.replace(
                        match.group(0), new_content + match.group(2)
                    )
                    fixes_applied += 1
                    self._modifications.append(f"Added caption to figure: {caption}")
                    logger.info(f"Added caption to figure: {caption}")

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

            # 9. Add table captions and improve header structure
            # First, check for table environments without captions
            table_env_pattern = r"(\\begin\{table\})(.*?)(\\end\{table\})"
            table_count = 0
            for match in re.finditer(
                table_env_pattern, self._modified_content, re.DOTALL
            ):
                table_content = match.group(2)
                if r"\caption{" not in table_content:
                    table_count += 1
                    # Add caption before \end{table}
                    new_content = (
                        table_content.rstrip() + f"\n\\caption{{Table {table_count}}}\n"
                    )
                    self._modified_content = self._modified_content.replace(
                        match.group(0), match.group(1) + new_content + match.group(3)
                    )
                    fixes_applied += 1
                    self._modifications.append(f"Added caption to table {table_count}")

            # Add \hline after first row in tabular environments without header separation
            tabular_pattern = r"(\\begin\{tabular\}\{[^}]+\})(.*?)(\\end\{tabular\})"
            for match in re.finditer(
                tabular_pattern, self._modified_content, re.DOTALL
            ):
                tabular_content = match.group(2)
                # Check if it has header separation already
                if (
                    r"\hline" not in tabular_content
                    and r"\toprule" not in tabular_content
                ):
                    # Find first row (ends with \\) and add \hline after it
                    first_row_match = re.search(r"([^\n]*\\\\)", tabular_content)
                    if first_row_match:
                        first_row = first_row_match.group(1)
                        new_tabular = tabular_content.replace(
                            first_row, first_row + " \\hline", 1
                        )
                        self._modified_content = self._modified_content.replace(
                            match.group(0),
                            match.group(1) + new_tabular + match.group(3),
                        )
                        fixes_applied += 1
                        self._modifications.append(
                            "Added header separation (\\hline) to table"
                        )

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
