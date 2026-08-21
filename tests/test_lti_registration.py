"""
Tests for LTI Registration Multi-Tenant Support

Tests cover:
- Department lookup from LTI launch (Canvas and Blackboard)
- Feature gating based on tier
- LTI launch with valid/invalid registrations
- Error page rendering for denied launches
"""

import pytest
from unittest.mock import MagicMock, patch

from src.db.models import LTIRegistration, LTIPlatform, Department
from src.api.lti_routes import (
    get_department_from_lti_launch,
    check_lti_feature_access,
    update_lti_launch_stats,
    _render_lti_error_page,
)
from src.api.blackboard_lti_routes import (
    get_department_from_lti_launch as bb_get_department_from_lti_launch,
    check_lti_feature_access as bb_check_lti_feature_access,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_db_session():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def mock_department_paid():
    """Create a mock department with paid tier (has lms_integration)."""
    dept = MagicMock(spec=Department)
    dept.id = "dept-paid-123"
    dept.name = "Paid Department"
    dept.tier = "department"
    dept.is_active = True
    return dept


@pytest.fixture
def mock_department_free():
    """Create a mock department with a synthetic limited tier (no lms_integration)."""
    dept = MagicMock(spec=Department)
    dept.id = "dept-free-456"
    dept.name = "Free Department"
    dept.tier = "limited_test_tier"
    dept.is_active = True
    return dept


@pytest.fixture
def mock_department_inactive():
    """Create a mock inactive department."""
    dept = MagicMock(spec=Department)
    dept.id = "dept-inactive-789"
    dept.name = "Inactive Department"
    dept.tier = "department"
    dept.is_active = False
    return dept


@pytest.fixture
def mock_lti_registration_canvas(mock_department_paid):
    """Create a mock Canvas LTI registration."""
    reg = MagicMock(spec=LTIRegistration)
    reg.id = "reg-canvas-123"
    reg.department_id = mock_department_paid.id
    reg.platform = LTIPlatform.CANVAS
    reg.issuer = "https://canvas.university.edu"
    reg.client_id = "canvas-client-12345"
    reg.is_active = True
    reg.launch_count = 5
    reg.last_launch_at = None
    return reg


@pytest.fixture
def mock_lti_registration_blackboard(mock_department_paid):
    """Create a mock Blackboard LTI registration."""
    reg = MagicMock(spec=LTIRegistration)
    reg.id = "reg-bb-456"
    reg.department_id = mock_department_paid.id
    reg.platform = LTIPlatform.BLACKBOARD
    reg.issuer = "https://blackboard.university.edu"
    reg.client_id = "bb-client-67890"
    reg.is_active = True
    reg.launch_count = 10
    reg.last_launch_at = None
    return reg


# =============================================================================
# Canvas LTI Registration Lookup Tests
# =============================================================================


class TestCanvasGetDepartmentFromLTILaunch:
    """Tests for Canvas LTI department lookup."""

    def test_successful_lookup(
        self, mock_db_session, mock_lti_registration_canvas, mock_department_paid
    ):
        """Test successful department lookup from LTI launch."""
        # Setup mock query chain
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            mock_lti_registration_canvas,  # First call for registration
            mock_department_paid,  # Second call for department
        ]

        registration, department, error = get_department_from_lti_launch(
            mock_db_session,
            issuer="https://canvas.university.edu",
            client_id="canvas-client-12345",
        )

        assert registration is not None
        assert department is not None
        assert error is None
        assert department.id == mock_department_paid.id

    def test_no_registration_found(self, mock_db_session):
        """Test lookup when no LTI registration exists."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        registration, department, error = get_department_from_lti_launch(
            mock_db_session,
            issuer="https://unknown-canvas.edu",
            client_id="unknown-client",
        )

        assert registration is None
        assert department is None
        assert error is not None
        assert "not registered" in error.lower()

    def test_department_not_found(self, mock_db_session, mock_lti_registration_canvas):
        """Test lookup when registration exists but department doesn't."""
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            mock_lti_registration_canvas,  # Registration found
            None,  # Department not found
        ]

        registration, department, error = get_department_from_lti_launch(
            mock_db_session,
            issuer="https://canvas.university.edu",
            client_id="canvas-client-12345",
        )

        assert registration is None
        assert department is None
        assert error is not None
        assert "configuration error" in error.lower()

    def test_inactive_department(
        self, mock_db_session, mock_lti_registration_canvas, mock_department_inactive
    ):
        """Test lookup when department is inactive."""
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            mock_lti_registration_canvas,
            mock_department_inactive,
        ]

        registration, department, error = get_department_from_lti_launch(
            mock_db_session,
            issuer="https://canvas.university.edu",
            client_id="canvas-client-12345",
        )

        assert registration is None
        assert department is None
        assert error is not None
        assert "inactive" in error.lower()


# =============================================================================
# Blackboard LTI Registration Lookup Tests
# =============================================================================


class TestBlackboardGetDepartmentFromLTILaunch:
    """Tests for Blackboard LTI department lookup."""

    def test_successful_lookup(
        self, mock_db_session, mock_lti_registration_blackboard, mock_department_paid
    ):
        """Test successful department lookup from Blackboard LTI launch."""
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            mock_lti_registration_blackboard,
            mock_department_paid,
        ]

        registration, department, error = bb_get_department_from_lti_launch(
            mock_db_session,
            issuer="https://blackboard.university.edu",
            client_id="bb-client-67890",
        )

        assert registration is not None
        assert department is not None
        assert error is None

    def test_no_registration_found(self, mock_db_session):
        """Test Blackboard lookup when no registration exists."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        registration, department, error = bb_get_department_from_lti_launch(
            mock_db_session,
            issuer="https://unknown-blackboard.edu",
            client_id="unknown-client",
        )

        assert registration is None
        assert department is None
        assert error is not None
        assert "not registered" in error.lower()


# =============================================================================
# Feature Access Tests
# =============================================================================


class TestCheckLTIFeatureAccess:
    """Tests for LTI feature access checking."""

    @patch("src.api.lti_routes.check_feature_access")
    def test_paid_tier_has_access(self, mock_check_feature, mock_department_paid):
        """Test that paid tier has LMS integration access."""
        mock_check_feature.return_value = True

        allowed, error = check_lti_feature_access(mock_department_paid)

        assert allowed is True
        assert error is None
        mock_check_feature.assert_called_once_with("department", "lms_integration")

    @patch("src.api.lti_routes.check_feature_access")
    def test_free_tier_denied(self, mock_check_feature, mock_department_free):
        """Test that free tier is denied LMS integration."""
        mock_check_feature.return_value = False

        with patch(
            "src.config.settings.TIER_QUOTAS",
            {
                "department": {"features": ["lms_integration"], "excluded": []},
                "limited_test_tier": {"features": [], "excluded": ["lms_integration"]},
            },
        ):
            allowed, error = check_lti_feature_access(mock_department_free)

        assert allowed is False
        assert error is not None
        assert "not enabled" in error.lower()
        assert "limited_test_tier" in error

    @patch("src.api.blackboard_lti_routes.check_feature_access")
    def test_blackboard_paid_tier_has_access(
        self, mock_check_feature, mock_department_paid
    ):
        """Test Blackboard feature access for paid tier."""
        mock_check_feature.return_value = True

        allowed, error = bb_check_lti_feature_access(mock_department_paid)

        assert allowed is True
        assert error is None


# =============================================================================
# Launch Statistics Tests
# =============================================================================


class TestUpdateLTILaunchStats:
    """Tests for LTI launch statistics updates."""

    def test_increment_launch_count(
        self, mock_db_session, mock_lti_registration_canvas
    ):
        """Test that launch count is incremented."""
        initial_count = mock_lti_registration_canvas.launch_count

        update_lti_launch_stats(mock_db_session, mock_lti_registration_canvas)

        assert mock_lti_registration_canvas.launch_count == initial_count + 1
        assert mock_lti_registration_canvas.last_launch_at is not None
        mock_db_session.commit.assert_called_once()

    def test_first_launch(self, mock_db_session, mock_lti_registration_canvas):
        """Test launch stats for first-time launch."""
        mock_lti_registration_canvas.launch_count = None

        update_lti_launch_stats(mock_db_session, mock_lti_registration_canvas)

        assert mock_lti_registration_canvas.launch_count == 1


# =============================================================================
# Error Page Rendering Tests
# =============================================================================


class TestRenderLTIErrorPage:
    """Tests for LTI error page rendering."""

    def test_basic_error_page(self):
        """Test basic error page rendering."""
        html = _render_lti_error_page(
            title="Test Error",
            message="Something went wrong",
            help_text="Contact support",
        )

        assert "Test Error" in html
        assert "Something went wrong" in html
        assert "Contact support" in html
        assert "<!DOCTYPE html>" in html

    def test_error_page_with_configuration_button(self):
        """Test error page with administrator-configuration guidance."""
        html = _render_lti_error_page(
            title="Feature Not Available",
            message="LMS integration requires administrator configuration",
            show_configuration_button=True,
        )

        assert "Ask your administrator" in html

    def test_error_page_without_configuration_button(self):
        """Test error page without configuration guidance."""
        html = _render_lti_error_page(
            title="Registration Required",
            message="Contact your admin",
            show_configuration_button=False,
        )

        assert "View Plans & Pricing" not in html

    def test_error_page_accessibility(self):
        """Test that error page has basic accessibility features."""
        html = _render_lti_error_page(
            title="Error",
            message="Test message",
        )

        assert 'lang="en"' in html
        assert "charset" in html.lower()
        assert "viewport" in html


# =============================================================================
# Integration Tests (Mock LTI Launch Flow)
# =============================================================================


class TestLTILaunchFlow:
    """Integration tests for the complete LTI launch flow."""

    @patch("src.api.lti_routes.check_feature_access")
    def test_complete_launch_success(
        self,
        mock_check_feature,
        mock_db_session,
        mock_lti_registration_canvas,
        mock_department_paid,
    ):
        """Test complete successful LTI launch flow."""
        # Setup
        mock_check_feature.return_value = True
        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            mock_lti_registration_canvas,
            mock_department_paid,
        ]

        # Execute lookup
        registration, department, error = get_department_from_lti_launch(
            mock_db_session,
            issuer="https://canvas.university.edu",
            client_id="canvas-client-12345",
        )

        # Verify lookup succeeded
        assert error is None
        assert department is not None

        # Check feature access
        allowed, feature_error = check_lti_feature_access(department)
        assert allowed is True

        # Update stats
        update_lti_launch_stats(mock_db_session, registration)
        assert registration.launch_count == 6  # Was 5, now 6

    @patch("src.api.lti_routes.check_feature_access")
    def test_complete_launch_feature_denied(
        self,
        mock_check_feature,
        mock_db_session,
        mock_department_free,
    ):
        """Test LTI launch flow with feature denial."""
        # Setup - free tier department
        mock_reg = MagicMock(spec=LTIRegistration)
        mock_reg.department_id = mock_department_free.id

        mock_db_session.query.return_value.filter.return_value.first.side_effect = [
            mock_reg,
            mock_department_free,
        ]

        mock_check_feature.return_value = False

        # Execute lookup - should succeed
        registration, department, error = get_department_from_lti_launch(
            mock_db_session,
            issuer="https://canvas.university.edu",
            client_id="free-tier-client",
        )

        assert error is None
        assert department is not None
        assert department.tier == "limited_test_tier"

        # Check feature access - should be denied
        with patch(
            "src.config.settings.TIER_QUOTAS",
            {
                "department": {"features": ["lms_integration"], "excluded": []},
            },
        ):
            allowed, feature_error = check_lti_feature_access(department)

        assert allowed is False
        assert feature_error is not None
