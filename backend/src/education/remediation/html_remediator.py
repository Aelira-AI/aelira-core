"""
HTML/Website Auto-Remediator for Aelira Accessibility Platform.

This module provides automatic remediation for HTML, CSS, and JavaScript files,
focusing on:
1. Missing alt text for images
2. Form label associations
3. ARIA attributes for interactive elements
4. Heading hierarchy fixes
5. Color contrast in CSS
6. Skip navigation links
7. Language attributes

HTML remediation is CRITICAL for LMS content (Canvas, Blackboard pages).
"""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from .base import (
    BaseRemediator,
    IssueCategory,
    RemediationConfig,
    RemediationIssue,
    RemediationResult,
)

logger = logging.getLogger(__name__)


class HtmlRemediator(BaseRemediator):
    """
    Auto-remediator for HTML/CSS/JavaScript files.

    Fixes accessibility issues including:
    - Missing image alt text
    - Missing form labels
    - ARIA landmarks and attributes
    - Heading hierarchy
    - Skip navigation
    - Language attributes
    - Color contrast (CSS)

    LMS-critical: Canvas and Blackboard pages are HTML-based.
    """

    DOCUMENT_TYPE = "html"
    SUPPORTED_EXTENSIONS = [".html", ".htm", ".css", ".js"]

    AUTO_FIXABLE_CATEGORIES = [
        IssueCategory.ALT_TEXT,
        IssueCategory.HEADING,
        IssueCategory.ARIA,
        IssueCategory.FORM,
        IssueCategory.LANGUAGE,
        IssueCategory.NAVIGATION,
        IssueCategory.LINK,
        IssueCategory.CONTRAST,
    ]

    def __init__(
        self,
        file_path: str,
        issues: List[Dict[str, Any]],
        config: Optional[RemediationConfig] = None,
        ai_client: Optional[Any] = None,
    ):
        """Initialize HTML remediator."""
        super().__init__(file_path, issues, config, ai_client)

        # Determine file type
        self.file_ext = Path(file_path).suffix.lower()
        self.is_html = self.file_ext in [".html", ".htm"]
        self.is_css = self.file_ext == ".css"
        self.is_js = self.file_ext == ".js"

        # Store content
        self._original_content: Optional[str] = None
        self._soup: Optional[BeautifulSoup] = None
        self._modified_content: Optional[str] = None

        # Track modifications
        self._modifications: List[str] = []

    def _load_document(self) -> Any:
        """Load the HTML/CSS/JS document."""
        with open(self.file_path, "r", encoding="utf-8", errors="ignore") as f:
            self._original_content = f.read()

        if self.is_html:
            self._soup = BeautifulSoup(self._original_content, "html.parser")
            return self._soup
        else:
            self._modified_content = self._original_content
            return self._modified_content

    def _save_document(self, document: Any) -> str:
        """Save the remediated document."""
        output_path = self._get_output_path()

        # Ensure directory exists
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        if self.is_html and self._soup:
            # Preserve original formatting as much as possible
            content = str(self._soup)
        else:
            content = self._modified_content or self._original_content

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Saved remediated HTML to: {output_path}")
        return output_path

    def can_auto_fix(self, issue: RemediationIssue) -> bool:
        """
        Determine if an HTML issue can be automatically fixed.

        Args:
            issue: The issue to check

        Returns:
            True if the issue can be auto-fixed
        """
        if issue.category in self.AUTO_FIXABLE_CATEGORIES:
            if issue.category == IssueCategory.ALT_TEXT:
                # Can fix images with AI or placeholders
                return self.config.use_ai or "img" in issue.description.lower()

            elif issue.category == IssueCategory.HEADING:
                return self.is_html

            elif issue.category == IssueCategory.ARIA:
                return self.is_html

            elif issue.category == IssueCategory.FORM:
                return self.is_html and "label" in issue.description.lower()

            elif issue.category == IssueCategory.LANGUAGE:
                return self.is_html

            elif issue.category == IssueCategory.NAVIGATION:
                return self.is_html

            elif issue.category == IssueCategory.CONTRAST:
                return self.is_css or (
                    self.is_html and "style" in issue.description.lower()
                )

        return False

    def apply_fix(
        self, issue: RemediationIssue, document: Any, fix_content: str
    ) -> bool:
        """
        Apply a fix to the HTML/CSS document.

        Args:
            issue: The issue being fixed
            document: The document object (BeautifulSoup for HTML)
            fix_content: The fix to apply

        Returns:
            True if fix was applied successfully
        """
        try:
            if issue.category == IssueCategory.ALT_TEXT:
                return self._apply_alt_text_fix(issue, fix_content)

            elif issue.category == IssueCategory.HEADING:
                return self._apply_heading_fix(issue, fix_content)

            elif issue.category == IssueCategory.ARIA:
                return self._apply_aria_fix(issue, fix_content)

            elif issue.category == IssueCategory.FORM:
                return self._apply_form_fix(issue, fix_content)

            elif issue.category == IssueCategory.LANGUAGE:
                return self._apply_language_fix(fix_content)

            elif issue.category == IssueCategory.NAVIGATION:
                return self._apply_navigation_fix(issue, fix_content)

            elif issue.category == IssueCategory.CONTRAST:
                return self._apply_contrast_fix(issue, fix_content)

            else:
                logger.warning(f"No handler for category: {issue.category}")
                return False

        except Exception as e:
            logger.error(f"Failed to apply fix: {e}")
            return False

    def _apply_alt_text_fix(self, issue: RemediationIssue, alt_text: str) -> bool:
        """Add or fix alt text for images."""
        if not self._soup:
            return False

        # Find the image referenced in the issue
        images = self._soup.find_all("img")

        # Try to match by location or src
        for img in images:
            src = img.get("src", "")
            current_alt = img.get("alt")

            # Match by src in issue metadata or location
            if issue.location and src in issue.location:
                img["alt"] = alt_text
                self._modifications.append(f"Added alt text to image: {src}")
                return True

            # Match images without alt
            if current_alt is None and not img.get("role") == "presentation":
                img["alt"] = alt_text
                self._modifications.append(f"Added alt text to image: {src}")
                return True

        return False

    def _apply_heading_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Fix heading hierarchy issues."""
        if not self._soup:
            return False

        # Common fixes:
        # 1. Skip from H1 to H3 (missing H2)
        # 2. Multiple H1s
        # 3. No H1

        if (
            "skip" in issue.description.lower()
            or "hierarchy" in issue.description.lower()
        ):
            # Find headings
            headings = self._soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])

            if headings:
                # Check for skipped levels
                prev_level = 0
                for heading in headings:
                    level = int(heading.name[1])
                    if level > prev_level + 1 and prev_level > 0:
                        # Change this heading to the correct level
                        correct_level = prev_level + 1
                        new_tag = self._soup.new_tag(f"h{correct_level}")
                        new_tag.string = heading.get_text()
                        # Copy attributes
                        for attr, value in heading.attrs.items():
                            new_tag[attr] = value
                        heading.replace_with(new_tag)
                        self._modifications.append(
                            f"Changed {heading.name} to h{correct_level} (fixed skip)"
                        )
                        return True
                    prev_level = level

        return False

    def _apply_aria_fix(self, issue: RemediationIssue, aria_value: str) -> bool:
        """Add ARIA attributes to elements."""
        if not self._soup:
            return False

        # Common ARIA fixes:
        # 1. aria-label on buttons/links
        # 2. role attributes
        # 3. aria-describedby

        if "landmark" in issue.description.lower():
            # Add ARIA landmarks
            main = self._soup.find("main")
            if not main:
                # Find the main content area
                content = self._soup.find(class_=re.compile(r"content|main|body"))
                if content:
                    content["role"] = "main"
                    self._modifications.append("Added role='main' to content area")
                    return True

        if "button" in issue.description.lower() or "link" in issue.description.lower():
            # Find buttons/links without accessible names
            elements = self._soup.find_all(["button", "a"])
            for elem in elements:
                if not elem.get_text().strip() and not elem.get("aria-label"):
                    elem["aria-label"] = aria_value
                    self._modifications.append(f"Added aria-label to {elem.name}")
                    return True

        return False

    def _apply_form_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Fix form accessibility issues (labels, etc.)."""
        if not self._soup:
            return False

        # Find inputs without associated labels
        inputs = self._soup.find_all(["input", "select", "textarea"])

        for inp in inputs:
            input_id = inp.get("id")
            input_type = inp.get("type", "text")

            # Skip hidden and submit inputs
            if input_type in ["hidden", "submit", "button"]:
                continue

            # Check if there's a label for this input
            if input_id:
                label = self._soup.find("label", {"for": input_id})
                if not label:
                    # Create a label
                    new_label = self._soup.new_tag("label")
                    new_label["for"] = input_id
                    new_label.string = fix_content or f"Enter {input_type}"
                    inp.insert_before(new_label)
                    self._modifications.append(f"Added label for input #{input_id}")
                    return True
            else:
                # Add an ID and create label
                new_id = f"input_{len(inputs)}"
                inp["id"] = new_id
                new_label = self._soup.new_tag("label")
                new_label["for"] = new_id
                new_label.string = fix_content or f"Enter {input_type}"
                inp.insert_before(new_label)
                self._modifications.append("Added ID and label for unlabeled input")
                return True

        return False

    def _apply_language_fix(self, lang: str) -> bool:
        """Add or fix document language attribute."""
        if not self._soup:
            return False

        html_tag = self._soup.find("html")
        if html_tag:
            html_tag["lang"] = lang
            self._modifications.append(f"Set html lang='{lang}'")
            return True

        return False

    def _apply_navigation_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Add skip navigation link or fix navigation issues."""
        if not self._soup:
            return False

        if "skip" in issue.description.lower():
            # Check if skip link exists
            skip_link = self._soup.find("a", class_=re.compile(r"skip"))
            if not skip_link:
                # Find body
                body = self._soup.find("body")
                if body:
                    # Create skip link
                    skip_link = self._soup.new_tag("a")
                    skip_link["href"] = "#main-content"
                    skip_link["class"] = "skip-nav"
                    skip_link.string = "Skip to main content"

                    # Insert at beginning of body
                    body.insert(0, skip_link)

                    # Find or create main content anchor
                    main = self._soup.find("main") or self._soup.find(role="main")
                    if main:
                        main["id"] = "main-content"

                    self._modifications.append("Added skip navigation link")
                    return True

        return False

    def _apply_contrast_fix(self, issue: RemediationIssue, fix_content: str) -> bool:
        """Fix color contrast issues in CSS or inline styles."""
        if self.is_css:
            # Add a comment noting the issue (not a structural fix)
            comment = f"\n/* ACCESSIBILITY: {issue.description} - {fix_content} */\n"
            self._modified_content = comment + (self._modified_content or "")
            self._modifications.append("Added contrast fix comment to CSS")
            return False  # Comment does not fix the contrast violation

        elif self._soup:
            # Find elements with style attributes containing color
            styled_elements = self._soup.find_all(style=re.compile(r"color|background"))
            if styled_elements:
                self._modifications.append("Noted contrast issues in styled elements")
                return False  # No actual color values were changed

        return False

    def _get_rule_based_fix(
        self, issue: RemediationIssue, document: Any
    ) -> Optional[str]:
        """Get rule-based fixes for HTML issues."""
        if issue.category == IssueCategory.LANGUAGE:
            return "en"  # Default to English

        elif issue.category == IssueCategory.NAVIGATION:
            return "Skip to main content"

        elif issue.category == IssueCategory.FORM:
            # Extract field type from description
            if "email" in issue.description.lower():
                return "Email Address"
            elif "password" in issue.description.lower():
                return "Password"
            elif "name" in issue.description.lower():
                return "Name"
            return "Input Field"

        return None

    def _get_template_fix(self, issue: RemediationIssue) -> Optional[str]:
        """Get template-based fixes for HTML issues."""
        templates = {
            IssueCategory.LANGUAGE: "en",
            IssueCategory.NAVIGATION: "Skip to main content",
            IssueCategory.ARIA: "Interactive element",
        }
        return templates.get(issue.category)

    def _get_ai_generated_fix(
        self, issue: RemediationIssue, document: Any
    ) -> Optional[str]:
        """Generate fix using AI."""
        if not self.ai_client:
            return None

        self.result.ai_calls_made += 1

        try:
            from ...utils.security import sanitize_for_prompt

            safe_desc = sanitize_for_prompt(issue.description or "", max_length=300)
            safe_content = (
                sanitize_for_prompt(issue.original_content or "", max_length=300)
                or "Not available"
            )

            if issue.category == IssueCategory.ALT_TEXT:
                # Get image context
                safe_location = (
                    sanitize_for_prompt(issue.location or "", max_length=100)
                    or "Unknown"
                )
                prompt = f"""Generate a brief, descriptive alt text for an HTML image.
Issue: {safe_desc}
Location: {safe_location}
Code snippet: {safe_content}

Provide ONLY the alt text, no explanation. Keep it under 125 characters.
If the image is purely decorative, respond with exactly: DECORATIVE"""

            elif issue.category == IssueCategory.ARIA:
                prompt = f"""Suggest an appropriate aria-label for this element.
Issue: {safe_desc}
Element type: {issue.element_type or 'Unknown'}
Code snippet: {safe_content}

Provide ONLY the aria-label value, no explanation. Keep it under 50 characters."""

            elif issue.category == IssueCategory.FORM:
                prompt = f"""Generate a clear, accessible label for this form field.
Issue: {safe_desc}
Element type: {issue.element_type or 'input'}
Code snippet: {safe_content}

Provide ONLY the label text, no explanation. Keep it under 30 characters."""

            else:
                prompt = f"""Suggest a fix for this HTML accessibility issue:
Issue: {safe_desc}
Category: {issue.category.value}
Code snippet: {safe_content}

Provide ONLY the fix content, no explanation."""

            result = self.ai_client.generate_text_sync(
                prompt=prompt, max_tokens=150, temperature=0.3
            )

            if result.get("success"):
                fix = result.get("content", "").strip()
                if fix == "DECORATIVE":
                    return ""  # Empty alt for decorative images
                return fix

        except Exception as e:
            logger.warning(f"AI fix generation failed: {e}")

        return None

    def _verify_fixes(self, output_path: str):
        """Verify that fixes were applied and HTML is valid."""
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()

            if self.is_html:
                # Check for balanced tags
                soup = BeautifulSoup(content, "html.parser")

                # Check for common issues
                issues = []

                # Check lang attribute
                html_tag = soup.find("html")
                if html_tag and not html_tag.get("lang"):
                    issues.append("Missing lang attribute on html element")

                # Check for images without alt
                for img in soup.find_all("img"):
                    if not img.get("alt") and img.get("role") != "presentation":
                        issues.append(f"Image missing alt: {img.get('src', 'unknown')}")

                if issues:
                    self.result.warnings.extend(issues[:5])  # Limit warnings

        except Exception as e:
            self.result.warnings.append(f"Verification failed: {e}")

    def auto_remediate(self) -> bool:
        """
        Perform automatic remediation without pre-scanned issues.

        This method scans for common HTML accessibility issues and fixes them:
        1. Missing lang attribute
        2. Images without alt text (adds placeholder)
        3. Missing skip navigation
        4. Form inputs without labels

        Returns:
            True if any fixes were applied
        """
        try:
            # Load document
            self._load_document()

            if not self._soup:
                return False

            fixes_applied = 0

            # 1. Ensure lang attribute exists
            html_tag = self._soup.find("html")
            if html_tag and not html_tag.get("lang"):
                html_tag["lang"] = "en"
                fixes_applied += 1
                logger.info("Added lang='en' to html element")

            # 2. Add placeholder alt to images without alt
            for img in self._soup.find_all("img"):
                if img.get("alt") is None and img.get("role") != "presentation":
                    src = img.get("src", "image")
                    filename = Path(src).stem if src else "image"
                    img["alt"] = f"Image: {filename} (needs description)"
                    fixes_applied += 1
                    logger.info(f"Added placeholder alt to image: {src}")

            # 3. Add skip navigation if not present
            body = self._soup.find("body")
            skip_link = self._soup.find("a", href=re.compile(r"#(main|content|skip)"))
            if body and not skip_link:
                skip = self._soup.new_tag("a")
                skip["href"] = "#main-content"
                skip["class"] = "skip-nav sr-only"
                skip.string = "Skip to main content"
                body.insert(0, skip)

                # Add ID to main content
                main = self._soup.find("main")
                if main:
                    main["id"] = "main-content"
                elif self._soup.find(class_=re.compile(r"content|main")):
                    self._soup.find(class_=re.compile(r"content|main"))["id"] = (
                        "main-content"
                    )

                fixes_applied += 1
                logger.info("Added skip navigation link")

            # 4. Add labels to unlabeled form inputs
            for inp in self._soup.find_all(["input", "select", "textarea"]):
                input_type = inp.get("type", "text")
                if input_type in ["hidden", "submit", "button", "reset"]:
                    continue

                input_id = inp.get("id")
                if input_id:
                    label = self._soup.find("label", {"for": input_id})
                    if not label:
                        # Create label
                        label = self._soup.new_tag("label")
                        label["for"] = input_id
                        label.string = inp.get(
                            "placeholder", f"{input_type.title()} field"
                        )
                        inp.insert_before(label)
                        fixes_applied += 1
                        logger.info(f"Added label for input: {input_id}")

            # Save if any fixes were applied
            if fixes_applied > 0:
                output_path = self._get_output_path()
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(str(self._soup))

                self.result.output_file = output_path
                self.result.fixed_count = fixes_applied
                logger.info(f"Applied {fixes_applied} automatic fixes to {output_path}")
                return True

            return False

        except Exception as e:
            logger.error(f"Auto-remediation failed: {e}")
            self.result.error_message = str(e)
            return False


# Convenience function for direct remediation
def remediate_html(
    file_path: str,
    issues: Optional[List[Dict[str, Any]]] = None,
    config: Optional[RemediationConfig] = None,
    ai_client: Optional[Any] = None,
) -> "RemediationResult":
    """
    Remediate an HTML/CSS/JS file.

    Args:
        file_path: Path to the file
        issues: List of issues from scanning (optional for auto-remediation)
        config: Remediation configuration
        ai_client: AI client for generating fixes

    Returns:
        RemediationResult with fixed and manual issues
    """

    remediator = HtmlRemediator(
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
