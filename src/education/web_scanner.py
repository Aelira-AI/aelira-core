"""
Web Accessibility Scanner Module

This module provides functionality to:
1. Crawl websites using Playwright
2. Run WCAG 2.1 compliance checks using axe-core
3. Extract and analyze images (calls image scanner API)
4. Extract and analyze multimedia content (calls multimedia scanner API)
5. Generate comprehensive accessibility reports
6. Support multi-page scanning with configurable depth
"""

from typing import List, Dict, Optional, Tuple
from enum import Enum
from pydantic import BaseModel
from playwright.sync_api import sync_playwright, Page
from axe_playwright_python.sync_playwright import Axe
import logging
import re
import time
from urllib.parse import urljoin, urlparse
import requests
import tempfile
import os
import base64
import psycopg2
import json

from src.ai.providers import get_provider_manager
from src.education.focus_order_analyzer import (
    FocusOrderAnalyzer,
    FocusOrderResult,
)
from src.education.color_blindness_simulator import (
    ColorBlindnessSimulator,
    ColorBlindnessAnalysisResult,
)

logger = logging.getLogger(__name__)


class SPAFramework(str, Enum):
    """Single Page Application framework detection."""

    REACT = "react"
    VUE = "vue"
    ANGULAR = "angular"
    SVELTE = "svelte"
    NEXT = "next"  # Next.js (React-based)
    NUXT = "nuxt"  # Nuxt.js (Vue-based)
    NONE = "none"


# Human-friendly fix descriptions for ALL axe-core WCAG 2.1 Level AA rules (~90+ rules)
# Comprehensive dictionary for market-leading accessibility guidance
HUMAN_FRIENDLY_FIXES = {
    # ==================== LANDMARK & STRUCTURE ====================
    "Some page content is not contained by landmarks": "Add semantic HTML5 landmarks (<header>, <nav>, <main>, <footer>) to help screen reader users navigate your page structure. All content should be inside a landmark region.",
    "Page must have one main landmark": "Add a <main> element to identify the primary content area of your page. This helps screen reader users skip navigation and go straight to the main content.",
    "All page content must be contained by landmarks": "Wrap all page content in appropriate HTML5 landmarks (<header>, <nav>, <main>, <aside>, <footer>) so screen reader users can navigate efficiently.",
    "Document has more than one banner landmark": "Remove duplicate <header> elements with banner role. Pages should have only one main banner (typically the site header). Use <header> without role='banner' for subsection headers.",
    "Document must not have more than one contentinfo landmark": "Remove duplicate <footer> elements with contentinfo role. Pages should have only one main footer. Use <footer> without role='contentinfo' for subsection footers.",
    "The landmark must have a unique aria-label, aria-labelledby, or title to make landmarks distinguishable": "Add unique aria-label attributes to distinguish multiple landmarks of the same type (e.g., <nav aria-label='Main navigation'> and <nav aria-label='Footer navigation'>).",
    "Page must contain a level-one heading": "Add an <h1> heading to identify your page's main topic. Every page needs exactly one h1 for document structure, SEO, and screen reader navigation.",
    "Document should not have more than one main landmark": "Remove duplicate <main> elements. Each page should have exactly one <main> landmark containing the primary content.",
    "Pages must have a way to bypass repeated content": "Add a 'Skip to main content' link at the top of your page that jumps to the main landmark. Keyboard users need to bypass repetitive navigation.",
    # ==================== HEADINGS ====================
    "Headings must not be empty": "Add text content to your heading element. Empty headings confuse screen reader users who rely on heading structure to navigate the page.",
    "Heading levels should only increase by one": "Don't skip heading levels (e.g., h2 to h5). Use sequential heading levels (h2 → h3 → h4) to create a clear document outline for screen readers.",
    "Heading order invalid": "Fix your heading structure to follow sequential order (h1 → h2 → h3, etc.). Don't skip levels like h1 → h3. Screen readers use heading hierarchy to understand page structure and allow users to navigate by headings.",
    "Page must have a level-one heading": "Add an <h1> heading to your page that describes the main content. Every page needs exactly one h1 for proper document structure and SEO. Screen reader users rely on the h1 to understand the page's primary purpose.",
    "Paragraph elements should not be used to style headings": "Use proper heading tags (<h1>-<h6>) instead of <p> with CSS styling. Screen readers need real headings to navigate, not styled paragraphs.",
    # ==================== LINKS ====================
    "Links must have discernible text": "Add text content or aria-label to your link so screen readers can announce where it goes. Empty or icon-only links are not accessible.",
    "Links with the same text should go to the same place": "Links with identical text should have the same destination. If they go to different places, make the link text more descriptive and unique.",
    "Ensure that links are distinguished from surrounding text in a way that does not rely on color": "Add underlines or other visual styling to links beyond just color. Users with color vision deficiencies can't rely on color alone to identify links.",
    # ==================== BUTTONS & INTERACTIVE ====================
    "Buttons must have discernible text": "Add text content, aria-label, or aria-labelledby to your button so screen readers can announce its purpose. Icon-only buttons need accessible names.",
    "Element has a tabindex greater than 0": "Change tabindex to 0 or -1. Positive tabindex values (tabindex='1', '2', etc.) disrupt natural tab order and create confusing keyboard navigation for all users.",
    "Elements must not have tabindex greater than zero": "Remove or change positive tabindex values. Use tabindex='0' to include in tab order or tabindex='-1' to exclude. Positive values break expected keyboard navigation flow.",
    # ==================== FORMS ====================
    "Form elements must have labels": "Add a <label> element associated with your input field (using the 'for' attribute matching the input's 'id'). Every form field needs a visible label so users know what to enter.",
    "Form elements must have discernible text": "Ensure your form element has a visible label or accessible name (via <label>, aria-label, or aria-labelledby) so all users know what information to provide.",
    "Form elements should have a visible label": "Add a visible <label> element to your form field. While aria-label works for screen readers, sighted users also need visible labels.",
    "Input elements must have an accessible name": "Add a <label>, aria-label, or aria-labelledby to your input field. Screen readers announce this name to identify what information users should provide.",
    "Select elements must have an accessible name": "Add a <label> element for your <select> dropdown. Every dropdown needs a label explaining what users are choosing.",
    "Textarea elements must have an accessible name": "Add a <label> element for your <textarea>. Text areas need labels explaining what content users should provide.",
    "Input buttons must have discernible text": "Add text content or a value attribute to your input button (e.g., <input type='submit' value='Submit'>). Buttons need labels so users know what they do.",
    # ==================== IMAGES ====================
    "Images must have alternate text": "Add an alt attribute to your <img> tag describing what the image shows. If the image is decorative, use alt='' (empty alt text) to hide it from screen readers.",
    "Image buttons must have alternate text": "Add an alt attribute to your image button that describes what clicking it will do (e.g., alt='Search' not alt='magnifying glass icon').",
    "Elements with role='img' must have an accessible name": "Add alt text, aria-label, or aria-labelledby to elements with role='img'. Screen readers need text alternatives for images.",
    "SVG elements with an img role must have an alternative text": "Add <title> inside your <svg> element or use aria-label to describe what the SVG graphic represents.",
    "Image alternative text should not be repeated as text": "Remove redundant alt text when the same text appears next to the image. Don't repeat information - screen readers will announce it twice.",
    "Object elements must have alternative text": "Add a text description inside <object> tags as fallback content. This helps users when plugins fail or screen readers can't access the embedded object.",
    # ==================== VIDEO & AUDIO ====================
    "Video elements must have captions": "Add <track> elements with captions to your <video>. Deaf and hard-of-hearing users need captions to access video content.",
    "Video elements must have audio descriptions": "Provide audio descriptions or a descriptive transcript for video content. Blind users need descriptions of visual-only content.",
    "Audio elements must have a transcript": "Provide a text transcript for audio content. Deaf users and users in sound-sensitive environments need text alternatives.",
    # ==================== COLOR & CONTRAST ====================
    "Elements must have sufficient color contrast": "Increase the contrast between text and background colors to at least 4.5:1 for normal text or 3:1 for large text (18pt+ or 14pt+ bold). Use a color contrast checker to verify.",
    "Elements must meet minimum color contrast ratio thresholds": "Ensure text has at least 4.5:1 contrast ratio with its background (3:1 for large text). Low contrast makes text unreadable for users with low vision or color blindness.",
    # ==================== TABLES ====================
    "Tables must have captions": "Add a <caption> element inside your <table> to describe what the table contains. Captions help all users understand the table's purpose.",
    "Table headers must be associated with data cells": "Use <th> elements with scope attributes (scope='col' or scope='row') to associate headers with data cells. Screen readers use this to announce which headers apply.",
    "All cells in a table element that use the headers attribute must only refer to other cells of that same table": "Fix headers attribute to reference only IDs within the same table. Cross-table references break screen reader table navigation.",
    "Scope attribute should be used correctly on tables": "Add scope='col' to column headers and scope='row' to row headers in <th> elements. This tells screen readers which cells each header describes.",
    "Data or header cells should not be used in layout tables": "Remove <th> and headers attributes from layout tables, or convert the layout to CSS. Layout tables shouldn't have data table semantics.",
    # ==================== LISTS ====================
    "List items must be contained in a list element": "Wrap <li> elements in a parent <ul>, <ol>, or <menu>. List items outside of lists break screen reader announcements.",
    "Lists must only directly contain li, script, or template elements": "Remove non-<li> content from directly inside <ul> or <ol>. Lists should only contain list items, not divs or other elements.",
    "Definition list elements must only contain properly ordered groups of dt and dd elements": "Fix your <dl> to contain pairs of <dt> (term) and <dd> (definition). Definition lists need proper structure.",
    # ==================== FRAMES & IFRAMES ====================
    "Frames must have an accessible name": "Add a title attribute to your <frame> that describes its content (e.g., title='Main content area'). Screen readers announce this to help users understand the frame's purpose.",
    "Frames must have a unique title attribute": "Ensure each <frame> has a unique title. Duplicate titles don't help users distinguish between different frames.",
    "Iframes must have a unique title attribute": "Ensure each <iframe> has a unique, descriptive title. Screen readers announce titles to help users understand what each iframe contains.",
    "Element has an empty title attribute": "Add a descriptive title attribute to your <iframe> that explains what content it contains (e.g., title='Embedded video player'). Screen readers need this to understand the iframe's purpose.",
    # ==================== ARIA ====================
    "ARIA attributes must conform to valid values": "Check that your ARIA attributes use valid values from the ARIA specification. Invalid ARIA can break screen reader functionality.",
    "Required ARIA attributes must be provided": "Add the required ARIA attributes for this role. Check the ARIA specification for which attributes are mandatory.",
    "aria-label attribute does not exist or is empty": "Add an aria-label attribute with a descriptive name. This provides an accessible name for screen reader users.",
    "aria-labelledby attribute does not exist, references elements that do not exist or references elements that are empty": "Fix your aria-labelledby attribute by ensuring it references an existing element ID that contains descriptive text.",
    "ARIA attributes must be used on elements that support them": "Remove ARIA attributes from elements that don't support them, or change the element type. Not all HTML elements can use all ARIA attributes.",
    "Elements with an ARIA role must have all required attributes for that role": "Add missing required ARIA attributes for your role. Each ARIA role has specific required attributes defined in the specification.",
    "ARIA role should be appropriate for the element": "Change the ARIA role to match the element's purpose, or use a different HTML element. Mismatched roles confuse screen readers.",
    "Elements must only use permitted ARIA attributes": "Remove ARIA attributes that aren't allowed on this element or role. Each role permits only specific ARIA attributes.",
    "ARIA attributes must not be used to duplicate an element's default semantic meaning": "Remove redundant ARIA attributes that duplicate native HTML semantics (e.g., <button> doesn't need role='button').",
    "Ensure elements with an ARIA role that require child roles contain them": "Add required child elements with proper ARIA roles. For example, role='list' requires children with role='listitem'.",
    "Ensure elements with an ARIA role that require parent roles are contained by them": "Wrap this element in a parent with the required ARIA role. For example, role='listitem' requires a parent with role='list'.",
    # ==================== IDS ====================
    "Document has multiple static elements with the same id attribute": "Each ID on your page must be unique. Find all elements with this duplicate ID and change them to have unique values. IDs are used by assistive technologies, JavaScript, and CSS - duplicate IDs break functionality and confuse screen readers.",
    "IDs used in ARIA and labels must be unique": "Ensure IDs referenced by aria-labelledby, aria-describedby, and label 'for' attributes are unique. Duplicate IDs break these associations.",
    "ARIA IDs must be unique": "All id attributes referenced by ARIA relationships must be unique on the page. Duplicate IDs break aria-labelledby, aria-describedby, and aria-controls.",
    # ==================== LANGUAGE ====================
    "The page must have a language attribute": "Add a lang attribute to your <html> tag (e.g., <html lang='en'>). Screen readers need this to pronounce content correctly.",
    "The lang attribute must have a valid value": "Fix your lang attribute to use a valid language code (e.g., 'en', 'es', 'fr'). Invalid codes prevent screen readers from using correct pronunciation.",
    "Elements must have a valid lang attribute": "Add or fix lang attributes on elements with different languages (e.g., <span lang='fr'>Bonjour</span>). This helps screen readers pronounce foreign words correctly.",
    # ==================== META & DOCUMENT ====================
    "Documents must have a title element to aid in navigation": "Add a <title> element in your <head> that describes the page content. Titles appear in browser tabs, bookmarks, and search results.",
    "Zooming and scaling must not be disabled": "Remove or fix the viewport meta tag to allow zooming (don't use maximum-scale=1 or user-scalable=no). Many users need to zoom to read content.",
    "The page should not refresh automatically": "Remove or increase the refresh delay in meta refresh tags (minimum 20 hours). Auto-refresh can disorient users and interrupt screen readers.",
    # ==================== SEMANTIC HTML ====================
    "Element's default semantics were not overridden with role='none' or role='presentation'": "If this element is decorative, add role='presentation' to hide it from screen readers. Otherwise, provide proper accessible names or labels.",
    "Marquee elements should not be used": "Replace <marquee> with CSS animations. Marquee elements are deprecated, inaccessible, and cause motion sickness.",
}


def humanize_fix_description(technical_message: str) -> str:
    """
    Transform axe-core technical messages into human-friendly, actionable guidance.

    Args:
        technical_message: Technical message from axe-core (e.g., "Fix any of the following: Some page content is not contained by landmarks")

    Returns:
        Human-friendly description with actionable guidance
    """
    # Remove common axe-core prefixes
    cleaned = technical_message
    for prefix in [
        "Fix any of the following:",
        "Fix all of the following:",
        "Fix one of the following:",
    ]:
        if cleaned.startswith(prefix):
            cleaned = cleaned.replace(prefix, "").strip()

    # Try exact match first
    if cleaned in HUMAN_FRIENDLY_FIXES:
        return HUMAN_FRIENDLY_FIXES[cleaned]

    # Try partial matches for common patterns
    for pattern, friendly_desc in HUMAN_FRIENDLY_FIXES.items():
        if pattern.lower() in cleaned.lower():
            return friendly_desc

    # Fallback: Clean up the technical message but keep it
    # Remove redundant "Fix any of the following" prefixes
    if ":" in technical_message:
        # Extract the actual issue after the colon
        parts = technical_message.split(":", 1)
        if len(parts) > 1:
            return parts[1].strip()

    return technical_message


class WebPageIssue(BaseModel):
    """Single accessibility issue found on a web page"""

    impact: str  # critical, serious, moderate, minor
    criterion: str  # WCAG criterion (e.g., "1.1.1", "2.4.1")
    description: str
    help_url: str
    element: Optional[str] = None
    fix: Optional[str] = None
    generated_code_fix: Optional[str] = (
        None  # AI-generated HTML/CSS/JS fix from Qwen Coder
    )
    # Location information
    page_url: Optional[str] = None  # URL of the page where issue was found
    selector: Optional[str] = None  # CSS selector to locate the element
    xpath: Optional[str] = None  # XPath to locate the element
    screenshot: Optional[str] = (
        None  # Base64-encoded screenshot of the element with the issue
    )
    # Deduplication metadata
    metadata: Optional[Dict] = (
        None  # Additional metadata (e.g., affected_pages for site-wide issues)
    )
    # Priority scoring (for sorting/display)
    priority: str = (
        "medium"  # critical, high, medium, low - derived from impact and criterion
    )

    def get_priority_score(self) -> int:
        """Get numeric priority score (higher = more urgent)"""
        priority_map = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        return priority_map.get(self.priority, 2)


class ImageScanResult(BaseModel):
    """Result from image scanner API"""

    url: str
    has_alt_text: bool
    existing_alt_text: Optional[str] = None  # The current alt text on the image
    alt_text_quality: Optional[float] = None
    suggested_alt_text: Optional[str] = None
    # Alt text validation fields (for images WITH alt text)
    alt_text_validated: bool = False  # Whether AI validation was performed
    alt_text_accurate: Optional[bool] = None  # Whether existing alt text is accurate
    alt_text_issues: Optional[List[str]] = None  # Specific issues found with alt text
    validation_reasoning: Optional[str] = None  # AI explanation of validation


class MultimediaScanResult(BaseModel):
    """Result from multimedia scanner API"""

    url: str
    has_captions: bool
    has_audio_description: bool
    caption_quality: Optional[float] = None


class MathContentResult(BaseModel):
    """Result from scanning mathematical content"""

    format: str  # latex, mathml, mathjax, katex
    content: str  # Original math expression
    has_alt_text: bool
    suggested_alt_text: Optional[str] = None  # AI-generated description
    accessible_mathml: Optional[str] = None  # Converted to accessible MathML


class WebPageScanResult(BaseModel):
    """Result from scanning a single web page"""

    url: str
    title: str
    scan_time: float
    compliance_score: float
    issues: List[WebPageIssue]
    image_scans: List[ImageScanResult] = []
    multimedia_scans: List[MultimediaScanResult] = []
    math_content: List[MathContentResult] = []  # LaTeX/MathML scanning
    page_structure: Dict = {}
    content_analysis: Optional[Dict] = None  # AI-powered content analysis from Ollama
    # Focus order analysis (WCAG 2.4.3)
    focus_order_analysis: Optional[FocusOrderResult] = None
    # Color vision deficiency analysis
    cvd_analysis: Optional[List[ColorBlindnessAnalysisResult]] = None
    # SPA detection
    spa_framework: SPAFramework = SPAFramework.NONE
    spa_hydration_waited: bool = False  # Whether we waited for SPA hydration
    # Shadow DOM detection (Task 13)
    shadow_dom_detected: bool = False  # Whether Shadow DOM was found on page
    shadow_dom_host_count: int = 0  # Number of shadow hosts found
    shadow_dom_issues_count: int = 0  # Number of issues found inside Shadow DOM


class WebScanResult(BaseModel):
    """Complete result from web scanning operation"""

    root_url: str
    pages_scanned: int
    total_scan_time: float
    overall_compliance_score: float
    pages: List[WebPageScanResult]
    summary: Dict[str, int]  # issue counts by severity
    grouped_issues: Optional[Dict[str, Dict]] = (
        None  # issues grouped across pages for easier triage
    )


class WebScanner:
    """Scan websites for WCAG 2.1 accessibility compliance"""

    def __init__(
        self,
        scan_images: bool = False,
        scan_multimedia: bool = False,
        scan_math: bool = False,
        validate_alt_text: bool = False,
        scan_focus_order: bool = False,
        scan_cvd: bool = False,
        max_depth: int = 1,
        max_pages: int = 10,
        api_base_url: str = "http://localhost:8000",
        use_ai_analysis: bool = True,
        capture_screenshots: bool = True,
        ollama_host: str = None,
        progress_callback=None,
        database_url: str = None,
        parallel_workers: int = 3,
        crawl_strategy: str = "breadth_first",
        priority_patterns: List[str] = None,
        exclude_patterns: List[str] = None,
    ):
        """
        Initialize web scanner

        Args:
            scan_images: Whether to scan images with AI alt text generation
            scan_multimedia: Whether to scan multimedia for captions
            scan_math: Whether to scan and convert LaTeX/MathML content
            validate_alt_text: Whether to validate existing alt text accuracy using AI vision
            scan_focus_order: Whether to analyze keyboard focus order (WCAG 2.4.3)
            scan_cvd: Whether to analyze colors for color vision deficiency accessibility
            max_depth: Maximum depth to crawl (1 = only provided URL)
            max_pages: Maximum number of pages to scan
            api_base_url: Base URL for image/multimedia scanner APIs
            use_ai_analysis: Whether to use Ollama for AI-powered content analysis
            capture_screenshots: Whether to capture element screenshots for visual context
            ollama_host: Ollama server URL (defaults to http://localhost:11434)
            progress_callback: Optional callback function(current, total, message) for progress updates
            database_url: PostgreSQL connection string for RAG knowledge base
            parallel_workers: Number of parallel browser contexts (1-5, default 3)
            crawl_strategy: "breadth_first" or "depth_first" crawling strategy
            priority_patterns: URL patterns to prioritize (e.g., ["/courses/", "/faculty/"])
            exclude_patterns: URL patterns to exclude (e.g., ["/wp-admin/", "/login/"])
        """
        self.scan_images = scan_images
        self.scan_multimedia = scan_multimedia
        self.scan_math = scan_math
        self.validate_alt_text = validate_alt_text
        self.scan_focus_order = scan_focus_order
        self.scan_cvd = scan_cvd
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.api_base_url = api_base_url
        self.use_ai_analysis = use_ai_analysis
        self.capture_screenshots = capture_screenshots
        self.ollama_host = ollama_host or os.getenv(
            "OLLAMA_HOST", "http://localhost:11434"
        )
        self.progress_callback = progress_callback
        # Get DATABASE_URL from parameter or environment (required for RAG features)
        self.database_url = database_url or os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError(
                "DATABASE_URL must be provided or set via environment variable. "
                "See backend/.env.example for template."
            )
        # Use LLM provider manager for AI processing (supports multiple providers)
        self.llm_client = get_provider_manager()

        # Initialize focus order analyzer if enabled
        self.focus_order_analyzer = FocusOrderAnalyzer() if scan_focus_order else None

        # Initialize color blindness simulator if enabled
        self.cvd_simulator = ColorBlindnessSimulator() if scan_cvd else None

        # Timeout configuration (prevent hanging scans)
        self.ai_timeout_seconds = 30  # Max time per AI fix generation
        self.ai_timeout_fallback = 15  # Retry timeout with faster model

        # Parallel scanning configuration
        self.parallel_workers = min(max(1, parallel_workers), 5)  # Clamp between 1-5
        self.crawl_strategy = crawl_strategy
        self.priority_patterns = priority_patterns or []
        self.exclude_patterns = exclude_patterns or [
            "/wp-admin/",
            "/wp-login/",
            "/feed/",
            "/trackback/",
            "/login/",
            "/admin/",
            "/signin/",
            "/signup/",
        ]
        self.visited_urls = set()

    def _should_exclude_url(self, url: str) -> bool:
        """Check if URL matches exclude patterns"""
        for pattern in self.exclude_patterns:
            if pattern in url:
                return True
        return False

    def _get_url_priority(self, url: str) -> int:
        """Get priority score for URL (higher = more important)"""
        for i, pattern in enumerate(self.priority_patterns):
            if pattern in url:
                return (
                    len(self.priority_patterns) - i
                )  # Earlier patterns = higher priority
        return 0

    def _sort_urls_by_priority(
        self, urls: List[Tuple[str, int]]
    ) -> List[Tuple[str, int]]:
        """Sort URLs by priority patterns (higher priority first)"""
        return sorted(urls, key=lambda x: self._get_url_priority(x[0]), reverse=True)

    # =========================================================================
    # SPA Detection and Hydration Support
    # =========================================================================

    def _detect_spa_framework(self, page: Page) -> SPAFramework:
        """
        Detect Single Page Application framework from DOM markers.

        SPAs require special handling because content is loaded dynamically
        via JavaScript. Scanning before hydration completes can miss content
        or produce incorrect results.

        Args:
            page: Playwright Page object

        Returns:
            SPAFramework enum indicating detected framework
        """
        try:
            # Run JavaScript to detect framework markers
            framework = page.evaluate("""
                () => {
                    // React detection
                    if (window.__REACT_DEVTOOLS_GLOBAL_HOOK__ ||
                        document.querySelector('[data-reactroot]') ||
                        document.querySelector('[data-reactid]') ||
                        window.React ||
                        window.__NEXT_DATA__) {  // Next.js

                        // Check for Next.js specifically
                        if (window.__NEXT_DATA__ || document.querySelector('#__next')) {
                            return 'next';
                        }
                        return 'react';
                    }

                    // Vue detection
                    if (window.__VUE__ ||
                        window.Vue ||
                        document.querySelector('[data-v-]') ||
                        document.querySelector('[data-server-rendered]') ||
                        window.__NUXT__) {  // Nuxt.js

                        // Check for Nuxt.js specifically
                        if (window.__NUXT__ || document.querySelector('#__nuxt')) {
                            return 'nuxt';
                        }
                        return 'vue';
                    }

                    // Angular detection
                    if (window.ng ||
                        window.getAllAngularRootElements ||
                        document.querySelector('[ng-version]') ||
                        document.querySelector('[ng-app]') ||
                        document.querySelector('app-root')) {
                        return 'angular';
                    }

                    // Svelte detection
                    if (window.__svelte ||
                        document.querySelector('[data-svelte]') ||
                        document.querySelector('svelte-announcer')) {
                        return 'svelte';
                    }

                    return 'none';
                }
            """)

            spa_framework = SPAFramework(framework)
            if spa_framework != SPAFramework.NONE:
                logger.info(
                    f"[WebScanner] Detected SPA framework: {spa_framework.value}"
                )
            return spa_framework

        except Exception as e:
            logger.warning(f"[WebScanner] SPA detection failed: {e}")
            return SPAFramework.NONE

    def _wait_for_spa_hydration(
        self, page: Page, framework: SPAFramework, timeout_ms: int = 10000
    ) -> bool:
        """
        Wait for SPA to fully hydrate before scanning.

        Hydration is the process where the client-side JavaScript takes over
        from server-rendered HTML. Content may change during this process,
        so we need to wait for it to complete.

        Args:
            page: Playwright Page object
            framework: Detected SPA framework
            timeout_ms: Maximum time to wait for hydration (milliseconds)

        Returns:
            True if hydration detected and waited, False otherwise
        """
        if framework == SPAFramework.NONE:
            return False

        try:
            logger.info(f"[WebScanner] Waiting for {framework.value} hydration...")

            if framework in [SPAFramework.REACT, SPAFramework.NEXT]:
                # React/Next.js hydration detection
                # Wait for React to mount and for network to settle
                try:
                    page.wait_for_function(
                        """
                        () => {
                            // Check if React DevTools hook indicates rendering complete
                            const hook = window.__REACT_DEVTOOLS_GLOBAL_HOOK__;
                            if (hook && hook.renderers && hook.renderers.size > 0) {
                                return true;
                            }
                            // Fallback: check for hydrated content markers
                            const root = document.querySelector('[data-reactroot]') ||
                                         document.querySelector('#__next') ||
                                         document.querySelector('#root');
                            return root && root.children.length > 0;
                        }
                        """,
                        timeout=timeout_ms,
                    )
                except Exception:
                    pass  # Continue even if specific check fails

            elif framework in [SPAFramework.VUE, SPAFramework.NUXT]:
                # Vue/Nuxt.js hydration detection
                try:
                    page.wait_for_function(
                        """
                        () => {
                            // Check for Vue instance
                            const vueElements = document.querySelectorAll('[data-v-]');
                            if (vueElements.length > 0) return true;

                            // Check for Nuxt
                            const nuxtRoot = document.querySelector('#__nuxt') ||
                                            document.querySelector('#app');
                            return nuxtRoot && nuxtRoot.children.length > 0;
                        }
                        """,
                        timeout=timeout_ms,
                    )
                except Exception:
                    pass

            elif framework == SPAFramework.ANGULAR:
                # Angular hydration detection
                try:
                    page.wait_for_function(
                        """
                        () => {
                            // Check for ng-version attribute (Angular 2+)
                            const ngElement = document.querySelector('[ng-version]');
                            if (ngElement) return true;

                            // Check for app-root component
                            const appRoot = document.querySelector('app-root');
                            return appRoot && appRoot.children.length > 0;
                        }
                        """,
                        timeout=timeout_ms,
                    )
                except Exception:
                    pass

            elif framework == SPAFramework.SVELTE:
                # Svelte hydration detection
                try:
                    page.wait_for_function(
                        """
                        () => {
                            // Check for Svelte markers
                            return document.querySelector('[data-svelte]') !== null ||
                                   document.querySelector('svelte-announcer') !== null;
                        }
                        """,
                        timeout=timeout_ms,
                    )
                except Exception:
                    pass

            # Always wait for network to settle after framework-specific checks
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:
                pass  # networkidle can timeout on very dynamic sites

            logger.info(f"[WebScanner] {framework.value} hydration complete")
            return True

        except Exception as e:
            logger.warning(
                f"[WebScanner] Hydration wait failed for {framework.value}: {e}"
            )
            return False

    def _scan_spa_routes(
        self, page: Page, routes: List[str], base_url: str
    ) -> List[Dict]:
        """
        Navigate SPA routes via history API instead of full page loads.

        For SPAs, we can use client-side navigation which is faster and
        maintains the application state.

        Args:
            page: Playwright Page object
            routes: List of route paths to scan (e.g., ['/about', '/contact'])
            base_url: Base URL of the site

        Returns:
            List of scan results for each route
        """
        results = []
        parsed_base = urlparse(base_url)
        base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"

        for route in routes:
            try:
                full_url = urljoin(base_origin, route)
                logger.info(f"[WebScanner] Scanning SPA route: {route}")

                # Use history.pushState for client-side navigation
                page.evaluate(f"""
                    () => {{
                        window.history.pushState({{}}, '', '{route}');
                        // Dispatch popstate event to trigger route change
                        window.dispatchEvent(new PopStateEvent('popstate', {{ state: {{}} }}));
                    }}
                """)

                # Wait for route change to complete
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass  # networkidle may timeout

                # Small delay for client-side rendering
                time.sleep(0.5)

                results.append({"url": full_url, "route": route, "scanned": True})

            except Exception as e:
                logger.warning(f"[WebScanner] Failed to scan SPA route {route}: {e}")
                results.append(
                    {
                        "url": urljoin(base_origin, route),
                        "route": route,
                        "scanned": False,
                        "error": str(e),
                    }
                )

        return results

    def _find_shadow_roots(self, page: Page) -> Tuple[int, List[Dict]]:
        """
        Find all Shadow DOM hosts on the page.

        Shadow DOM encapsulates content that is invisible to standard DOM queries.
        Web components often use Shadow DOM for style isolation, but this can hide
        accessibility issues from standard scanning tools.

        Args:
            page: Playwright Page object

        Returns:
            Tuple of (shadow_host_count, list of shadow host info dicts)
        """
        try:
            result = page.evaluate("""
                () => {
                    const shadowHosts = [];
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_ELEMENT
                    );

                    let count = 0;
                    while (walker.nextNode()) {
                        const node = walker.currentNode;
                        if (node.shadowRoot) {
                            count++;
                            // Gather info about shadow host
                            const tagName = node.tagName.toLowerCase();
                            const id = node.id || '';
                            const classes = Array.from(node.classList).join(' ');
                            const childCount = node.shadowRoot.childElementCount;

                            // Check if shadow root is open or closed
                            const mode = node.shadowRoot.mode || 'unknown';

                            shadowHosts.push({
                                tagName: tagName,
                                id: id,
                                classes: classes,
                                mode: mode,
                                childCount: childCount,
                                // Get a selector that can be used to find this element
                                selector: id ? `#${id}` : `${tagName}${classes ? '.' + classes.split(' ')[0] : ''}`
                            });
                        }
                    }

                    return {
                        count: count,
                        hosts: shadowHosts
                    };
                }
            """)

            shadow_count = result.get("count", 0)
            shadow_hosts = result.get("hosts", [])

            if shadow_count > 0:
                logger.info(f"[WebScanner] Found {shadow_count} Shadow DOM hosts")
                for host in shadow_hosts[:5]:  # Log first 5
                    logger.debug(
                        f"[WebScanner] Shadow host: <{host['tagName']}> mode={host['mode']} children={host['childCount']}"
                    )

            return shadow_count, shadow_hosts

        except Exception as e:
            logger.warning(f"[WebScanner] Shadow DOM detection failed: {e}")
            return 0, []

    def _scan_shadow_dom(
        self, page: Page, shadow_hosts: List[Dict]
    ) -> List[WebPageIssue]:
        """
        Scan accessibility issues inside Shadow DOM content.

        Uses Playwright's pierce selectors and JavaScript injection to access
        shadow root content. Runs simplified accessibility checks since axe-core
        has limited shadow DOM support.

        Args:
            page: Playwright Page object
            shadow_hosts: List of shadow host info from _find_shadow_roots

        Returns:
            List of WebPageIssue objects found inside shadow DOM
        """
        issues = []

        if not shadow_hosts:
            return issues

        try:
            # Run accessibility checks inside all shadow roots
            shadow_issues = page.evaluate("""
                () => {
                    const issues = [];

                    function checkShadowRoot(shadowRoot, hostSelector) {
                        if (!shadowRoot) return;

                        // Check images without alt text
                        const images = shadowRoot.querySelectorAll('img');
                        images.forEach((img, idx) => {
                            if (!img.alt && !img.getAttribute('aria-label') && !img.getAttribute('aria-labelledby')) {
                                issues.push({
                                    type: 'image-alt',
                                    element: img.outerHTML.substring(0, 200),
                                    selector: `${hostSelector} >>> img:nth-of-type(${idx + 1})`,
                                    impact: 'critical',
                                    criterion: '1.1.1',
                                    description: 'Image inside Shadow DOM missing alt text'
                                });
                            }
                        });

                        // Check buttons without accessible names
                        const buttons = shadowRoot.querySelectorAll('button, [role="button"]');
                        buttons.forEach((btn, idx) => {
                            const hasName = btn.textContent.trim() ||
                                           btn.getAttribute('aria-label') ||
                                           btn.getAttribute('aria-labelledby') ||
                                           btn.getAttribute('title');
                            if (!hasName) {
                                issues.push({
                                    type: 'button-name',
                                    element: btn.outerHTML.substring(0, 200),
                                    selector: `${hostSelector} >>> button:nth-of-type(${idx + 1})`,
                                    impact: 'critical',
                                    criterion: '4.1.2',
                                    description: 'Button inside Shadow DOM missing accessible name'
                                });
                            }
                        });

                        // Check links without accessible names
                        const links = shadowRoot.querySelectorAll('a[href]');
                        links.forEach((link, idx) => {
                            const hasName = link.textContent.trim() ||
                                           link.getAttribute('aria-label') ||
                                           link.getAttribute('aria-labelledby');
                            if (!hasName) {
                                issues.push({
                                    type: 'link-name',
                                    element: link.outerHTML.substring(0, 200),
                                    selector: `${hostSelector} >>> a:nth-of-type(${idx + 1})`,
                                    impact: 'serious',
                                    criterion: '2.4.4',
                                    description: 'Link inside Shadow DOM missing accessible name'
                                });
                            }
                        });

                        // Check form inputs without labels
                        const inputs = shadowRoot.querySelectorAll('input, select, textarea');
                        inputs.forEach((input, idx) => {
                            const id = input.id;
                            const hasLabel = (id && shadowRoot.querySelector(`label[for="${id}"]`)) ||
                                            input.getAttribute('aria-label') ||
                                            input.getAttribute('aria-labelledby') ||
                                            input.placeholder;
                            const type = input.type || 'text';
                            // Skip hidden and submit/button types
                            if (type !== 'hidden' && type !== 'submit' && type !== 'button' && !hasLabel) {
                                issues.push({
                                    type: 'form-label',
                                    element: input.outerHTML.substring(0, 200),
                                    selector: `${hostSelector} >>> input:nth-of-type(${idx + 1})`,
                                    impact: 'critical',
                                    criterion: '1.3.1',
                                    description: 'Form input inside Shadow DOM missing label'
                                });
                            }
                        });

                        // Check for color contrast issues (basic check)
                        const textElements = shadowRoot.querySelectorAll('p, span, div, h1, h2, h3, h4, h5, h6, li, td, th');
                        textElements.forEach((el) => {
                            const style = window.getComputedStyle(el);
                            const color = style.color;
                            const bgColor = style.backgroundColor;
                            // Just flag if both are very similar (basic heuristic)
                            if (color === bgColor && color !== 'rgba(0, 0, 0, 0)') {
                                issues.push({
                                    type: 'color-contrast',
                                    element: el.outerHTML.substring(0, 200),
                                    selector: `${hostSelector} >>> ${el.tagName.toLowerCase()}`,
                                    impact: 'serious',
                                    criterion: '1.4.3',
                                    description: 'Potential color contrast issue inside Shadow DOM'
                                });
                            }
                        });

                        // Recursively check nested shadow roots
                        const nestedHosts = shadowRoot.querySelectorAll('*');
                        nestedHosts.forEach((el) => {
                            if (el.shadowRoot) {
                                checkShadowRoot(el.shadowRoot, hostSelector + ' >>> ' + el.tagName.toLowerCase());
                            }
                        });
                    }

                    // Walk through all shadow hosts
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_ELEMENT
                    );

                    while (walker.nextNode()) {
                        const node = walker.currentNode;
                        if (node.shadowRoot) {
                            const selector = node.id ? `#${node.id}` : node.tagName.toLowerCase();
                            checkShadowRoot(node.shadowRoot, selector);
                        }
                    }

                    return issues;
                }
            """)

            # Convert to WebPageIssue objects
            for issue_data in shadow_issues:
                issue = WebPageIssue(
                    impact=issue_data.get("impact", "moderate"),
                    criterion=issue_data.get("criterion", ""),
                    description=issue_data.get("description", ""),
                    help_url="https://www.w3.org/WAI/WCAG21/Understanding/info-and-relationships.html",
                    element=issue_data.get("element", ""),
                    fix=f"Add accessibility attributes to element inside Shadow DOM. Selector: {issue_data.get('selector', '')}",
                    selector=issue_data.get("selector", ""),
                    priority=(
                        "high" if issue_data.get("impact") == "critical" else "medium"
                    ),
                    metadata={
                        "shadow_dom": True,
                        "issue_type": issue_data.get("type", "unknown"),
                    },
                )
                issues.append(issue)

            if issues:
                logger.info(
                    f"[WebScanner] Found {len(issues)} accessibility issues inside Shadow DOM"
                )

        except Exception as e:
            logger.warning(f"[WebScanner] Shadow DOM scanning failed: {e}")

        return issues

    def scan_website(self, url: str) -> WebScanResult:
        """
        Scan a website for accessibility issues

        Args:
            url: Root URL to start scanning from

        Returns:
            WebScanResult with all findings
        """
        logger.info(f"[WebScanner] Starting scan_website for {url}")
        start_time = time.time()
        pages_results = []

        logger.info("[WebScanner] About to call sync_playwright()")
        import sys

        sys.stdout.flush()
        sys.stderr.flush()

        playwright_instance = None
        try:
            playwright_instance = sync_playwright().start()
            logger.info("[WebScanner] sync_playwright().start() returned successfully")
            sys.stdout.flush()

            logger.info("[WebScanner] About to launch Chromium browser")
            browser = playwright_instance.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
                timeout=60000,  # 60 second timeout
            )
            logger.info("[WebScanner] Browser launched successfully")
            sys.stdout.flush()
            context = browser.new_context()

            try:
                # Start crawling from root URL
                urls_to_scan = [(url, 0)]  # (url, depth)

                while urls_to_scan and len(pages_results) < self.max_pages:
                    # Apply crawl strategy
                    if self.crawl_strategy == "depth_first":
                        current_url, depth = urls_to_scan.pop()  # LIFO (depth-first)
                    else:  # breadth_first (default)
                        current_url, depth = urls_to_scan.pop(0)  # FIFO (breadth-first)

                    # Skip if already visited, max depth exceeded, or excluded
                    if current_url in self.visited_urls or depth > self.max_depth:
                        continue

                    # Skip excluded URLs
                    if self._should_exclude_url(current_url):
                        logger.info(f"Skipping excluded URL: {current_url}")
                        continue

                    self.visited_urls.add(current_url)

                    # Scan this page
                    logger.info(
                        f"Scanning page {len(pages_results) + 1}: {current_url}"
                    )

                    # Report progress and store current page for sub-progress messages
                    current_page = len(pages_results) + 1
                    self._current_page = current_page  # Store for use in sub-methods
                    if self.progress_callback:
                        self.progress_callback(
                            current_page,
                            self.max_pages,
                            f"Scanning page {current_page} of {self.max_pages}: {current_url[:50]}...",
                        )

                    page_result = self._scan_page(context, current_url)
                    pages_results.append(page_result)

                    # Find links to scan (only if depth allows)
                    if depth < self.max_depth:
                        new_urls = self._extract_internal_links(
                            context, current_url, url
                        )
                        # Filter out excluded URLs
                        new_urls = [
                            u for u in new_urls if not self._should_exclude_url(u)
                        ]
                        # Create URL tuples with depth
                        new_url_tuples = [
                            (u, depth + 1) for u in new_urls[:10]
                        ]  # Increased branching limit
                        # Sort by priority if patterns are configured
                        if self.priority_patterns:
                            new_url_tuples = self._sort_urls_by_priority(new_url_tuples)
                        # Add to scan queue
                        urls_to_scan.extend(new_url_tuples)

            finally:
                browser.close()
                if playwright_instance:
                    playwright_instance.stop()
                    logger.info("[WebScanner] Playwright stopped")
        except Exception as e:
            logger.error(
                f"[WebScanner] Error during Playwright execution: {e}", exc_info=True
            )
            if playwright_instance:
                try:
                    playwright_instance.stop()
                except Exception:
                    pass
            raise

        total_time = time.time() - start_time

        # Calculate overall compliance score
        overall_score = self._calculate_overall_score(pages_results)

        # Deduplicate site-wide issues (issues that appear on multiple pages)
        pages_results = self._deduplicate_sitewide_issues(pages_results)

        # Add page-level summaries (top 3 priority issues per page)
        pages_results = self._add_page_summaries(pages_results)

        # Group similar issues across pages for easier triage
        grouped_issues = self._group_issues_across_pages(pages_results)

        # Summarize issues
        summary = self._summarize_issues(pages_results)

        return WebScanResult(
            root_url=url,
            pages_scanned=len(pages_results),
            total_scan_time=total_time,
            overall_compliance_score=overall_score,
            pages=pages_results,
            summary=summary,
            grouped_issues=grouped_issues,
        )

    def _scan_page(self, context, url: str) -> WebPageScanResult:
        """Scan a single page for accessibility issues"""
        page_start = time.time()

        page = context.new_page()
        spa_framework = SPAFramework.NONE
        spa_hydration_waited = False

        try:
            # Load page
            page.goto(url, wait_until="networkidle", timeout=30000)

            # Detect SPA framework
            spa_framework = self._detect_spa_framework(page)

            # Wait for SPA hydration if needed
            if spa_framework != SPAFramework.NONE:
                spa_hydration_waited = self._wait_for_spa_hydration(page, spa_framework)

            title = page.title()
            logger.debug(
                f"Page loaded: {url}, title: {title}, spa: {spa_framework.value}"
            )

            # Run axe-core accessibility scan with comprehensive options
            axe = Axe()
            logger.debug(f"Running axe-core scan on {url}")

            # Configure axe to run ALL rules including best practices
            axe_options = {
                "runOnly": {
                    "type": "tag",
                    "values": [
                        "wcag2a",  # WCAG 2.0 Level A
                        "wcag2aa",  # WCAG 2.0 Level AA
                        "wcag21a",  # WCAG 2.1 Level A
                        "wcag21aa",  # WCAG 2.1 Level AA
                        "wcag22aa",  # WCAG 2.1 Level AA
                        "best-practice",  # Best practices
                    ],
                }
            }

            results = axe.run(page, options=axe_options)
            logger.debug("Axe scan completed")
            logger.debug(f"Results type: {type(results)}")
            logger.debug(f"Results dir: {dir(results)}")

            # Check if results has violations_count attribute (axe-playwright-python API)
            if hasattr(results, "violations_count"):
                logger.debug(f"results.violations_count: {results.violations_count}")

            # Check if results has response attribute
            if hasattr(results, "response"):
                logger.debug(f"results.response type: {type(results.response)}")
                actual_results = results.response  # The actual axe-core JSON response
            else:
                actual_results = results

            logger.debug(f"Actual results to parse: {type(actual_results)}")

            # Convert axe results to our format
            # Pass the actual results dict (either results.response or results itself), page URL, and page object for screenshots
            issues = self._parse_axe_results(actual_results, page_url=url, page=page)
            logger.debug(f"Parsed {len(issues)} issues from axe results")

            # Scan images if enabled
            image_scans = []
            if self.scan_images:
                image_scans = self._scan_page_images(page)

            # Scan multimedia if enabled
            multimedia_scans = []
            if self.scan_multimedia:
                multimedia_scans = self._scan_page_multimedia(page)

            # Scan LaTeX/MathML if enabled
            math_content = []
            if self.scan_math:
                math_content = self._scan_page_math(page)

            # Extract page structure
            structure = self._extract_page_structure(page)

            # AI-powered content analysis with Ollama
            # DISABLED: Content analysis (readability scores) not displayed in dashboard
            # Saves ~5-10 seconds per page by skipping Llama 3.2 inference
            content_analysis = None
            # if self.use_ai_analysis:
            #     content_analysis = self._analyze_content_with_ai(page, title, issues)

            # Focus order analysis (WCAG 2.4.3)
            focus_order_analysis = None
            if self.scan_focus_order and self.focus_order_analyzer:
                logger.debug(f"Running focus order analysis for {url}")
                try:
                    # Note: Focus order analyzer uses async, need to run separately
                    # For sync scanner, we analyze the page structure without full TAB simulation
                    focus_order_analysis = self._analyze_focus_order_sync(page, url)
                except Exception as e:
                    logger.warning(f"Focus order analysis failed for {url}: {e}")

            # Color Vision Deficiency (CVD) analysis
            cvd_analysis = None
            if self.scan_cvd and self.cvd_simulator:
                logger.debug(f"Running CVD analysis for {url}")
                try:
                    cvd_analysis = self._analyze_cvd_sync(page)
                except Exception as e:
                    logger.warning(f"CVD analysis failed for {url}: {e}")

            # Shadow DOM analysis (WCAG 4.1.2 - Name, Role, Value for web components)
            shadow_dom_detected = False
            shadow_dom_host_count = 0
            shadow_dom_issues_count = 0
            shadow_dom_issues = []
            try:
                shadow_dom_host_count, shadow_hosts = self._find_shadow_roots(page)
                shadow_dom_detected = shadow_dom_host_count > 0

                if shadow_dom_detected:
                    logger.debug(
                        f"Scanning {shadow_dom_host_count} Shadow DOM hosts for {url}"
                    )
                    shadow_dom_issues = self._scan_shadow_dom(page, shadow_hosts)
                    shadow_dom_issues_count = len(shadow_dom_issues)

                    # Add shadow DOM issues to main issues list
                    if shadow_dom_issues:
                        issues.extend(shadow_dom_issues)
                        logger.info(
                            f"[WebScanner] Added {shadow_dom_issues_count} Shadow DOM issues to scan results"
                        )
            except Exception as e:
                logger.warning(f"Shadow DOM analysis failed for {url}: {e}")

            # Calculate compliance score
            compliance_score = self._calculate_page_score(issues)

            scan_time = time.time() - page_start

            return WebPageScanResult(
                url=url,
                title=title,
                scan_time=scan_time,
                compliance_score=compliance_score,
                issues=issues,
                image_scans=image_scans,
                multimedia_scans=multimedia_scans,
                math_content=math_content,
                page_structure=structure,
                content_analysis=content_analysis,
                focus_order_analysis=focus_order_analysis,
                cvd_analysis=cvd_analysis,
                spa_framework=spa_framework,
                spa_hydration_waited=spa_hydration_waited,
                shadow_dom_detected=shadow_dom_detected,
                shadow_dom_host_count=shadow_dom_host_count,
                shadow_dom_issues_count=shadow_dom_issues_count,
            )

        finally:
            page.close()

    def _parse_axe_results(
        self, results, page_url: str = None, page: Page = None
    ) -> List[WebPageIssue]:
        """Parse axe-core results into our issue format with AI-generated code fixes and element screenshots"""
        issues = []

        # DEBUG: Log raw axe results object
        logger.debug(f"_parse_axe_results - input type: {type(results)}")

        # Handle different result formats
        violations = []

        # If results has a .response attribute, use that instead (axe-playwright-python wrapper)
        if hasattr(results, "response"):
            logger.debug("Unwrapping results.response")
            results = results.response

        # If results is an AxeResults object, try accessing its __dict__
        elif hasattr(results, "__dict__") and not isinstance(results, dict):
            logger.debug(f"Accessing AxeResults.__dict__: {results.__dict__.keys()}")
            # Try to find the violations data in the object's dict
            if "_AxeResults__violation_report" in results.__dict__:
                logger.debug("Found __violation_report, extracting...")
                results = results.__dict__["_AxeResults__violation_report"]
            elif "response" in results.__dict__:
                results = results.__dict__["response"]
            else:
                logger.error(
                    f"[DEBUG] Cannot find violations data in __dict__: {list(results.__dict__.keys())}"
                )
                return []

        # Case 1: Dict with 'violations' key (axe-core JSON format)
        if isinstance(results, dict):
            logger.debug(f"Results is dict, keys: {list(results.keys())}")
            violations = results.get("violations", [])
            logger.debug(f"Got violations from dict, count: {len(violations)}")

            # Log other result categories for debugging
            passes_count = len(results.get("passes", []))
            incomplete_count = len(results.get("incomplete", []))
            inapplicable_count = len(results.get("inapplicable", []))
            logger.debug(
                f"Passes: {passes_count}, Incomplete: {incomplete_count}, Inapplicable: {inapplicable_count}"
            )

        # Case 2: Object with violations attribute
        elif hasattr(results, "violations"):
            violations = results.violations
            logger.debug(f"Got violations from attribute, count: {len(violations)}")

        # Case 3: Can't find violations
        else:
            logger.error(f"[DEBUG] Cannot parse results! Type: {type(results)}")
            logger.error(f"[DEBUG] Results content: {str(results)[:500]}")
            return []

        logger.debug(f"Total violations to process: {len(violations)}")
        if len(violations) > 0:
            logger.debug(f"First violation ID: {violations[0].get('id', 'unknown')}")

        # Extract page context once for context-aware AI fixes (if page is available)
        # This avoids repeated DOM queries for each violation
        page_context = {
            "heading_hierarchy": [],
            "landmarks": [],
            "lists": [],
            "tables": [],
            "forms": [],
            "links": [],
        }
        if page:
            logger.info("[CONTEXT] Extracting page structure for AI context...")
            page_context["heading_hierarchy"] = self._extract_heading_hierarchy(page)
            page_context["landmarks"] = self._extract_landmark_structure(page)
            page_context["lists"] = self._extract_list_structure(page)
            page_context["tables"] = self._extract_table_structure(page)
            page_context["forms"] = self._extract_form_structure(page)
            page_context["links"] = self._extract_link_context(page)
            logger.info(
                f"[CONTEXT] Extracted: {len(page_context['heading_hierarchy'])} headings, "
                f"{len(page_context['landmarks'])} landmarks, {len(page_context['lists'])} lists, "
                f"{len(page_context['tables'])} tables, {len(page_context['forms'])} forms, "
                f"{len(page_context['links'])} links"
            )

        total_violations = len(violations)
        # Keep user informed about AI fix generation
        if total_violations > 0 and self.progress_callback:
            # Use stored current page to maintain progress bar position
            current_page = getattr(self, "_current_page", 1)
            self.progress_callback(
                current_page,
                self.max_pages,
                f"Generating AI fixes for {total_violations} issues...",
            )

        # Process violations (actual issues)
        for idx, violation in enumerate(violations):
            violation_id = violation.get("id", "unknown")
            logger.debug(
                f"Processing violation {idx+1}/{total_violations}: {violation_id}"
            )
            for node_idx, node in enumerate(violation.get("nodes", [])):
                logger.debug(
                    f"Processing node {node_idx+1} for violation {violation_id}"
                )
                element_html = node.get("html", "")[:200]  # Truncate long HTML
                logger.debug(f"Got element HTML for {violation_id}")
                failure_summary = node.get("failureSummary", "")
                logger.debug(f"Got failure summary for {violation_id}")

                # Extract location information from axe-core node
                # axe-core provides 'target' as an array of CSS selectors
                # target can contain nested arrays for elements inside iframes,
                # e.g. [["iframe", ".content"]] — flatten before joining
                target = node.get("target", [])
                if isinstance(target, list) and target:
                    flat_targets = []
                    for item in target:
                        if isinstance(item, list):
                            flat_targets.append(" > ".join(str(s) for s in item))
                        else:
                            flat_targets.append(str(item))
                    selector = ", ".join(flat_targets)
                else:
                    selector = None

                # axe-core may provide xpath in the node data
                xpath = node.get("xpath") if "xpath" in node else None

                # Capture screenshot of the element if enabled, page object is available, and selector is valid
                screenshot = None
                if self.capture_screenshots and page and selector:
                    try:
                        # Use the first selector from the target array (most specific)
                        first_selector = (
                            target[0]
                            if isinstance(target, list) and len(target) > 0
                            else None
                        )
                        if first_selector:
                            element = page.query_selector(first_selector)
                            if element:
                                # Capture screenshot and convert to base64
                                screenshot_bytes = element.screenshot()
                                screenshot = base64.b64encode(screenshot_bytes).decode(
                                    "utf-8"
                                )
                                logger.debug(
                                    f"Captured screenshot for element: {first_selector[:50]}"
                                )
                    except Exception as e:
                        logger.warning(
                            f"Failed to capture screenshot for element {first_selector}: {e}"
                        )

                # Create context-rich description with element details
                logger.debug(f"Creating contextual description for {violation_id}")
                contextual_description = self._create_contextual_description(
                    violation.get("description", ""),
                    element_html,
                    selector,
                    violation.get("id", ""),
                )
                logger.debug(f"Created contextual description for {violation_id}")

                # Transform axe-core technical message into human-friendly guidance
                # Using dictionary mapping for fast, deterministic results
                # LLM enhancement happens in _batch_generate_fixes below for
                # critical/serious issues; this humanized text is the fallback.
                logger.debug(f"Humanizing fix description for {violation_id}")
                enhanced_fix = (
                    humanize_fix_description(failure_summary)
                    if failure_summary
                    else failure_summary
                )
                logger.debug(f"Humanized fix description for {violation_id}")

                # Collect issue data — AI fixes are generated in batch below
                issue = WebPageIssue(
                    impact=violation.get("impact", "minor"),
                    criterion=(
                        violation.get("tags", [""])[0]
                        if violation.get("tags")
                        else "unknown"
                    ),
                    description=contextual_description,
                    help_url=violation.get("helpUrl", ""),
                    element=element_html,
                    fix=enhanced_fix,  # Fallback — may be upgraded by batch AI below
                    generated_code_fix=None,
                    page_url=page_url,
                    selector=selector,
                    xpath=xpath,
                    screenshot=screenshot,
                )

                # Assign priority based on impact and WCAG criterion
                issue.priority = self._assign_priority_to_issue(issue)

                issues.append(issue)

        # === BATCH AI FIX GENERATION ===
        # Instead of one API call per violation (N calls), batch critical/serious
        # violations into groups and generate fixes with fewer API calls.
        if self.use_ai_analysis and issues:
            critical_serious = [
                i for i in issues if i.impact in ("critical", "serious")
            ]
            if critical_serious:
                logger.info(
                    f"[BATCH FIX] Generating AI fixes for {len(critical_serious)} critical/serious issues "
                    f"(skipping {len(issues) - len(critical_serious)} moderate/minor)"
                )
                if self.progress_callback:
                    current_page = getattr(self, "_current_page", 1)
                    self.progress_callback(
                        current_page,
                        self.max_pages,
                        f"Generating AI fixes for {len(critical_serious)} critical/serious issues...",
                    )
                self._batch_generate_fixes(critical_serious, page_context)

                # Second pass: humanize explanations using the text model (gemma3)
                # The code model (qwen2.5-coder) produces technical explanations.
                # The text model produces warmer, faculty-friendly descriptions.
                fixed_issues = [i for i in critical_serious if i.generated_code_fix]
                if fixed_issues:
                    self._batch_humanize_explanations(fixed_issues)

        return issues

    def _create_contextual_description(
        self, base_description: str, element_html: str, selector: str, rule_id: str
    ) -> str:
        """Create a context-rich description by adding specific element details"""
        import re

        # Extract element type and key attributes for context
        element_type = "Element"
        element_context = []

        try:
            # Extract tag name
            tag_match = re.match(r"<(\w+)", element_html)
            if tag_match:
                element_type = tag_match.group(1).upper()

            # Extract key attributes for context
            # Class
            class_match = re.search(r'class="([^"]*)"', element_html)
            if class_match:
                classes = class_match.group(1).strip()
                if classes:
                    element_context.append(f'class="{classes[:80]}"')

            # ID
            id_match = re.search(r'id="([^"]*)"', element_html)
            if id_match:
                element_context.append(f'id="{id_match.group(1)}"')

            # Type (for inputs/buttons)
            type_match = re.search(r'type="([^"]*)"', element_html)
            if type_match:
                element_context.append(f'type="{type_match.group(1)}"')

            # Href (for links)
            href_match = re.search(r'href="([^"]*)"', element_html)
            if href_match:
                href = href_match.group(1)[:50]
                element_context.append(f'href="{href}"')

            # Src (for images/media)
            src_match = re.search(r'src="([^"]*)"', element_html)
            if src_match:
                src = src_match.group(1)[:50]
                element_context.append(f'src="{src}"')

            # Role
            role_match = re.search(r'role="([^"]*)"', element_html)
            if role_match:
                element_context.append(f'role="{role_match.group(1)}"')

        except Exception as e:
            logger.warning(f"Failed to extract element context: {e}")

        # Build contextual description
        if element_context:
            context_str = f"{element_type} with {', '.join(element_context[:3])}"  # Limit to 3 attributes
            return f"{base_description}: {context_str}"
        elif selector:
            # Fallback to selector if no attributes found
            selector_short = selector[:100] if selector else "unknown"
            return f"{base_description}: {element_type} ({selector_short})"
        else:
            # Final fallback
            return f"{base_description}: {element_type}"

    def _analyze_focus_order_sync(
        self, page: Page, url: str
    ) -> Optional[FocusOrderResult]:
        """
        Analyze keyboard focus order synchronously using the Playwright page.
        This is a simplified version that analyzes the DOM without full TAB simulation.

        For full TAB-based analysis, use FocusOrderAnalyzer.analyze_focus_order() async method.
        """
        from src.education.focus_order_analyzer import (
            FocusableElement,
            FocusOrderIssue,
            FocusOrderResult,
        )

        focus_sequence = []
        issues = []

        try:
            # Get all focusable elements
            focusable_data = page.evaluate("""
                () => {
                    const focusableSelectors = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';
                    const elements = Array.from(document.querySelectorAll(focusableSelectors));

                    return elements.map((el, idx) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);

                        const isVisible = style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            style.opacity !== '0' &&
                            rect.width > 0 &&
                            rect.height > 0;

                        const isOffscreen = (
                            rect.right < 0 ||
                            rect.bottom < 0 ||
                            rect.left > window.innerWidth ||
                            rect.top > window.innerHeight
                        ) && isVisible;

                        // Generate selector
                        let selector = '';
                        if (el.id) {
                            selector = '#' + el.id;
                        } else {
                            const siblings = el.parentElement
                                ? Array.from(el.parentElement.children).filter(c => c.tagName === el.tagName)
                                : [];
                            const index = siblings.indexOf(el) + 1;
                            selector = el.tagName.toLowerCase() + ':nth-of-type(' + index + ')';
                        }

                        return {
                            elementId: idx,
                            tagName: el.tagName.toLowerCase(),
                            selector: selector,
                            textContent: el.textContent?.trim().substring(0, 100) || null,
                            ariaLabel: el.getAttribute('aria-label'),
                            role: el.getAttribute('role'),
                            isVisible: isVisible,
                            isOffscreen: isOffscreen,
                            boundingBox: {
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: rect.height
                            },
                            tabIndex: el.tabIndex
                        };
                    });
                }
            """)

            # Convert to FocusableElement objects
            for data in focusable_data:
                element = FocusableElement(
                    element_id=data["elementId"],
                    tag_name=data["tagName"],
                    selector=data["selector"],
                    text_content=data["textContent"],
                    aria_label=data["ariaLabel"],
                    role=data["role"],
                    is_visible=data["isVisible"],
                    is_offscreen=data["isOffscreen"],
                    bounding_box=data["boundingBox"],
                    tab_index=data["tabIndex"],
                )
                focus_sequence.append(element)

            # Detect issues
            # 1. Invisible elements in focus order
            for element in focus_sequence:
                if not element.is_visible:
                    issues.append(
                        FocusOrderIssue(
                            issue_type="invisible_element",
                            severity="serious",
                            description=f"Element is in focus order but not visible: {element.selector}",
                            element=element,
                            suggested_fix="Remove tabindex or set tabindex='-1' for invisible elements.",
                            wcag_criterion="2.4.3",
                        )
                    )

            # 2. Off-screen elements (not skip links)
            for element in focus_sequence:
                if element.is_offscreen:
                    is_skip_link = element.text_content and (
                        "skip" in element.text_content.lower()
                        or "jump" in element.text_content.lower()
                    )
                    if not is_skip_link:
                        issues.append(
                            FocusOrderIssue(
                                issue_type="offscreen_element",
                                severity="moderate",
                                description=f"Element is positioned off-screen but in focus order: {element.selector}",
                                element=element,
                                suggested_fix="If skip link, make visible on focus. Otherwise, remove from focus order.",
                                wcag_criterion="2.4.3",
                            )
                        )

            # 3. Large visual jumps in focus order
            for i in range(1, len(focus_sequence)):
                prev = focus_sequence[i - 1]
                curr = focus_sequence[i]

                if prev.bounding_box and curr.bounding_box:
                    prev_x = prev.bounding_box["x"] + prev.bounding_box["width"] / 2
                    prev_y = prev.bounding_box["y"] + prev.bounding_box["height"] / 2
                    curr_x = curr.bounding_box["x"] + curr.bounding_box["width"] / 2
                    curr_y = curr.bounding_box["y"] + curr.bounding_box["height"] / 2

                    distance = ((curr_x - prev_x) ** 2 + (curr_y - prev_y) ** 2) ** 0.5

                    if distance > 500:
                        issues.append(
                            FocusOrderIssue(
                                issue_type="illogical_order",
                                severity="moderate",
                                description=f"Large visual jump in focus order ({prev.selector} to {curr.selector})",
                                element=curr,
                                suggested_fix="Reorder HTML or use tabindex for logical focus order.",
                                wcag_criterion="2.4.3",
                            )
                        )

            # Calculate compliance score
            score = 100.0
            severity_weights = {
                "critical": 30,
                "serious": 15,
                "moderate": 5,
                "minor": 2,
            }
            for issue in issues:
                score -= severity_weights.get(issue.severity, 5)
            score = max(0.0, min(100.0, score))

            wcag_compliant = score >= 80 and not any(
                issue.severity == "critical" for issue in issues
            )

            return FocusOrderResult(
                url=url,
                total_focusable_elements=len(focus_sequence),
                focus_sequence=focus_sequence,
                issues=issues,
                compliance_score=score,
                wcag_compliant=wcag_compliant,
            )

        except Exception as e:
            logger.error(f"Error analyzing focus order: {e}")
            return None

    def _analyze_cvd_sync(
        self, page: Page
    ) -> Optional[List[ColorBlindnessAnalysisResult]]:
        """
        Analyze color pairs on the page for color vision deficiency accessibility.
        Extracts foreground/background color combinations and tests them.
        """
        results = []

        try:
            # Extract color pairs from the page
            color_pairs = page.evaluate("""
                () => {
                    const pairs = [];
                    const elements = document.querySelectorAll('*');
                    const seen = new Set();

                    for (const el of elements) {
                        const style = window.getComputedStyle(el);
                        const text = el.textContent?.trim();

                        // Skip elements without visible text
                        if (!text || text.length === 0) continue;

                        const fgColor = style.color;
                        const bgColor = style.backgroundColor;

                        // Skip transparent backgrounds (inherit from parent)
                        if (bgColor === 'rgba(0, 0, 0, 0)' || bgColor === 'transparent') continue;

                        // Create unique key for deduplication
                        const key = fgColor + '|' + bgColor;
                        if (seen.has(key)) continue;
                        seen.add(key);

                        // Parse RGB values
                        const fgMatch = fgColor.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                        const bgMatch = bgColor.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);

                        if (!fgMatch || !bgMatch) continue;

                        // Convert to hex
                        const toHex = (r, g, b) => '#' + [r, g, b]
                            .map(x => parseInt(x, 10).toString(16).padStart(2, '0'))
                            .join('');

                        const fgHex = toHex(fgMatch[1], fgMatch[2], fgMatch[3]);
                        const bgHex = toHex(bgMatch[1], bgMatch[2], bgMatch[3]);

                        pairs.push({
                            foreground: fgHex,
                            background: bgHex,
                            element: el.tagName.toLowerCase(),
                            textSample: text.substring(0, 50)
                        });

                        // Limit to 20 unique color pairs
                        if (pairs.length >= 20) break;
                    }

                    return pairs;
                }
            """)

            # Analyze each color pair
            for pair in color_pairs[:20]:  # Limit analysis
                try:
                    analysis = self.cvd_simulator.analyze_color_accessibility(
                        pair["foreground"], pair["background"]
                    )

                    # Only include if there are issues
                    if not analysis.accessible_for_all:
                        results.append(analysis)
                except Exception as e:
                    logger.debug(f"Error analyzing color pair: {e}")
                    continue

            return results if results else None

        except Exception as e:
            logger.error(f"Error analyzing CVD accessibility: {e}")
            return None

    def _scan_page_images(self, page: Page) -> List[ImageScanResult]:
        """Extract and scan images on the page"""
        results = []

        try:
            # Find all images
            images = page.query_selector_all("img")

            # Keep user informed about image analysis
            if len(images) > 0 and self.progress_callback:
                current_page = getattr(self, "_current_page", 1)
                action = "Analyzing" if self.scan_images else "Scanning"
                if self.validate_alt_text:
                    action = "Analyzing & validating"
                self.progress_callback(
                    current_page,
                    self.max_pages,
                    f"{action} {min(len(images), 10)} images...",
                )

            for i, img in enumerate(images[:10]):  # Limit to 10 images per page
                try:
                    src = img.get_attribute("src")
                    alt = img.get_attribute("alt")

                    if not src:
                        continue

                    # Make absolute URL
                    full_url = urljoin(page.url, src)

                    # Check if has alt text
                    has_alt = alt is not None and len(alt.strip()) > 0
                    is_decorative = (
                        alt == ""
                    )  # Empty string means explicitly decorative

                    # Smart AI analysis filtering (to reduce API calls)
                    # IMPORTANT: We still REPORT all images for compliance
                    # We only skip expensive AI analysis for confirmed decorative images
                    suggested_alt = None
                    quality = None

                    # Validation results (for images WITH alt text)
                    alt_validated = False
                    alt_accurate = None
                    alt_issues = None
                    validation_reasoning = None

                    # Check if we should skip AI for this image (heuristics)
                    skip_ai = False

                    # Heuristic 1: Very small images (likely decorative)
                    try:
                        width = img.get_attribute("width")
                        height = img.get_attribute("height")
                        if width and height:
                            w, h = int(width), int(height)
                            if w < 20 and h < 20:
                                skip_ai = True
                                logger.debug(
                                    f"Skipping AI for tiny image ({w}x{h}): {src}"
                                )
                    except (ValueError, TypeError):
                        pass

                    # Heuristic 2: Common spacer/tracking pixel patterns
                    spacer_patterns = ["spacer", "pixel", "transparent", "blank", "1x1"]
                    if any(pattern in src.lower() for pattern in spacer_patterns):
                        skip_ai = True
                        logger.debug(f"Skipping AI for spacer/pixel: {src}")

                    if not skip_ai:
                        if self.scan_images and not has_alt and not is_decorative:
                            # Generate alt text for images missing it
                            suggested_alt = self._call_image_scanner_api(full_url)
                            if suggested_alt:
                                quality = 0.8  # Placeholder quality score

                        elif self.validate_alt_text and has_alt and not is_decorative:
                            # Validate existing alt text for accuracy
                            # Only validate non-decorative images that have alt text
                            validation_result = self._call_image_validation_api(
                                full_url, alt.strip()
                            )
                            if validation_result:
                                alt_validated = True
                                alt_accurate = validation_result.get(
                                    "is_accurate", True
                                )
                                alt_issues = validation_result.get("issues", [])
                                validation_reasoning = validation_result.get(
                                    "reasoning", ""
                                )
                                quality = validation_result.get("accuracy_score", 0.5)

                                # If alt text is inaccurate, provide suggested improvement
                                if not alt_accurate and validation_result.get(
                                    "suggested_improvement"
                                ):
                                    suggested_alt = validation_result.get(
                                        "suggested_improvement"
                                    )

                    # ALWAYS report the image (for compliance audit trail)
                    results.append(
                        ImageScanResult(
                            url=full_url,
                            has_alt_text=has_alt,
                            existing_alt_text=alt.strip() if alt else None,
                            alt_text_quality=quality,
                            suggested_alt_text=suggested_alt,
                            alt_text_validated=alt_validated,
                            alt_text_accurate=alt_accurate,
                            alt_text_issues=alt_issues,
                            validation_reasoning=validation_reasoning,
                        )
                    )

                except Exception as e:
                    logger.warning(f"Failed to scan image {i}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to scan images: {e}")

        return results

    def _scan_page_multimedia(self, page: Page) -> List[MultimediaScanResult]:
        """Extract and scan multimedia content on the page"""
        results = []

        try:
            # Find video and audio elements
            videos = page.query_selector_all("video")
            audios = page.query_selector_all("audio")

            total_media = len(videos) + len(audios)
            # Keep user informed about media analysis
            if total_media > 0 and self.progress_callback:
                current_page = getattr(self, "_current_page", 1)
                self.progress_callback(
                    current_page,
                    self.max_pages,
                    f"Checking {total_media} video/audio elements for captions...",
                )

            for element in videos + audios:
                try:
                    src = element.get_attribute("src")
                    if not src:
                        # Check for source child elements
                        sources = element.query_selector_all("source")
                        if sources:
                            src = sources[0].get_attribute("src")

                    if not src:
                        continue

                    full_url = urljoin(page.url, src)

                    # Check for captions/subtitles
                    tracks = element.query_selector_all(
                        'track[kind="captions"], track[kind="subtitles"]'
                    )
                    has_captions = len(tracks) > 0

                    # Check for audio description
                    desc_tracks = element.query_selector_all(
                        'track[kind="descriptions"]'
                    )
                    has_audio_desc = len(desc_tracks) > 0

                    results.append(
                        MultimediaScanResult(
                            url=full_url,
                            has_captions=has_captions,
                            has_audio_description=has_audio_desc,
                        )
                    )

                except Exception as e:
                    logger.warning(f"Failed to scan multimedia: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to scan multimedia: {e}")

        return results

    def _call_image_scanner_api(self, image_url: str) -> Optional[str]:
        """Call image scanner API to generate alt text"""
        try:
            # Validate URL against SSRF before downloading
            from src.utils.security import validate_url_not_private

            try:
                validate_url_not_private(image_url)
            except ValueError:
                logger.warning(f"Blocked private/reserved image URL: {image_url[:100]}")
                return None

            # Download image
            response = requests.get(image_url, timeout=10)
            if response.status_code != 200:
                return None

            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            try:
                # Call image scanner API
                with open(tmp_path, "rb") as f:
                    files = {"file": f}
                    api_response = requests.post(
                        f"{self.api_base_url}/api/education/image/analyze",
                        files=files,
                        timeout=30,
                    )

                if api_response.status_code == 200:
                    data = api_response.json()
                    return data.get("alt_text", "")

            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.warning(f"Failed to call image scanner API: {e}")

        return None

    def _call_image_validation_api(
        self, image_url: str, existing_alt_text: str
    ) -> Optional[Dict]:
        """Call image validation API to check if existing alt text is accurate"""
        try:
            # Validate URL against SSRF before downloading
            from src.utils.security import validate_url_not_private

            try:
                validate_url_not_private(image_url)
            except ValueError:
                logger.warning(f"Blocked private/reserved image URL: {image_url[:100]}")
                return None

            # Download image
            response = requests.get(image_url, timeout=10)
            if response.status_code != 200:
                logger.warning(f"Failed to download image for validation: {image_url}")
                return None

            # Save to temp file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            try:
                # Call image validation API
                with open(tmp_path, "rb") as f:
                    files = {"file": f}
                    data = {"existing_alt_text": existing_alt_text}
                    api_response = requests.post(
                        f"{self.api_base_url}/api/education/image/validate-alt-text",
                        files=files,
                        data=data,
                        timeout=45,  # Validation can take longer than generation
                    )

                if api_response.status_code == 200:
                    return api_response.json()
                else:
                    logger.warning(
                        f"Image validation API returned {api_response.status_code}: {api_response.text[:200]}"
                    )

            finally:
                os.unlink(tmp_path)

        except Exception as e:
            logger.warning(f"Failed to call image validation API: {e}")

        return None

    def _extract_page_structure(self, page: Page) -> Dict:
        """Extract semantic page structure"""
        structure = {
            "headings": [],
            "landmarks": [],
            "links": 0,
            "images": 0,
            "forms": 0,
        }

        try:
            # Count headings
            for level in range(1, 7):
                headings = page.query_selector_all(f"h{level}")
                structure["headings"].append({"level": level, "count": len(headings)})

            # Find landmarks
            landmarks = page.query_selector_all(
                '[role="main"], [role="navigation"], [role="banner"], [role="contentinfo"]'
            )
            structure["landmarks"] = [elem.get_attribute("role") for elem in landmarks]

            # Count interactive elements
            structure["links"] = len(page.query_selector_all("a[href]"))
            structure["images"] = len(page.query_selector_all("img"))
            structure["forms"] = len(page.query_selector_all("form"))

        except Exception as e:
            logger.error(f"Failed to extract page structure: {e}")

        return structure

    def _extract_heading_hierarchy(self, page: Page) -> List[Dict]:
        """
        Extract the full heading hierarchy from the page with text content.
        Used to provide context for heading-order AI fixes.

        Returns:
            List of dicts with level, text, and selector for each heading
        """
        heading_hierarchy = []

        try:
            # Query all headings in document order
            all_headings = page.query_selector_all("h1, h2, h3, h4, h5, h6")

            for idx, heading in enumerate(all_headings):
                try:
                    tag_name = heading.evaluate("el => el.tagName.toLowerCase()")
                    level = int(tag_name[1])  # h1 -> 1, h2 -> 2, etc.
                    text = heading.inner_text().strip()[:100]  # Truncate long text

                    heading_hierarchy.append(
                        {
                            "level": level,
                            "text": text or "[empty heading]",
                            "index": idx,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract heading {idx}: {e}")
                    continue

            logger.info(
                f"[HEADINGS] Extracted {len(heading_hierarchy)} headings from page"
            )

        except Exception as e:
            logger.error(f"Failed to extract heading hierarchy: {e}")

        return heading_hierarchy

    def _format_heading_context(
        self, heading_hierarchy: List[Dict], problem_element_html: str
    ) -> str:
        """
        Format heading hierarchy for AI prompt, highlighting where the problem heading fits.

        Args:
            heading_hierarchy: List of heading dicts from _extract_heading_hierarchy
            problem_element_html: The HTML of the problematic heading element

        Returns:
            Formatted string showing heading structure for AI context
        """
        if not heading_hierarchy:
            return "No heading structure found on page."

        # Determine which heading level the problem element is
        problem_level = None
        for level in range(1, 7):
            if f"<h{level}" in problem_element_html.lower():
                problem_level = level
                break

        # Format the heading structure
        lines = ["Current page heading structure:"]
        for h in heading_hierarchy:
            indent = "  " * (h["level"] - 1)
            marker = "→ " if h["level"] == problem_level else "  "
            lines.append(
                f"{indent}{marker}h{h['level']}: {h['text'][:50]}{'...' if len(h['text']) > 50 else ''}"
            )

        # Add recommendation based on structure analysis
        if heading_hierarchy:
            # Find the preceding heading level
            h1_exists = any(h["level"] == 1 for h in heading_hierarchy)
            any(h["level"] == 2 for h in heading_hierarchy)

            lines.append("")
            lines.append("Based on this structure:")

            if not h1_exists:
                lines.append("- Page is missing an H1 (required)")

            if problem_level:
                # Find where this heading appears in the order
                preceding_levels = []
                for h in heading_hierarchy:
                    if h["level"] == problem_level:
                        break
                    preceding_levels.append(h["level"])

                if preceding_levels:
                    max_preceding = max(preceding_levels)
                    correct_level = min(max_preceding + 1, 6)
                    lines.append(
                        f"- The problematic h{problem_level} should likely be h{correct_level} (follows h{max_preceding})"
                    )
                elif h1_exists:
                    lines.append(
                        f"- The problematic h{problem_level} should likely be h2 (follows h1)"
                    )
                else:
                    lines.append(
                        f"- The problematic h{problem_level} should likely be h1 (first heading on page)"
                    )

        return "\n".join(lines)

    def _extract_landmark_structure(self, page: Page) -> List[Dict]:
        """
        Extract ARIA landmarks and their structure from the page.
        Used to provide context for landmark-related AI fixes.

        Returns:
            List of dicts with role, tag, label for each landmark
        """
        landmarks = []

        try:
            # Query all landmark elements (both implicit and explicit ARIA roles)
            landmark_selectors = [
                'header, [role="banner"]',
                'nav, [role="navigation"]',
                'main, [role="main"]',
                'aside, [role="complementary"]',
                'footer, [role="contentinfo"]',
                '[role="search"]',
                '[role="form"]',
                '[role="region"][aria-label], [role="region"][aria-labelledby]',
                "section[aria-label], section[aria-labelledby]",
            ]

            for selector in landmark_selectors:
                elements = page.query_selector_all(selector)
                for el in elements:
                    try:
                        tag_name = el.evaluate("el => el.tagName.toLowerCase()")
                        role = el.get_attribute("role") or self._get_implicit_role(
                            tag_name
                        )
                        label = el.get_attribute("aria-label") or ""
                        labelledby = el.get_attribute("aria-labelledby")

                        if labelledby:
                            # Try to get text from referenced element
                            try:
                                label_el = page.query_selector(f"#{labelledby}")
                                if label_el:
                                    label = label_el.inner_text().strip()[:50]
                            except Exception:
                                pass

                        landmarks.append(
                            {
                                "role": role,
                                "tag": tag_name,
                                "label": label or "[unlabeled]",
                            }
                        )
                    except Exception as e:
                        logger.warning(f"Failed to extract landmark: {e}")
                        continue

            logger.info(f"[LANDMARKS] Extracted {len(landmarks)} landmarks from page")

        except Exception as e:
            logger.error(f"Failed to extract landmark structure: {e}")

        return landmarks

    def _get_implicit_role(self, tag_name: str) -> str:
        """Get implicit ARIA role for HTML elements"""
        role_map = {
            "header": "banner",
            "nav": "navigation",
            "main": "main",
            "aside": "complementary",
            "footer": "contentinfo",
            "form": "form",
            "section": "region",
        }
        return role_map.get(tag_name, tag_name)

    def _format_landmark_context(self, landmarks: List[Dict]) -> str:
        """Format landmark structure for AI prompt"""
        if not landmarks:
            return "No landmarks found on page. Page should have main, navigation, and contentinfo landmarks at minimum."

        lines = ["Current page landmark structure:"]

        # Group by role
        role_counts = {}
        for lm in landmarks:
            role = lm["role"]
            role_counts[role] = role_counts.get(role, 0) + 1
            lines.append(f"  - {role}: <{lm['tag']}> {lm['label']}")

        lines.append("")
        lines.append("Analysis:")

        # Check for required landmarks
        has_main = any(lm["role"] == "main" for lm in landmarks)
        has_banner = any(lm["role"] == "banner" for lm in landmarks)
        has_contentinfo = any(lm["role"] == "contentinfo" for lm in landmarks)

        if not has_main:
            lines.append("- MISSING: main landmark (required)")
        if not has_banner:
            lines.append("- MISSING: banner landmark (recommended)")
        if not has_contentinfo:
            lines.append("- MISSING: contentinfo landmark (recommended)")

        # Check for duplicates that need labels
        for role, count in role_counts.items():
            if count > 1:
                unlabeled = sum(
                    1
                    for lm in landmarks
                    if lm["role"] == role and lm["label"] == "[unlabeled]"
                )
                if unlabeled > 0:
                    lines.append(
                        f"- {role}: has {count} instances, {unlabeled} need unique labels"
                    )

        return "\n".join(lines)

    def _extract_list_structure(self, page: Page) -> List[Dict]:
        """
        Extract list structure from the page for list-related issues.

        Returns:
            List of dicts with type, item_count, nested, parent_tag for each list
        """
        lists = []

        try:
            all_lists = page.query_selector_all("ul, ol, dl")

            for idx, list_el in enumerate(all_lists):
                try:
                    tag_name = list_el.evaluate("el => el.tagName.toLowerCase()")

                    # Count items
                    if tag_name == "dl":
                        items = list_el.query_selector_all("dt, dd")
                    else:
                        items = list_el.query_selector_all(":scope > li")

                    # Check if nested
                    parent = list_el.evaluate(
                        "el => el.parentElement ? el.parentElement.tagName.toLowerCase() : null"
                    )
                    is_nested = parent in ["li", "dd"]

                    # Check for invalid children
                    if tag_name in ["ul", "ol"]:
                        invalid_children = list_el.evaluate("""el => {
                            return Array.from(el.children).filter(c => c.tagName.toLowerCase() !== 'li').length;
                        }""")
                    else:  # dl
                        invalid_children = list_el.evaluate("""el => {
                            return Array.from(el.children).filter(c => !['dt', 'dd'].includes(c.tagName.toLowerCase())).length;
                        }""")

                    lists.append(
                        {
                            "type": tag_name,
                            "item_count": len(items),
                            "nested": is_nested,
                            "parent_tag": parent,
                            "invalid_children": invalid_children,
                            "index": idx,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract list {idx}: {e}")
                    continue

            logger.info(f"[LISTS] Extracted {len(lists)} lists from page")

        except Exception as e:
            logger.error(f"Failed to extract list structure: {e}")

        return lists

    def _format_list_context(self, lists: List[Dict], problem_element_html: str) -> str:
        """Format list structure for AI prompt"""
        if not lists:
            return "No lists found on page."

        lines = ["Current page list structure:"]

        for lst in lists:
            nested_str = " (nested)" if lst["nested"] else ""
            invalid_str = (
                f" - {lst['invalid_children']} invalid children!"
                if lst["invalid_children"] > 0
                else ""
            )
            lines.append(
                f"  - <{lst['type']}> with {lst['item_count']} items{nested_str}{invalid_str}"
            )

        lines.append("")
        lines.append("List rules:")
        lines.append("- <ul> and <ol> must only contain <li> children")
        lines.append("- <dl> must only contain <dt> and <dd> children")
        lines.append("- <li> must be inside <ul> or <ol>")
        lines.append("- <dt>/<dd> must be inside <dl>")

        return "\n".join(lines)

    def _extract_table_structure(self, page: Page) -> List[Dict]:
        """
        Extract table structure for table-related issues.

        Returns:
            List of dicts with rows, cols, has_headers, caption for each table
        """
        tables = []

        try:
            all_tables = page.query_selector_all("table")

            for idx, table in enumerate(all_tables):
                try:
                    # Count rows and columns
                    rows = table.query_selector_all("tr")
                    first_row_cells = table.query_selector_all(
                        "tr:first-child > th, tr:first-child > td"
                    )

                    # Check for headers
                    th_elements = table.query_selector_all("th")
                    has_scope = table.evaluate("""t => {
                        const ths = t.querySelectorAll('th');
                        return Array.from(ths).some(th => th.hasAttribute('scope'));
                    }""")

                    # Check for caption
                    caption = table.query_selector("caption")
                    caption_text = (
                        caption.inner_text().strip()[:50] if caption else None
                    )

                    # Check for headers attribute on td
                    has_headers_attr = table.evaluate("""t => {
                        const tds = t.querySelectorAll('td');
                        return Array.from(tds).some(td => td.hasAttribute('headers'));
                    }""")

                    tables.append(
                        {
                            "row_count": len(rows),
                            "col_count": len(first_row_cells),
                            "has_th": len(th_elements) > 0,
                            "th_count": len(th_elements),
                            "has_scope": has_scope,
                            "has_headers_attr": has_headers_attr,
                            "caption": caption_text,
                            "index": idx,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract table {idx}: {e}")
                    continue

            logger.info(f"[TABLES] Extracted {len(tables)} tables from page")

        except Exception as e:
            logger.error(f"Failed to extract table structure: {e}")

        return tables

    def _format_table_context(
        self, tables: List[Dict], problem_element_html: str
    ) -> str:
        """Format table structure for AI prompt"""
        if not tables:
            return "No tables found on page."

        lines = ["Current page table structure:"]

        for idx, tbl in enumerate(tables):
            caption_str = (
                f' caption="{tbl["caption"]}"' if tbl["caption"] else " (no caption)"
            )
            scope_str = " with scope" if tbl["has_scope"] else ""
            headers_str = " with headers attr" if tbl["has_headers_attr"] else ""
            lines.append(
                f"  Table {idx+1}: {tbl['row_count']}×{tbl['col_count']}, {tbl['th_count']} <th>{scope_str}{headers_str}{caption_str}"
            )

        lines.append("")
        lines.append("Table accessibility rules:")
        lines.append("- Data tables need <th> elements for headers")
        lines.append("- <th> should have scope='col' or scope='row'")
        lines.append("- Complex tables need headers attribute on <td>")
        lines.append("- Tables should have <caption> for context")

        return "\n".join(lines)

    def _extract_form_structure(self, page: Page) -> List[Dict]:
        """
        Extract form structure for form-related issues.

        Returns:
            List of dicts with input info for each form
        """
        forms = []

        try:
            all_forms = page.query_selector_all("form")

            # Also check for inputs outside forms
            orphan_inputs = page.query_selector_all(
                "input:not(form input), select:not(form select), textarea:not(form textarea)"
            )

            for idx, form in enumerate(all_forms):
                try:
                    inputs = form.query_selector_all("input, select, textarea")
                    labels = form.query_selector_all("label")

                    inputs_data = []
                    for inp in inputs:
                        inp_type = inp.get_attribute("type") or "text"
                        inp_id = inp.get_attribute("id")
                        inp_name = inp.get_attribute("name")
                        has_label = False

                        if inp_id:
                            # Check for associated label
                            label = form.query_selector(f'label[for="{inp_id}"]')
                            has_label = label is not None

                        # Check for aria-label
                        aria_label = inp.get_attribute("aria-label")
                        aria_labelledby = inp.get_attribute("aria-labelledby")

                        inputs_data.append(
                            {
                                "type": inp_type,
                                "id": inp_id,
                                "name": inp_name,
                                "has_label": has_label,
                                "has_aria_label": bool(aria_label),
                                "has_aria_labelledby": bool(aria_labelledby),
                            }
                        )

                    forms.append(
                        {
                            "input_count": len(inputs),
                            "label_count": len(labels),
                            "inputs": inputs_data[:10],  # Limit to first 10 for brevity
                            "index": idx,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract form {idx}: {e}")
                    continue

            # Add orphan inputs info
            if orphan_inputs:
                forms.append(
                    {
                        "input_count": len(orphan_inputs),
                        "label_count": 0,
                        "inputs": [],
                        "index": -1,  # Indicates orphan inputs
                        "is_orphan": True,
                    }
                )

            logger.info(f"[FORMS] Extracted {len(forms)} form contexts from page")

        except Exception as e:
            logger.error(f"Failed to extract form structure: {e}")

        return forms

    def _format_form_context(self, forms: List[Dict], problem_element_html: str) -> str:
        """Format form structure for AI prompt"""
        if not forms:
            return "No forms found on page."

        lines = ["Current page form structure:"]

        for form in forms:
            if form.get("is_orphan"):
                lines.append(
                    f"  WARNING: {form['input_count']} inputs outside of <form> elements"
                )
            else:
                unlabeled = sum(
                    1
                    for inp in form["inputs"]
                    if not inp["has_label"]
                    and not inp["has_aria_label"]
                    and not inp["has_aria_labelledby"]
                )
                lines.append(
                    f"  Form {form['index']+1}: {form['input_count']} inputs, {form['label_count']} labels, {unlabeled} unlabeled inputs"
                )

                for inp in form["inputs"]:
                    if (
                        not inp["has_label"]
                        and not inp["has_aria_label"]
                        and not inp["has_aria_labelledby"]
                    ):
                        lines.append(
                            f"    - UNLABELED: <input type='{inp['type']}' id='{inp['id']}' name='{inp['name']}'>"
                        )

        lines.append("")
        lines.append("Form labeling rules:")
        lines.append(
            "- Every input needs a <label for='id'> or aria-label/aria-labelledby"
        )
        lines.append("- Placeholder is NOT a substitute for label")
        lines.append("- Labels should be visible, not hidden")

        return "\n".join(lines)

    def _extract_link_context(self, page: Page) -> List[Dict]:
        """
        Extract link context for link-in-text-block issues.

        Returns:
            List of dicts with link info
        """
        links_context = []

        try:
            # Find links that may be in text blocks
            text_links = page.query_selector_all("p a, li a, td a, div a, span a")

            for idx, link in enumerate(text_links[:20]):  # Limit to 20 for performance
                try:
                    text = link.inner_text().strip()[:50]
                    href = link.get_attribute("href")

                    # Check styling
                    is_underlined = link.evaluate(
                        'el => window.getComputedStyle(el).textDecoration.includes("underline")'
                    )
                    color = link.evaluate("el => window.getComputedStyle(el).color")

                    # Check parent text
                    parent_text = link.evaluate(
                        'el => el.parentElement ? el.parentElement.textContent.substring(0, 100) : ""'
                    )

                    links_context.append(
                        {
                            "text": text,
                            "href": href[:50] if href else None,
                            "underlined": is_underlined,
                            "color": color,
                            "parent_text": parent_text[:100] if parent_text else "",
                            "index": idx,
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to extract link {idx}: {e}")
                    continue

            logger.info(
                f"[LINKS] Extracted {len(links_context)} link contexts from page"
            )

        except Exception as e:
            logger.error(f"Failed to extract link context: {e}")

        return links_context

    def _format_link_context(self, links: List[Dict]) -> str:
        """Format link context for AI prompt"""
        if not links:
            return "No inline text links found."

        lines = ["Links in text blocks:"]

        problem_links = [link for link in links if not link["underlined"]]
        if problem_links:
            lines.append(
                f"  Found {len(problem_links)} links that may not be distinguishable from surrounding text:"
            )
            for link in problem_links[:5]:
                lines.append(
                    f"    - '{link['text']}' (color: {link['color']}, underlined: {link['underlined']})"
                )

        lines.append("")
        lines.append("Link-in-text-block rules (WCAG 1.4.1):")
        lines.append(
            "- Links must be distinguishable from surrounding text by more than just color"
        )
        lines.append(
            "- Options: underline, bold, border, icon, or other visual indicator"
        )
        lines.append("- 3:1 contrast ratio between link and text if no other indicator")

        return "\n".join(lines)

    def _build_context_for_issue(
        self, issue_id: str, element_html: str, page_context: Dict
    ) -> str:
        """
        Build appropriate context section for AI prompt based on issue type.

        Maps axe-core rule IDs to the relevant page context and returns formatted
        context string for the AI prompt.

        Args:
            issue_id: axe-core rule ID
            element_html: The problematic element's HTML
            page_context: Dict containing all extracted page context

        Returns:
            Formatted context string to include in AI prompt, or empty string
        """
        # Define which issues need which context types
        heading_issues = {
            "heading-order",
            "page-has-heading-one",
            "empty-heading",
            "p-as-heading",
        }
        landmark_issues = {
            "landmark-banner-is-top-level",
            "landmark-complementary-is-top-level",
            "landmark-contentinfo-is-top-level",
            "landmark-main-is-top-level",
            "landmark-no-duplicate-banner",
            "landmark-no-duplicate-contentinfo",
            "landmark-no-duplicate-main",
            "landmark-one-main",
            "landmark-unique",
            "bypass",
            "region",
        }
        list_issues = {"list", "listitem", "definition-list", "dlitem"}
        table_issues = {
            "td-headers-attr",
            "th-has-data-cells",
            "table-duplicate-name",
            "table-fake-caption",
            "td-has-header",
            "scope-attr-valid",
        }
        form_issues = {
            "label",
            "label-title-only",
            "select-name",
            "form-field-multiple-labels",
        }
        link_issues = {"link-in-text-block"}

        context_parts = []

        # Add heading context for heading-related issues
        if issue_id in heading_issues and page_context.get("heading_hierarchy"):
            heading_context = self._format_heading_context(
                page_context["heading_hierarchy"], element_html
            )
            context_parts.append(
                f"""
PAGE HEADING STRUCTURE:
{heading_context}

IMPORTANT: Use the heading structure above to determine the CORRECT heading level.
The fix should maintain proper document hierarchy (h1 → h2 → h3, no skipping levels)."""
            )

        # Add landmark context for landmark-related issues
        if issue_id in landmark_issues and page_context.get("landmarks"):
            landmark_context = self._format_landmark_context(page_context["landmarks"])
            context_parts.append(f"""
PAGE LANDMARK STRUCTURE:
{landmark_context}

IMPORTANT: Use the landmark structure above to determine the correct fix.
Ensure landmarks are unique, properly nested, and appropriately labeled.""")

        # Add list context for list-related issues
        if issue_id in list_issues and page_context.get("lists"):
            list_context = self._format_list_context(
                page_context["lists"], element_html
            )
            context_parts.append(
                f"""
PAGE LIST STRUCTURE:
{list_context}

IMPORTANT: Ensure list elements are properly nested and use correct parent/child relationships."""
            )

        # Add table context for table-related issues
        if issue_id in table_issues and page_context.get("tables"):
            table_context = self._format_table_context(
                page_context["tables"], element_html
            )
            context_parts.append(f"""
PAGE TABLE STRUCTURE:
{table_context}

IMPORTANT: Use proper header associations with scope or headers attributes.""")

        # Add form context for form-related issues
        if issue_id in form_issues and page_context.get("forms"):
            form_context = self._format_form_context(
                page_context["forms"], element_html
            )
            context_parts.append(f"""
PAGE FORM STRUCTURE:
{form_context}

IMPORTANT: Every form input needs a programmatically associated label.""")

        # Add link context for link-in-text-block issues
        if issue_id in link_issues and page_context.get("links"):
            link_context = self._format_link_context(page_context["links"])
            context_parts.append(
                f"""
PAGE LINK CONTEXT:
{link_context}

IMPORTANT: Links must be distinguishable from surrounding text by more than color alone."""
            )

        return "\n".join(context_parts)

    def _extract_internal_links(
        self, context, current_url: str, root_url: str
    ) -> List[str]:
        """Extract internal links from page"""
        links = []

        try:
            page = context.new_page()
            page.goto(current_url, wait_until="networkidle", timeout=30000)

            # Get all links
            all_links = page.query_selector_all("a[href]")
            root_domain = urlparse(root_url).netloc

            for link in all_links:
                href = link.get_attribute("href")
                if not href:
                    continue

                # Make absolute
                full_url = urljoin(current_url, href)
                parsed = urlparse(full_url)

                # Only internal links from same domain
                if parsed.netloc == root_domain and parsed.scheme in ("http", "https"):
                    # Remove fragment
                    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    if clean_url not in self.visited_urls:
                        links.append(clean_url)

            page.close()

        except Exception as e:
            logger.error(f"Failed to extract links: {e}")

        return list(set(links))  # Deduplicate

    def _calculate_page_score(self, issues: List[WebPageIssue]) -> float:
        """Calculate compliance score based on issues"""
        if not issues:
            return 100.0

        # Weight by severity
        weights = {"critical": 10.0, "serious": 5.0, "moderate": 2.0, "minor": 1.0}

        total_penalty = sum(weights.get(issue.impact, 1.0) for issue in issues)

        # Score from 0-100 (100 = perfect)
        score = max(0.0, 100.0 - total_penalty)

        return round(score, 2)

    def _calculate_overall_score(self, pages: List[WebPageScanResult]) -> float:
        """Calculate overall compliance score across all pages"""
        if not pages:
            return 0.0

        avg_score = sum(page.compliance_score for page in pages) / len(pages)
        return round(avg_score, 2)

    def _deduplicate_sitewide_issues(
        self, pages: List[WebPageScanResult]
    ) -> List[WebPageScanResult]:
        """
        Deduplicate issues that appear on multiple pages (site-wide template issues)

        Groups identical issues (same selector, description, fix) and tracks which
        pages they appear on. Keeps first occurrence with metadata about other pages.

        Args:
            pages: List of page scan results with issues

        Returns:
            List of page scan results with deduplicated issues
        """
        from collections import defaultdict

        # Track unique issues across all pages
        # Key: (selector, description, how_to_fix_text) - uniquely identifies an issue
        # Value: {first_page_idx, first_issue_idx, affected_pages: [urls]}
        unique_issues = {}
        issues_to_remove = defaultdict(list)  # page_idx -> [issue_indices_to_remove]

        # First pass: identify duplicates
        for page_idx, page in enumerate(pages):
            for issue_idx, issue in enumerate(page.issues):
                # Create unique identifier for this issue
                issue_key = (issue.selector, issue.description, issue.fix)

                if issue_key not in unique_issues:
                    # First occurrence - track it
                    unique_issues[issue_key] = {
                        "first_page_idx": page_idx,
                        "first_issue_idx": issue_idx,
                        "affected_pages": [page.url],
                    }
                else:
                    # Duplicate found - mark for removal and track affected page
                    issues_to_remove[page_idx].append(issue_idx)
                    unique_issues[issue_key]["affected_pages"].append(page.url)

        # Second pass: remove duplicates and annotate first occurrences
        deduplicated_pages = []
        for page_idx, page in enumerate(pages):
            # Get issues to keep (not marked for removal)
            indices_to_remove = set(issues_to_remove.get(page_idx, []))
            kept_issues = []

            for issue_idx, issue in enumerate(page.issues):
                if issue_idx not in indices_to_remove:
                    # Check if this is a first occurrence that appears on multiple pages
                    issue_key = (issue.selector, issue.description, issue.fix)
                    issue_info = unique_issues.get(issue_key)

                    if issue_info and len(issue_info["affected_pages"]) > 1:
                        # Annotate the issue with affected pages info
                        affected_count = len(issue_info["affected_pages"])

                        # Create a modified copy of the issue with updated description and metadata
                        issue_dict = issue.model_dump()

                        # Add note to the description
                        original_desc = issue_dict["description"]
                        issue_dict["description"] = (
                            f"{original_desc}\n\n"
                            f"📍 **Site-wide Issue**: This issue appears on {affected_count} pages "
                            f"(likely in a shared template/component)."
                        )

                        # Store affected pages in metadata
                        issue_dict["metadata"] = {
                            "affected_pages": issue_info["affected_pages"],
                            "is_sitewide": True,
                        }

                        # Create new issue object with updated data
                        updated_issue = WebPageIssue(**issue_dict)
                        kept_issues.append(updated_issue)
                    else:
                        kept_issues.append(issue)

            # Create new page result with deduplicated issues
            deduplicated_page = WebPageScanResult(
                url=page.url,
                title=page.title,
                compliance_score=page.compliance_score,
                issues=kept_issues,
                scan_time=page.scan_time,
                image_scans=page.image_scans,
                multimedia_scans=page.multimedia_scans,
                math_content=page.math_content,
                page_structure=page.page_structure,
                content_analysis=page.content_analysis,
            )
            deduplicated_pages.append(deduplicated_page)

        return deduplicated_pages

    def _assign_priority_to_issue(self, issue: WebPageIssue) -> str:
        """
        Assign priority level based on impact and WCAG criterion.
        Priority helps faculty/admins triage fixes efficiently.

        Priority Levels:
        - critical: Blocks all users, legal risk (missing H1, no landmarks, missing alt on functional images)
        - high: Affects major features, usability issues (form labels, contrast, heading structure)
        - medium: Impacts some users or specific scenarios (link text, button labels)
        - low: Best practices, minor improvements (meta tags, redundant text)

        Args:
            issue: WebPageIssue to prioritize

        Returns:
            Priority level: "critical", "high", "medium", or "low"
        """
        impact = issue.impact.lower()
        issue.criterion.lower() if issue.criterion else ""
        description = issue.description.lower()

        # Map axe-core impact to base priority
        # critical impact -> critical/high priority
        # serious impact -> high/medium priority
        # moderate impact -> medium/low priority
        # minor impact -> low priority

        if impact == "critical":
            # Critical impact issues - determine if truly critical or high
            # True critical: Blocks ALL users or severe legal risk
            critical_patterns = [
                "page must have one main landmark",
                "page must contain a level-one heading",
                "page must have a way to bypass",
                "images must have alternate text",  # When on functional elements
                "document must have a title",
                "html element must have a lang attribute",
            ]

            for pattern in critical_patterns:
                if pattern in description:
                    return "critical"

            # Other critical impact issues -> high priority
            return "high"

        elif impact == "serious":
            # Serious impact issues - determine if high or medium
            # High: Major usability barriers (forms, contrast, navigation)
            high_patterns = [
                "form elements must have labels",
                "color contrast",
                "heading levels should only increase by one",
                "links must have discernible text",
                "buttons must have discernible text",
                "input elements must have an accessible name",
                "select elements must have an accessible name",
            ]

            for pattern in high_patterns:
                if pattern in description:
                    return "high"

            # Other serious impact issues -> medium priority
            return "medium"

        elif impact == "moderate":
            # Moderate impact issues - usually medium, sometimes low
            # Medium: Affects specific user groups or scenarios
            low_patterns = [
                "meta",  # Meta tags
                "title element",  # Title improvements (not missing title)
                "duplicate",  # Duplicate IDs, landmarks (not ideal but not blocking)
                "redundant",  # Redundant text
            ]

            for pattern in low_patterns:
                if pattern in description:
                    return "low"

            return "medium"

        else:  # minor impact
            # Minor impact issues -> low priority (best practices)
            return "low"

    def _group_issues_across_pages(
        self, pages: List[WebPageScanResult]
    ) -> Dict[str, Dict]:
        """
        Group similar issues that appear across multiple pages.
        This reduces report verbosity and helps identify site-wide problems.

        Example:
        Instead of showing "Missing alt text" 15 times across 3 pages,
        show: "Missing alt text (15 instances across 3 pages)"

        Args:
            pages: List of page scan results

        Returns:
            Dict mapping issue description to grouped data:
            {
                "Images must have alternate text": {
                    "count": 15,
                    "impact": "critical",
                    "priority": "critical",
                    "pages": ["url1", "url2", "url3"],
                    "criterion": "wcag2a",
                    "fix": "Add an alt attribute...",
                    "example_selectors": ["img#logo", "img.hero", ...]
                }
            }
        """
        grouped = {}

        for page in pages:
            for issue in page.issues:
                # Create unique key from description + criterion
                # This groups issues that are the same type
                key = f"{issue.description}|{issue.criterion}"

                if key not in grouped:
                    grouped[key] = {
                        "description": issue.description,
                        "count": 0,
                        "impact": issue.impact,
                        "priority": issue.priority,
                        "criterion": issue.criterion,
                        "fix": issue.fix,
                        "help_url": issue.help_url,
                        "pages": set(),
                        "example_selectors": [],
                    }

                # Increment count and track pages
                grouped[key]["count"] += 1
                grouped[key]["pages"].add(page.url)

                # Collect example selectors (up to 5)
                if issue.selector and len(grouped[key]["example_selectors"]) < 5:
                    grouped[key]["example_selectors"].append(issue.selector)

        # Convert page sets to sorted lists
        for key in grouped:
            grouped[key]["pages"] = sorted(list(grouped[key]["pages"]))

        # Return dictionary keyed by description (without criterion suffix)
        result = {}
        for key, data in grouped.items():
            result[data["description"]] = data

        return result

    def _add_page_summaries(
        self, pages: List[WebPageScanResult]
    ) -> List[WebPageScanResult]:
        """
        Add top 3 priority issues to each page for quick triage.
        Helps faculty quickly see what needs fixing on each page.

        Args:
            pages: List of page scan results

        Returns:
            Pages with page_structure["top_issues"] added
        """
        for page in pages:
            if not page.issues:
                page.page_structure["top_issues"] = []
                continue

            # Sort issues by priority score (highest first)
            sorted_issues = sorted(
                page.issues, key=lambda x: x.get_priority_score(), reverse=True
            )

            # Take top 3 issues
            top_3 = sorted_issues[:3]

            # Create summary for each top issue
            page.page_structure["top_issues"] = [
                {
                    "priority": issue.priority,
                    "impact": issue.impact,
                    "description": issue.description,
                    "fix": issue.fix,
                }
                for issue in top_3
            ]

        return pages

    def _summarize_issues(self, pages: List[WebPageScanResult]) -> Dict[str, int]:
        """Summarize issues by severity"""
        summary = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0, "total": 0}

        for page in pages:
            for issue in page.issues:
                summary[issue.impact] = summary.get(issue.impact, 0) + 1
                summary["total"] += 1

        return summary

    def _analyze_content_with_ai(
        self, page: Page, title: str, issues: List[WebPageIssue]
    ) -> Dict:
        """
        Use Ollama to analyze page content for readability, clarity, and accessibility

        Args:
            page: Playwright page object
            title: Page title
            issues: List of detected accessibility issues

        Returns:
            Dict with AI analysis results
        """
        try:
            # Extract visible text content
            text_content = page.evaluate("""
                () => {
                    const body = document.body;
                    const text = body.innerText;
                    return text.slice(0, 2000);  // Limit to 2000 chars
                }
            """)

            if not text_content or len(text_content.strip()) < 50:
                return {
                    "readability_score": None,
                    "clarity_assessment": "Insufficient content for analysis",
                    "suggestions": [],
                }

            # Build prompt for Ollama
            prompt = f"""Analyze this web page content for accessibility and readability:

Title: {title}

Content: {text_content}

Detected Issues: {len(issues)} accessibility issues found

Please provide:
1. Readability score (0-100, where 100 is most readable)
2. Brief clarity assessment (1-2 sentences)
3. Top 3 suggestions to improve content accessibility

Format your response as JSON with keys: readability_score, clarity_assessment, suggestions (array)"""

            # Call Gemini for content analysis
            try:
                system_prompt = "You are an accessibility expert analyzing web content. Provide readability scores and actionable suggestions to improve content accessibility for users with disabilities."

                result = self.llm_client.generate_text_sync(
                    prompt=prompt,
                    max_tokens=500,
                    temperature=0.3,
                    system_prompt=system_prompt,
                )

                if not result.get("success"):
                    logger.warning(
                        f"[AI] Content analysis failed: {result.get('error')}"
                    )
                    return {}

            except Exception as e:
                logger.warning(f"[AI] Content analysis failed: {e}")
                return {}

            # Parse response
            content = result["content"]

            # Try to parse as JSON
            import json

            try:
                analysis = json.loads(content)
            except Exception:
                # Fallback if not valid JSON
                analysis = {
                    "readability_score": 70,
                    "clarity_assessment": content[:200],
                    "suggestions": [
                        "Review content structure",
                        "Improve heading hierarchy",
                    ],
                }

            return analysis

        except Exception as e:
            logger.error(f"AI content analysis failed: {e}")
            return {
                "readability_score": None,
                "clarity_assessment": f"Analysis failed: {str(e)}",
                "suggestions": [],
            }

    def _generate_code_fix(
        self,
        description: str,
        element_html: str,
        failure_summary: str,
        issue_id: str,
        impact: str = "moderate",
        page_context: Dict = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Generate BOTH explanation and code fix using single unified model

        Uses Gemini with structured EXPLANATION/CODE prompt.
        Provides page context for context-aware fixes.

        Args:
            description: Issue description from axe
            element_html: The problematic HTML element
            failure_summary: Summary of what's failing
            issue_id: axe rule ID (e.g., "color-contrast", "image-alt")
            impact: Issue severity ('critical', 'serious', 'moderate', 'minor')
            page_context: Dict with page structure (headings, landmarks, lists, tables, forms, links)

        Returns:
            Tuple of (explanation: str, code_fix: str), or fallback if generation fails
        """
        try:
            # Truncate extremely long element HTML
            truncated_html = element_html[:400] + (
                "..." if len(element_html) > 400 else ""
            )

            # Fallback fixes
            simple_fixes = {
                "frame-title": '<iframe title="[Describe the iframe content]" ... >',
                "image-alt": '<img alt="[Describe the image]" ... >',
                "button-name": '<button aria-label="[Describe button action]">...</button>',
                "link-name": '<a aria-label="[Describe link destination]">...</a>',
                "label": '<label for="input-id">Label text</label>\n<input id="input-id" type="text">',
                "color-contrast": "<!-- Increase contrast: text #000000 on background #FFFFFF (21:1 ratio) -->",
                "heading-order": "<!-- Change heading level to maintain proper hierarchy (h1 → h2 → h3) -->",
            }

            # Get WCAG guidance
            wcag_guidance = self._get_wcag_guidance_for_issue(issue_id)

            # Build context section based on issue type
            context_section = ""
            if page_context:
                context_section = self._build_context_for_issue(
                    issue_id, element_html, page_context
                )

            # Build unified prompt with EXPLANATION/CODE sections
            prompt = f"""You are an accessibility expert. Analyze and fix this WCAG accessibility issue:

ISSUE DETAILS:
- Description: {description}
- Rule ID: {issue_id}
- Problem: {failure_summary}
- Severity: {impact}
{context_section}
WCAG GUIDANCE:
{wcag_guidance}

ORIGINAL HTML (truncated):
```html
{truncated_html}
```

You MUST provide your response in EXACTLY this format:

EXPLANATION:
[Write 2-3 sentences explaining what's wrong and how to fix it]

CODE:
[Write ONLY the corrected HTML code - NO explanatory text, ONLY HTML tags]

CRITICAL RULES FOR THE CODE SECTION:
- The CODE section must ONLY contain HTML tags like <div>, <img>, <a>, etc.
- DO NOT write explanatory text under CODE
- DO NOT write "Why this fix..." under CODE
- DO NOT write bullet points under CODE
- ONLY HTML CODE WITH < AND > BRACKETS

Example (FOLLOW THIS EXACTLY):

EXPLANATION:
This image violates WCAG 1.1.1 (Non-text Content) because it lacks alt text. Screen reader users cannot understand the image's purpose. Add descriptive alt text that conveys the image's meaning and function.

CODE:
<img src="logo.png" alt="Company ABC logo">

Now provide your response (remember: CODE section = ONLY HTML, NO TEXT):"""

            logger.info(f"[CODE FIX] Calling Gemini for {issue_id}")

            # Use Gemini for code fix generation
            system_prompt = "You are an expert web accessibility developer specializing in WCAG 2.1 AA compliance. Generate production-ready HTML fixes with clear explanations."

            result = self.llm_client.generate_text_sync(
                prompt=prompt,
                max_tokens=600,
                temperature=0.2,
                system_prompt=system_prompt,
            )

            if not result.get("success"):
                logger.warning(
                    f"[CODE FIX] Gemini failed for {issue_id}: {result.get('error')}"
                )
                return (
                    f"Fix this {issue_id} issue by following WCAG 2.1 AA guidelines.",
                    simple_fixes.get(issue_id),
                )

            logger.info(
                f"[CODE FIX] Gemini responded successfully for {issue_id} (provider: {result.get('provider')})"
            )

            # Parse EXPLANATION and CODE sections
            full_response = result["content"].strip()
            explanation = None
            code_fix = None

            if "EXPLANATION:" in full_response and "CODE:" in full_response:
                parts = full_response.split("CODE:")
                if len(parts) == 2:
                    explanation = parts[0].replace("EXPLANATION:", "").strip()
                    code_part = parts[1].strip()
                    code_fix = self._clean_code_fences(code_part)
            else:
                logger.warning(
                    f"[CODE FIX] Response missing EXPLANATION/CODE sections for {issue_id}"
                )
                code_fix = self._clean_code_fences(full_response)
                explanation = (
                    f"Fix this {issue_id} issue by following WCAG 2.1 AA guidelines."
                )

            # Validate code
            if code_fix and self._validate_code_fix(code_fix, issue_id):
                logger.info(f"[CODE FIX] Successfully generated for {issue_id}")
                return (explanation, code_fix)
            else:
                logger.warning(
                    f"[CODE FIX] Validation failed for {issue_id}, using fallback"
                )
                return (
                    f"Fix this {issue_id} issue by following WCAG 2.1 AA guidelines.",
                    simple_fixes.get(issue_id),
                )

        except TimeoutError:
            logger.warning(
                f"[CODE FIX] Timeout after 60s for {issue_id}, using fallback"
            )
            return (
                f"Fix this {issue_id} issue by following WCAG 2.1 AA guidelines.",
                simple_fixes.get(issue_id),
            )
        except Exception as e:
            logger.warning(f"[CODE FIX] Failed for {issue_id}: {e}")
            return (
                f"Fix this {issue_id} issue by following WCAG 2.1 AA guidelines.",
                simple_fixes.get(issue_id),
            )

    def _batch_generate_fixes(
        self, issues: list, page_context: Dict = None, batch_size: int = 5
    ) -> None:
        """
        Generate AI fixes for multiple issues in batched API calls.

        Instead of one API call per violation (N calls causing rate limits),
        groups issues into batches and generates fixes with fewer calls.
        Mutates the issue objects in-place (sets .fix and .generated_code_fix).

        Args:
            issues: List of WebPageIssue objects to generate fixes for
            page_context: Page structure context for better fixes
            batch_size: Number of issues per API call (default 5)
        """
        for batch_start in range(0, len(issues), batch_size):
            batch = issues[batch_start : batch_start + batch_size]
            logger.info(
                f"[BATCH FIX] Processing batch {batch_start // batch_size + 1} "
                f"({len(batch)} issues)"
            )

            # Build page context summary (shared across all issues in batch)
            context_summary = ""
            if page_context:
                headings = page_context.get("heading_hierarchy", [])
                landmarks = page_context.get("landmarks", [])
                if headings:
                    context_summary += f"\nPage heading structure: {', '.join(str(h) for h in headings[:10])}"
                if landmarks:
                    context_summary += f"\nPage landmarks: {', '.join(str(lm) for lm in landmarks[:8])}"

            # Build a single prompt for all issues in this batch
            issues_text = ""
            for i, issue in enumerate(batch, 1):
                truncated_html = (issue.element or "")[:300]
                # Get WCAG guidance for this issue type
                wcag_guidance = self._get_wcag_guidance_for_issue(issue.criterion or "")
                issues_text += f"""
--- ISSUE {i} ---
Rule: {issue.criterion}
Impact: {issue.impact}
Description: {issue.description[:200] if issue.description else ''}
Element: {truncated_html}
Fix guidance: {issue.fix[:200] if issue.fix else ''}
WCAG: {wcag_guidance[:200] if wcag_guidance else 'Follow WCAG 2.1 AA guidelines'}
"""

            prompt = f"""You are an accessibility expert. Generate fixes for {len(batch)} WCAG accessibility issues found on a web page.
{context_summary}
{issues_text}

For EACH issue, provide your response in this EXACT format (use the exact issue numbers):

--- FIX 1 ---
EXPLANATION: [2-3 sentences explaining the problem and fix]
CODE: [corrected HTML only — no explanatory text]

--- FIX 2 ---
EXPLANATION: [2-3 sentences]
CODE: [corrected HTML only]

(Continue for all {len(batch)} issues, in order, using numbers 1 through {len(batch)})

RULES:
- CODE sections must contain ONLY HTML tags
- Keep explanations concise and actionable
- Reference specific WCAG criteria where relevant
- You MUST provide exactly {len(batch)} fixes, numbered 1 to {len(batch)}"""

            try:
                result = self.llm_client.generate_code_sync(
                    prompt=prompt,
                    language="html",
                    max_tokens=max(2048, 400 * len(batch)),
                    temperature=0.2,
                )

                if not result.get("success"):
                    logger.warning(
                        f"[BATCH FIX] API call failed: {result.get('error')}"
                    )
                    continue

                # Parse the batch response — extract fixes by number
                content = result.get("content", "")
                fixes = self._parse_batch_fixes(content, len(batch))

                # Apply fixes to the issue objects
                for i, issue in enumerate(batch):
                    if i < len(fixes) and any(fixes[i]):
                        explanation, code_fix = fixes[i]
                        if explanation:
                            issue.fix = explanation
                        if code_fix:
                            cleaned = self._clean_code_fences(code_fix)
                            if cleaned and self._validate_code_fix(
                                cleaned, issue.criterion or ""
                            ):
                                issue.generated_code_fix = cleaned
                                logger.info(
                                    f"[BATCH FIX] Applied fix for issue {batch_start + i + 1}"
                                )
                            else:
                                logger.info(
                                    f"[BATCH FIX] Code validation failed for issue {batch_start + i + 1}, keeping explanation only"
                                )
                    else:
                        logger.warning(
                            f"[BATCH FIX] No fix parsed for issue {batch_start + i + 1}"
                        )

            except Exception as e:
                logger.warning(f"[BATCH FIX] Batch failed: {e}")
                # Issues keep their fallback fix — no crash

    def _parse_batch_fixes(
        self, content: str, expected_count: int
    ) -> list[tuple[Optional[str], Optional[str]]]:
        """
        Parse a batch AI response into individual (explanation, code_fix) tuples.
        Uses numbered FIX markers for correct alignment (not positional splitting).

        Args:
            content: Raw AI response text
            expected_count: Number of fixes expected

        Returns:
            List of (explanation, code_fix) tuples indexed by fix number
        """
        # Initialize result array
        fixes: list[tuple[Optional[str], Optional[str]]] = [
            (None, None)
        ] * expected_count

        # Find all "--- FIX N ---" sections with their number
        pattern = r"---\s*FIX\s*(\d+)\s*---\s*(.*?)(?=---\s*FIX\s*\d+\s*---|$)"
        matches = re.findall(pattern, content, re.DOTALL)

        for num_str, fix_content in matches:
            fix_num = int(num_str) - 1  # Convert to 0-indexed
            if fix_num < 0 or fix_num >= expected_count:
                continue

            fix_content = fix_content.strip()
            explanation = None
            code_fix = None

            # Extract EXPLANATION section
            exp_match = re.search(
                r"EXPLANATION:\s*(.+?)(?=CODE:|$)", fix_content, re.DOTALL
            )
            if exp_match:
                explanation = exp_match.group(1).strip()

            # Extract CODE section
            code_match = re.search(r"CODE:\s*(.+?)$", fix_content, re.DOTALL)
            if code_match:
                code_text = code_match.group(1).strip()
                if "<" in code_text:  # Basic sanity — contains HTML
                    code_fix = code_text

            fixes[fix_num] = (explanation, code_fix)

        return fixes

    def _batch_humanize_explanations(self, issues: list) -> None:
        """
        Use the text model (gemma3:4b) to rewrite technical explanations into
        warm, faculty-friendly language. Single API call for all issues.

        Mutates issue.fix in-place.
        """
        if not issues:
            return

        issues_text = ""
        for i, issue in enumerate(issues, 1):
            issues_text += f"\n{i}. [{issue.impact}] {issue.fix[:200] if issue.fix else 'No description'}"

        prompt = f"""You are helping university faculty understand accessibility issues in their course materials. Rewrite each of these technical fix descriptions into clear, warm, actionable guidance.

For each issue, write 2-3 sentences that:
- Explain what's wrong in plain language (no jargon)
- Say WHY it matters for students using assistive technology
- Give a specific, actionable fix

Issues to humanize:
{issues_text}

Respond with ONLY the rewritten descriptions, numbered 1 to {len(issues)}, one per line:
1. [rewritten description]
2. [rewritten description]
..."""

        try:
            # Use generate_text_sync which routes to the text model (gemma3:4b)
            result = self.llm_client.generate_text_sync(
                prompt=prompt,
                max_tokens=max(1024, 150 * len(issues)),
                temperature=0.4,
            )

            if not result.get("success"):
                logger.warning(f"[HUMANIZE] Failed: {result.get('error')}")
                return

            content = result.get("content", "")
            lines = content.strip().split("\n")

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                # Parse "N. description" format
                match = re.match(r"^(\d+)\.\s*(.+)", line)
                if match:
                    idx = int(match.group(1)) - 1
                    description = match.group(2).strip()
                    if 0 <= idx < len(issues) and len(description) > 20:
                        issues[idx].fix = description
                        logger.info(
                            f"[HUMANIZE] Rewrote explanation for issue {idx + 1}"
                        )

        except Exception as e:
            logger.warning(f"[HUMANIZE] Batch humanization failed: {e}")
            # Issues keep their code-model explanations — still usable

    def _get_wcag_guidance_for_issue(self, issue_id: str) -> str:
        """
        Get WCAG criterion-specific guidance and examples for an issue type.
        Helps AI generate better, more contextual fixes.

        Args:
            issue_id: axe-core rule ID

        Returns:
            Formatted guidance string with WCAG criterion and examples
        """
        # WCAG guidance for common issues
        guidance_map = {
            "image-alt": """
WCAG Criterion: 1.1.1 Non-text Content (Level A)
All images must have alt text describing their purpose/content.
- Informative images: Describe what the image shows
- Functional images (links, buttons): Describe the action
- Decorative images: Use alt=""

Good examples:
- <img src="chart.png" alt="Sales increased 25% in Q3 2023">
- <img src="search-icon.png" alt="Search">
- <img src="decoration.png" alt="">
""",
            "button-name": """
WCAG Criterion: 4.1.2 Name, Role, Value (Level A)
All buttons must have accessible names for screen readers.
- Use visible text content (preferred)
- Use aria-label for icon buttons
- Use aria-labelledby to reference other elements

Good examples:
- <button>Submit Form</button>
- <button aria-label="Close dialog"><span class="icon-x"></span></button>
- <button aria-labelledby="save-label"><span id="save-label">Save</span></button>
""",
            "link-name": """
WCAG Criterion: 2.4.4 Link Purpose (Level A), 4.1.2 Name, Role, Value
All links must have accessible names describing their purpose.
- Use descriptive link text (not "click here")
- Use aria-label for icon links
- Include context in the link text itself

Good examples:
- <a href="/contact">Contact Us</a>
- <a href="/report.pdf">Download Annual Report (PDF, 2MB)</a>
- <a href="#" aria-label="Share on Twitter"><i class="twitter-icon"></i></a>
""",
            "color-contrast": """
WCAG Criterion: 1.4.3 Contrast (Level AA)
Text must have sufficient contrast with background:
- Normal text: 4.5:1 minimum
- Large text (18pt+ or 14pt+ bold): 3:1 minimum
- UI components: 3:1 minimum

Good examples:
- Normal text: #000000 on #FFFFFF (21:1) ✓
- Normal text: #595959 on #FFFFFF (4.54:1) ✓
- Large text: #767676 on #FFFFFF (3.0:1) ✓
Use contrast checker tools to verify.
""",
            "label": """
WCAG Criterion: 3.3.2 Labels or Instructions (Level A), 1.3.1 Info and Relationships
All form inputs must have associated labels.
- Use <label> with for/id association (preferred)
- Use aria-label for special cases
- Placeholders are NOT labels

Good examples:
- <label for="email">Email Address</label>
  <input id="email" type="email">
- <label>Name: <input type="text" name="name"></label>
- <input type="search" aria-label="Search site" placeholder="Enter keywords">
""",
            "heading-order": """
WCAG Criterion: 1.3.1 Info and Relationships (Level A)
Headings must follow sequential order without skipping levels.
- Start with <h1> (one per page)
- Progress: h1 → h2 → h3 (not h1 → h3)
- Can go backwards: h3 → h2 ✓

Good examples:
- <h1>Page Title</h1>
  <h2>Section</h2>
    <h3>Subsection</h3>
    <h3>Another Subsection</h3>
  <h2>Another Section</h2>
""",
            "frame-title": """
WCAG Criterion: 4.1.2 Name, Role, Value (Level A)
All frames and iframes must have descriptive title attributes.

Good examples:
- <iframe src="video.html" title="Product demonstration video"></iframe>
- <iframe src="map.html" title="Office location map"></iframe>
- <iframe src="ad.html" title="Advertisement"></iframe>
""",
            "landmark-one-main": """
WCAG Criterion: 1.3.1 Info and Relationships (Level A), 2.4.1 Bypass Blocks
Every page must have exactly one main landmark.
- Use <main> element OR role="main"
- Contains the primary content
- Only one per page

Good examples:
- <main>Primary page content here</main>
- <div role="main">Primary content</div>
""",
            "landmark-unique": """
WCAG Criterion: 1.3.1 Info and Relationships (Level A)
Landmarks of the same type must have unique accessible names.
- Use aria-label or aria-labelledby
- Required when multiple nav, aside, etc.

Good examples:
- <nav aria-label="Main navigation">...</nav>
- <nav aria-label="Footer navigation">...</nav>
- <aside aria-label="Related articles">...</aside>
""",
            "region": """
WCAG Criterion: 1.3.1 Info and Relationships (Level A)
All page content should be contained in landmarks.
- Use semantic HTML5 elements: <header>, <nav>, <main>, <aside>, <footer>
- Or use ARIA roles: role="banner", role="navigation", etc.

Good structure:
<body>
  <header>Banner content</header>
  <nav>Navigation links</nav>
  <main>Primary content</main>
  <aside>Sidebar content</aside>
  <footer>Footer content</footer>
</body>
""",
            "list": """
WCAG Criterion: 1.3.1 Info and Relationships (Level A)
Lists must be properly structured.
- <ul>/<ol> can only contain <li> children
- <dl> can only contain <dt>/<dd> children
- <li> must be inside <ul> or <ol>

Good examples:
- <ul><li>Item 1</li><li>Item 2</li></ul>
- <ol><li>First</li><li>Second</li></ol>
- <dl><dt>Term</dt><dd>Definition</dd></dl>
""",
            "listitem": """
WCAG Criterion: 1.3.1 Info and Relationships (Level A)
List items (<li>) must be contained in <ul> or <ol>.

Good examples:
- <ul><li>Item</li></ul>
- <ol><li>Step 1</li><li>Step 2</li></ol>

Bad example:
- <li>Orphan item</li> (not inside list)
""",
            "td-has-header": """
WCAG Criterion: 1.3.1 Info and Relationships (Level A)
Data cells in tables must reference header cells.
- Use <th> elements for headers
- Use scope attribute for simple tables
- Use headers attribute for complex tables

Good example:
<table>
  <tr>
    <th scope="col">Name</th>
    <th scope="col">Age</th>
  </tr>
  <tr>
    <td>John</td>
    <td>25</td>
  </tr>
</table>
""",
            "th-has-data-cells": """
WCAG Criterion: 1.3.1 Info and Relationships (Level A)
Table header cells (<th>) must relate to data cells.
- Use scope="col" for column headers
- Use scope="row" for row headers
- Ensure headers are in <thead> or first row/column

Good example:
<th scope="col">Product</th>
<th scope="row">Total</th>
""",
            "link-in-text-block": """
WCAG Criterion: 1.4.1 Use of Color (Level A)
Links in text must be distinguishable by more than color.
- Add underline (most common)
- Add bold, border, or icon
- OR maintain 3:1 contrast ratio between link and surrounding text

Good examples:
- <a href="..." style="text-decoration: underline">Link text</a>
- <a href="..." class="with-underline">Link text</a>
""",
        }

        return guidance_map.get(
            issue_id,
            """
WCAG 2.1 Level AA Compliance Required.
Refer to axe-core documentation for specific criterion details.
""",
        )

    def _clean_code_fences(self, code: str) -> str:
        """
        Remove markdown code fences and language identifiers from AI-generated code.

        Args:
            code: Raw code from AI (may include ```html markers)

        Returns:
            Cleaned code without markdown formatting
        """
        if "```" not in code:
            return code

        # Extract code between ``` markers
        parts = code.split("```")
        for part in parts:
            # Skip language identifiers like "html", "css", "javascript"
            part = part.strip()
            if part and not part.lower().startswith(
                ("html", "css", "javascript", "js", "xml")
            ):
                return part

        # Fallback: return original if extraction failed
        return code

    def _validate_code_fix(self, code_fix: str, issue_id: str) -> bool:
        """
        Validate generated code fix for basic correctness.

        Checks:
        - Contains HTML tags
        - Has balanced tags (opening/closing)
        - No obvious placeholder text like [describe...]
        - Contains relevant attributes for the issue type

        Args:
            code_fix: Generated code fix
            issue_id: axe-core rule ID

        Returns:
            True if fix passes validation, False otherwise
        """
        if not code_fix or len(code_fix.strip()) < 3:
            return False

        # Must contain HTML tags
        if "<" not in code_fix or ">" not in code_fix:
            logger.warning(f"[VALIDATION] No HTML tags in fix for {issue_id}")
            return False

        # Check for placeholder text (indicates incomplete fix)
        placeholder_patterns = [
            "[describe",
            "[your",
            "[add",
            "[insert",
            "[content",
            "placeholder text",
            "describe the",
            "describe this",
        ]
        code_lower = code_fix.lower()
        for pattern in placeholder_patterns:
            if pattern in code_lower:
                logger.warning(
                    f"[VALIDATION] Placeholder text found in fix for {issue_id}: {pattern}"
                )
                return False

        # Issue-specific validation
        validation_rules = {
            "image-alt": "alt=",
            "button-name": ("aria-label=", ">"),  # Either aria-label or text content
            "link-name": ("aria-label=", ">"),
            "label": ("for=", "<label"),
            "frame-title": "title=",
        }

        if issue_id in validation_rules:
            required = validation_rules[issue_id]
            if isinstance(required, tuple):
                # At least one of the options must be present
                if not any(opt in code_fix for opt in required):
                    logger.warning(
                        f"[VALIDATION] Missing required attribute for {issue_id}: {required}"
                    )
                    return False
            else:
                # Single required attribute
                if required not in code_fix:
                    logger.warning(
                        f"[VALIDATION] Missing required attribute for {issue_id}: {required}"
                    )
                    return False

        # Basic HTML balance check (simplified)
        # Count opening vs closing tags
        opening_tags = code_fix.count("<") - code_fix.count("</")
        closing_tags = code_fix.count("</")

        # Allow self-closing tags and snippets
        # Just warn if severely unbalanced
        if abs(opening_tags - closing_tags) > 3:
            logger.warning(
                f"[VALIDATION] Potentially unbalanced tags in fix for {issue_id}"
            )
            # Don't fail validation - snippet might be intentionally partial
            # return False

        logger.info(f"[VALIDATION] Code fix passed validation for {issue_id}")
        return True

    def _get_wcag_guideline_from_rag(self, rule_id: str) -> Optional[Dict]:
        """
        Query WCAG Knowledge Base (RAG) for canonical guideline information.

        Args:
            rule_id: axe-core rule ID (e.g., "button-name", "color-contrast")

        Returns:
            Dictionary with WCAG guideline info including fix_examples and best_practices,
            or None if not found
        """
        try:
            # Connect to PostgreSQL database with timeout
            conn = psycopg2.connect(self.database_url, connect_timeout=10)
            cur = conn.cursor()

            # Set statement timeout (10 seconds)
            cur.execute("SET statement_timeout = 10000")  # 10 seconds in milliseconds

            # Query for the specific rule
            cur.execute(
                """
                SELECT
                    rule_id,
                    wcag_criterion,
                    wcag_level,
                    title,
                    description,
                    principle,
                    guideline,
                    severity_criteria,
                    business_impact_template,
                    technical_impact,
                    fix_examples,
                    best_practices,
                    tags
                FROM wcag_guidelines
                WHERE rule_id = %s
            """,
                (rule_id,),
            )

            row = cur.fetchone()
            cur.close()
            conn.close()

            if not row:
                logger.debug(f"[RAG] No guideline found for rule_id: {rule_id}")
                return None

            # Convert to dictionary
            guideline = {
                "rule_id": row[0],
                "wcag_criterion": row[1],
                "wcag_level": row[2],
                "title": row[3],
                "description": row[4],
                "principle": row[5],
                "guideline": row[6],
                "severity_criteria": row[7],  # JSONB field
                "business_impact_template": row[8],
                "technical_impact": row[9],
                "fix_examples": row[10],  # JSONB array
                "best_practices": row[11],  # JSONB array
                "tags": row[12],  # ARRAY field
            }

            logger.info(
                f"[RAG] Retrieved guideline for {rule_id}: {guideline.get('title', 'Unknown')}"
            )
            return guideline

        except Exception as e:
            logger.warning(f"[RAG] Failed to query knowledge base for {rule_id}: {e}")
            return None

    def _enhance_fix_description(
        self, description: str, failure_summary: str, element_html: str, issue_id: str
    ) -> Optional[str]:
        """
        Use Ollama + RAG to transform technical axe-core fix descriptions into human-friendly,
        actionable guidance grounded in canonical WCAG guidelines.

        Args:
            description: Issue description from axe
            failure_summary: Technical fix description from axe-core
            element_html: The problematic HTML element
            issue_id: axe rule ID (e.g., "color-contrast", "image-alt")

        Returns:
            Enhanced, human-friendly fix description, or None if enhancement fails
        """
        try:
            # Query RAG database for canonical WCAG guideline
            wcag_guideline = self._get_wcag_guideline_from_rag(issue_id)

            # Build prompt with RAG context if available
            if wcag_guideline:
                # Extract fix examples and best practices from RAG
                fix_examples = wcag_guideline.get("fix_examples", [])
                best_practices = wcag_guideline.get("best_practices", [])
                canonical_description = wcag_guideline.get("description", "")

                # Format RAG context
                rag_context = f"""
CANONICAL WCAG GUIDELINE ({wcag_guideline.get('wcag_criterion', '')} Level {wcag_guideline.get('wcag_level', '')}):
{canonical_description}

FIX EXAMPLES:
{json.dumps(fix_examples, indent=2) if fix_examples else 'No specific examples available'}

BEST PRACTICES:
{json.dumps(best_practices, indent=2) if best_practices else 'No specific practices listed'}
"""
            else:
                rag_context = "(No canonical guideline found in knowledge base)"

            # Build prompt for Ollama with RAG-grounded context
            prompt = f"""You are an accessibility expert helping developers fix WCAG issues. Transform this technical fix description into clear, actionable guidance using the canonical WCAG guidelines below.

{rag_context}

DETECTED ISSUE:
Issue: {description}
Rule ID: {issue_id}

Technical fix description from axe-core:
{failure_summary}

Element:
{element_html[:300]}

Using the canonical WCAG guidance above, rewrite the fix description to be:
1. Clear and conversational (not robotic)
2. Specific and actionable (concrete steps from best practices)
3. Brief (2-3 sentences maximum)
4. Grounded in WCAG standards (use the fix examples above)
5. Focused on WHAT to do and WHY it matters for accessibility

Provide ONLY the improved fix description (no explanations, no introductions):"""

            # Call Gemini for enhanced fix description
            try:
                result = self.llm_client.generate_text_sync(
                    prompt=prompt, max_tokens=250, temperature=0.3
                )

                if not result.get("success"):
                    logger.warning(
                        f"[AI+RAG] Fix description enhancement failed for {issue_id}: {result.get('error')}"
                    )
                    return None

            except Exception as e:
                logger.warning(
                    f"[AI+RAG] Fix description enhancement failed for {issue_id}: {e}"
                )
                return None

            # Extract enhanced description
            enhanced_description = result["content"].strip()

            # Basic validation: should be reasonably short and not empty
            if (
                enhanced_description
                and len(enhanced_description) > 20
                and len(enhanced_description) < 600
            ):
                logger.info(
                    f"[AI+RAG] Enhanced fix description for {issue_id} (RAG: {'✓' if wcag_guideline else '✗'})"
                )
                return enhanced_description
            else:
                logger.warning(
                    f"[AI+RAG] Enhanced fix description rejected (length: {len(enhanced_description)})"
                )
                return None

        except Exception as e:
            logger.warning(f"Failed to enhance fix description for {issue_id}: {e}")
            return None

    def _scan_page_math(self, page: Page) -> List[MathContentResult]:
        """
        Scan page for LaTeX/MathML mathematical content and convert to accessible format

        Args:
            page: Playwright page object

        Returns:
            List of math content results with accessibility information
        """
        results = []

        try:
            # Keep user informed about math analysis
            if self.progress_callback:
                current_page = getattr(self, "_current_page", 1)
                self.progress_callback(
                    current_page, self.max_pages, "Analyzing mathematical content..."
                )

            # Detect various math formats on the page

            # 1. MathML elements
            mathml_elements = page.query_selector_all("math, mml\\:math")
            for elem in mathml_elements:
                try:
                    content = elem.inner_html()
                    alt_text = elem.get_attribute("alttext") or elem.get_attribute(
                        "aria-label"
                    )

                    results.append(
                        MathContentResult(
                            format="mathml",
                            content=content[:500],  # Truncate long expressions
                            has_alt_text=bool(alt_text),
                            suggested_alt_text=(
                                self._describe_math_with_ai(content)
                                if not alt_text
                                else None
                            ),
                            accessible_mathml=content,  # Already MathML
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to process MathML: {e}")

            # 2. LaTeX in various delimiters
            # Check for common LaTeX patterns: $...$, $$...$$, \[...\], \(...\)
            page_text = page.evaluate("() => document.body.innerText")

            import re

            # Find inline math $...$
            inline_matches = re.findall(r"\$([^\$]+)\$", page_text)
            # Find display math $$...$$
            display_matches = re.findall(r"\$\$([^\$]+)\$\$", page_text)

            for latex in (inline_matches + display_matches)[:10]:  # Limit to 10
                latex = latex.strip()
                if len(latex) > 5:  # Skip very short expressions
                    results.append(
                        MathContentResult(
                            format="latex",
                            content=latex[:500],
                            has_alt_text=False,
                            suggested_alt_text=self._describe_math_with_ai(latex),
                            accessible_mathml=self._convert_latex_to_mathml(latex),
                        )
                    )

            # 3. MathJax/KaTeX containers
            mathjax_elements = page.query_selector_all(
                '[class*="MathJax"], [class*="katex"]'
            )
            for elem in mathjax_elements[:10]:  # Limit to 10
                try:
                    # Try to extract original LaTeX if available
                    latex = elem.get_attribute("data-latex") or elem.get_attribute(
                        "data-math"
                    )
                    if latex:
                        results.append(
                            MathContentResult(
                                format=(
                                    "mathjax"
                                    if "MathJax" in elem.get_attribute("class")
                                    else "katex"
                                ),
                                content=latex[:500],
                                has_alt_text=bool(elem.get_attribute("aria-label")),
                                suggested_alt_text=self._describe_math_with_ai(latex),
                                accessible_mathml=self._convert_latex_to_mathml(latex),
                            )
                        )
                except Exception as e:
                    logger.warning(f"Failed to process MathJax/KaTeX: {e}")

        except Exception as e:
            logger.error(f"Failed to scan math content: {e}")

        return results

    def _describe_math_with_ai(self, math_expression: str) -> Optional[str]:
        """
        Use Ollama to generate natural language description of math expression

        Args:
            math_expression: LaTeX or MathML expression

        Returns:
            Natural language description
        """
        try:
            prompt = f"""Describe this mathematical expression in clear, accessible language:

{math_expression}

Provide a concise description (1-2 sentences) that would help a screen reader user understand the math. Focus on the meaning, not just the symbols."""

            # Call Gemini for math description
            try:
                result = self.llm_client.generate_text_sync(
                    prompt=prompt, max_tokens=150, temperature=0.2
                )
                if result.get("success"):
                    return result["content"].strip()
                else:
                    logger.warning(
                        f"[AI] Math description failed: {result.get('error')}"
                    )
                    return None
            except Exception as e:
                logger.warning(f"[AI] Math description failed: {e}")
                return None

        except Exception as e:
            logger.warning(f"Failed to describe math with AI: {e}")
            return None

    def _convert_latex_to_mathml(self, latex: str) -> Optional[str]:
        """
        Convert LaTeX expression to accessible MathML

        Args:
            latex: LaTeX expression

        Returns:
            MathML string or None if conversion fails
        """
        try:
            # Use latex2mathml library if available
            try:
                from latex2mathml.converter import convert

                mathml = convert(latex)
                return mathml
            except ImportError:
                # Fallback: return basic MathML structure
                return f'<math xmlns="http://www.w3.org/1998/Math/MathML"><mtext>{latex}</mtext></math>'

        except Exception as e:
            logger.warning(f"Failed to convert LaTeX to MathML: {e}")
            return None
