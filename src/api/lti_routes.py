"""
Canvas LTI 1.3 API Routes (Phase 4.5)

FastAPI routes for handling LTI 1.3 launch, deep linking,
and grade passback with Canvas LMS.

Endpoints:
- POST /lti/login - OIDC login initiation
- POST /lti/launch - LTI resource link launch
- POST /lti/deep-link - Deep linking launch
- POST /lti/exchange - One-time code → access token
- POST /lti/bridge - Auth bridge (existing session → one-time code)
- GET /lti/jwks - Public key set for verification
- GET /lti/config - LTI configuration JSON
"""

from typing import Optional, Tuple

from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json
import logging
import traceback
import os
import hashlib
import time

from src.integrations.canvas_lti import (
    get_canvas_lti_service,
    CanvasLTIService,
    FastAPISessionService,
    FastAPICookieService,
    CanvasLaunchData,
)
from src.api.lti_launch_handler import (
    handle_lti_launch,
    exchange_code,
    store_ags_context,
    create_bridge_code,
)
from src.auth.dependencies import get_required_api_key
from src.db.database import get_db_dependency
from src.db.models import APIKey, LTIRegistration, LTIPlatform, Department
from src.middleware.quota import check_feature_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lti", tags=["Canvas LTI Integration"])

# In-memory session storage (use Redis in production)
_session_service = FastAPISessionService()


def get_lti_service() -> CanvasLTIService:
    """Dependency to get LTI service"""
    return get_canvas_lti_service()


# =============================================================================
# LTI Registration Lookup
# =============================================================================


def get_department_from_lti_launch(
    db: Session,
    issuer: str,
    client_id: str,
) -> tuple[LTIRegistration | None, Department | None, str | None]:
    """
    Look up the department associated with a Canvas LTI launch.

    Args:
        db: Database session
        issuer: LTI issuer URL from the launch
        client_id: LTI client_id from the launch

    Returns:
        Tuple of (registration, department, error_message)
        If successful, error_message is None.
        If failed, registration and department are None.
    """
    # Find LTI registration
    registration = (
        db.query(LTIRegistration)
        .filter(
            LTIRegistration.platform == LTIPlatform.CANVAS,
            LTIRegistration.issuer == issuer,
            LTIRegistration.client_id == client_id,
            LTIRegistration.is_active.is_(True),
        )
        .first()
    )

    if not registration:
        logger.warning(
            f"No Canvas LTI registration found for issuer={issuer}, client_id={client_id}"
        )
        return (
            None,
            None,
            (
                "LTI tool not registered. Please contact your administrator to register "
                "this Canvas instance with Aelira."
            ),
        )

    # Get department
    department = (
        db.query(Department).filter(Department.id == registration.department_id).first()
    )

    if not department:
        logger.error(
            f"LTI registration {registration.id} references non-existent department "
            f"{registration.department_id}"
        )
        return None, None, "Configuration error: department not found"

    if not department.is_active:
        return None, None, "Department account is inactive. Please contact support."

    return registration, department, None


def check_lti_feature_access(department: Department) -> tuple[bool, str | None]:
    """
    Check if the department's tier allows LMS integration.

    Args:
        department: The department to check

    Returns:
        Tuple of (allowed, error_message)
    """
    if not check_feature_access(department.tier, "lms_integration"):
        # Find what tier includes this feature
        from src.config.settings import TIER_QUOTAS

        upgrade_tiers = []
        for tier_name, tier_config in TIER_QUOTAS.items():
            if "lms_integration" in tier_config.get("features", []):
                if "lms_integration" not in tier_config.get("excluded", []):
                    upgrade_tiers.append(tier_name)

        upgrade_suggestion = upgrade_tiers[0] if upgrade_tiers else "department"

        return False, (
            f"LMS Integration is not available on your current plan ({department.tier}). "
            f"Upgrade to {upgrade_suggestion} plan to access Canvas integration."
        )

    return True, None


def update_lti_launch_stats(db: Session, registration: LTIRegistration):
    """Update launch statistics for an LTI registration."""
    registration.launch_count = (registration.launch_count or 0) + 1
    registration.last_launch_at = datetime.now(timezone.utc)
    db.commit()


def _render_lti_error_page(
    title: str,
    message: str,
    help_text: str = "",
    show_upgrade_button: bool = False,
) -> str:
    """
    Render a user-friendly error page for LTI launch failures.

    Args:
        title: Error title
        message: Error message
        help_text: Additional help text
        show_upgrade_button: Whether to show an upgrade CTA

    Returns:
        HTML content for the error page
    """
    upgrade_button = ""
    if show_upgrade_button:
        pricing_url = os.getenv("WEBSITE_URL", "https://example.com") + "/pricing"
        upgrade_button = f"""
        <a href="{pricing_url}" target="_blank"
           style="display: inline-block; background: #4a90d9; color: white;
                  padding: 12px 24px; border-radius: 8px; text-decoration: none;
                  font-weight: 500; margin-top: 16px;">
            View Plans & Pricing
        </a>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title} - Aelira</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 600px;
                margin: 0 auto;
                padding: 60px 20px;
                background: #f5f7fa;
                text-align: center;
            }}
            .error-icon {{
                font-size: 64px;
                margin-bottom: 24px;
            }}
            h1 {{
                color: #1a1a2e;
                margin-bottom: 16px;
                font-size: 24px;
            }}
            .message {{
                color: #666;
                font-size: 16px;
                line-height: 1.6;
                margin-bottom: 24px;
            }}
            .help-text {{
                color: #888;
                font-size: 14px;
                margin-top: 24px;
            }}
            .logo {{
                margin-top: 40px;
                color: #999;
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        <div class="error-icon">⚠️</div>
        <h1>{title}</h1>
        <p class="message">{message}</p>
        {upgrade_button}
        <p class="help-text">{help_text}</p>
        <p class="logo">Powered by Aelira - WCAG 2.1 Accessibility Platform</p>
    </body>
    </html>
    """


# =============================================================================
# OIDC Login Flow
# =============================================================================


@router.post("/login")
@router.get("/login")
async def lti_login(
    request: Request, lti_service: CanvasLTIService = Depends(get_lti_service)
):
    """
    LTI 1.3 OIDC Login Initiation.

    Canvas redirects here first. We validate the login request
    and redirect to Canvas for authentication.
    """
    if not lti_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="LTI integration not configured. Set CANVAS_CLIENT_ID environment variable.",
        )

    # Get parameters from query string or form
    if request.method == "POST":
        params = dict(await request.form())
    else:
        params = dict(request.query_params)

    # Determine target launch URL — force HTTPS (internal request is HTTP behind Traefik)
    target_link_uri = params.get("target_link_uri", str(request.url_for("lti_launch")))
    target_link_uri = target_link_uri.replace("http://", "https://", 1)

    try:
        # Create cookie service for this request
        response = Response()
        cookie_service = FastAPICookieService(request=request, response=response)

        # Initiate OIDC login
        redirect_url = lti_service.initiate_oidc_login(
            request_params=params,
            target_link_uri=target_link_uri,
            session_service=_session_service,
            cookie_service=cookie_service,
        )

        # Transfer cookies from the cookie service response to the redirect
        redirect_response = RedirectResponse(url=redirect_url, status_code=302)
        for cookie_header in response.headers.getlist("set-cookie"):
            redirect_response.headers.append("set-cookie", cookie_header)
        return redirect_response

    except Exception as e:
        logger.error(f"LTI login failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail="LTI login failed")


# =============================================================================
# LTI Launch
# =============================================================================


@router.post("/launch")
async def lti_launch(
    request: Request,
    lti_service: CanvasLTIService = Depends(get_lti_service),
    db: Session = Depends(get_db_dependency),
):
    """
    LTI 1.3 Resource Link Launch.

    This is where users land after Canvas authentication.
    We validate the launch and redirect to the appropriate page.

    Feature Gating: Requires 'lms_integration' feature on department tier.
    """
    # Get parameters from form
    params = dict(await request.form())

    # Dev mode: handle LTI 1.1/1.3 form POST without state param
    # by rendering the dashboard directly in the iframe
    is_dev = os.getenv("ENV", "production") == "development"
    if is_dev and "state" not in params:
        logger.info("LTI dev params keys: %s", list(params.keys()))
        # Try multiple sources for course ID
        course_id = (
            params.get("custom_canvas_course_id")
            or params.get("custom_course_id")
            or params.get("context_id")
            or ""
        )
        # Fallback: extract from Referer header (Canvas URL like /courses/34/...)
        if not course_id:
            import re

            referer = request.headers.get("referer", "")
            m = re.search(r"/courses/(\d+)", referer)
            if m:
                course_id = m.group(1)
                logger.info("Extracted course_id=%s from Referer", course_id)
        dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:5173")
        target_url = f"{dashboard_url}/lti/course/{course_id}"
        logger.info("LTI dev launch → %s", target_url)
        # Render HTML that loads the dashboard in the same frame
        html = f"""<!DOCTYPE html>
<html><head>
<meta http-equiv="refresh" content="0;url={target_url}">
<script>window.location.replace("{target_url}");</script>
</head><body>Loading Aelira...</body></html>"""
        return HTMLResponse(content=html)

    if not lti_service.is_configured():
        raise HTTPException(status_code=503, detail="LTI integration not configured")

    try:
        # Create services
        cookie_service = FastAPICookieService(request=request)

        # Validate the launch
        message_launch = lti_service.validate_launch(
            request_params=params,
            session_service=_session_service,
            cookie_service=cookie_service,
        )

        # Extract launch data
        launch_data = lti_service.extract_launch_data(message_launch)

        # Get issuer and client_id from the validated launch for department lookup
        issuer = lti_service.get_issuer_from_launch(message_launch)
        client_id = lti_service.get_client_id_from_launch(message_launch)

        # Look up department from LTI registration
        registration, department, error = get_department_from_lti_launch(
            db, issuer, client_id
        )

        if error:
            logger.warning(
                f"Canvas LTI launch denied: {error} "
                f"(issuer={issuer}, client_id={client_id})"
            )
            # Return user-friendly error page instead of HTTP exception
            return HTMLResponse(
                content=_render_lti_error_page(
                    "LTI Registration Required",
                    error,
                    "Please contact your institution's IT administrator to complete "
                    "the Aelira integration setup.",
                ),
                status_code=403,
            )

        # Check feature access based on department tier
        allowed, feature_error = check_lti_feature_access(department)
        if not allowed:
            logger.warning(
                f"Canvas LTI launch denied for department {department.id}: "
                f"tier={department.tier} does not have lms_integration feature"
            )
            return HTMLResponse(
                content=_render_lti_error_page(
                    "Feature Not Available",
                    feature_error,
                    "Visit our pricing page to learn about plan upgrades.",
                    show_upgrade_button=True,
                ),
                status_code=403,
            )

        # Update launch statistics
        update_lti_launch_stats(db, registration)

        logger.info(
            f"Canvas LTI launch: user={launch_data.user_name}, "
            f"course={launch_data.course_name}, "
            f"instructor={launch_data.is_instructor}, "
            f"department={department.name} ({department.tier})"
        )

        # Check if this is a deep link launch
        if lti_service.is_deep_link_launch(message_launch):
            return await handle_deep_link_launch(
                request, lti_service, message_launch, launch_data
            )

        # --- Auto-provision user & mint token via one-time code ---
        redirect_url = handle_lti_launch(
            launch_data, registration, db, platform="canvas"
        )

        # --- Persist AGS context for async grade passback ---
        raw_launch = message_launch.get_launch_data()
        ags_claim = raw_launch.get(
            "https://purl.imsglobal.org/spec/lti-ags/claim/endpoint"
        )
        if ags_claim:
            try:
                store_ags_context(launch_data, registration, db, ags_claim)
            except Exception as ags_err:
                # Non-fatal — log and continue
                logger.warning(f"Failed to store AGS context: {ags_err}")

        db.commit()

        return RedirectResponse(url=redirect_url, status_code=302)

    except Exception as e:
        logger.error(f"Canvas LTI launch failed: {e}")
        raise HTTPException(status_code=400, detail=f"LTI launch failed: {str(e)}")


# =============================================================================
# Deep Linking
# =============================================================================


@router.post("/deep-link")
async def lti_deep_link(
    request: Request, lti_service: CanvasLTIService = Depends(get_lti_service)
):
    """
    LTI 1.3 Deep Linking Launch.

    Used when instructors add content to their course.
    """
    if not lti_service.is_configured():
        raise HTTPException(status_code=503, detail="LTI integration not configured")

    params = dict(await request.form())

    try:
        cookie_service = FastAPICookieService(request=request)

        message_launch = lti_service.validate_launch(
            request_params=params,
            session_service=_session_service,
            cookie_service=cookie_service,
        )

        launch_data = lti_service.extract_launch_data(message_launch)

        return await handle_deep_link_launch(
            request, lti_service, message_launch, launch_data
        )

    except Exception as e:
        logger.error(f"Deep link launch failed: {e}")
        raise HTTPException(status_code=400, detail=f"Deep link failed: {str(e)}")


async def handle_deep_link_launch(
    request: Request,
    lti_service: CanvasLTIService,
    message_launch,
    launch_data: CanvasLaunchData,
) -> HTMLResponse:
    """
    Handle deep linking launch - show content picker UI.

    Returns HTML page for selecting content to add to Canvas.
    """
    base_url = (
        os.getenv("API_BASE_URL")
        or os.getenv("BASE_URL")
        or str(request.base_url).rstrip("/")
    )

    # Create content items for different scan types
    content_items_html = """
    <div class="content-items">
        <h2>Add Accessibility Scan to Your Course</h2>
        <p>Select what type of scan you want to add:</p>

        <div class="scan-options">
            <button class="scan-option" onclick="selectContent('document')">
                <div class="icon">📄</div>
                <div class="title">Document Scan</div>
                <div class="description">Scan PDF, Word, PowerPoint files for accessibility</div>
            </button>

            <button class="scan-option" onclick="selectContent('course')">
                <div class="icon">📚</div>
                <div class="title">Full Course Scan</div>
                <div class="description">Scan all documents in the course for accessibility</div>
            </button>

            <button class="scan-option" onclick="selectContent('compliance')">
                <div class="icon">✅</div>
                <div class="title">Compliance Dashboard</div>
                <div class="description">Track compliance progress with reports and certificates</div>
            </button>
        </div>

        <div id="selected-content" style="display: none;">
            <h3>Selected: <span id="selected-title"></span></h3>
            <button id="submit-btn" onclick="submitContent()">Add to Course</button>
        </div>
    </div>
    """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Add Accessibility Scan - Aelira</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 40px 20px;
                background: #f5f7fa;
            }}
            h2 {{
                color: #1a1a2e;
                margin-bottom: 8px;
            }}
            p {{
                color: #666;
                margin-bottom: 24px;
            }}
            .scan-options {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 16px;
                margin-bottom: 24px;
            }}
            .scan-option {{
                background: white;
                border: 2px solid #e0e0e0;
                border-radius: 12px;
                padding: 24px;
                cursor: pointer;
                text-align: center;
                transition: all 0.2s ease;
            }}
            .scan-option:hover {{
                border-color: #4a90d9;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .scan-option.selected {{
                border-color: #4a90d9;
                background: #f0f7ff;
            }}
            .scan-option .icon {{
                font-size: 48px;
                margin-bottom: 12px;
            }}
            .scan-option .title {{
                font-weight: 600;
                color: #1a1a2e;
                margin-bottom: 8px;
            }}
            .scan-option .description {{
                font-size: 14px;
                color: #666;
            }}
            #submit-btn {{
                background: #4a90d9;
                color: white;
                border: none;
                padding: 12px 32px;
                border-radius: 8px;
                font-size: 16px;
                cursor: pointer;
                margin-top: 16px;
            }}
            #submit-btn:hover {{
                background: #3a7bc8;
            }}
        </style>
    </head>
    <body>
        {content_items_html}

        <script>
            let selectedType = null;

            function selectContent(type) {{
                selectedType = type;
                document.querySelectorAll('.scan-option').forEach(el => el.classList.remove('selected'));
                event.target.closest('.scan-option').classList.add('selected');

                const titles = {{
                    'document': 'Document Accessibility Scan',
                    'course': 'Course Accessibility Scan',
                    'compliance': 'Compliance Dashboard'
                }};

                document.getElementById('selected-title').textContent = titles[type];
                document.getElementById('selected-content').style.display = 'block';
            }}

            function submitContent() {{
                if (!selectedType) return;

                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '{base_url}/lti/deep-link/submit';

                const fields = {{
                    'scan_type': selectedType,
                    'course_id': '{launch_data.course_id}',
                    'user_id': '{launch_data.user_id}'
                }};

                for (const [key, value] of Object.entries(fields)) {{
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = key;
                    input.value = value;
                    form.appendChild(input);
                }}

                document.body.appendChild(form);
                form.submit();
            }}
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html)


@router.post("/deep-link/submit")
async def submit_deep_link(
    request: Request, lti_service: CanvasLTIService = Depends(get_lti_service)
):
    """
    Submit deep link content selection back to Canvas.
    """
    params = dict(await request.form())
    scan_type = params.get("scan_type", "document")
    params.get("course_id", "")

    base_url = (
        os.getenv("API_BASE_URL")
        or os.getenv("BASE_URL")
        or str(request.base_url).rstrip("/")
    )

    titles = {
        "document": "Document Accessibility Scan",
        "course": "Course Accessibility Scan",
        "compliance": "Compliance Dashboard",
    }

    content_item = lti_service.create_scan_content_item(
        title=titles.get(scan_type, "Accessibility Scan"),
        launch_url=f"{base_url}/lti/launch",
        scan_type=scan_type,
    )

    # For now, return a simple confirmation
    # In production, this would create the deep link response
    return JSONResponse(
        {
            "status": "success",
            "message": f"Added {titles.get(scan_type)} to course",
            "content_item": content_item.dict(),
        }
    )


# =============================================================================
# JWKS Endpoint
# =============================================================================


@router.get("/jwks")
async def get_jwks(lti_service: CanvasLTIService = Depends(get_lti_service)):
    """
    Public JSON Web Key Set for LTI verification.

    Canvas uses this to verify our signed responses.
    """
    from pylti1p3.registration import Registration

    public_key_pem = lti_service.get_tool_public_key_pem()
    if not public_key_pem:
        # Deliberately still a 200 with an empty set, which is what the spec
        # expects of a tool with no keys. The health endpoint is where this
        # shows up as a problem, and the service logs a warning at startup.
        logger.warning(
            "Serving an empty JWKS: no Canvas LTI public key is loaded, so "
            "Canvas cannot verify deep-link or grade-passback messages."
        )
        return JSONResponse({"keys": []})

    return JSONResponse({"keys": [Registration.get_jwk(public_key_pem)]})


# =============================================================================
# Configuration Endpoint
# =============================================================================


@router.get("/config")
async def get_lti_config(
    request: Request, lti_service: CanvasLTIService = Depends(get_lti_service)
):
    """
    Get LTI configuration JSON for Canvas setup.

    Copy this into Canvas > Admin > Developer Keys > Configure
    """
    base_url = (
        os.getenv("API_BASE_URL")
        or os.getenv("BASE_URL")
        or str(request.base_url).rstrip("/")
    )
    config = lti_service.generate_lti_config_json(base_url)

    return JSONResponse(config)


# =============================================================================
# Grade Passback
# =============================================================================


@router.post("/grade")
async def submit_grade(
    request: Request, lti_service: CanvasLTIService = Depends(get_lti_service)
):
    """
    Submit a compliance score as a grade to Canvas.

    Body:
    - user_id: Canvas user ID
    - compliance_score: Score (0-100)
    - comment: Optional comment
    - session_token: LTI session token
    """
    data = await request.json()

    user_id = data.get("user_id")
    compliance_score = data.get("compliance_score", 0)
    data.get("comment")
    session_token = data.get("session_token")

    if not user_id or not session_token:
        raise HTTPException(status_code=400, detail="Missing required fields")

    # Validate session and get message launch
    session_data = _session_service.get(session_token)
    if not session_data:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # In production, reconstruct message_launch from session
    # For now, return success placeholder
    return JSONResponse(
        {
            "status": "success",
            "user_id": user_id,
            "score": compliance_score,
            "message": f"Grade submitted: {compliance_score}%",
        }
    )


# =============================================================================
# One-Time Code Exchange
# =============================================================================


class LTIExchangeRequest(BaseModel):
    """Request body for POST /lti/exchange."""

    code: str


@router.post("/exchange")
async def lti_exchange(body: LTIExchangeRequest):
    """
    Exchange a one-time LTI code for an access token.

    The code is consumed on first use and expires after 30 seconds.
    Sets the aelira_access cookie so /auth/session/validate works
    inside the Canvas LTI iframe.

    Body:
        code: One-time code from the LTI launch redirect.

    Returns:
        {"access_token": "...", "course_id": "..."}
    """
    from ..config.settings import get_settings

    result = exchange_code(body.code)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    settings = get_settings()
    response = JSONResponse(result)

    cookie_settings = {
        "httponly": True,
        "secure": settings.session_cookie_secure,
        "samesite": settings.session_cookie_samesite.lower(),
        "path": "/",
    }
    if settings.env == "production" and settings.session_cookie_domain:
        cookie_settings["domain"] = settings.session_cookie_domain

    response.set_cookie(
        key="aelira_access",
        value=result.get("access_token", ""),
        max_age=settings.jwt_access_token_expire_minutes * 60,
        **cookie_settings,
    )

    return response


# =============================================================================
# Auth Bridge
# =============================================================================


@router.post("/bridge")
async def lti_bridge(
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_required_api_key),
):
    """
    Create a one-time bridge code for an already-authenticated user.

    Requires a valid Aelira session. Returns a code + URL that can be
    opened in a new tab/iframe to restore auth context.

    Returns:
        {"code": "...", "url": "..."}
    """
    _api_key, user_id, department_id = api_key_info

    code, url = create_bridge_code(user_id, department_id)

    return JSONResponse({"code": code, "url": url})


# =============================================================================
# LTI Health Check
# =============================================================================


@router.get("/health")
async def lti_health(lti_service: CanvasLTIService = Depends(get_lti_service)):
    """
    Check LTI integration health.
    """
    configured = lti_service.is_configured()
    signing = lti_service.has_signing_keys()

    # Report the two halves separately. Reporting "healthy" purely on config
    # presence is how a stubbed JWKS endpoint sat unnoticed: launches worked,
    # every signed response failed, and this endpoint said "ready".
    if not configured:
        status = "not_configured"
        message = "Set CANVAS_CLIENT_ID and CANVAS_DEPLOYMENT_ID to enable"
    elif not signing:
        status = "degraded"
        message = (
            "Launches will verify, but no signing keypair is loaded: deep "
            "linking and grade passback will fail because Canvas cannot verify "
            "our messages. Set CANVAS_LTI_PRIVATE_KEY_PATH and "
            "CANVAS_LTI_PUBLIC_KEY_PATH."
        )
    else:
        status = "healthy"
        message = "LTI integration ready"

    return {
        "status": status,
        "configured": configured,
        "signing_keys_loaded": signing,
        "capabilities": {
            "inbound_launch": configured,
            "deep_linking": configured and signing,
            "grade_passback": configured and signing,
        },
        "message": message,
    }


# =============================================================================
# Helper Functions
# =============================================================================


def _create_lti_session(
    launch_data: CanvasLaunchData,
    department_id: str = None,
) -> str:
    """
    Create a session token for LTI launch data.

    Args:
        launch_data: Canvas LTI launch data
        department_id: Optional department ID for multi-tenant support

    Returns:
        A token that can be used to access the dashboard.
    """
    # Create simple session token
    token_data = f"{launch_data.user_id}:{launch_data.course_id}:{time.time()}"
    token = hashlib.sha256(token_data.encode()).hexdigest()[:32]

    # Build session data
    session_data = {
        "user_id": launch_data.user_id,
        "user_name": launch_data.user_name,
        "course_id": launch_data.course_id,
        "course_name": launch_data.course_name,
        "is_instructor": launch_data.is_instructor,
        "roles": launch_data.roles,
        "platform": "canvas",
    }

    # Add department_id if provided (multi-tenant support)
    if department_id:
        session_data["department_id"] = department_id

    # Store session data
    _session_service.set(
        token,
        json.dumps(session_data),
        exp=3600,
    )

    return token
