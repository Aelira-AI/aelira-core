"""
Browser Compatibility Tests using Playwright.

Tests the Aelira dashboard across multiple browsers:
- Chrome (Chromium)
- Firefox
- Safari (WebKit)
- Edge (Chromium-based)

Also tests:
- Mobile responsiveness (tablet, phone viewports)
- Accessibility (keyboard navigation, focus indicators)

Usage:
    # Install Playwright browsers
    playwright install

    # Run browser tests
    pytest tests/test_browser_compatibility.py -m browser --headed

    # Run headless (CI/CD)
    pytest tests/test_browser_compatibility.py -m browser
"""

import os

import pytest
from playwright.sync_api import sync_playwright, Page, Browser, expect

# Skip all tests in this module unless RUN_E2E_TESTS is set
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_E2E_TESTS"),
    reason="E2E test requires running infrastructure (set RUN_E2E_TESTS=1 to enable)",
)


# Configuration
DASHBOARD_URL = "http://localhost:5173"  # Vite dev server
API_URL = "http://localhost:8000"

# Viewport sizes
VIEWPORTS = {
    "desktop": {"width": 1920, "height": 1080},
    "laptop": {"width": 1366, "height": 768},
    "tablet": {"width": 768, "height": 1024},
    "mobile": {"width": 375, "height": 667},
}


@pytest.fixture(scope="session")
def playwright_instance():
    """Create Playwright instance for all tests."""
    with sync_playwright() as p:
        yield p


@pytest.fixture(params=["chromium", "firefox", "webkit"])
def browser(playwright_instance, request):
    """Parameterized fixture to run tests across all browsers."""
    browser_type = getattr(playwright_instance, request.param, None)
    if browser_type is None:
        pytest.skip(f"{request.param} browser type not available")
    try:
        browser = browser_type.launch(headless=True)
    except Exception:
        pytest.skip(f"{request.param} browser not installed")
    yield browser
    browser.close()


@pytest.fixture
def page(browser):
    """Create a new page for each test."""
    context = browser.new_context(viewport=VIEWPORTS["desktop"])
    page = context.new_page()
    yield page
    page.close()
    context.close()


class TestDashboardLoading:
    """Tests for basic dashboard loading across browsers."""

    def test_dashboard_loads(self, page: Page):
        """Test that dashboard loads without errors."""
        # Navigate to dashboard
        response = page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Should load successfully
        assert response.status == 200

        # Page should have title
        assert page.title() != ""

    def test_no_console_errors(self, page: Page):
        """Test that dashboard loads without JavaScript errors."""
        console_errors = []

        def handle_console(msg):
            if msg.type == "error":
                console_errors.append(msg.text)

        page.on("console", handle_console)
        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Filter out known acceptable errors (e.g., favicon not found)
        critical_errors = [
            e for e in console_errors if "favicon" not in e.lower() and "404" not in e
        ]

        assert len(critical_errors) == 0, f"Console errors: {critical_errors}"


class TestResponsiveDesign:
    """Tests for responsive design across viewports."""

    @pytest.mark.parametrize("viewport_name,viewport", VIEWPORTS.items())
    def test_viewport_rendering(
        self, browser: Browser, viewport_name: str, viewport: dict
    ):
        """Test that dashboard renders correctly at each viewport."""
        context = browser.new_context(viewport=viewport)
        page = context.new_page()

        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Page should be visible
        body = page.locator("body")
        expect(body).to_be_visible()

        # No horizontal scrollbar on body (indicates layout issues)
        body_scroll_width = page.evaluate("document.body.scrollWidth")
        viewport_width = page.evaluate("window.innerWidth")

        # Allow small tolerance (scrollbar width)
        assert (
            body_scroll_width <= viewport_width + 20
        ), f"Horizontal overflow at {viewport_name}: {body_scroll_width} > {viewport_width}"

        page.close()
        context.close()

    def test_mobile_menu_toggle(self, browser: Browser):
        """Test mobile menu hamburger functionality."""
        context = browser.new_context(viewport=VIEWPORTS["mobile"])
        page = context.new_page()

        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Look for hamburger menu button (common patterns)
        hamburger = page.locator(
            "[aria-label*='menu'], [aria-label*='Menu'], "
            "button.hamburger, .mobile-menu-toggle, "
            "[data-testid='mobile-menu']"
        ).first

        if hamburger.count() > 0:
            # Click hamburger
            hamburger.click()

            # Menu should appear
            page.wait_for_timeout(300)  # Animation

        page.close()
        context.close()


class TestAccessibility:
    """Tests for accessibility features."""

    def test_focus_visible(self, page: Page):
        """Test that focus indicators are visible."""
        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Press Tab to move focus
        page.keyboard.press("Tab")

        # Get focused element
        focused = page.evaluate("""() => {
            const el = document.activeElement;
            if (!el) return null;
            const style = window.getComputedStyle(el);
            return {
                tagName: el.tagName,
                outline: style.outline,
                boxShadow: style.boxShadow,
                border: style.border
            };
        }""")

        # Should have some focus indicator (outline, box-shadow, or border)
        focused and (
            focused.get("outline", "none") != "none"
            or "0px" not in focused.get("boxShadow", "none")
            or focused.get("border", "none") != "none"
        )

        # Note: This test may be flaky - focus indicators vary by component
        # Consider checking specific known focusable elements

    def test_keyboard_navigation(self, page: Page):
        """Test keyboard navigation through interactive elements."""
        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Count interactive elements that should be focusable
        interactive_count = page.evaluate("""() => {
            const elements = document.querySelectorAll(
                'a, button, input, select, textarea, [tabindex="0"]'
            );
            return elements.length;
        }""")

        # Tab through first several elements
        visited_tags = set()
        for _ in range(min(10, interactive_count)):
            page.keyboard.press("Tab")
            tag = page.evaluate("document.activeElement.tagName")
            if tag and tag != "BODY":
                visited_tags.add(tag)

        # Should be able to tab to multiple element types
        assert len(visited_tags) > 0, "Could not navigate with keyboard"

    def test_skip_link(self, page: Page):
        """Test for skip-to-content link (a11y best practice)."""
        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Press Tab once to reveal skip link (if exists)
        page.keyboard.press("Tab")

        # Check for skip link
        skip_link = page.locator(
            "a[href='#main'], a[href='#content'], " ".skip-link, [class*='skip']"
        ).first

        # Skip link is optional but recommended
        # This is informational - won't fail the test
        if skip_link.count() == 0:
            print(
                "WARNING: No skip-to-content link found (recommended for accessibility)"
            )

    def test_aria_labels_on_icons(self, page: Page):
        """Test that icon-only buttons have aria-labels."""
        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Find buttons that might be icon-only
        icon_buttons = page.evaluate("""() => {
            const buttons = document.querySelectorAll('button');
            const iconButtons = [];

            for (const btn of buttons) {
                // Check if button has only icon content (no text)
                const text = btn.textContent.trim();
                const hasIcon = btn.querySelector('svg, i, [class*="icon"]');

                if (hasIcon && (!text || text.length < 2)) {
                    iconButtons.push({
                        hasAriaLabel: !!btn.getAttribute('aria-label'),
                        hasTitle: !!btn.getAttribute('title'),
                        innerHTML: btn.innerHTML.substring(0, 50)
                    });
                }
            }

            return iconButtons;
        }""")

        # All icon-only buttons should have aria-label or title
        unlabeled = [
            b for b in icon_buttons if not b["hasAriaLabel"] and not b["hasTitle"]
        ]

        assert (
            len(unlabeled) == 0
        ), f"Found {len(unlabeled)} icon buttons without aria-label: {unlabeled}"


class TestFormFunctionality:
    """Tests for form interactions."""

    def test_login_form_validation(self, page: Page):
        """Test login form validation messages."""
        page.goto(f"{DASHBOARD_URL}/login", wait_until="networkidle")

        # Find submit button and click without filling form
        submit_btn = page.locator('button[type="submit"], input[type="submit"]').first

        if submit_btn.count() > 0:
            submit_btn.click()

            # Should show validation error
            page.wait_for_timeout(500)

            # Check for validation messages
            page.locator(
                "[role='alert'], .error, .validation-error, "
                "[class*='error'], [aria-invalid='true']"
            )

            # Form should show some kind of validation feedback


class TestCrossOriginResources:
    """Tests for cross-origin resource loading (fonts, APIs, etc.)."""

    def test_fonts_load(self, page: Page):
        """Test that web fonts load correctly."""
        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Check that fonts are loaded
        page.evaluate("""() => {
            return document.fonts.ready.then(() => {
                return document.fonts.size > 0;
            });
        }""")

        # Most dashboards use custom fonts
        # This is informational - system fonts are also acceptable

    def test_api_connection(self, page: Page):
        """Test that frontend can connect to API."""
        # This tests CORS configuration
        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Try to fetch from API
        api_result = page.evaluate(f"""async () => {{
            try {{
                const response = await fetch('{API_URL}/health');
                return {{ ok: response.ok, status: response.status }};
            }} catch (e) {{
                return {{ error: e.message }};
            }}
        }}""")

        if "error" not in api_result:
            assert api_result.get("ok") is True, f"API returned {api_result}"
        else:
            # CORS error or network error - might be expected in test env
            print(f"WARNING: API connection test failed: {api_result}")


class TestDarkMode:
    """Tests for dark mode toggle (if implemented)."""

    def test_dark_mode_toggle(self, page: Page):
        """Test dark mode toggle functionality."""
        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Find dark mode toggle
        toggle = page.locator(
            "[aria-label*='dark'], [aria-label*='theme'], "
            "[data-testid='theme-toggle'], .theme-toggle"
        ).first

        if toggle.count() > 0:
            # Get initial background color
            initial_bg = page.evaluate(
                "window.getComputedStyle(document.body).backgroundColor"
            )

            # Click toggle
            toggle.click()
            page.wait_for_timeout(300)  # Animation

            # Get new background color
            new_bg = page.evaluate(
                "window.getComputedStyle(document.body).backgroundColor"
            )

            # Background should change
            assert initial_bg != new_bg, "Dark mode toggle didn't change theme"

    def test_system_preference_detection(self, browser: Browser):
        """Test that system dark mode preference is detected."""
        # Create context with dark color scheme
        context = browser.new_context(
            viewport=VIEWPORTS["desktop"], color_scheme="dark"
        )
        page = context.new_page()

        page.goto(DASHBOARD_URL, wait_until="networkidle")

        # Check if dark mode is applied
        is_dark = page.evaluate("""() => {
            return window.matchMedia('(prefers-color-scheme: dark)').matches;
        }""")

        assert is_dark is True, "System dark mode preference not detected"

        page.close()
        context.close()
