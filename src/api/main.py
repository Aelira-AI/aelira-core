"""Aelira Backend API - ADA Compliance Scanner with AI Analysis."""

from fastapi import FastAPI, HTTPException, Request, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    JSONResponse,
)
from fastapi.openapi.docs import get_redoc_html
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from typing import Optional, List, Tuple
import asyncio
import os
import re
import httpx
import tempfile
import logging
from pathlib import Path

from src.ai.ollama_client import OllamaClient
from src.ai.gemini_client import get_gemini_client
from src.ai.providers import (
    get_provider_manager,
    initialize_provider_manager,
    close_provider_manager,
)
from src.api.education import router as education_router
from src.api.auth_routes import router as auth_router
from src.api.oauth_routes import (
    router as oauth_router,
)  # OAuth login for department tier
from src.api.analytics import router as analytics_router
from src.api.lti_routes import router as lti_router  # Canvas LTI
from src.api.llm_providers import router as llm_providers_router
from src.api.tts import router as tts_router
from src.api.google_routes import (
    router as google_router,
)  # Google Workspace integration
from src.api.microsoft_routes import (
    router as microsoft_router,
)  # Microsoft 365 integration
from src.api.canvas_routes import (
    router as canvas_router,
)  # Canvas LMS integration
from src.api.canvas_scan_routes import (
    router as canvas_scan_router,
)  # Canvas scan/status/upload
from src.api.canvas_content_routes import (
    router as canvas_content_router,
)  # Canvas content scan/review/writeback
from src.api.blackboard_routes import (
    router as blackboard_router,
)  # Blackboard Learn integration
from src.api.moodle_routes import (
    router as moodle_router,
)  # Moodle LMS integration
from src.api.brightspace_routes import (
    router as brightspace_router,
)  # D2L Brightspace integration
from src.api.blackboard_lti_routes import (
    router as blackboard_lti_router,
)  # Blackboard LTI
from src.api.brightspace_lti_routes import (
    router as brightspace_lti_router,
)  # Brightspace LTI
from src.api.webhook_routes import router as webhook_router  # Cloud webhooks
from src.api.alert_routes import router as alert_router  # Email alert settings
from src.api.integration_routes import (
    router as integration_router,
)  # Integration status
from src.api.user_management import (
    router as user_management_router,
    accept_router as invitation_accept_router,
)  # User management
from src.api.account_routes import (
    router as account_router,
)  # Account deletion/deactivation
from src.api.review_routes import router as review_router  # Remediation review workflow
from src.config.settings import get_settings
from src.middleware.security import SecurityHeadersMiddleware, CSRFMiddleware
from src.auth.dependencies import get_required_api_key
from src.auth.redis_rate_limiter import get_redis_client
from src.db.database import get_db_dependency
from src.db.models import APIKey

# Prometheus metrics
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
import time

# Configure logging FIRST
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
logger = logging.getLogger(__name__)

# Get settings
settings = get_settings()

# =============================================================================
# Sentry Error Tracking (Production)
# =============================================================================
sentry_dsn = os.getenv("SENTRY_DSN")
if sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

    sentry_traces_sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

    # Constant, uninteresting traffic: uptime probes and metrics scrapes.
    _UNSAMPLED_TRACE_PATHS = frozenset({"/health", "/api/health", "/metrics"})

    def _traces_sampler(sampling_context: dict) -> float:
        """Drop probe/scrape traffic; sample everything else at the configured rate.

        Must always return a number — returning None makes the SDK treat the
        rate as invalid and discard the transaction outright.
        """
        path = sampling_context.get("asgi_scope", {}).get("path", "")
        if path in _UNSAMPLED_TRACE_PATHS:
            return 0.0
        return sentry_traces_sample_rate

    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", settings.env),
        # Tie issues and session health to a deploy. Override SENTRY_RELEASE
        # per deploy (e.g. with the git SHA) for commit-level resolution.
        release=os.getenv("SENTRY_RELEASE") or f"aelira-backend@{settings.api_version}",
        profiles_sample_rate=float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1")),
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        # Don't send PII to Sentry
        send_default_pii=False,
        traces_sampler=_traces_sampler,
    )
    logger.info(
        f"Sentry initialized for {os.getenv('SENTRY_ENVIRONMENT', settings.env)}"
    )

# Security for docs authentication
docs_security = HTTPBearer(auto_error=False)


def verify_api_key_for_docs(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(docs_security),
    api_key: Optional[str] = Query(
        None, alias="api_key", description="API key for docs access"
    ),
):
    """
    Verify API key for docs access.
    Accepts Bearer token in header OR api_key query parameter.
    In development mode, allows access without authentication.
    """
    # In development mode, allow access without authentication
    if settings.env == "development":
        return True

    from src.auth.auth_service import AuthService
    from src.db.database import get_db

    # Extract key from header or query param
    key = None
    if credentials and credentials.credentials:
        key = credentials.credentials
    elif api_key:
        key = api_key

    # Validate key against database
    if key:
        with get_db() as db:
            validated = AuthService.validate_api_key(db, key)
            if validated:
                return True

    raise HTTPException(
        status_code=401,
        detail="Valid API key required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Initialize FastAPI app with docs disabled (we'll add protected versions)
app = FastAPI(
    title=settings.api_title,
    description="AI-powered WCAG 2.1 AA accessibility compliance platform for higher education. Bulk remediation for PDFs, PowerPoints, LaTeX, and video with Canvas, Blackboard, Google Workspace, and Microsoft 365 integrations.",
    version=settings.api_version,
    docs_url=None,  # Disable default /docs
    redoc_url=None,  # Disable default /redoc
    openapi_url=None,  # Disable default /openapi.json
)

# Include routers
app.include_router(auth_router)
app.include_router(oauth_router)  # OAuth for department tier users
app.include_router(education_router)
app.include_router(analytics_router)
app.include_router(lti_router)  # Canvas LTI
app.include_router(llm_providers_router)  # LLM provider management API
app.include_router(tts_router)  # Text-to-Speech for accessibility
app.include_router(google_router)  # Google Workspace integration
app.include_router(microsoft_router)  # Microsoft 365 integration
app.include_router(canvas_router)  # Canvas LMS integration (REST API)
app.include_router(canvas_scan_router)  # Canvas scan/status/upload
app.include_router(canvas_content_router)  # Canvas content scan/review/writeback
app.include_router(blackboard_router)  # Blackboard Learn integration (REST API)
app.include_router(moodle_router)  # Moodle LMS integration (REST API)
app.include_router(brightspace_router)  # D2L Brightspace integration (REST API)
app.include_router(blackboard_lti_router)  # Blackboard LTI integration
app.include_router(brightspace_lti_router)  # Brightspace LTI integration
app.include_router(webhook_router)  # Cloud webhooks (Google, Microsoft)
app.include_router(alert_router, prefix="/api")  # Email alert settings
app.include_router(integration_router)  # Integration status (all providers)
app.include_router(user_management_router)  # User management (admin only)
app.include_router(invitation_accept_router)  # Invitation acceptance (public)
app.include_router(account_router)  # Account deletion/deactivation
app.include_router(review_router, prefix="/api")  # Remediation review workflow


# Startup/Shutdown event handlers for RAG knowledge base and LLM providers
@app.on_event("startup")
async def startup_event():
    """Initialize RAG knowledge base and LLM providers on API startup."""
    # SECURITY: Fail fast if mock auth is misconfigured in production
    if settings.env == "production" and settings.allow_mock_auth:
        logger.critical(
            "SECURITY CRITICAL: ALLOW_MOCK_AUTH=true in production environment! "
            "This is a severe security vulnerability. Shutting down immediately. "
            "Set ALLOW_MOCK_AUTH=false or remove the environment variable."
        )
        import sys

        sys.exit(1)

    logger.info("Initializing RAG knowledge base...")
    try:
        await ollama_client.initialize()
        logger.info("RAG knowledge base initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize RAG knowledge base: {e}")
        logger.warning("API will fall back to non-RAG classification")

    # Initialize the new LLM provider system
    logger.info("Initializing LLM provider manager...")
    try:
        await initialize_provider_manager()
        manager = get_provider_manager()
        logger.info(
            f"LLM provider manager initialized (primary: {manager.primary_type.value})"
        )
    except Exception as e:
        logger.error(f"Failed to initialize LLM provider manager: {e}")

    # Start scan timeout monitor (auto-fails stuck scans)
    try:
        from src.jobs.scan_timeout import start_scan_timeout_loop

        asyncio.create_task(start_scan_timeout_loop())
        logger.info("Scan timeout monitor started")
    except Exception as e:
        logger.error(f"Failed to start scan timeout monitor: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Close RAG knowledge base and LLM providers on API shutdown."""
    logger.info("Closing RAG knowledge base...")
    try:
        await ollama_client.close()
        logger.info("RAG knowledge base closed successfully")
    except Exception as e:
        logger.error(f"Error closing RAG knowledge base: {e}")

    # Close the LLM provider manager
    logger.info("Closing LLM provider manager...")
    try:
        await close_provider_manager()
        logger.info("LLM provider manager closed successfully")
    except Exception as e:
        logger.error(f"Error closing LLM provider manager: {e}")


# CORS middleware - properly configured
# Note: Cannot use ["*"] with credentials=True (CORS spec violation)
# Using explicit origins list for both dev and prod
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

# Security Headers middleware (CSP, HSTS, X-Frame-Options, etc.)
if settings.enable_security_headers:
    app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=settings.enable_hsts and settings.env == "production",
        hsts_max_age=settings.hsts_max_age,
    )
    logger.info("Security headers middleware enabled")

# CSRF Protection middleware
if settings.enable_csrf:
    app.add_middleware(
        CSRFMiddleware,
        cookie_secure=settings.csrf_cookie_secure and settings.env == "production",
        cookie_samesite=settings.csrf_cookie_samesite,
        # The CSRF token is a double-submit value, not a secret credential:
        # the SPA must be able to read it to echo it in X-CSRF-Token. The
        # SESSION cookie stays httpOnly; only this token cookie is readable.
        cookie_httponly=False,
        enabled=settings.env == "production",  # Only enforce in production
    )
    logger.info("CSRF protection middleware enabled")


# Prometheus metrics middleware for request tracking
@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Track request metrics for Prometheus and log requests for debugging."""
    from src.monitoring.metrics import (
        REQUEST_COUNT,
        REQUEST_LATENCY,
        ACTIVE_CONNECTIONS,
        normalize_endpoint,
    )

    # Track active connections
    ACTIVE_CONNECTIONS.inc()

    start_time = time.time()
    method = request.method
    path = request.url.path

    # Log incoming request
    client_ip = request.client.host if request.client else "unknown"
    logger.info(f"[MIDDLEWARE] {method} {path} from {client_ip}")

    try:
        response = await call_next(request)
        duration = time.time() - start_time
        status_code = response.status_code

        # Skip metrics endpoint to avoid recursion and reduce noise
        if path != "/metrics":
            # Prefer the FastAPI-matched route template so scanner probes
            # for non-existent paths bucket under "/__other__" instead of
            # creating per-probe series in the histogram.
            matched_route = request.scope.get("route")
            route_path = getattr(matched_route, "path", None)
            normalized_path = normalize_endpoint(path, route_path)

            REQUEST_COUNT.labels(
                method=method,
                endpoint=normalized_path,
                status_code=str(status_code),
            ).inc()

            REQUEST_LATENCY.labels(
                method=method,
                endpoint=normalized_path,
            ).observe(duration)

        logger.info(f"[MIDDLEWARE] Response: {status_code} ({duration:.3f}s)")
        return response

    finally:
        ACTIVE_CONNECTIONS.dec()


# Initialize AI clients
# Gemini is primary (fast cloud API), Ollama is fallback (local/air-gapped)
gemini_client = get_gemini_client()

# Initialize Ollama client with RAG enabled (for fallback and RAG features)
ollama_client = OllamaClient(
    host=settings.ollama_host,
    enable_rag=True,  # Enable RAG for consistent classifications
)


# Pydantic models
class HealthResponse(BaseModel):
    status: str
    message: str


class ViolationAnalysisRequest(BaseModel):
    rule_id: str
    impact: str
    html_snippet: str
    selector: str
    generate_fix: bool = True
    # Context-aware fix generation (optional)
    page_url: Optional[str] = None
    page_title: Optional[str] = None
    page_context: Optional[str] = None  # Meta description, heading context, etc.


class BatchAnalysisViolation(BaseModel):
    id: str  # Unique identifier for this violation
    rule_id: str
    impact: str
    html_snippet: str
    selector: str
    description: Optional[str] = None
    # Context-aware fix generation (optional)
    page_url: Optional[str] = None
    page_title: Optional[str] = None
    page_context: Optional[str] = None


class BatchAnalysisRequest(BaseModel):
    violations: List[BatchAnalysisViolation]
    generate_fixes: bool = (
        True  # Whether to generate code fixes for Critical/High issues
    )


# Helper function for image-alt violations
async def generate_image_alt_text(
    html_snippet: str, base_url: Optional[str] = None
) -> Optional[str]:
    """
    Extract image from HTML and generate AI alt text using vision model.

    Args:
        html_snippet: HTML containing <img> tag
        base_url: Base URL to resolve relative image paths (optional)

    Returns:
        AI-generated alt text or None if failed
    """
    try:
        # Extract src attribute from img tag
        src_match = re.search(r'src=["\'](.*?)["\']', html_snippet)
        if not src_match:
            return None

        image_url = src_match.group(1)

        # Resolve relative URLs if base_url provided
        if base_url and not image_url.startswith(("http://", "https://", "data:")):
            from urllib.parse import urljoin

            image_url = urljoin(base_url, image_url)

        # Skip data URLs (would need base64 decoding)
        if image_url.startswith("data:"):
            return None

        # SECURITY (SSRF): refuse private/reserved targets and non-http(s)
        # schemes before issuing any request. image_url originates from
        # caller-supplied HTML, so it is untrusted.
        from ..utils.security import validate_url_not_private

        try:
            validate_url_not_private(image_url)
        except ValueError as exc:
            logger.warning(f"[generate_image_alt_text] Blocked image URL: {exc}")
            return None

        # Download image
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(image_url)
            if response.status_code != 200:
                return None

            # Save to temp file
            image_bytes = response.content
            suffix = ".jpg"  # Default
            if "content-type" in response.headers:
                content_type = response.headers["content-type"]
                if "png" in content_type:
                    suffix = ".png"
                elif "gif" in content_type:
                    suffix = ".gif"
                elif "webp" in content_type:
                    suffix = ".webp"

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(image_bytes)
                tmp_path = tmp.name

        # Use vision AI to generate alt text
        try:
            from ..education.image_alt_text import ImageAltTextGenerator

            generator = ImageAltTextGenerator()
            result = await generator.generate_alt_text(
                image_path=tmp_path,
                context=f"Image from website ({image_url})",
                educational_context=False,  # General web image, not educational
            )

            if result.get("success"):
                return result.get("alt_text")
        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        return None

    except Exception as e:
        print(f"[generate_image_alt_text] Failed: {e}")
        return None


# Protected API Documentation Endpoints
@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_schema(authorized: bool = Depends(verify_api_key_for_docs)):
    """
    OpenAPI schema - requires API key in production.
    Access via: /openapi.json?api_key=YOUR_KEY or Authorization header.
    """
    return get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )


@app.get("/docs", include_in_schema=False)
async def get_swagger_docs(authorized: bool = Depends(verify_api_key_for_docs)):
    """
    Swagger UI documentation with dark/light mode toggle.
    Access via: /docs?api_key=YOUR_KEY or Authorization header.
    """
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{app.title} - API Documentation</title>
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
        <link rel="icon" href="https://fastapi.tiangolo.com/img/favicon.png">
        <style>
            :root {{
                --aelira-purple: #7c3aed;
                --aelira-purple-light: #a78bfa;
                --aelira-purple-dark: #5b21b6;
            }}

            /* Light mode (default) */
            body {{
                margin: 0;
                background: #fafafa;
                transition: background-color 0.3s ease;
            }}

            .swagger-ui .topbar {{
                background: linear-gradient(135deg, var(--aelira-purple) 0%, var(--aelira-purple-dark) 100%);
                padding: 10px 0;
            }}

            .swagger-ui .topbar .download-url-wrapper .download-url-button {{
                background: var(--aelira-purple-light);
            }}

            .theme-toggle {{
                position: fixed;
                bottom: 20px;
                right: 20px;
                z-index: 10000;
                background: rgba(255,255,255,0.95);
                border: 1px solid #ddd;
                border-radius: 50%;
                width: 44px;
                height: 44px;
                padding: 0;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: all 0.3s ease;
                box-shadow: 0 2px 12px rgba(0,0,0,0.15);
            }}

            .theme-toggle:hover {{
                background: #fff;
                box-shadow: 0 4px 16px rgba(0,0,0,0.2);
                transform: scale(1.05);
            }}

            /* Dark mode */
            body.dark-mode {{
                background: #1a1a2e;
            }}

            body.dark-mode .swagger-ui {{
                filter: invert(88%) hue-rotate(180deg);
            }}

            body.dark-mode .swagger-ui .model-box,
            body.dark-mode .swagger-ui img,
            body.dark-mode .swagger-ui .topbar {{
                filter: invert(100%) hue-rotate(180deg);
            }}

            body.dark-mode .theme-toggle {{
                background: rgba(30,30,50,0.9);
                border-color: #444;
                color: #eee;
            }}

            body.dark-mode .theme-toggle:hover {{
                background: #2a2a4e;
            }}

            /* Header branding */
            .aelira-header {{
                background: linear-gradient(135deg, var(--aelira-purple) 0%, var(--aelira-purple-dark) 100%);
                color: white;
                padding: 16px 24px;
                display: flex;
                align-items: center;
                justify-content: space-between;
            }}

            .aelira-header h1 {{
                margin: 0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 20px;
                font-weight: 600;
            }}

            .aelira-header .version {{
                background: rgba(255,255,255,0.2);
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 12px;
            }}

            body.dark-mode .aelira-header {{
                filter: none;
            }}

            .theme-toggle svg {{
                width: 18px;
                height: 18px;
                stroke: currentColor;
                stroke-width: 2;
                stroke-linecap: round;
                stroke-linejoin: round;
                fill: none;
            }}

            .icon-sun {{ display: none; }}
            .icon-moon {{ display: block; }}

            body.dark-mode .icon-sun {{ display: block; }}
            body.dark-mode .icon-moon {{ display: none; }}
        </style>
    </head>
    <body>
        <div class="aelira-header">
            <h1>Aelira API Documentation</h1>
            <span class="version">v{app.version}</span>
        </div>

        <button class="theme-toggle" onclick="toggleTheme()" aria-label="Toggle dark mode">
            <svg class="icon-moon" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
            <svg class="icon-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"></circle><line x1="12" y1="1" x2="12" y2="3"></line><line x1="12" y1="21" x2="12" y2="23"></line><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line><line x1="1" y1="12" x2="3" y2="12"></line><line x1="21" y1="12" x2="23" y2="12"></line><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line></svg>
        </button>

        <div id="swagger-ui"></div>

        <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
        <script>
            // Theme toggle functionality
            function toggleTheme() {{
                document.body.classList.toggle('dark-mode');
                const isDark = document.body.classList.contains('dark-mode');
                localStorage.setItem('aelira-docs-theme', isDark ? 'dark' : 'light');
            }}

            // Load saved theme preference
            (function() {{
                if (localStorage.getItem('aelira-docs-theme') === 'dark') {{
                    document.body.classList.add('dark-mode');
                }}
            }})();

            // Initialize Swagger UI
            window.onload = function() {{
                SwaggerUIBundle({{
                    url: '/openapi.json',
                    dom_id: '#swagger-ui',
                    layout: 'BaseLayout',
                    deepLinking: true,
                    showExtensions: true,
                    showCommonExtensions: true,
                    presets: [
                        SwaggerUIBundle.presets.apis,
                        SwaggerUIBundle.SwaggerUIStandalonePreset
                    ]
                }});
            }};
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/redoc", include_in_schema=False)
async def get_redoc_docs(authorized: bool = Depends(verify_api_key_for_docs)):
    """
    ReDoc documentation - requires API key in production.
    Access via: /redoc?api_key=YOUR_KEY or Authorization header.
    """
    return get_redoc_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - ReDoc",
        redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js",
    )


# Block search engine crawling of the API
@app.get("/robots.txt", include_in_schema=False)
async def robots_txt():
    """Return robots.txt to block all search engine crawling of the API."""
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


# Health check endpoints
@app.get("/", response_model=HealthResponse)
async def root():
    """Root endpoint - API health check."""
    return {"status": "healthy", "message": "Aelira ADA Compliance API is running"}


@app.get("/health", response_model=HealthResponse)
@app.get("/api/health", response_model=HealthResponse)
async def health():
    """Health check endpoint (available at both /health and /api/health)."""
    return {"status": "healthy", "message": "API is operational"}


@app.get("/ready")
async def readiness(
    db: Session = Depends(get_db_dependency),
    redis_client=Depends(get_redis_client),
):
    """
    Kubernetes readiness probe.

    Actively checks that the app can currently reach its dependencies —
    a trivial `SELECT 1` against Postgres and a Redis PING — rather than
    just reporting that the process is up (that's what /health is for).
    Returns 200 only when both succeed; 503 with the failing check(s)
    marked "failed" otherwise, so k8s stops routing traffic here until
    the dependency recovers.
    """
    checks = {"database": "ok", "redis": "ok"}
    ready = True

    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.warning(f"Readiness check: database unreachable: {e}")
        checks["database"] = "failed"
        ready = False

    if not settings.redis_enabled:
        # A deployment that deliberately disabled Redis must not read as
        # unready forever; report the choice instead of a failure.
        checks["redis"] = "disabled"
    else:
        try:
            if redis_client is None or not redis_client.ping():
                raise ConnectionError("Redis client unavailable")
        except Exception as e:
            logger.warning(f"Readiness check: redis unreachable: {e}")
            checks["redis"] = "failed"
            ready = False

    if ready:
        return {"status": "ready", "checks": checks}
    return JSONResponse(
        status_code=503,
        content={"status": "not_ready", "checks": checks},
    )


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Expose Prometheus metrics for scraping.

    Returns the current metrics registry in Prometheus text exposition
    format. Excluded from the OpenAPI schema. No authentication is
    enforced here; access is restricted at the network edge instead
    (see inline comment below).
    """
    # Auth lives at the network edge, not here: keep this endpoint on an
    # internal network or behind your reverse proxy's basicauth. Adding a
    # DB-backed auth dependency here turns every DB blip into a flood of
    # 500s on Prometheus scrapes (learned in production).
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/api/csrf-token")
async def get_csrf_token_endpoint(request: Request):
    """
    Get CSRF token for form submissions.

    For cookie-based authentication, include this token in the X-CSRF-Token
    header for all state-changing requests (POST, PUT, DELETE, PATCH).

    Note: API requests using Bearer token authentication do not need CSRF tokens.
    """
    from src.middleware.security import get_csrf_token

    token = get_csrf_token(request)
    response = {"csrf_token": token}
    return response


@app.get("/api/ai/health")
async def ai_health():
    """Check AI models status and availability.

    Returns health info for both Gemini (primary) and Ollama (fallback).
    """
    # Get Gemini health (primary provider)
    gemini_health = gemini_client.health_check()

    # Get Ollama health (fallback provider)
    ollama_health = ollama_client.health_check()

    # Determine overall status
    if gemini_health.get("use_gemini") and gemini_health.get("gemini_configured"):
        overall_status = "healthy"
        primary_provider = "gemini"
    elif ollama_health.get("status") == "healthy":
        overall_status = "degraded"
        primary_provider = "ollama"
    else:
        overall_status = "unhealthy"
        primary_provider = "none"

    return {
        "status": overall_status,
        "primary_provider": primary_provider,
        "gemini": {
            "configured": gemini_health.get("gemini_configured", False),
            "enabled": gemini_health.get("use_gemini", False),
            "text_model": gemini_health.get("gemini_text_model"),
            "code_model": gemini_health.get("gemini_code_model"),
            "vision_model": settings.gemini_vision_model,
        },
        "ollama_fallback": {
            "status": ollama_health.get("status"),
            "host": gemini_health.get("ollama_host"),
            "fallback_text_model": gemini_health.get("ollama_fallback"),
            "fallback_vision_model": settings.ollama_fallback_vision,
            "available_models": ollama_health.get("available_models", []),
            "rag_enabled": ollama_health.get("rag_enabled", False),
        },
        "model_config": {
            "vision": {
                "primary": settings.gemini_vision_model,
                "fallback": settings.ollama_fallback_vision,
            },
            "text": {
                "primary": settings.gemini_text_model,
                "fallback": settings.ollama_fallback_text,
            },
            "code": {
                "primary": settings.gemini_code_model,
                "fallback": settings.ollama_fallback_text,
            },
        },
    }


# AI analysis endpoints (PROTECTED - requires API key)
@app.post("/api/ai/analyze")
async def analyze_violation(
    request: ViolationAnalysisRequest,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """Analyze accessibility violation with AI.

    Requires authentication via API key.

    Uses Gemini with RAG-enhanced WCAG context for grounded, consistent classifications.

     For image-alt violations, uses vision AI to generate proper alt text!
    """
    try:
        # Step 1: Classify issue severity using Gemini with RAG-enhanced WCAG context
        classification_result = await gemini_client.classify_severity_with_rag(
            rule_id=request.rule_id,
            impact=request.impact,
            html_snippet=request.html_snippet,
            selector=request.selector,
            violation_description=None,
        )

        if classification_result.get("success"):
            classification = {
                "severity": classification_result.get("severity", "Medium"),
                "explanation": classification_result.get("explanation", ""),
                "business_impact": classification_result.get("business_impact", ""),
                "provider": classification_result.get("provider"),
                "model": classification_result.get("model"),
                "inference_time": classification_result.get("inference_time", 0),
                "rag_enabled": classification_result.get("rag_enabled", False),
                "rag_guidelines": classification_result.get("rag_guidelines", []),
            }
        else:
            # Fallback to Ollama RAG classification
            logger.warning(
                "Gemini RAG classification failed, falling back to Ollama RAG"
            )
            classification = await ollama_client.classify_issue_with_rag(
                rule_id=request.rule_id,
                impact=request.impact,
                html_snippet=request.html_snippet,
                selector=request.selector,
                violation_description=None,
            )
            classification["provider"] = "ollama"

        # Step 2: Generate fix if requested and severity is High/Critical
        if request.generate_fix and classification.get("severity") in [
            "Critical",
            "High",
        ]:
            # For image-alt violations, use vision AI to generate alt text
            if request.rule_id == "image-alt":
                ai_alt_text = await generate_image_alt_text(request.html_snippet)
                if ai_alt_text:
                    src_match = re.search(r'src=["\'](.*?)["\']', request.html_snippet)
                    src = src_match.group(1) if src_match else "image.jpg"

                    classification["fix"] = {
                        "fix_recommendation": f"""## Fixed HTML
```html
<img src="{src}" alt="{ai_alt_text}">
```

## AI-Generated Alt Text The vision model analyzed the image and generated: "{ai_alt_text}"

## Implementation Steps
1. Add the alt attribute with the AI-generated description
2. Verify the description accurately represents the image content
3. Test with screen reader to ensure proper announcement
""",
                        "model": f"{settings.gemini_vision_model} (vision)",
                        "inference_time": 0.0,
                        "ai_generated_alt_text": ai_alt_text,
                        "provider": "gemini",
                    }
                else:
                    # Fallback to Gemini code fix generation
                    fix_result = await gemini_client.generate_code_fix(
                        html_snippet=request.html_snippet,
                        rule_id=request.rule_id,
                        issue_description=classification.get("explanation", ""),
                        context=request.page_context,
                    )
                    classification["fix"] = {
                        "fix_recommendation": fix_result.get("fixed_code", ""),
                        "explanation": fix_result.get("explanation", ""),
                        "model": fix_result.get("model"),
                        "inference_time": fix_result.get("inference_time", 0),
                        "provider": fix_result.get("provider"),
                        "vision_ai_failed": True,
                    }
            else:
                # Non-image violations: use Gemini code fix generation
                fix_result = await gemini_client.generate_code_fix(
                    html_snippet=request.html_snippet,
                    rule_id=request.rule_id,
                    issue_description=classification.get("explanation", ""),
                    context=request.page_context,
                )
                classification["fix"] = {
                    "fix_recommendation": fix_result.get("fixed_code", ""),
                    "explanation": fix_result.get("explanation", ""),
                    "model": fix_result.get("model"),
                    "inference_time": fix_result.get("inference_time", 0),
                    "provider": fix_result.get("provider"),
                }

        return classification

    except Exception as e:
        logger.error(f"AI analysis failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="AI analysis failed. Please try again."
        )


@app.post("/api/ai/batch-analyze")
async def batch_analyze_violations(
    request: BatchAnalysisRequest,
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """Batch analyze multiple violations with AI (optimized for CLI).

    Requires authentication via API key.

    This endpoint is designed for CLI tools that scan entire pages
    and need efficient AI analysis of all violations at once.

    Uses Gemini with RAG-enhanced WCAG context for grounded, consistent classifications.
    Returns AI classification for all issues, plus code fixes for
    Critical/High severity violations if requested.
    """
    results = []

    for violation in request.violations:
        try:
            # Step 1: Classify with Gemini + RAG (primary) or Ollama RAG (fallback)
            classification_result = await gemini_client.classify_severity_with_rag(
                rule_id=violation.rule_id,
                impact=violation.impact,
                html_snippet=violation.html_snippet,
                selector=violation.selector,
                violation_description=violation.description,
            )

            if classification_result.get("success"):
                classification = {
                    "severity": classification_result.get("severity", "Medium"),
                    "explanation": classification_result.get("explanation", ""),
                    "business_impact": classification_result.get("business_impact", ""),
                    "provider": classification_result.get("provider"),
                    "model": classification_result.get("model"),
                    "inference_time": classification_result.get("inference_time", 0),
                    "rag_enabled": classification_result.get("rag_enabled", False),
                    "rag_guidelines": classification_result.get("rag_guidelines", []),
                }
            else:
                # Fallback to Ollama RAG classification
                classification = await ollama_client.classify_issue_with_rag(
                    rule_id=violation.rule_id,
                    impact=violation.impact,
                    html_snippet=violation.html_snippet,
                    selector=violation.selector,
                    violation_description=violation.description,
                )
                classification["provider"] = "ollama"

            violation_result = {
                "id": violation.id,
                "rule_id": violation.rule_id,
                "classification": classification,
            }

            # Step 2: Generate fix if requested and severity warrants it
            if request.generate_fixes and classification.get("severity") in [
                "Critical",
                "High",
            ]:
                if violation.rule_id == "image-alt":
                    ai_alt_text = await generate_image_alt_text(violation.html_snippet)
                    if ai_alt_text:
                        src_match = re.search(
                            r'src=["\'](.*?)["\']', violation.html_snippet
                        )
                        src = src_match.group(1) if src_match else "image.jpg"

                        violation_result["fix"] = {
                            "fix_recommendation": f"""## Fixed HTML
```html
<img src="{src}" alt="{ai_alt_text}">
```

## AI-Generated Alt Text The vision model analyzed the image and generated: "{ai_alt_text}"

## Implementation Steps
1. Add the alt attribute with the AI-generated description
2. Verify the description accurately represents the image content
3. Test with screen reader to ensure proper announcement
""",
                            "model": f"{settings.gemini_vision_model} (vision)",
                            "inference_time": 0.0,
                            "ai_generated_alt_text": ai_alt_text,
                            "provider": "gemini",
                        }
                    else:
                        # Fallback to Gemini code fix
                        fix_result = await gemini_client.generate_code_fix(
                            html_snippet=violation.html_snippet,
                            rule_id=violation.rule_id,
                            issue_description=classification.get("explanation", ""),
                            context=violation.page_context,
                        )
                        violation_result["fix"] = {
                            "fix_recommendation": fix_result.get("fixed_code", ""),
                            "explanation": fix_result.get("explanation", ""),
                            "model": fix_result.get("model"),
                            "inference_time": fix_result.get("inference_time", 0),
                            "provider": fix_result.get("provider"),
                            "vision_ai_failed": True,
                        }
                else:
                    # Non-image violations: use Gemini code fix generation
                    fix_result = await gemini_client.generate_code_fix(
                        html_snippet=violation.html_snippet,
                        rule_id=violation.rule_id,
                        issue_description=classification.get("explanation", ""),
                        context=violation.page_context,
                    )
                    violation_result["fix"] = {
                        "fix_recommendation": fix_result.get("fixed_code", ""),
                        "explanation": fix_result.get("explanation", ""),
                        "model": fix_result.get("model"),
                        "inference_time": fix_result.get("inference_time", 0),
                        "provider": fix_result.get("provider"),
                    }

            results.append(violation_result)

        except Exception as e:
            results.append(
                {"id": violation.id, "rule_id": violation.rule_id, "error": str(e)}
            )

    return {
        "total_violations": len(request.violations),
        "analyzed": len(results),
        "results": results,
    }


# Test endpoint for development
@app.get("/api/test-ai")
async def test_ai(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """Test AI integration with a sample violation using Gemini.

    Requires authentication via API key.
    """
    sample_violation = {
        "rule_id": "image-alt",
        "impact": "critical",
        "html_snippet": '<img src="logo.png">',
        "selector": "img:nth-child(1)",
    }

    try:
        # Test Gemini classification
        result = await gemini_client.classify_severity(**sample_violation)

        return {
            "message": "AI test successful",
            "sample_violation": sample_violation,
            "ai_analysis": result,
            "provider": result.get("provider", "gemini"),
            "model": result.get("model"),
            "inference_time": result.get("inference_time"),
        }
    except Exception as e:
        return {"message": "AI test failed", "error": str(e)}


# Mount email static assets (logo, etc.) at /static
# Served at api.example.com/static/logo.png for use in email templates
static_path = Path(__file__).parent.parent / "static"
if static_path.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(static_path)),
        name="email-static",
    )

# Mount dashboard static files
dashboard_path = Path(__file__).parent.parent.parent / "dashboard" / "dist"
if dashboard_path.exists():
    # Mount static assets at /dashboard/assets for legacy paths
    app.mount(
        "/dashboard/assets",
        StaticFiles(directory=str(dashboard_path / "assets")),
        name="dashboard-assets",
    )
    # Mount static assets at /assets for dashboard.example.com subdomain (root-level)
    app.mount(
        "/assets",
        StaticFiles(directory=str(dashboard_path / "assets")),
        name="root-assets",
    )

    # Serve dashboard index.html for all /dashboard routes (SPA routing)
    @app.get("/dashboard{path:path}")
    async def serve_dashboard(path: str):
        """Serve the React dashboard SPA."""
        index_file = dashboard_path / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Root-level SPA routes for dashboard.example.com subdomain
    # These handle direct navigation to /signup, /login, etc.
    @app.get("/signup")
    @app.get("/login")
    @app.get("/auth/verify")
    async def serve_dashboard_root():
        """Serve dashboard SPA for root-level auth routes."""
        index_file = dashboard_path / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        raise HTTPException(status_code=404, detail="Dashboard not found")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
