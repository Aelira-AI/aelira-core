"""
Code Scanner Module - Scan HTML/CSS/JS files for accessibility issues

This module analyzes uploaded website code (HTML, CSS, JavaScript) for WCAG 2.2
accessibility compliance without requiring the site to be deployed.

Features:
- HTML structure analysis (semantic markup, ARIA)
- CSS analysis (color contrast, font sizes, focus indicators)
- JavaScript analysis (keyboard navigation, dynamic content)
- Image detection and alt text validation
- Form accessibility checking
- Heading hierarchy validation
- AI-powered code fixes using Qwen Coder
"""

import os
import tempfile
import zipfile
from pathlib import Path
from typing import List, Dict, Optional, Any
from pydantic import BaseModel
from bs4 import BeautifulSoup
import cssutils
import re
import logging

from src.ai.providers import get_provider_manager
from src.education.color_blindness_simulator import (
    ColorBlindnessSimulator,
    ColorBlindnessAnalysisResult,
)

# Configure cssutils to suppress warnings
cssutils.log.setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


class CodeIssue(BaseModel):
    """Represents a single accessibility issue in code"""

    severity: str  # critical, serious, moderate, minor
    category: str  # html, css, javascript, aria
    rule: str  # WCAG criterion or rule ID
    description: str
    file_path: str
    line_number: Optional[int] = None
    code_snippet: Optional[str] = None
    fix_suggestion: str
    ai_generated_fix: Optional[str] = None
    wcag_criterion: str  # e.g., "1.1.1", "1.4.3"


class ImageInCode(BaseModel):
    """Represents an image found in code"""

    src: str
    alt: Optional[str] = None
    has_alt: bool
    is_decorative: bool
    file_path: str
    suggested_alt: Optional[str] = None
    # Alt text validation fields
    alt_text_validated: bool = False
    alt_text_accurate: Optional[bool] = None
    validation_issues: Optional[List[str]] = None
    validation_reasoning: Optional[str] = None


class CodeScanResult(BaseModel):
    """Complete results from code scanning"""

    project_name: str
    files_analyzed: int
    total_lines: int
    issues: List[CodeIssue]
    images: List[ImageInCode]
    summary: Dict[str, int]
    compliance_score: float
    recommendations: List[str]
    scan_time: float
    # Color vision deficiency analysis from CSS
    cvd_analysis: Optional[List[ColorBlindnessAnalysisResult]] = None


class CodeScanner:
    """Scans uploaded website code for accessibility issues"""

    def __init__(
        self,
        scan_images: bool = False,
        generate_fixes: bool = True,
        validate_alt_text: bool = False,
        scan_cvd: bool = False,
        progress_callback: callable = None,
    ):
        """
        Initialize code scanner

        Args:
            scan_images: Whether to analyze images with AI
            generate_fixes: Whether to generate AI code fixes
            validate_alt_text: Whether to validate existing alt text accuracy
            scan_cvd: Whether to analyze colors for CVD accessibility
            progress_callback: Optional callback function(current, total, message) for progress updates
        """
        self.scan_images = scan_images
        self.generate_fixes = generate_fixes
        self.validate_alt_text = validate_alt_text
        self.scan_cvd = scan_cvd
        self.progress_callback = progress_callback
        self.llm_client = get_provider_manager()
        # Initialize CVD simulator if enabled
        self.cvd_simulator = ColorBlindnessSimulator() if scan_cvd else None

    def scan_uploaded_code(
        self, file_path: str, project_name: Optional[str] = None
    ) -> CodeScanResult:
        """
        Scan uploaded code file (zip, html, css, js)

        Args:
            file_path: Path to uploaded file
            project_name: Name of the project

        Returns:
            CodeScanResult with all findings
        """
        import time

        start_time = time.time()

        # Determine if it's a zip or single file
        file_ext = Path(file_path).suffix.lower()

        if file_ext == ".zip":
            # Extract zip to temp directory
            with tempfile.TemporaryDirectory() as temp_dir:
                with zipfile.ZipFile(file_path, "r") as zip_ref:
                    zip_ref.extractall(temp_dir)

                result = self._scan_directory(
                    temp_dir, project_name or "Uploaded Project"
                )
        else:
            # Single file
            result = self._scan_single_file(
                file_path, project_name or Path(file_path).name
            )

        result.scan_time = time.time() - start_time
        return result

    def _scan_directory(self, directory: str, project_name: str) -> CodeScanResult:
        """Scan all code files in a directory"""
        issues = []
        images = []
        total_lines = 0
        files_analyzed = 0
        file_contexts = {}  # Store page context per HTML file

        # Count files first for progress tracking
        all_files = []
        for root, dirs, files in os.walk(directory):
            for file in files:
                file_path = Path(root) / file
                ext = file_path.suffix.lower()
                if ext in [".html", ".htm", ".css", ".js"]:
                    all_files.append(file_path)

        total_files = len(all_files)

        if self.progress_callback:
            self.progress_callback(
                0, total_files + 2, f"Found {total_files} files to scan..."
            )

        # Scan all HTML, CSS, JS files
        for idx, file_path in enumerate(all_files):
            ext = file_path.suffix.lower()
            files_analyzed += 1

            if self.progress_callback:
                self.progress_callback(
                    idx + 1,
                    total_files + 2,
                    f"Scanning {file_path.name} ({idx + 1} of {total_files})...",
                )

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                total_lines += len(content.split("\n"))

            relative_path = str(file_path.relative_to(directory))

            if ext in [".html", ".htm"]:
                html_issues, html_images, page_context = self._scan_html(
                    content, relative_path
                )
                issues.extend(html_issues)
                images.extend(html_images)
                file_contexts[relative_path] = (
                    page_context  # Store context for AI fixes
                )
            elif ext == ".css":
                css_issues = self._scan_css(content, relative_path)
                issues.extend(css_issues)
            elif ext == ".js":
                js_issues = self._scan_javascript(content, relative_path)
                issues.extend(js_issues)

        # Generate AI fixes if requested - with context
        if self.generate_fixes:
            if self.progress_callback:
                self.progress_callback(
                    total_files + 1,
                    total_files + 2,
                    "Generating AI-powered fix suggestions...",
                )
            for issue in issues:
                if issue.severity in ["critical", "serious"]:
                    # Get context for this file if available
                    page_context = file_contexts.get(issue.file_path)
                    issue.ai_generated_fix = self._generate_code_fix(
                        issue.description,
                        issue.code_snippet or "",
                        issue.file_path,
                        issue.category,
                        rule=issue.rule,
                        page_context=page_context,
                    )

        # Calculate summary and score
        summary = {
            "critical": sum(1 for i in issues if i.severity == "critical"),
            "serious": sum(1 for i in issues if i.severity == "serious"),
            "moderate": sum(1 for i in issues if i.severity == "moderate"),
            "minor": sum(1 for i in issues if i.severity == "minor"),
        }

        compliance_score = self._calculate_compliance_score(summary, files_analyzed)
        recommendations = self._generate_recommendations(issues, images)

        # Perform CVD analysis on CSS color pairs if enabled
        cvd_analysis = None
        if self.scan_cvd and self.cvd_simulator:
            cvd_analysis = self._analyze_cvd_from_directory(directory)

        return CodeScanResult(
            project_name=project_name,
            files_analyzed=files_analyzed,
            total_lines=total_lines,
            issues=issues,
            images=images,
            summary=summary,
            compliance_score=compliance_score,
            recommendations=recommendations,
            scan_time=0,  # Will be set by caller
            cvd_analysis=cvd_analysis,
        )

    def _scan_single_file(self, file_path: str, project_name: str) -> CodeScanResult:
        """Scan a single code file"""
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        ext = Path(file_path).suffix.lower()
        issues = []
        images = []
        page_context = None

        if ext in [".html", ".htm"]:
            issues, images, page_context = self._scan_html(
                content, Path(file_path).name
            )
        elif ext == ".css":
            issues = self._scan_css(content, Path(file_path).name)
        elif ext == ".js":
            issues = self._scan_javascript(content, Path(file_path).name)

        # Generate AI fixes if requested - with context
        if self.generate_fixes:
            for issue in issues:
                if issue.severity in ["critical", "serious"]:
                    issue.ai_generated_fix = self._generate_code_fix(
                        issue.description,
                        issue.code_snippet or "",
                        issue.file_path,
                        issue.category,
                        rule=issue.rule,
                        page_context=page_context,
                    )

        summary = {
            "critical": sum(1 for i in issues if i.severity == "critical"),
            "serious": sum(1 for i in issues if i.severity == "serious"),
            "moderate": sum(1 for i in issues if i.severity == "moderate"),
            "minor": sum(1 for i in issues if i.severity == "minor"),
        }

        compliance_score = self._calculate_compliance_score(summary, 1)
        recommendations = self._generate_recommendations(issues, images)

        # Perform CVD analysis if enabled and this is a CSS file
        cvd_analysis = None
        if self.scan_cvd and self.cvd_simulator and ext == ".css":
            cvd_analysis = self._analyze_cvd_from_css(content)

        return CodeScanResult(
            project_name=project_name,
            files_analyzed=1,
            total_lines=len(content.split("\n")),
            issues=issues,
            images=images,
            summary=summary,
            compliance_score=compliance_score,
            recommendations=recommendations,
            scan_time=0,
            cvd_analysis=cvd_analysis,
        )

    def _scan_html(self, html_content: str, file_path: str) -> tuple:
        """Scan HTML file for accessibility issues"""
        issues = []
        images = []

        soup = BeautifulSoup(html_content, "html.parser")

        # Check for lang attribute
        html_tag = soup.find("html")
        if not html_tag or not html_tag.get("lang"):
            issues.append(
                CodeIssue(
                    severity="serious",
                    category="html",
                    rule="lang-attribute",
                    description="HTML element must have a lang attribute",
                    file_path=file_path,
                    fix_suggestion='Add lang="en" (or appropriate language code) to <html> tag',
                    wcag_criterion="3.1.1",
                )
            )

        # Check for page title
        title = soup.find("title")
        if not title or not title.string or not title.string.strip():
            issues.append(
                CodeIssue(
                    severity="serious",
                    category="html",
                    rule="page-title",
                    description="Page must have a descriptive title",
                    file_path=file_path,
                    fix_suggestion="Add a <title> element with descriptive text",
                    wcag_criterion="2.4.2",
                )
            )

        # Check images for alt text
        for img in soup.find_all("img"):
            src = img.get("src", "")
            alt = img.get("alt")
            has_alt = alt is not None

            # Determine if decorative
            is_decorative = (
                alt == ""
                or "decorative" in img.get("class", [])
                or img.get("role") == "presentation"
            )

            image_info = ImageInCode(
                src=src,
                alt=alt,
                has_alt=has_alt,
                is_decorative=is_decorative,
                file_path=file_path,
            )

            # Validate existing alt text if enabled
            if self.validate_alt_text and has_alt and alt and not is_decorative:
                validation_result = self._validate_alt_text_quality(alt, src)
                image_info.alt_text_validated = True
                image_info.alt_text_accurate = validation_result.get("is_valid", True)
                image_info.validation_issues = validation_result.get("issues", [])
                image_info.validation_reasoning = validation_result.get("reasoning")

                # Add issue if alt text is problematic
                if not validation_result.get("is_valid", True):
                    issues.append(
                        CodeIssue(
                            severity="moderate",
                            category="html",
                            rule="image-alt-quality",
                            description=f'Alt text quality issue for {src}: {", ".join(validation_result.get("issues", []))}',
                            file_path=file_path,
                            code_snippet=str(img),
                            fix_suggestion=validation_result.get(
                                "suggested_improvement",
                                "Improve alt text to be more descriptive",
                            ),
                            wcag_criterion="1.1.1",
                        )
                    )

            images.append(image_info)

            if not has_alt and not is_decorative:
                issues.append(
                    CodeIssue(
                        severity="critical",
                        category="html",
                        rule="image-alt",
                        description=f"Image missing alt text: {src}",
                        file_path=file_path,
                        code_snippet=str(img),
                        fix_suggestion='Add descriptive alt text: alt="description of image"',
                        wcag_criterion="1.1.1",
                    )
                )

        # Check form labels
        for input_elem in soup.find_all(["input", "textarea", "select"]):
            input_id = input_elem.get("id")
            input_type = input_elem.get("type", "text")

            if input_type in ["submit", "button", "reset", "hidden"]:
                continue

            # Check for label
            label = soup.find("label", attrs={"for": input_id}) if input_id else None
            aria_label = input_elem.get("aria-label")
            aria_labelledby = input_elem.get("aria-labelledby")

            if not label and not aria_label and not aria_labelledby:
                issues.append(
                    CodeIssue(
                        severity="serious",
                        category="html",
                        rule="form-label",
                        description=f'Form input missing label: {input_elem.get("name", "unnamed")}',
                        file_path=file_path,
                        code_snippet=str(input_elem),
                        fix_suggestion='Add <label for="input-id"> or aria-label attribute',
                        wcag_criterion="3.3.2",
                    )
                )

        # Check heading hierarchy
        headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        if headings:
            prev_level = 0
            for heading in headings:
                level = int(heading.name[1])

                if prev_level > 0 and level > prev_level + 1:
                    issues.append(
                        CodeIssue(
                            severity="moderate",
                            category="html",
                            rule="heading-hierarchy",
                            description=f"Heading hierarchy skipped level (from h{prev_level} to h{level})",
                            file_path=file_path,
                            code_snippet=str(heading),
                            fix_suggestion="Use sequential heading levels (h1, h2, h3, etc.)",
                            wcag_criterion="2.4.6",
                        )
                    )

                prev_level = level

        # Check for semantic landmarks
        has_main = soup.find(["main", "div"], attrs={"role": "main"})
        soup.find(["nav", "div"], attrs={"role": "navigation"})

        if not has_main:
            issues.append(
                CodeIssue(
                    severity="moderate",
                    category="aria",
                    rule="landmark-main",
                    description="Page should have a main landmark",
                    file_path=file_path,
                    fix_suggestion='Add <main> element or role="main"',
                    wcag_criterion="1.3.1",
                )
            )

        # Check for ARIA usage
        for elem in soup.find_all(attrs={"role": True}):
            role = elem.get("role")

            # Check button role has keyboard support
            if role == "button" and elem.name not in ["button", "input"]:
                if not elem.get("tabindex"):
                    issues.append(
                        CodeIssue(
                            severity="serious",
                            category="aria",
                            rule="button-keyboard",
                            description='Element with role="button" must be keyboard accessible',
                            file_path=file_path,
                            code_snippet=str(elem),
                            fix_suggestion='Add tabindex="0" to make element keyboard accessible',
                            wcag_criterion="2.1.1",
                        )
                    )

        # Extract page context for AI fixes
        page_context = self._extract_page_context(soup)

        return issues, images, page_context

    def _extract_page_context(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """
        Extract comprehensive page context from BeautifulSoup parsed HTML
        for providing to AI when generating context-aware fixes.
        """
        return {
            "heading_hierarchy": self._extract_heading_hierarchy_bs(soup),
            "landmarks": self._extract_landmark_structure_bs(soup),
            "forms": self._extract_form_structure_bs(soup),
            "lists": self._extract_list_structure_bs(soup),
            "tables": self._extract_table_structure_bs(soup),
        }

    def _extract_heading_hierarchy_bs(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract heading hierarchy from BeautifulSoup parsed HTML"""
        headings = []
        for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            level = int(heading.name[1])
            text = heading.get_text(strip=True)[:100]
            headings.append(
                {
                    "level": level,
                    "tag": heading.name,
                    "text": text or "[empty heading]",
                    "id": heading.get("id"),
                    "classes": heading.get("class", []),
                }
            )
        return headings

    def _extract_landmark_structure_bs(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract ARIA landmarks from BeautifulSoup parsed HTML"""
        landmarks = []

        # Native landmark elements
        landmark_elements = {
            "main": "main",
            "nav": "navigation",
            "header": "banner",
            "footer": "contentinfo",
            "aside": "complementary",
            "section": "region",
            "article": "article",
            "form": "form",
        }

        for tag, role in landmark_elements.items():
            for elem in soup.find_all(tag):
                landmarks.append(
                    {
                        "tag": tag,
                        "role": elem.get("role", role),
                        "label": elem.get("aria-label") or elem.get("aria-labelledby"),
                        "id": elem.get("id"),
                    }
                )

        # Elements with explicit role
        for elem in soup.find_all(attrs={"role": True}):
            role = elem.get("role")
            if role in [
                "main",
                "navigation",
                "banner",
                "contentinfo",
                "complementary",
                "region",
                "search",
                "form",
            ]:
                if elem.name not in landmark_elements:  # Avoid duplicates
                    landmarks.append(
                        {
                            "tag": elem.name,
                            "role": role,
                            "label": elem.get("aria-label")
                            or elem.get("aria-labelledby"),
                            "id": elem.get("id"),
                        }
                    )

        return landmarks

    def _extract_form_structure_bs(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract form structure from BeautifulSoup parsed HTML"""
        forms = []

        for form in soup.find_all("form"):
            form_info = {
                "id": form.get("id"),
                "name": form.get("name"),
                "action": form.get("action"),
                "inputs": [],
            }

            for input_elem in form.find_all(["input", "textarea", "select"]):
                input_id = input_elem.get("id")
                input_name = input_elem.get("name")
                input_type = input_elem.get("type", "text")

                # Check for associated label
                label = None
                if input_id:
                    label_elem = soup.find("label", attrs={"for": input_id})
                    if label_elem:
                        label = label_elem.get_text(strip=True)

                aria_label = input_elem.get("aria-label")
                aria_labelledby = input_elem.get("aria-labelledby")

                form_info["inputs"].append(
                    {
                        "type": input_type,
                        "id": input_id,
                        "name": input_name,
                        "has_label": label is not None,
                        "label_text": label,
                        "has_aria_label": aria_label is not None,
                        "has_aria_labelledby": aria_labelledby is not None,
                        "required": input_elem.has_attr("required"),
                    }
                )

            forms.append(form_info)

        return forms

    def _extract_list_structure_bs(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract list structure from BeautifulSoup parsed HTML"""
        lists = []

        for list_elem in soup.find_all(["ul", "ol", "dl"]):
            list_info = {
                "type": list_elem.name,
                "id": list_elem.get("id"),
                "item_count": 0,
                "nested": False,
                "invalid_children": [],
            }

            if list_elem.name in ["ul", "ol"]:
                items = list_elem.find_all("li", recursive=False)
                list_info["item_count"] = len(items)

                # Check for invalid direct children
                for child in list_elem.children:
                    if hasattr(child, "name") and child.name and child.name != "li":
                        list_info["invalid_children"].append(child.name)

                # Check for nested lists
                if list_elem.find(["ul", "ol"]):
                    list_info["nested"] = True
            else:  # dl
                list_info["item_count"] = len(
                    list_elem.find_all(["dt", "dd"], recursive=False)
                )

            lists.append(list_info)

        return lists

    def _extract_table_structure_bs(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract table structure from BeautifulSoup parsed HTML"""
        tables = []

        for table in soup.find_all("table"):
            table_info = {
                "id": table.get("id"),
                "has_caption": table.find("caption") is not None,
                "caption_text": None,
                "row_count": 0,
                "col_count": 0,
                "has_thead": table.find("thead") is not None,
                "has_th": len(table.find_all("th")) > 0,
                "th_scope_usage": [],
                "has_headers_attr": False,
            }

            caption = table.find("caption")
            if caption:
                table_info["caption_text"] = caption.get_text(strip=True)[:50]

            rows = table.find_all("tr")
            table_info["row_count"] = len(rows)
            if rows:
                first_row = rows[0]
                cells = first_row.find_all(["td", "th"])
                table_info["col_count"] = len(cells)

            # Check th scope usage
            for th in table.find_all("th"):
                scope = th.get("scope")
                if scope:
                    table_info["th_scope_usage"].append(scope)

            # Check for headers attribute on td
            for td in table.find_all("td"):
                if td.get("headers"):
                    table_info["has_headers_attr"] = True
                    break

            tables.append(table_info)

        return tables

    def _build_context_for_issue(
        self, rule: str, code_snippet: str, page_context: Dict
    ) -> str:
        """
        Build relevant context string for an issue based on its rule type.
        Maps issue rules to the appropriate page context.
        """
        context_parts = []

        # Heading-related issues
        heading_rules = {"heading-hierarchy", "page-title", "h1-present"}
        if rule in heading_rules and page_context.get("heading_hierarchy"):
            headings = page_context["heading_hierarchy"]
            if headings:
                context_parts.append("Current heading hierarchy in document:")
                for h in headings:
                    indent = "  " * (h["level"] - 1)
                    context_parts.append(f"  {indent}{h['tag']}: \"{h['text']}\"")
                context_parts.append("")

        # Landmark-related issues
        landmark_rules = {"landmark-main", "landmark-navigation", "bypass", "region"}
        if rule in landmark_rules and page_context.get("landmarks"):
            landmarks = page_context["landmarks"]
            if landmarks:
                context_parts.append("Current landmarks in document:")
                for lm in landmarks:
                    label = f" (label: {lm['label']})" if lm.get("label") else ""
                    context_parts.append(f"  - <{lm['tag']}> role={lm['role']}{label}")
                context_parts.append("")

        # Form-related issues
        form_rules = {"form-label", "label", "input-button-name", "select-name"}
        if rule in form_rules and page_context.get("forms"):
            forms = page_context["forms"]
            if forms:
                context_parts.append("Form structure in document:")
                for form in forms:
                    form_id = form.get("id") or form.get("name") or "unnamed"
                    context_parts.append(f"  Form '{form_id}':")
                    for inp in form.get("inputs", [])[:10]:  # Limit to 10 inputs
                        has_label = (
                            "✓"
                            if inp.get("has_label") or inp.get("has_aria_label")
                            else "✗"
                        )
                        context_parts.append(
                            f"    - {inp['type']} '{inp.get('name', 'unnamed')}' label: {has_label}"
                        )
                context_parts.append("")

        # List-related issues
        list_rules = {"list", "listitem"}
        if rule in list_rules and page_context.get("lists"):
            lists = page_context["lists"]
            if lists:
                context_parts.append("List structure in document:")
                for lst in lists[:5]:  # Limit to 5 lists
                    invalid = (
                        f" (invalid children: {', '.join(lst['invalid_children'])})"
                        if lst.get("invalid_children")
                        else ""
                    )
                    context_parts.append(
                        f"  - <{lst['type']}> with {lst['item_count']} items{invalid}"
                    )
                context_parts.append("")

        # Table-related issues
        table_rules = {
            "td-headers-attr",
            "th-has-data-cells",
            "table-fake-caption",
            "scope-attr-valid",
        }
        if rule in table_rules and page_context.get("tables"):
            tables = page_context["tables"]
            if tables:
                context_parts.append("Table structure in document:")
                for tbl in tables[:3]:  # Limit to 3 tables
                    caption = (
                        f" caption='{tbl['caption_text']}'"
                        if tbl.get("caption_text")
                        else " (no caption)"
                    )
                    headers = (
                        " has-headers" if tbl.get("has_th") else " (no th headers)"
                    )
                    context_parts.append(
                        f"  - Table ({tbl['row_count']}x{tbl['col_count']}){caption}{headers}"
                    )
                context_parts.append("")

        return "\n".join(context_parts) if context_parts else ""

    def _scan_css(self, css_content: str, file_path: str) -> List[CodeIssue]:
        """Scan CSS file for accessibility issues"""
        issues = []

        try:
            sheet = cssutils.parseString(css_content)

            for rule in sheet:
                if rule.type == rule.STYLE_RULE:
                    style = rule.style

                    # Check for focus indicators
                    if ":focus" in rule.selectorText:
                        outline = style.getPropertyValue("outline")
                        if outline == "none" or outline == "0":
                            issues.append(
                                CodeIssue(
                                    severity="serious",
                                    category="css",
                                    rule="focus-indicator",
                                    description="Focus indicator removed (outline: none)",
                                    file_path=file_path,
                                    code_snippet=rule.cssText,
                                    fix_suggestion="Provide alternative visible focus indicator",
                                    wcag_criterion="2.4.7",
                                )
                            )

                    # Check font sizes
                    font_size = style.getPropertyValue("font-size")
                    if font_size and "px" in font_size:
                        size_val = (
                            int(re.findall(r"\d+", font_size)[0])
                            if re.findall(r"\d+", font_size)
                            else 0
                        )
                        if size_val < 12:
                            issues.append(
                                CodeIssue(
                                    severity="moderate",
                                    category="css",
                                    rule="font-size",
                                    description=f"Font size too small: {font_size}",
                                    file_path=file_path,
                                    code_snippet=rule.cssText,
                                    fix_suggestion="Use font-size >= 12px or relative units (em, rem)",
                                    wcag_criterion="1.4.4",
                                )
                            )

        except Exception as e:
            logger.warning(f"Error parsing CSS {file_path}: {e}")

        return issues

    def _scan_javascript(self, js_content: str, file_path: str) -> List[CodeIssue]:
        """Scan JavaScript file for accessibility issues"""
        issues = []

        # Check for keyboard event handlers
        has_click = "onclick" in js_content.lower() or ".click(" in js_content.lower()
        has_keydown = (
            "onkeydown" in js_content.lower() or "keydown" in js_content.lower()
        )
        has_keypress = (
            "onkeypress" in js_content.lower() or "keypress" in js_content.lower()
        )

        if has_click and not (has_keydown or has_keypress):
            issues.append(
                CodeIssue(
                    severity="moderate",
                    category="javascript",
                    rule="keyboard-handler",
                    description="Click handlers detected without keyboard equivalents",
                    file_path=file_path,
                    fix_suggestion="Add keydown/keypress event listeners for keyboard users",
                    wcag_criterion="2.1.1",
                )
            )

        # Check for ARIA manipulation
        if "setAttribute(" in js_content and "aria-" in js_content:
            issues.append(
                CodeIssue(
                    severity="minor",
                    category="javascript",
                    rule="aria-dynamic",
                    description="Dynamic ARIA attributes detected - ensure proper updates",
                    file_path=file_path,
                    fix_suggestion="Test ARIA updates with screen readers",
                    wcag_criterion="4.1.3",
                )
            )

        # Check for auto-play
        if ".play(" in js_content or "autoplay" in js_content.lower():
            issues.append(
                CodeIssue(
                    severity="moderate",
                    category="javascript",
                    rule="auto-play",
                    description="Auto-play media detected",
                    file_path=file_path,
                    fix_suggestion="Provide user controls to pause/stop media",
                    wcag_criterion="1.4.2",
                )
            )

        return issues

    def _generate_code_fix(
        self,
        description: str,
        code_snippet: str,
        file_path: str,
        category: str,
        rule: str = "",
        page_context: Optional[Dict] = None,
    ) -> Optional[str]:
        """Generate AI-powered code fix using Gemini with document context"""
        try:
            # Build context string if available
            context_str = ""
            if page_context and rule:
                context_str = self._build_context_for_issue(
                    rule, code_snippet, page_context
                )
                if context_str:
                    context_str = f"\n\nDOCUMENT CONTEXT:\n{context_str}"

            # Build WCAG guidance based on rule
            wcag_guidance = self._get_wcag_guidance_for_rule(rule)

            prompt = f"""You are an accessibility expert. Generate a code fix for this issue:

Issue: {description}
File: {file_path}
Category: {category}
Rule: {rule}

Current code:
```
{code_snippet[:500]}
```
{context_str}

WCAG 2.2 GUIDANCE:
{wcag_guidance}

REQUIREMENTS:
1. Provide ONLY the corrected code without explanations
2. Make it WCAG 2.2 Level AA compliant
3. Use the document context to make contextually appropriate changes
4. For heading issues, choose heading levels that fit the existing hierarchy
5. For form issues, use labels that match the form's purpose
6. For landmark issues, consider existing landmark structure"""

            result = self.llm_client.generate_text_sync(
                prompt=prompt, max_tokens=500, temperature=0.2
            )

            if result.get("success"):
                logger.info(
                    f"[CodeScanner] Generated context-aware fix using {result.get('provider')}"
                )
                return result["content"].strip()
            else:
                logger.warning(
                    f"[CodeScanner] Failed to generate fix: {result.get('error')}"
                )

        except Exception as e:
            logger.warning(f"Failed to generate code fix: {e}")

        return None

    def _get_wcag_guidance_for_rule(self, rule: str) -> str:
        """Get WCAG-specific guidance for different rule types"""
        guidance = {
            "heading-hierarchy": """
- Headings must follow sequential order (h1 → h2 → h3, no skipping levels)
- Each page should have exactly one h1
- Choose heading level based on document structure, not visual size
- Use CSS for visual styling, not heading level""",
            "form-label": """
- Every form input must have an associated label
- Use <label for="input-id"> or wrap input in <label>
- Labels must be programmatically associated (not just visually nearby)
- Label text should describe the input's purpose""",
            "landmark-main": """
- Every page should have one <main> element
- main should contain the primary content
- Multiple mains require unique aria-labels
- Don't nest landmarks incorrectly (e.g., main inside main)""",
            "image-alt": """
- All images must have alt attributes
- Decorative images: alt=""
- Informative images: describe the content/purpose
- Complex images: provide detailed description or link to one""",
            "lang-attribute": """
- <html> must have lang attribute (e.g., lang="en")
- Use correct language codes (BCP 47)
- Mark up changes in language within content""",
            "button-keyboard": """
- All interactive elements must be keyboard accessible
- Custom buttons need tabindex="0"
- Add keydown handlers for Enter/Space
- Use native <button> when possible""",
            "focus-indicator": """
- Never remove focus indicators (outline: none) without replacement
- Provide visible focus state with 3:1 contrast ratio
- Focus should be visible and clear""",
        }

        return guidance.get(
            rule, "Follow WCAG 2.2 Level AA guidelines for accessibility."
        )

    def _validate_alt_text_quality(
        self, alt_text: str, image_src: str
    ) -> Dict[str, Any]:
        """
        Validate alt text quality without access to the actual image.
        Uses heuristics and AI to check for common alt text problems.

        Args:
            alt_text: The existing alt text to validate
            image_src: The image source URL/path for context

        Returns:
            Dict with is_valid, issues, reasoning, and suggested_improvement
        """
        issues = []
        is_valid = True

        # Common problematic alt text patterns
        problematic_patterns = [
            ("image", 'Alt text should not just say "image"'),
            ("photo", 'Alt text should not just say "photo"'),
            ("picture", 'Alt text should not just say "picture"'),
            ("img", 'Alt text should not just say "img"'),
            ("icon", "Alt text should describe what the icon represents"),
            ("graphic", 'Alt text should not just say "graphic"'),
            ("screenshot", "Alt text should describe what the screenshot shows"),
            ("untitled", "Alt text should describe the image content"),
            ("dsc_", "Alt text appears to be a camera filename"),
            ("img_", "Alt text appears to be a camera filename"),
            (".jpg", "Alt text appears to contain a filename"),
            (".png", "Alt text appears to contain a filename"),
            (".gif", "Alt text appears to contain a filename"),
        ]

        alt_lower = alt_text.lower().strip()

        # Check for very short alt text (likely not descriptive)
        if len(alt_text.strip()) < 5:
            issues.append("Alt text is too short to be descriptive")
            is_valid = False

        # Check for very long alt text (might need description instead)
        if len(alt_text) > 200:
            issues.append(
                "Alt text is very long - consider using a description or longdesc"
            )

        # Check for problematic patterns
        for pattern, message in problematic_patterns:
            if pattern in alt_lower:
                # Only flag if it's essentially JUST the pattern
                if len(alt_lower) < len(pattern) + 10:
                    issues.append(message)
                    is_valid = False
                    break

        # Check if alt text matches the filename (common lazy practice)
        if image_src:
            filename = (
                image_src.split("/")[-1]
                .split(".")[0]
                .lower()
                .replace("-", " ")
                .replace("_", " ")
            )
            if (
                alt_lower == filename
                or filename in alt_lower
                and len(alt_text) < len(filename) + 10
            ):
                issues.append("Alt text appears to be the same as the filename")
                is_valid = False

        # Use AI for more nuanced validation if enabled
        if is_valid and self.llm_client:
            try:
                prompt = f"""Analyze this alt text for accessibility quality:

Alt text: "{alt_text}"
Image source: {image_src}

Is this alt text likely to be helpful for screen reader users?
Consider:
1. Does it describe the image's purpose or content?
2. Is it specific enough to be useful?
3. Is it appropriate in length (not too brief, not too verbose)?

Respond in JSON format:
{{
    "is_valid": true/false,
    "issues": ["list of specific issues if any"],
    "reasoning": "brief explanation",
    "suggested_improvement": "improved alt text if needed"
}}"""

                result = self.llm_client.generate_text_sync(
                    prompt=prompt, max_tokens=300, temperature=0.2
                )

                if result.get("success"):
                    content = result["content"].strip()
                    # Try to parse JSON response
                    import json

                    try:
                        # Extract JSON from response
                        json_match = re.search(r"\{[^}]+\}", content, re.DOTALL)
                        if json_match:
                            parsed = json.loads(json_match.group())
                            if not parsed.get("is_valid", True):
                                is_valid = False
                                issues.extend(parsed.get("issues", []))
                            return {
                                "is_valid": parsed.get("is_valid", is_valid),
                                "issues": list(set(issues + parsed.get("issues", []))),
                                "reasoning": parsed.get("reasoning", ""),
                                "suggested_improvement": parsed.get(
                                    "suggested_improvement"
                                ),
                            }
                    except json.JSONDecodeError:
                        logger.warning(
                            f"Failed to parse AI validation response: {content}"
                        )

            except Exception as e:
                logger.warning(f"AI alt text validation failed: {e}")

        return {
            "is_valid": is_valid,
            "issues": issues,
            "reasoning": "Validated using heuristic checks",
            "suggested_improvement": (
                None
                if is_valid
                else "Provide a descriptive alt text that explains the image content"
            ),
        }

    def _calculate_compliance_score(
        self, summary: Dict[str, int], files_count: int
    ) -> float:
        """Calculate overall compliance score using unified scoring system.

        Maps axe-core severity terms to standard severities:
        - critical -> critical
        - serious -> high
        - moderate -> medium
        - minor -> low
        """
        from .compliance_scoring import score_from_severity_counts

        if files_count == 0:
            return 100.0

        # Map axe-core severities to standard severities
        result = score_from_severity_counts(
            critical=summary.get("critical", 0),
            high=summary.get("serious", 0),  # axe uses 'serious' for high
            medium=summary.get("moderate", 0),  # axe uses 'moderate' for medium
            low=summary.get("minor", 0),  # axe uses 'minor' for low
            total_elements=None,  # Use penalty-based scoring
        )
        return result.score

    def _generate_recommendations(
        self, issues: List[CodeIssue], images: List[ImageInCode]
    ) -> List[str]:
        """Generate top recommendations based on findings"""
        recommendations = []

        # Count by category
        critical = [i for i in issues if i.severity == "critical"]
        serious = [i for i in issues if i.severity == "serious"]

        if critical:
            recommendations.append(
                f"Fix {len(critical)} critical issues immediately (especially image alt text)"
            )

        if serious:
            recommendations.append(
                f"Address {len(serious)} serious issues (form labels, ARIA, keyboard access)"
            )

        # Image-specific
        missing_alt = [
            img for img in images if not img.has_alt and not img.is_decorative
        ]
        if missing_alt:
            recommendations.append(f"Add alt text to {len(missing_alt)} images")

        # Category-specific
        html_issues = [i for i in issues if i.category == "html"]
        aria_issues = [i for i in issues if i.category == "aria"]

        if html_issues:
            recommendations.append("Review HTML semantic structure and form labels")

        if aria_issues:
            recommendations.append("Validate ARIA usage and keyboard accessibility")

        return recommendations[:5]  # Top 5

    def _analyze_cvd_from_directory(
        self, directory: str
    ) -> Optional[List[ColorBlindnessAnalysisResult]]:
        """
        Analyze all CSS files in a directory for color vision deficiency issues.
        Extracts color pairs and tests them for accessibility.
        """
        results = []
        color_pairs = set()

        # Find all CSS files
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.endswith(".css"):
                    file_path = Path(root) / file
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                        pairs = self._extract_color_pairs_from_css(content)
                        color_pairs.update(pairs)

        # Analyze unique color pairs
        for fg, bg in list(color_pairs)[:20]:  # Limit to 20 pairs
            try:
                analysis = self.cvd_simulator.analyze_color_accessibility(fg, bg)
                if not analysis.accessible_for_all:
                    results.append(analysis)
            except Exception as e:
                logger.debug(f"Error analyzing color pair ({fg}, {bg}): {e}")
                continue

        return results if results else None

    def _analyze_cvd_from_css(
        self, css_content: str
    ) -> Optional[List[ColorBlindnessAnalysisResult]]:
        """
        Analyze CSS content for color vision deficiency issues.
        """
        results = []
        color_pairs = self._extract_color_pairs_from_css(css_content)

        # Analyze unique color pairs
        for fg, bg in list(color_pairs)[:20]:  # Limit to 20 pairs
            try:
                analysis = self.cvd_simulator.analyze_color_accessibility(fg, bg)
                if not analysis.accessible_for_all:
                    results.append(analysis)
            except Exception as e:
                logger.debug(f"Error analyzing color pair ({fg}, {bg}): {e}")
                continue

        return results if results else None

    def _extract_color_pairs_from_css(self, css_content: str) -> set:
        """
        Extract foreground/background color pairs from CSS content.
        Returns set of (foreground_hex, background_hex) tuples.
        """
        # Regex for hex colors (3 and 6 digit)
        hex_color_pattern = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b")

        # Regex for rgb/rgba colors
        rgb_pattern = re.compile(
            r"rgb\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)"
        )

        color_pairs = set()

        # Parse CSS rules
        lines = css_content.split("\n")
        current_selector = None
        current_colors = {"color": None, "background": None}

        for line in lines:
            # Check for selector
            if "{" in line and "}" not in line:
                # Start of a rule - save selector
                current_selector = line.split("{")[0].strip()
                current_colors = {"color": None, "background": None}
            elif "}" in line:
                # End of a rule - save any color pairs
                if current_colors["color"] and current_colors["background"]:
                    fg = self._normalize_color(current_colors["color"])
                    bg = self._normalize_color(current_colors["background"])
                    if fg and bg:
                        color_pairs.add((fg, bg))
                current_selector = None  # noqa: F841
            else:
                # Check for color properties
                if "color:" in line.lower() and "background" not in line.lower():
                    match = hex_color_pattern.search(line) or rgb_pattern.search(line)
                    if match:
                        current_colors["color"] = match.group(0)
                elif "background" in line.lower():
                    match = hex_color_pattern.search(line) or rgb_pattern.search(line)
                    if match:
                        current_colors["background"] = match.group(0)

        return color_pairs

    def _normalize_color(self, color_str: str) -> Optional[str]:
        """Normalize color string to 6-digit hex format."""
        if not color_str:
            return None

        # Handle hex colors
        if color_str.startswith("#"):
            hex_val = color_str[1:]
            if len(hex_val) == 3:
                # Expand 3-digit to 6-digit
                return f"#{hex_val[0]*2}{hex_val[1]*2}{hex_val[2]*2}"
            elif len(hex_val) == 6:
                return color_str.lower()

        # Handle rgb()
        rgb_match = re.match(
            r"rgb\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)",
            color_str,
        )
        if rgb_match:
            r, g, b = (
                int(rgb_match.group(1)),
                int(rgb_match.group(2)),
                int(rgb_match.group(3)),
            )
            return f"#{r:02x}{g:02x}{b:02x}"

        return None
