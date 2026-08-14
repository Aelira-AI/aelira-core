"""Tests for the department review summary endpoint.

Tests cover:
- DepartmentSummary Pydantic model serialization and edge cases
- Endpoint authentication requirements
- Empty department (no scans/fixes)
- Department with mixed review statuses
- Correct percentage calculations
- Average confidence computation
- Boundary conditions (all approved, all rejected, all pending)
- Auto-approved fixes counted as approved
"""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.review_routes import DepartmentSummary

# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestDepartmentSummaryModel:
    """Tests for the DepartmentSummary response model."""

    def test_basic_serialization(self):
        summary = DepartmentSummary(
            total_documents=10,
            reviewed_percent=60.0,
            approved_count=4,
            pending_count=3,
            rejected_count=2,
            avg_confidence=0.85,
        )
        data = summary.model_dump()
        assert data["total_documents"] == 10
        assert data["reviewed_percent"] == 60.0
        assert data["approved_count"] == 4
        assert data["pending_count"] == 3
        assert data["rejected_count"] == 2
        assert data["avg_confidence"] == 0.85

    def test_all_zeros(self):
        summary = DepartmentSummary(
            total_documents=0,
            reviewed_percent=0.0,
            approved_count=0,
            pending_count=0,
            rejected_count=0,
            avg_confidence=0.0,
        )
        assert summary.total_documents == 0
        assert summary.reviewed_percent == 0.0
        assert summary.avg_confidence == 0.0

    def test_hundred_percent_reviewed(self):
        summary = DepartmentSummary(
            total_documents=5,
            reviewed_percent=100.0,
            approved_count=5,
            pending_count=0,
            rejected_count=0,
            avg_confidence=0.95,
        )
        assert summary.reviewed_percent == 100.0
        assert summary.pending_count == 0

    def test_zero_percent_reviewed(self):
        summary = DepartmentSummary(
            total_documents=5,
            reviewed_percent=0.0,
            approved_count=0,
            pending_count=5,
            rejected_count=0,
            avg_confidence=0.5,
        )
        assert summary.reviewed_percent == 0.0

    def test_float_confidence(self):
        summary = DepartmentSummary(
            total_documents=3,
            reviewed_percent=66.67,
            approved_count=1,
            pending_count=1,
            rejected_count=1,
            avg_confidence=0.7333,
        )
        assert abs(summary.avg_confidence - 0.7333) < 0.0001


# ---------------------------------------------------------------------------
# Endpoint integration tests using TestClient with mocked dependencies
# ---------------------------------------------------------------------------


def _make_client(mock_db_session, auth_override=None):
    """Create a test client with overridden dependencies."""
    from src.api.main import app
    from src.auth.dependencies import get_required_api_key
    from src.db.database import get_db_dependency

    if auth_override is None:
        mock_api_key = MagicMock()
        mock_api_key.user_id = "test-user"
        mock_api_key.department_id = "test-dept"
        auth_override = lambda: (mock_api_key, "test-user", "test-dept")  # noqa: E731

    app.dependency_overrides[get_required_api_key] = auth_override
    app.dependency_overrides[get_db_dependency] = lambda: mock_db_session

    client = TestClient(app)
    return client, app


def _cleanup(app):
    """Remove dependency overrides."""
    from src.auth.dependencies import get_required_api_key
    from src.db.database import get_db_dependency

    app.dependency_overrides.pop(get_required_api_key, None)
    app.dependency_overrides.pop(get_db_dependency, None)


class TestDepartmentSummaryEndpoint:
    """Integration tests for GET /reviews/department-summary."""

    def test_empty_department_returns_zeros(self):
        """Department with no scans should return all zeros."""
        mock_db = MagicMock()
        # scalar() called twice: first for total_documents, then for avg_confidence
        mock_db.query.return_value.join.return_value.filter.return_value.scalar.side_effect = [
            0,
            None,
        ]
        # query for scan fix stats returns empty list
        mock_db.query.return_value.join.return_value.filter.return_value.group_by.return_value.all.return_value = (
            []
        )

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            assert data["total_documents"] == 0
            assert data["reviewed_percent"] == 0.0
            assert data["approved_count"] == 0
            assert data["pending_count"] == 0
            assert data["rejected_count"] == 0
            assert data["avg_confidence"] == 0.0
        finally:
            _cleanup(app)

    def test_department_with_mixed_statuses(self):
        """Department with fixes in various review states."""
        mock_db = MagicMock()

        # The endpoint makes 3 queries:
        # 1. scalar() for total_documents (count distinct scans)
        # 2. group_by().all() for status counts
        # 3. scalar() for avg confidence

        query_mock = MagicMock()
        mock_db.query.return_value = query_mock

        join_mock = MagicMock()
        query_mock.join.return_value = join_mock

        filter_mock = MagicMock()
        join_mock.filter.return_value = filter_mock

        # scalar() called twice: total_documents=5, avg_confidence=0.78
        filter_mock.scalar.side_effect = [5, 0.78]

        group_mock = MagicMock()
        filter_mock.group_by.return_value = group_mock
        group_mock.all.return_value = [
            ("approved", 3),
            ("auto_approved", 2),
            ("pending", 4),
            ("rejected", 1),
        ]

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            # approved = 3 + 2 (auto_approved) = 5
            assert data["approved_count"] == 5
            assert data["pending_count"] == 4
            assert data["rejected_count"] == 1
            # reviewed = (approved + rejected) / total = (5 + 1) / 10 * 100 = 60%
            assert data["reviewed_percent"] == 60.0
            assert data["avg_confidence"] == 0.78
            assert data["total_documents"] == 5
        finally:
            _cleanup(app)

    def test_all_approved_returns_100_percent(self):
        """When all fixes are approved, reviewed_percent should be 100."""
        mock_db = MagicMock()
        query_mock = MagicMock()
        mock_db.query.return_value = query_mock
        join_mock = MagicMock()
        query_mock.join.return_value = join_mock
        filter_mock = MagicMock()
        join_mock.filter.return_value = filter_mock

        filter_mock.scalar.side_effect = [3, 0.92]
        group_mock = MagicMock()
        filter_mock.group_by.return_value = group_mock
        group_mock.all.return_value = [("approved", 8)]

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            assert data["reviewed_percent"] == 100.0
            assert data["approved_count"] == 8
            assert data["pending_count"] == 0
            assert data["rejected_count"] == 0
        finally:
            _cleanup(app)

    def test_all_pending_returns_0_percent(self):
        """When all fixes are pending, reviewed_percent should be 0."""
        mock_db = MagicMock()
        query_mock = MagicMock()
        mock_db.query.return_value = query_mock
        join_mock = MagicMock()
        query_mock.join.return_value = join_mock
        filter_mock = MagicMock()
        join_mock.filter.return_value = filter_mock

        filter_mock.scalar.side_effect = [2, 0.45]
        group_mock = MagicMock()
        filter_mock.group_by.return_value = group_mock
        group_mock.all.return_value = [("pending", 6)]

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            assert data["reviewed_percent"] == 0.0
            assert data["pending_count"] == 6
            assert data["approved_count"] == 0
            assert data["rejected_count"] == 0
        finally:
            _cleanup(app)

    def test_all_rejected_returns_100_percent(self):
        """When all fixes are rejected, reviewed_percent should be 100."""
        mock_db = MagicMock()
        query_mock = MagicMock()
        mock_db.query.return_value = query_mock
        join_mock = MagicMock()
        query_mock.join.return_value = join_mock
        filter_mock = MagicMock()
        join_mock.filter.return_value = filter_mock

        filter_mock.scalar.side_effect = [1, 0.30]
        group_mock = MagicMock()
        filter_mock.group_by.return_value = group_mock
        group_mock.all.return_value = [("rejected", 3)]

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            assert data["reviewed_percent"] == 100.0
            assert data["approved_count"] == 0
            assert data["pending_count"] == 0
            assert data["rejected_count"] == 3
        finally:
            _cleanup(app)

    def test_auto_approved_counted_as_approved(self):
        """auto_approved fixes should be included in approved_count."""
        mock_db = MagicMock()
        query_mock = MagicMock()
        mock_db.query.return_value = query_mock
        join_mock = MagicMock()
        query_mock.join.return_value = join_mock
        filter_mock = MagicMock()
        join_mock.filter.return_value = filter_mock

        filter_mock.scalar.side_effect = [4, 0.88]
        group_mock = MagicMock()
        filter_mock.group_by.return_value = group_mock
        group_mock.all.return_value = [
            ("auto_approved", 7),
            ("pending", 3),
        ]

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            assert data["approved_count"] == 7
            assert data["pending_count"] == 3
            # reviewed = 7 / 10 * 100 = 70%
            assert data["reviewed_percent"] == 70.0
        finally:
            _cleanup(app)

    def test_avg_confidence_none_defaults_to_zero(self):
        """When no fixes exist, avg confidence should default to 0.0."""
        mock_db = MagicMock()
        query_mock = MagicMock()
        mock_db.query.return_value = query_mock
        join_mock = MagicMock()
        query_mock.join.return_value = join_mock
        filter_mock = MagicMock()
        join_mock.filter.return_value = filter_mock

        filter_mock.scalar.side_effect = [0, None]
        group_mock = MagicMock()
        filter_mock.group_by.return_value = group_mock
        group_mock.all.return_value = []

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            assert data["avg_confidence"] == 0.0
        finally:
            _cleanup(app)

    def test_auth_required(self):
        """Endpoint should return 401 when no auth is provided."""
        from src.api.main import app
        from src.auth.dependencies import get_required_api_key
        from src.db.database import get_db_dependency

        # Clear any overrides to test real auth behavior
        app.dependency_overrides.pop(get_required_api_key, None)
        app.dependency_overrides.pop(get_db_dependency, None)

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/reviews/department-summary")
        # Without valid auth, should get 401 or 403
        assert response.status_code in (401, 403)

    def test_reviewed_percent_precision(self):
        """Reviewed percent should be rounded to 2 decimal places."""
        mock_db = MagicMock()
        query_mock = MagicMock()
        mock_db.query.return_value = query_mock
        join_mock = MagicMock()
        query_mock.join.return_value = join_mock
        filter_mock = MagicMock()
        join_mock.filter.return_value = filter_mock

        filter_mock.scalar.side_effect = [2, 0.65]
        group_mock = MagicMock()
        filter_mock.group_by.return_value = group_mock
        # 1 approved out of 3 total fixes = 33.33...%
        group_mock.all.return_value = [
            ("approved", 1),
            ("pending", 2),
        ]

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            # 1 / 3 * 100 = 33.33
            assert data["reviewed_percent"] == 33.33
        finally:
            _cleanup(app)

    def test_in_review_counted_as_pending(self):
        """in_review status should be counted as pending (not yet reviewed)."""
        mock_db = MagicMock()
        query_mock = MagicMock()
        mock_db.query.return_value = query_mock
        join_mock = MagicMock()
        query_mock.join.return_value = join_mock
        filter_mock = MagicMock()
        join_mock.filter.return_value = filter_mock

        filter_mock.scalar.side_effect = [3, 0.72]
        group_mock = MagicMock()
        filter_mock.group_by.return_value = group_mock
        group_mock.all.return_value = [
            ("approved", 2),
            ("in_review", 3),
            ("pending", 1),
        ]

        client, app = _make_client(mock_db)
        try:
            response = client.get("/api/reviews/department-summary")
            assert response.status_code == 200
            data = response.json()
            # in_review counted as pending
            assert data["pending_count"] == 4  # 3 in_review + 1 pending
            assert data["approved_count"] == 2
            # reviewed = 2 / 6 * 100 = 33.33
            assert data["reviewed_percent"] == 33.33
        finally:
            _cleanup(app)
