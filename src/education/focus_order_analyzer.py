"""
Focus Order Analysis Module (NerdeFocus Integration)

This module provides functionality to:
1. Analyze keyboard focus order in HTML/web content
2. Detect focus traps, skip link failures, and modal focus issues
3. Simulate TAB key navigation and track focus sequence
4. Validate WCAG 2.4.3 (Focus Order) compliance
5. Identify invisible elements in focus order

Based on NerdeFocus approach: Visualize and validate keyboard navigation order
"""

from typing import List, Dict, Optional
from pydantic import BaseModel
from playwright.async_api import async_playwright, Page
import asyncio
import logging

logger = logging.getLogger(__name__)


class FocusableElement(BaseModel):
    """Represents a focusable element in the DOM"""

    element_id: int  # Sequential number in focus order
    tag_name: str
    selector: str  # CSS selector
    xpath: Optional[str] = None
    text_content: Optional[str] = None
    aria_label: Optional[str] = None
    role: Optional[str] = None
    is_visible: bool
    is_offscreen: bool  # Visible but positioned off-screen
    bounding_box: Optional[Dict[str, float]] = None  # {x, y, width, height}
    tab_index: Optional[int] = None


class FocusOrderIssue(BaseModel):
    """Detected issue in focus order"""

    issue_type: str  # "focus_trap", "invisible_element", "illogical_order", "skip_link_failure", "missing_focus_indicator"
    severity: str  # "critical", "serious", "moderate", "minor"
    description: str
    element: Optional[FocusableElement] = None
    suggested_fix: Optional[str] = None
    wcag_criterion: str = "2.4.3"  # Focus Order (Level A)


class FocusOrderResult(BaseModel):
    """Result of focus order analysis"""

    url: str
    total_focusable_elements: int
    focus_sequence: List[FocusableElement]
    issues: List[FocusOrderIssue]
    compliance_score: float  # 0-100
    wcag_compliant: bool


class FocusOrderAnalyzer:
    """Analyze keyboard focus order in web content"""

    def __init__(self):
        self.playwright = None
        self.browser = None

    async def analyze_focus_order(
        self, url: str, max_tabs: int = 100
    ) -> FocusOrderResult:
        """
        Analyze focus order for a given URL

        Args:
            url: URL to analyze
            max_tabs: Maximum number of TAB keys to simulate (default: 100)

        Returns:
            FocusOrderResult with focus sequence and detected issues
        """
        async with async_playwright() as p:
            self.playwright = p
            self.browser = await p.chromium.launch(headless=True)
            page = await self.browser.new_page()

            try:
                # Load the page
                await page.goto(url, wait_until="networkidle")

                # Get all focusable elements
                focus_sequence = await self._track_focus_sequence(page, max_tabs)

                # Analyze for issues
                issues = await self._detect_focus_issues(page, focus_sequence)

                # Calculate compliance score
                compliance_score = self._calculate_compliance_score(
                    focus_sequence, issues
                )

                # Check WCAG compliance
                wcag_compliant = compliance_score >= 80 and not any(
                    issue.severity == "critical" for issue in issues
                )

                return FocusOrderResult(
                    url=url,
                    total_focusable_elements=len(focus_sequence),
                    focus_sequence=focus_sequence,
                    issues=issues,
                    compliance_score=compliance_score,
                    wcag_compliant=wcag_compliant,
                )

            finally:
                await page.close()
                await self.browser.close()

    async def _track_focus_sequence(
        self, page: Page, max_tabs: int
    ) -> List[FocusableElement]:
        """
        Track focus sequence by simulating TAB key navigation

        Args:
            page: Playwright page object
            max_tabs: Maximum number of TAB keys to simulate

        Returns:
            List of FocusableElement objects in focus order
        """
        focus_sequence = []
        seen_selectors = set()
        previous_selector = None

        # Focus the body first
        await page.evaluate("document.body.focus()")

        for i in range(max_tabs):
            # Press TAB
            await page.keyboard.press("Tab")
            await asyncio.sleep(0.05)  # Wait for focus to settle

            # Get currently focused element
            element_info = await page.evaluate("""
                () => {
                    const el = document.activeElement;
                    if (!el || el === document.body) return null;

                    // Get unique selector
                    const selector = el.id
                        ? `#${el.id}`
                        : `${el.tagName.toLowerCase()}:nth-of-type(${
                            Array.from(el.parentElement?.children || [])
                                .filter(c => c.tagName === el.tagName)
                                .indexOf(el) + 1
                        })`;

                    // Get bounding box
                    const rect = el.getBoundingClientRect();

                    // Check if element is visible
                    const style = window.getComputedStyle(el);
                    const isVisible = style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0'
                        && rect.width > 0
                        && rect.height > 0;

                    // Check if element is off-screen
                    const isOffscreen = (
                        rect.right < 0 ||
                        rect.bottom < 0 ||
                        rect.left > window.innerWidth ||
                        rect.top > window.innerHeight
                    ) && isVisible;

                    return {
                        tagName: el.tagName.toLowerCase(),
                        selector: selector,
                        textContent: el.textContent?.trim().substring(0, 100),
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
                }
            """)

            # Stop if we've looped back to the start
            if element_info is None:
                break

            current_selector = element_info["selector"]

            # Detect focus loop (went back to a previously seen element)
            if current_selector in seen_selectors:
                # Check if we've completed one full loop
                if current_selector == (
                    focus_sequence[0].selector if focus_sequence else None
                ):
                    break
                # Otherwise, continue tracking (might be a focus trap)

            # Avoid duplicates in sequence
            if current_selector == previous_selector:
                continue

            seen_selectors.add(current_selector)
            previous_selector = current_selector

            # Create FocusableElement
            focusable = FocusableElement(
                element_id=len(focus_sequence),
                tag_name=element_info["tagName"],
                selector=element_info["selector"],
                text_content=element_info["textContent"],
                aria_label=element_info["ariaLabel"],
                role=element_info["role"],
                is_visible=element_info["isVisible"],
                is_offscreen=element_info["isOffscreen"],
                bounding_box=element_info["boundingBox"],
                tab_index=element_info["tabIndex"],
            )

            focus_sequence.append(focusable)

        return focus_sequence

    async def _detect_focus_issues(
        self, page: Page, focus_sequence: List[FocusableElement]
    ) -> List[FocusOrderIssue]:
        """
        Detect focus order issues

        Args:
            page: Playwright page object
            focus_sequence: List of focusable elements in order

        Returns:
            List of detected issues
        """
        issues = []

        # 1. Detect invisible elements in focus order
        for element in focus_sequence:
            if not element.is_visible:
                issues.append(
                    FocusOrderIssue(
                        issue_type="invisible_element",
                        severity="serious",
                        description=f"Element is in focus order but not visible: {element.selector}",
                        element=element,
                        suggested_fix="Remove tabindex or set to tabindex='-1' for invisible elements, or ensure element is visible when focused.",
                        wcag_criterion="2.4.3",
                    )
                )

        # 2. Detect off-screen elements (potential skip links)
        offscreen_elements = [el for el in focus_sequence if el.is_offscreen]
        if offscreen_elements:
            # Check if these are legitimate skip links
            for element in offscreen_elements:
                # Skip links typically have text like "Skip to main content"
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
                            suggested_fix="If this is a skip link, ensure it becomes visible on focus. If not, remove from focus order.",
                            wcag_criterion="2.4.3",
                        )
                    )

        # 3. Detect illogical focus order (large jumps in visual position)
        for i in range(1, len(focus_sequence)):
            prev = focus_sequence[i - 1]
            current = focus_sequence[i]

            if prev.bounding_box and current.bounding_box:
                # Calculate distance between elements
                prev_center_x = prev.bounding_box["x"] + prev.bounding_box["width"] / 2
                prev_center_y = prev.bounding_box["y"] + prev.bounding_box["height"] / 2
                current_center_x = (
                    current.bounding_box["x"] + current.bounding_box["width"] / 2
                )
                current_center_y = (
                    current.bounding_box["y"] + current.bounding_box["height"] / 2
                )

                distance = (
                    (current_center_x - prev_center_x) ** 2
                    + (current_center_y - prev_center_y) ** 2
                ) ** 0.5

                # Flag if distance is very large (> 500px jump)
                if distance > 500:
                    issues.append(
                        FocusOrderIssue(
                            issue_type="illogical_order",
                            severity="moderate",
                            description=f"Large visual jump in focus order (from {prev.selector} to {current.selector})",
                            element=current,
                            suggested_fix="Reorder HTML or use tabindex to create logical focus order that matches visual layout.",
                            wcag_criterion="2.4.3",
                        )
                    )

        # 4. Detect focus traps (elements that don't allow tabbing out)
        # Check if focus sequence is very short (< 5 elements) but page has more content
        total_interactive_elements = await page.evaluate("""
            () => {
                const selectors = 'a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])';
                return document.querySelectorAll(selectors).length;
            }
        """)

        if total_interactive_elements > 10 and len(focus_sequence) < 5:
            issues.append(
                FocusOrderIssue(
                    issue_type="focus_trap",
                    severity="critical",
                    description=f"Potential focus trap detected: {total_interactive_elements} interactive elements but only {len(focus_sequence)} reachable via keyboard",
                    suggested_fix="Remove JavaScript that captures TAB key or restricts focus movement. Ensure all interactive elements are keyboard-accessible.",
                    wcag_criterion="2.1.2",
                )
            )

        # 5. Check for missing focus indicators
        # This requires visual analysis, so we'll check for CSS outline/focus styles
        missing_focus_styles = await page.evaluate("""
            () => {
                const focusableElements = document.querySelectorAll(
                    'a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])'
                );

                let elementsWithoutFocusStyle = 0;

                focusableElements.forEach(el => {
                    // Temporarily focus to check computed styles
                    const originalFocus = document.activeElement;
                    el.focus();

                    const style = window.getComputedStyle(el);
                    const hasFocusStyle = (
                        style.outline !== 'none' &&
                        style.outline !== 'rgb(0, 0, 0) none 0px' &&
                        style.outlineWidth !== '0px'
                    ) || (
                        style.boxShadow !== 'none'
                    );

                    if (!hasFocusStyle) {
                        elementsWithoutFocusStyle++;
                    }

                    // Restore original focus
                    if (originalFocus) originalFocus.focus();
                });

                return {
                    total: focusableElements.length,
                    withoutFocusStyle: elementsWithoutFocusStyle
                };
            }
        """)

        if missing_focus_styles["withoutFocusStyle"] > 0:
            percentage = (
                missing_focus_styles["withoutFocusStyle"]
                / missing_focus_styles["total"]
            ) * 100

            if percentage > 50:
                issues.append(
                    FocusOrderIssue(
                        issue_type="missing_focus_indicator",
                        severity="serious",
                        description=f"{missing_focus_styles['withoutFocusStyle']} elements ({percentage:.1f}%) lack visible focus indicators",
                        suggested_fix="Add :focus styles with visible outline or box-shadow to all interactive elements. Ensure focus is clearly visible.",
                        wcag_criterion="2.4.7",
                    )
                )

        return issues

    def _calculate_compliance_score(
        self, focus_sequence: List[FocusableElement], issues: List[FocusOrderIssue]
    ) -> float:
        """
        Calculate focus order compliance score (0-100)

        Args:
            focus_sequence: List of focusable elements
            issues: List of detected issues

        Returns:
            Compliance score (0-100)
        """
        if not focus_sequence:
            return 0.0

        # Start at 100 and deduct points for issues
        score = 100.0

        # Weight issues by severity
        severity_weights = {"critical": 30, "serious": 15, "moderate": 5, "minor": 2}

        for issue in issues:
            deduction = severity_weights.get(issue.severity, 5)
            score -= deduction

        # Ensure score is between 0 and 100
        return max(0.0, min(100.0, score))

    async def analyze_html_content(
        self, html_content: str, base_url: str = "http://localhost"
    ) -> FocusOrderResult:
        """
        Analyze focus order for HTML content (not a live URL)

        Args:
            html_content: HTML content to analyze
            base_url: Base URL for the content (default: http://localhost)

        Returns:
            FocusOrderResult with focus sequence and detected issues
        """
        async with async_playwright() as p:
            self.playwright = p
            self.browser = await p.chromium.launch(headless=True)
            page = await self.browser.new_page()

            try:
                # Load HTML content
                await page.set_content(html_content, wait_until="networkidle")

                # Get all focusable elements
                focus_sequence = await self._track_focus_sequence(page, max_tabs=100)

                # Analyze for issues
                issues = await self._detect_focus_issues(page, focus_sequence)

                # Calculate compliance score
                compliance_score = self._calculate_compliance_score(
                    focus_sequence, issues
                )

                # Check WCAG compliance
                wcag_compliant = compliance_score >= 80 and not any(
                    issue.severity == "critical" for issue in issues
                )

                return FocusOrderResult(
                    url=base_url,
                    total_focusable_elements=len(focus_sequence),
                    focus_sequence=focus_sequence,
                    issues=issues,
                    compliance_score=compliance_score,
                    wcag_compliant=wcag_compliant,
                )

            finally:
                await page.close()
                await self.browser.close()
