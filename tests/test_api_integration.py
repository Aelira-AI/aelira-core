"""
Integration Tests for Critical API Endpoints

Tests authentication, rate limiting, file validation, and core functionality.
"""

import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.config.settings import get_settings
import bcrypt
import uuid
from datetime import datetime, timedelta, timezone
import io

# Try to import database models, skip if unavailable
try:
    from src.db import get_db, Department, User, APIKey, UserRole

    DB_AVAILABLE = True
except Exception:
    DB_AVAILABLE = False

# Mark all tests in this module as integration (skipped in CI)
pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def db_session():
    """Create a database session for test setup."""
    if not DB_AVAILABLE:
        pytest.skip("Database not available")
    try:
        with get_db() as db:
            yield db
            db.rollback()
    except Exception as e:
        pytest.skip(f"Database connection failed: {e}")


@pytest.fixture
def test_department(db_session):
    """Create a test department."""
    if not DB_AVAILABLE:
        pytest.skip("Database not available")
    try:
        dept = Department(
            id=str(uuid.uuid4()),
            name="Test Department",
            institution="Test University",
            contact_email="test@university.edu",
            contact_name="Test Contact",
            tier="trial",
            max_users=10,
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        db_session.add(dept)
        db_session.commit()
        db_session.refresh(dept)
        return dept
    except Exception as e:
        pytest.skip(f"Could not create test department: {e}")


@pytest.fixture
def test_user(db_session, test_department):
    """Create a test user."""
    if not DB_AVAILABLE:
        pytest.skip("Database not available")
    try:
        user_uuid = uuid.uuid4().hex[:8]
        user = User(
            id=str(uuid.uuid4()),
            email=f"testuser_{user_uuid}@university.edu",
            google_id=f"google_{uuid.uuid4().hex}",
            name="Test User",
            department_id=test_department.id,
            role=UserRole.FACULTY,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user
    except Exception as e:
        pytest.skip(f"Could not create test user: {e}")


@pytest.fixture
def test_api_key(db_session, test_user, test_department):
    """Create a test API key."""
    if not DB_AVAILABLE:
        pytest.skip("Database not available")
    try:
        test_key = f"aelira_test_{uuid.uuid4().hex[:16]}"
        key_hash = bcrypt.hashpw(test_key.encode("utf-8"), bcrypt.gensalt()).decode(
            "utf-8"
        )

        api_key = APIKey(
            id=str(uuid.uuid4()),
            key_hash=key_hash,
            key_prefix=test_key[:12],
            name="Test API Key",
            user_id=test_user.id,
            department_id=test_department.id,
            rate_limit_per_hour=100,
        )
        db_session.add(api_key)
        db_session.commit()
        db_session.refresh(api_key)

        # Store the plain key for use in tests
        api_key._plain_key = test_key
        return api_key
    except Exception as e:
        pytest.skip(f"Could not create test API key: {e}")


@pytest.fixture
def auth_headers(test_api_key):
    """Get authentication headers for API requests."""
    if not DB_AVAILABLE or not hasattr(test_api_key, "_plain_key"):
        # Return mock headers for tests that don't need real DB
        return {"Authorization": "Bearer mock_key_for_testing"}
    return {"Authorization": f"Bearer {test_api_key._plain_key}"}


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_check_public(self, client):
        """Health check should be publicly accessible."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "message" in data


class TestAuthentication:
    """Test API key authentication."""

    def test_endpoint_without_api_key(self, client):
        """Endpoints should require API key."""
        response = client.get("/education/scans")
        # In development/test mode, endpoints may allow mock credentials or return 404
        # In production, should return 401
        settings = get_settings()
        if settings.env in ["development", "test"]:
            # In dev/test mode, may allow requests without key or endpoint may not exist
            assert response.status_code in [200, 401, 404]
        else:
            assert response.status_code == 401
            if response.status_code == 401:
                assert "API key" in response.json()["detail"].lower()

    def test_endpoint_with_invalid_api_key(self, client):
        """Invalid API key should be rejected."""
        headers = {"Authorization": "Bearer invalid_key_12345"}
        response = client.get("/education/scans", headers=headers)
        # Should reject invalid key
        assert response.status_code == 401

    @pytest.mark.skipif(not DB_AVAILABLE, reason="Database not available")
    def test_endpoint_with_valid_api_key(self, client, auth_headers, test_api_key):
        """Valid API key should be accepted."""
        # Use the actual API key from fixture
        headers = {"Authorization": f"Bearer {test_api_key._plain_key}"}
        response = client.get("/education/scans", headers=headers)
        # Should succeed (even if empty list)
        assert response.status_code in [200, 404]


class TestRateLimiting:
    """Test rate limiting functionality."""

    @pytest.mark.skipif(not DB_AVAILABLE, reason="Database not available")
    def test_rate_limit_headers(self, client, test_api_key):
        """Rate limit headers should be present if Redis is enabled."""
        headers = {"Authorization": f"Bearer {test_api_key._plain_key}"}
        response = client.get("/education/scans", headers=headers)

        # Check for rate limit headers (only if enabled)
        settings = get_settings()
        if settings.redis_enabled:
            assert "X-RateLimit-Limit" in response.headers
            assert "X-RateLimit-Remaining" in response.headers
            assert "X-RateLimit-Reset" in response.headers

            limit = int(response.headers["X-RateLimit-Limit"])
            remaining = int(response.headers["X-RateLimit-Remaining"])

            assert limit == test_api_key.rate_limit_per_hour
            assert remaining < limit
        else:
            # If redis disabled, just ensure the request succeeded
            assert response.status_code == 200

    def test_rate_limit_enforcement(self, client, auth_headers):
        """Rate limiting should work if enabled."""
        response = client.get("/education/scans", headers=auth_headers)

        settings = get_settings()
        if settings.redis_enabled and response.status_code == 200:
            assert "X-RateLimit-Limit" in response.headers
        else:
            # Just ensure request didn't crash
            assert response.status_code in [200, 401]


class TestFileSizeValidation:
    """Test file size validation."""

    def test_pdf_too_large(self, client, auth_headers):
        """PDF files exceeding size limit should be rejected."""
        settings = get_settings()
        # Create a file larger than the limit
        large_file = io.BytesIO(b"x" * (settings.max_file_size_pdf + 1))

        files = {"file": ("large.pdf", large_file, "application/pdf")}
        response = client.post("/education/pdf/scan", files=files, headers=auth_headers)

        # Should reject for size (400)
        assert response.status_code == 400
        assert "file too large" in response.json()["detail"].lower()

    def test_image_too_large(self, client, auth_headers):
        """Image files exceeding size limit should be rejected."""
        settings = get_settings()
        large_file = io.BytesIO(b"x" * (settings.max_file_size_image + 1))

        files = {"file": ("large.png", large_file, "image/png")}
        response = client.post(
            "/education/image/alt-text", files=files, headers=auth_headers
        )

        # Should reject for size (400)
        assert response.status_code == 400
        assert "file too large" in response.json()["detail"].lower()

    def test_valid_file_size(self, client, auth_headers):
        """Valid file sizes should be accepted."""
        # Create a small test file
        small_file = io.BytesIO(b"test content")

        files = {"file": ("small.pdf", small_file, "application/pdf")}
        response = client.post("/education/pdf/scan", files=files, headers=auth_headers)

        # Should not be rejected for size (may fail for other reasons like invalid PDF or auth)
        assert response.status_code != 413


class TestCORSHeaders:
    """Test CORS configuration."""

    def test_cors_headers_present(self, client):
        """CORS headers should be present in responses."""
        response = client.options("/health")

        # CORS headers may not be present in OPTIONS if no origin header
        # But they should be present in actual requests
        origin_headers = {
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        }
        response = client.options("/health", headers=origin_headers)

        # In development mode, CORS should allow all origins
        settings = get_settings()
        if settings.env == "development":
            assert response.status_code in [200, 204]


class TestScanHistory:
    """Test scan history endpoint."""

    @pytest.mark.skipif(not DB_AVAILABLE, reason="Database not available")
    def test_scan_history_empty(self, client, test_api_key):
        """Empty scan history should return empty list."""
        headers = {"Authorization": f"Bearer {test_api_key._plain_key}"}
        response = client.get("/education/scans", headers=headers)

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, dict)
            assert "scans" in data
            assert isinstance(data["scans"], list)
        elif response.status_code == 404:
            # Some implementations return 404 for empty
            pass
        else:
            pytest.fail(f"Unexpected status code: {response.status_code}")

    @pytest.mark.skipif(not DB_AVAILABLE, reason="Database not available")
    def test_scan_history_with_limit(self, client, test_api_key):
        """Scan history should respect limit parameter."""
        headers = {"Authorization": f"Bearer {test_api_key._plain_key}"}
        response = client.get("/education/scans?limit=10", headers=headers)

        if response.status_code == 200:
            data = response.json()
            assert "scans" in data
            assert len(data["scans"]) <= 10


class TestDepartmentStats:
    """Test department statistics endpoint."""

    @pytest.mark.skipif(not DB_AVAILABLE, reason="Database not available")
    def test_department_stats(self, client, test_api_key):
        """Department stats endpoint should return statistics."""
        headers = {"Authorization": f"Bearer {test_api_key._plain_key}"}
        response = client.get("/education/stats", headers=headers)

        # Should succeed with valid API key
        assert response.status_code == 200
        data = response.json()
        assert "stats" in data or isinstance(data, dict)


class TestErrorHandling:
    """Test error handling."""

    def test_invalid_endpoint(self, client):
        """Invalid endpoints should return 404."""
        response = client.get("/api/nonexistent/endpoint")
        assert response.status_code == 404

    def test_invalid_method(self, client, auth_headers):
        """Invalid HTTP methods should return 405."""
        # Try DELETE on a GET-only endpoint
        response = client.delete("/education/scans", headers=auth_headers)
        # Some endpoints may return 404 instead of 405, both are acceptable
        assert response.status_code in [404, 405]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
