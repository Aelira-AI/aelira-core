"""
Security Middleware for FastAPI

Provides:
1. Security Headers (CSP, HSTS, X-Frame-Options, etc.)
2. CSRF Protection for cookie-based sessions

Author: Aelira Team
Created: January 2026
"""

import secrets
import logging
from typing import Callable, Optional
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add security headers to all responses.

    Headers added:
    - Content-Security-Policy (CSP)
    - Strict-Transport-Security (HSTS)
    - X-Frame-Options
    - X-Content-Type-Options
    - X-XSS-Protection
    - Referrer-Policy
    - Permissions-Policy
    """

    def __init__(
        self,
        app,
        enable_hsts: bool = True,
        hsts_max_age: int = 31536000,  # 1 year
        frame_options: str = "DENY",
        content_type_options: str = "nosniff",
        referrer_policy: str = "strict-origin-when-cross-origin",
        csp_policy: Optional[str] = None,
        permissions_policy: Optional[str] = None,
    ):
        super().__init__(app)
        self.enable_hsts = enable_hsts
        self.hsts_max_age = hsts_max_age
        self.frame_options = frame_options
        self.content_type_options = content_type_options
        self.referrer_policy = referrer_policy

        # Default CSP - restrictive but allows API functionality
        self.csp_policy = csp_policy or self._default_csp()

        # Default Permissions Policy
        self.permissions_policy = (
            permissions_policy or self._default_permissions_policy()
        )

    def _default_csp(self) -> str:
        """Generate default Content-Security-Policy for API server."""
        directives = [
            "default-src 'self'",
            # Allow Swagger UI from jsdelivr CDN
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "img-src 'self' data: https:",
            "font-src 'self' data: https://cdn.jsdelivr.net",
            "connect-src 'self' https:",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "base-uri 'self'",
            "object-src 'none'",
        ]
        return "; ".join(directives)

    def _default_permissions_policy(self) -> str:
        """Generate default Permissions-Policy."""
        policies = [
            "accelerometer=()",
            "camera=()",
            "geolocation=()",
            "gyroscope=()",
            "magnetometer=()",
            "microphone=()",
            "payment=()",
            "usb=()",
        ]
        return ", ".join(policies)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # LTI routes must be frameable by Canvas LMS
        is_lti_path = request.url.path.startswith("/lti/")

        # Add security headers
        response.headers["X-Content-Type-Options"] = self.content_type_options
        if is_lti_path:
            # Allow Canvas to frame LTI pages
            if "X-Frame-Options" in response.headers:
                del response.headers["X-Frame-Options"]
            lti_csp = self.csp_policy.replace(
                "frame-ancestors 'none'",
                "frame-ancestors 'self' *.instructure.com canvas.example.com",
            )
            response.headers["Content-Security-Policy"] = lti_csp
        else:
            response.headers["X-Frame-Options"] = self.frame_options
            response.headers["Content-Security-Policy"] = self.csp_policy
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = self.referrer_policy
        response.headers["Permissions-Policy"] = self.permissions_policy

        # Add HSTS header only for HTTPS requests or in production
        if self.enable_hsts:
            response.headers["Strict-Transport-Security"] = (
                f"max-age={self.hsts_max_age}; includeSubDomains; preload"
            )

        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    CSRF Protection Middleware for cookie-based sessions.

    For API endpoints using Bearer token authentication, CSRF is not
    needed as the token must be explicitly included in requests.

    This middleware protects:
    - Form submissions that use cookie-based auth
    - OAuth callback flows
    - Any state-changing operations using session cookies

    Usage:
    - Client must include X-CSRF-Token header on state-changing requests
    - Token is generated and stored in a secure cookie
    - Token is validated against the header on POST/PUT/DELETE/PATCH
    """

    # Paths that don't require CSRF validation.
    #
    # The dashboard SPA uses session cookies (SameSite=Lax, Secure) with
    # CORS origin checks, which already prevents cross-site request forgery.
    # Dashboard endpoints are exempt because the SPA doesn't implement
    # CSRF tokens — the cookie attributes provide equivalent protection.
    # Only NON-GET requests reach CSRF validation (safe methods are skipped),
    # and Bearer/API-key requests are skipped too (CSRF is a cookie-auth
    # problem). So this list is exclusively for endpoints that take a
    # state-changing, COOKIE-authenticated request which legitimately cannot
    # carry a double-submit CSRF token:
    #   - the token comes from an external party (OAuth callbacks, webhooks,
    #     LMS-signed LTI launches), or
    #   - it is the token-rotation / logout flow whose own secret (the
    #     httpOnly refresh cookie) is the CSRF defense.
    # Cookie-authenticated dashboard mutations (/education, /auth/keys,
    # /auth/profile, /alerts, /analytics, /llm, /integrations, /admin, ...)
    # are deliberately NOT here: the SPA sends X-CSRF-Token and the
    # middleware enforces it. Do not re-add a dashboard route to this list.
    EXEMPT_PATHS = [
        # ── Public / externally-authenticated endpoints ──
        # Magic link endpoints (rate-limited, public)
        "/auth/magic-link/request",
        "/auth/magic-link/check",
        "/auth/magic-link/verify",
        # Session refresh (uses httpOnly refresh token cookie)
        "/auth/session/refresh",
        # OAuth callbacks (token comes from OAuth provider)
        "/auth/google/callback",
        "/auth/microsoft/callback",
        "/google/callback",
        "/microsoft/callback",
        "/canvas/oauth/callback",
        "/blackboard/oauth/callback",
        "/moodle/callback",
        "/brightspace/callback",
        # LTI endpoints (authenticated via LMS JWT)
        "/lti/",
        "/lti/brightspace/login",
        "/lti/brightspace/launch",
        "/lti/brightspace/deep-link",
        "/blackboard-lti/",
        # Canvas content endpoints (authenticated via Bearer JWT)
        "/canvas/content/",
        "/canvas/courses/",
        # Webhooks (authenticated via subscription ID/signature)
        "/webhooks/",
        # Health checks
        "/health",
        "/api/health",
        # API documentation
        "/docs",
        "/redoc",
        "/openapi.json",
    ]

    # Safe HTTP methods that don't need CSRF protection
    SAFE_METHODS = ["GET", "HEAD", "OPTIONS", "TRACE"]

    def __init__(
        self,
        app,
        cookie_name: str = "csrf_token",
        header_name: str = "X-CSRF-Token",
        cookie_secure: bool = True,
        cookie_httponly: bool = True,
        cookie_samesite: str = "Lax",
        cookie_domain: str | None = None,
        token_length: int = 32,
        enabled: bool = True,
    ):
        super().__init__(app)
        self.cookie_name = cookie_name
        self.header_name = header_name
        self.cookie_secure = cookie_secure
        self.cookie_httponly = cookie_httponly
        self.cookie_samesite = cookie_samesite
        self.cookie_domain = cookie_domain
        self.token_length = token_length
        self.enabled = enabled

        # Never allow CSRF to be disabled outside development
        if not self.enabled:
            import os

            if os.getenv("ENVIRONMENT", "development") != "development":
                logger.warning(
                    "CSRF cannot be disabled outside development — forcing enabled"
                )
                self.enabled = True

    def _generate_token(self) -> str:
        """Generate a cryptographically secure CSRF token."""
        return secrets.token_urlsafe(self.token_length)

    def _is_exempt(self, path: str) -> bool:
        """Check if the path is exempt from CSRF validation."""
        for exempt_path in self.EXEMPT_PATHS:
            if path.startswith(exempt_path):
                return True
        return False

    def _has_bearer_auth(self, request: Request) -> bool:
        """Check if request uses Bearer token authentication."""
        auth_header = request.headers.get("Authorization", "")
        return auth_header.startswith("Bearer ")

    def _bearer_auth_is_csrf_exempt(self, request: Request) -> bool:
        """Keep key CRUD cookie-bound when its preferred principal is a session.

        Key-management authentication prefers ``aelira_access`` over Bearer
        credentials. Cookie presence therefore determines the CSRF boundary,
        even when that cookie is invalid and the Bearer credential could be a
        valid API key. This intentionally fails closed before authentication.
        """
        is_key_management = request.url.path.startswith("/auth/keys")
        has_access_cookie = "aelira_access" in request.cookies
        return self._has_bearer_auth(request) and not (
            is_key_management and has_access_cookie
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip if CSRF is disabled
        if not self.enabled:
            return await call_next(request)

        # Skip for safe methods
        if request.method in self.SAFE_METHODS:
            response = await call_next(request)
            # Ensure CSRF token cookie exists for subsequent requests
            self._ensure_csrf_cookie(request, response)
            return response

        # Skip for exempt paths
        if self._is_exempt(request.url.path):
            return await call_next(request)

        # Skip for Bearer token authenticated requests
        # (CSRF is only relevant for cookie-based auth)
        if self._bearer_auth_is_csrf_exempt(request):
            return await call_next(request)

        # Validate CSRF token for state-changing requests
        cookie_token = request.cookies.get(self.cookie_name)
        header_token = request.headers.get(self.header_name)

        if not cookie_token or not header_token:
            logger.warning(
                "CSRF validation failed: missing token; method=%s", request.method
            )
            # Return JSONResponse instead of raising HTTPException so that
            # CORSMiddleware can add Access-Control-Allow-Origin headers.
            # Raising inside BaseHTTPMiddleware.dispatch() bypasses CORS.
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing. Include X-CSRF-Token header."},
            )

        if not secrets.compare_digest(cookie_token, header_token):
            logger.warning(
                "CSRF validation failed: token mismatch; method=%s", request.method
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token invalid."},
            )

        # Token is valid, proceed with request
        response = await call_next(request)
        return response

    def _ensure_csrf_cookie(self, request: Request, response: Response) -> None:
        """Ensure the CSRF token exists at the configured cookie scope.

        Requests do not expose the Domain attribute of an incoming cookie. If
        a parent domain is configured, reissuing the observed value is the only
        safe way to promote an existing host-only token without rotating it.
        """
        token = request.cookies.get(self.cookie_name)
        if token and not self.cookie_domain:
            return
        if not token:
            token = self._generate_token()

        response.set_cookie(
            key=self.cookie_name,
            value=token,
            secure=self.cookie_secure,
            httponly=self.cookie_httponly,
            samesite=self.cookie_samesite,
            domain=self.cookie_domain,
            max_age=86400,  # 24 hours
        )


def get_csrf_token(request: Request) -> str:
    """
    Get or generate CSRF token for a request.

    Use this in endpoints that need to provide a CSRF token to the client.
    """
    token = request.cookies.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
    return token
