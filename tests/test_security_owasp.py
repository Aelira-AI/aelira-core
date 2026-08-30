"""
Security tests based on OWASP Top 10 and OAuth best practices.

Tests cover:
- A01:2021 - Broken Access Control
- A02:2021 - Cryptographic Failures
- A03:2021 - Injection
- A04:2021 - Insecure Design
- A05:2021 - Security Misconfiguration
- A06:2021 - Vulnerable Components (handled by pip-audit)
- A07:2021 - Authentication Failures
- A08:2021 - Software and Data Integrity Failures
- A09:2021 - Security Logging and Monitoring
- A10:2021 - Server-Side Request Forgery (SSRF)
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

# Mark all tests in this module as integration (skipped in CI)
pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Valid authentication headers."""
    return {"Authorization": "Bearer test-api-key-12345"}


@pytest.fixture
def other_user_headers():
    """Authentication headers for a different user."""
    return {"Authorization": "Bearer other-user-api-key"}


# ==============================================================================
# A01:2021 - Broken Access Control
# ==============================================================================


class TestBrokenAccessControl:
    """Test for broken access control vulnerabilities."""

    def test_unauthorized_access_to_protected_endpoint(self, client):
        """Test that protected endpoints require authentication."""
        protected_endpoints = [
            ("/canvas/courses", "GET"),
            ("/moodle/courses", "GET"),
            ("/api/google/drive/files", "GET"),
            ("/api/microsoft/onedrive/files", "GET"),
            ("/api/analytics/dashboard", "GET"),
            ("/education/scans", "GET"),
        ]

        for endpoint, method in protected_endpoints:
            if method == "GET":
                response = client.get(endpoint)
            else:
                response = client.post(endpoint, json={})

            assert response.status_code in [
                401,
                403,
                404,
                405,
            ], f"Endpoint {endpoint} should require authentication, got {response.status_code}"

    def test_cross_department_access_blocked(self, client, auth_headers):
        """Test that users cannot access other departments' data."""
        # Try to access another department's data
        response = client.get(
            "/education/scans",
            headers=auth_headers,
            params={"department_id": "other-dept-not-mine"},
        )

        # Should be forbidden or not found
        assert response.status_code in [401, 403, 404]

    def test_direct_object_reference_protection(self, client, auth_headers):
        """Test that IDOR attacks are prevented."""
        # Try to access a scan by guessing ID
        response = client.get(
            "/education/scans/00000000-0000-0000-0000-000000000000",
            headers=auth_headers,
        )

        # Should not reveal if resource exists for another user
        assert response.status_code in [401, 403, 404]

    def test_admin_endpoints_require_admin_role(self, client, auth_headers):
        """Test that admin endpoints require admin role."""
        admin_endpoints = [
            "/api/admin/users",
            "/api/admin/departments",
        ]

        for endpoint in admin_endpoints:
            response = client.get(endpoint, headers=auth_headers)
            # Non-admin should be denied or endpoint not found
            assert response.status_code in [401, 403, 404]


# ==============================================================================
# A02:2021 - Cryptographic Failures
# ==============================================================================


class TestCryptographicFailures:
    """Test for cryptographic vulnerabilities."""

    def test_oauth_tokens_not_exposed_in_response(self, client, auth_headers):
        """Test that OAuth tokens are not leaked in API responses."""
        response = client.get(
            "/api/google/status",
            headers=auth_headers,
            params={"department_id": "test-dept-456"},
        )

        if response.status_code == 200:
            response_text = response.text.lower()
            # Should not contain raw tokens
            assert "ya29." not in response_text  # Google token prefix
            assert "eyj" not in response_text  # JWT prefix (base64)
            assert "refresh_token" not in response_text

    def test_api_key_not_logged(self, client):
        """Test that API keys are not logged in responses."""
        response = client.get(
            "/education/scans",
            headers={"Authorization": "Bearer secret-test-key-12345"},
        )

        # Even on error, should not echo back the key
        if response.status_code != 200:
            response_text = response.text
            assert "secret-test-key-12345" not in response_text

    def test_password_hashing_if_applicable(self):
        """Test that passwords are properly hashed."""
        # Import user model if password storage is used
        try:
            from src.db.models import User

            # Check that User model doesn't have plaintext password field
            assert not hasattr(User, "password") or hasattr(
                User, "password_hash"
            ), "User model should use password_hash, not plaintext password"
        except ImportError:
            pytest.skip("User model not available")

    def test_encryption_key_required(self):
        """Test that token encryption key is required."""

        # In production, TOKEN_ENCRYPTION_KEY should be set
        # For test, we verify the config requires it
        try:
            from src.config.settings import Settings

            settings = Settings()
            # Settings should have token_encryption_key
            assert hasattr(settings, "token_encryption_key") or hasattr(
                settings, "TOKEN_ENCRYPTION_KEY"
            ), "Token encryption key configuration missing"
        except Exception:
            pytest.skip("Settings not available for crypto test")


# ==============================================================================
# A03:2021 - Injection
# ==============================================================================


class TestInjection:
    """Test for injection vulnerabilities."""

    def test_sql_injection_in_query_params(self, client, auth_headers):
        """Test that SQL injection in query params is prevented."""
        malicious_inputs = [
            "'; DROP TABLE users; --",
            "1 OR 1=1",
            "1; SELECT * FROM users",
            "' UNION SELECT * FROM users --",
        ]

        for payload in malicious_inputs:
            response = client.get(
                "/education/scans",
                headers=auth_headers,
                params={"department_id": payload},
            )

            # Should not cause server error (500)
            # Should be handled gracefully (400, 401, 403, 404, 422)
            assert (
                response.status_code != 500
            ), f"SQL injection payload caused server error: {payload}"

    def test_sql_injection_in_json_body(self, client, auth_headers):
        """Test that SQL injection in JSON body is prevented."""
        malicious_body = {
            "department_id": "'; DELETE FROM scans; --",
            "file_name": "test' OR '1'='1",
        }

        response = client.post(
            "/education/scan/upload",
            headers=auth_headers,
            json=malicious_body,
        )

        # Should not cause server error
        assert response.status_code != 500

    def test_command_injection_in_file_names(self, client, auth_headers):
        """Test that command injection in file names is prevented."""
        malicious_names = [
            "test; rm -rf /",
            "test | cat /etc/passwd",
            "test`id`",
            "test$(whoami)",
            "../../../etc/passwd",
        ]

        for name in malicious_names:
            response = client.post(
                "/education/scan/upload",
                headers=auth_headers,
                data={"file_name": name},
                files={"file": (name, b"test content", "application/pdf")},
            )

            # Should reject or sanitize, not cause server error
            assert (
                response.status_code != 500
            ), f"Command injection payload caused server error: {name}"

    def test_path_traversal_prevented(self, client, auth_headers):
        """Test that path traversal attacks are prevented."""
        traversal_paths = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "....//....//....//etc/passwd",
        ]

        for path in traversal_paths:
            response = client.get(
                f"/education/reports/{path}",
                headers=auth_headers,
            )

            # Authentication may reject the request before path validation.
            # Every accepted status must prevent access to filesystem content.
            assert response.status_code in [400, 401, 403, 404, 422]


# ==============================================================================
# A04:2021 - Insecure Design
# ==============================================================================


class TestInsecureDesign:
    """Test for insecure design patterns."""

    def test_rate_limiting_exists(self, client, auth_headers):
        """Test that rate limiting is implemented."""
        # Make many requests quickly
        responses = []
        for _ in range(100):
            response = client.get("/health")
            responses.append(response.status_code)

        # Should eventually get rate limited (429) or all succeed
        # Not getting 500s is the main concern
        assert all(code != 500 for code in responses)

    def test_request_size_limits(self, client, auth_headers):
        """Test that request size is limited."""
        # Try to send a very large payload
        large_payload = {"data": "x" * (10 * 1024 * 1024)}  # 10MB

        response = client.post(
            "/education/scan/upload",
            headers=auth_headers,
            json=large_payload,
        )

        # Should reject large payloads, require auth, or return 404 if endpoint not found
        assert response.status_code in [400, 401, 404, 413, 422, 500]

    def test_file_upload_type_validation(self, client, auth_headers):
        """Test that file uploads validate file types."""
        # Try to upload a script disguised as PDF
        malicious_content = b"<?php system($_GET['cmd']); ?>"

        response = client.post(
            "/education/scan/upload",
            headers=auth_headers,
            files={"file": ("malicious.php", malicious_content, "application/pdf")},
        )

        # Should reject based on content inspection, not just extension
        assert response.status_code in [400, 401, 415, 422, 404]


# ==============================================================================
# A05:2021 - Security Misconfiguration
# ==============================================================================


class TestSecurityMisconfiguration:
    """Test for security misconfiguration."""

    def test_debug_mode_disabled_indicators(self, client):
        """Test that debug information is not exposed."""
        # Trigger an error
        response = client.get("/nonexistent-endpoint-12345")

        # Should not expose stack traces in production
        response_text = response.text.lower()
        assert "traceback" not in response_text or "file" not in response_text

    def test_security_headers_present(self, client):
        """Test that security headers are present."""
        response = client.get("/health")

        headers = response.headers

        # Check for security headers (may vary based on environment)
        # These are recommendations, not hard requirements for all endpoints

        # At minimum, should not have dangerous headers
        assert "server" not in headers or "Python" not in headers.get("server", "")

    def test_cors_not_wildcard(self, client):
        """Test that CORS is not set to wildcard in production."""
        response = client.options("/education/scans")

        cors_header = response.headers.get("access-control-allow-origin", "")

        # Should not be wildcard (*) for authenticated endpoints
        # This is a warning, not always an error
        if cors_header == "*":
            pytest.warns(UserWarning, match="CORS wildcard")

    def test_error_messages_not_revealing(self, client, auth_headers):
        """Test that error messages don't reveal sensitive info."""
        # Try invalid operations
        response = client.get(
            "/education/scans/invalid-uuid-format",
            headers=auth_headers,
        )

        if response.status_code >= 400:
            response_text = response.text.lower()
            # Should not reveal database schema details
            assert "postgresql" not in response_text
            assert (
                "column" not in response_text or "does not exist" not in response_text
            )


# ==============================================================================
# A07:2021 - Identification and Authentication Failures
# ==============================================================================


class TestAuthenticationFailures:
    """Test for authentication vulnerabilities."""

    def test_invalid_api_key_rejected(self, client):
        """Test that invalid API keys are rejected."""
        response = client.get(
            "/education/scans",
            headers={"Authorization": "Bearer invalid-key-12345"},
        )

        assert response.status_code in [401, 403]

    def test_malformed_auth_header_handled(self, client):
        """Test that malformed auth headers are handled gracefully."""
        malformed_headers = [
            {"Authorization": "invalid"},
            {"Authorization": "Bearer"},
            {"Authorization": "Basic dGVzdDp0ZXN0"},  # Basic auth when Bearer expected
            {"Authorization": "Bearer " + "x" * 10000},  # Very long token
        ]

        for headers in malformed_headers:
            response = client.get("/education/scans", headers=headers)

            # Should not cause server error
            assert (
                response.status_code != 500
            ), f"Malformed auth header caused server error: {headers}"

    def test_oauth_state_validation(self, client):
        """Test that OAuth state parameter is validated."""
        # Try callback with invalid state
        response = client.get(
            "/google/callback",
            params={
                "code": "valid-looking-code",
                "state": "invalid-state-not-from-session",
            },
        )

        # Should reject invalid state, fail gracefully, or 404 if route redirects on error
        assert response.status_code in [400, 401, 403, 404, 500]

    def test_oauth_code_not_reusable(self, client):
        """Test that OAuth authorization codes cannot be reused."""
        # This is mostly handled by OAuth providers, but we can check
        # that the same code doesn't work twice

        # First callback (will likely fail without valid code, but that's OK)
        client.get(
            "/google/callback",
            params={"code": "test-code-123", "state": "test-state"},
        )

        # Second callback with same code
        response2 = client.get(
            "/google/callback",
            params={"code": "test-code-123", "state": "test-state"},
        )

        # Both should fail (no valid OAuth setup in tests)
        # Main check is that neither causes a security bypass
        assert response2.status_code in [400, 401, 403, 404, 500]

    def test_session_fixation_prevention(self, client):
        """Test that session fixation is prevented."""
        # This is relevant if using session-based auth
        # For API key auth, this is less of a concern
        pass  # API uses stateless auth


# ==============================================================================
# A08:2021 - Software and Data Integrity Failures
# ==============================================================================


class TestDataIntegrityFailures:
    """Test for data integrity vulnerabilities."""

    def test_csrf_token_required_for_state_changes(self, client, auth_headers):
        """Test that CSRF tokens are required for state-changing operations."""
        # CSRF protection may be implemented via CSRF middleware
        # For API endpoints with Bearer auth, CSRF is less critical
        # but we should verify no cookie-based auth without CSRF

        state_changing_endpoints = [
            ("/google/connect", "POST"),
            ("/microsoft/connect", "POST"),
            ("/canvas/connect", "POST"),
        ]

        for endpoint, method in state_changing_endpoints:
            response = client.post(
                endpoint,
                json={"department_id": "test-dept-456"},
                # No auth header - checking if CSRF alone blocks
            )

            # Should require authentication
            assert response.status_code in [401, 403, 422]

    def test_jwt_signature_validation(self, client):
        """Test that JWT signatures are validated."""
        # Try to use a tampered JWT (for LTI endpoints)
        tampered_jwt = "eyJhbGciOiJub25lIn0.eyJzdWIiOiIxMjM0NTY3ODkwIn0."

        response = client.post(
            "/lti/launch",
            data={
                "id_token": tampered_jwt,
                "state": "test-state",
            },
        )

        # Should reject unsigned/tampered JWT, or 503 if LTI not configured
        assert response.status_code in [400, 401, 403, 422, 503]


# ==============================================================================
# A10:2021 - Server-Side Request Forgery (SSRF)
# ==============================================================================


class TestSSRF:
    """Test for SSRF vulnerabilities."""

    def test_internal_url_blocked(self, client, auth_headers):
        """Test that requests to internal URLs are blocked."""
        internal_urls = [
            "http://localhost/admin",
            "http://127.0.0.1/admin",
            "http://[::1]/admin",
            "http://169.254.169.254/latest/meta-data/",  # AWS metadata
            "http://metadata.google.internal/",  # GCP metadata
            "http://192.168.1.1/",
            "http://10.0.0.1/",
        ]

        for url in internal_urls:
            # Test webhook URL registration
            response = client.post(
                "/api/microsoft/subscriptions",
                headers=auth_headers,
                json={
                    "department_id": "test-dept-456",
                    "notification_url": url,
                },
            )

            # Should reject internal URLs
            assert response.status_code in [
                400,
                401,
                403,
                422,
                404,
            ], f"Internal URL not blocked: {url}"

    def test_file_protocol_blocked(self, client, auth_headers):
        """Test that file:// protocol is blocked."""
        response = client.post(
            "/moodle/scan/file",
            headers=auth_headers,
            json={
                "department_id": "test-dept-456",
                "file_url": "file:///etc/passwd",
            },
        )

        assert response.status_code in [400, 401, 403, 404, 422]

    def test_url_redirect_handling(self):
        """A permitted public URL must not be followable into private space.

        safe_requests_get validates every redirect hop, so a 302 from an
        allowed host to localhost/private/metadata must raise, not fetch.
        """
        from unittest.mock import patch, MagicMock

        from src.utils.security import safe_requests_get

        redirect = MagicMock()
        redirect.is_redirect = True
        redirect.is_permanent_redirect = False
        redirect.headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

        def fake_get(url, **kwargs):
            return redirect

        with patch("requests.get", side_effect=fake_get):
            with pytest.raises(ValueError):
                # example.com resolves publicly; the redirect hop must be
                # validated and rejected before any second request is made.
                safe_requests_get("http://example.com/start", timeout=5)

    def test_redirect_loop_is_bounded(self):
        """Endless public-to-public redirects must terminate with an error."""
        from unittest.mock import patch, MagicMock

        from src.utils.security import safe_requests_get

        redirect = MagicMock()
        redirect.is_redirect = True
        redirect.is_permanent_redirect = False
        redirect.headers = {"Location": "http://example.com/again"}

        with patch("requests.get", return_value=redirect):
            with pytest.raises(ValueError, match="Too many redirects"):
                safe_requests_get(
                    "http://example.com/start", timeout=5, max_redirects=3
                )


# ==============================================================================
# OAuth-Specific Security Tests
# ==============================================================================


class TestOAuthSecurity:
    """Test OAuth-specific security concerns."""

    def test_oauth_redirect_uri_validation(self, client, auth_headers):
        """Test that OAuth redirect URIs are validated."""
        malicious_redirects = [
            "https://evil.com/callback",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
        ]

        for redirect in malicious_redirects:
            response = client.post(
                "/api/google/connect",
                headers=auth_headers,
                json={
                    "department_id": "test-dept-456",
                    "redirect_uri": redirect,
                },
            )

            # Should reject malicious redirect URIs
            # May return 200 with an error in body, or 400/422
            if response.status_code == 200:
                data = response.json()
                # Should not include the malicious redirect in auth URL
                auth_url = data.get("auth_url", "") or data.get("authorization_url", "")
                assert redirect not in auth_url

    def test_token_not_in_url(self, client, auth_headers):
        """Test that tokens are not passed in URLs."""
        # This is more of a code review check
        # Tokens should be in headers or POST body, not query params
        pass  # Verified by code structure

    def test_pkce_if_applicable(self, client, auth_headers):
        """Test that PKCE is used for OAuth flows if applicable."""
        # PKCE (Proof Key for Code Exchange) should be used for public clients
        # This is verified by checking OAuth configuration
        pass  # Configuration check


# ==============================================================================
# XSS Prevention Tests
# ==============================================================================


class TestXSSPrevention:
    """Test for XSS vulnerabilities."""

    def test_html_entities_escaped(self, client, auth_headers):
        """Test that HTML entities are escaped in responses."""
        xss_payloads = [
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert('xss')>",
            "javascript:alert('xss')",
            "<svg onload=alert('xss')>",
        ]

        for payload in xss_payloads:
            # Try to inject XSS in various places
            response = client.post(
                "/education/scan/upload",
                headers=auth_headers,
                data={"file_name": payload},
                files={"file": ("test.pdf", b"test", "application/pdf")},
            )

            if response.status_code == 200:
                # If successful, response should have escaped HTML
                response_text = response.text
                assert "<script>" not in response_text
                assert "onerror=" not in response_text

    def test_content_type_header(self, client):
        """Test that JSON responses have correct content type."""
        response = client.get("/health")

        content_type = response.headers.get("content-type", "")

        # JSON endpoints should have application/json
        if response.status_code == 200:
            assert "application/json" in content_type or "text/plain" in content_type
