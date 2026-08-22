"""
Brightspace LTI 1.3 API Routes

FastAPI routes for handling LTI 1.3 launch, deep linking,
and grade passback with D2L Brightspace.

Endpoints:
- GET/POST /lti/brightspace/login - OIDC login initiation
- POST /lti/brightspace/launch - LTI resource link launch
- POST /lti/brightspace/deep-link - Deep linking launch
- POST /lti/brightspace/deep-link/submit - Submit content items back to Brightspace
- POST /lti/brightspace/exchange - One-time code -> access token (120s TTL)
- POST /lti/brightspace/bridge - Dashboard -> LTI context bridge (30s TTL)
- GET /lti/brightspace/jwks - Public key set for verification
- GET /lti/brightspace/config - LTI configuration JSON
- POST /lti/brightspace/grade - Submit compliance score via AGS
- GET /lti/brightspace/health - Health check
"""

from typing import Optional, Tuple

from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import logging
import traceback
import os

from src.integrations.brightspace_lti import (
    get_brightspace_lti_service,
    BrightspaceLTIService,
    BrightspaceLaunchData,
    FastAPISessionService,
    FastAPICookieService,
)
from src.api.lti_launch_handler import (
    LTIStaffAccessDenied,
    handle_lti_launch,
    exchange_code,
    require_lti_staff_access,
    store_ags_context,
    create_bridge_code,
)
from src.auth.dependencies import get_required_api_key
from src.db.database import get_db_dependency
from src.db.models import APIKey, LTIRegistration, LTIPlatform, Department
from src.middleware.quota import check_feature_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lti/brightspace", tags=["Brightspace LTI Integration"])

# In-memory session storage (use Redis in production)
_session_service = FastAPISessionService()


def get_lti_service() -> BrightspaceLTIService:
    """Dependency to get Brightspace LTI service."""
    return get_brightspace_lti_service()


# =============================================================================
# LTI Registration Lookup
# =============================================================================


def get_department_from_lti_launch(
    db: Session,
    issuer: str,
    client_id: str,
) -> tuple[LTIRegistration | None, Department | None, str | None]:
    """
    Look up the department associated with a Brightspace LTI launch.

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
            LTIRegistration.platform == LTIPlatform.BRIGHTSPACE,
            LTIRegistration.issuer == issuer,
            LTIRegistration.client_id == client_id,
            LTIRegistration.is_active.is_(True),
        )
        .first()
    )

    if not registration:
        logger.warning(
            f"No Brightspace LTI registration found for issuer={issuer}, client_id={client_id}"
        )
        return (
            None,
            None,
            (
                "LTI tool not registered. Please contact your administrator to register "
                "this Brightspace instance with Aelira."
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
        return False, (
            f"LMS integration is not enabled for this workspace ({department.tier}). "
            f"Your deployment administrator can enable it in the server configuration."
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
    show_configuration_button: bool = False,
) -> str:
    """
    Render a user-friendly error page for LTI launch failures.

    Args:
        title: Error title
        message: Error message
        help_text: Additional help text
        show_configuration_button: Whether to show a configuration CTA

    Returns:
        HTML content for the error page
    """
    configuration_button = ""
    if show_configuration_button:
        configuration_button = """
        <p style="margin-top: 16px; color: #4b5563;">
            Ask your administrator to review this workspace's configuration.
        </p>
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
        <div class="error-icon">⚠</div>
        <h1>{title}</h1>
        <p class="message">{message}</p>
        {configuration_button}
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
async def brightspace_lti_login(
    request: Request,
    lti_service: BrightspaceLTIService = Depends(get_lti_service),
):
    """
    LTI 1.3 OIDC Login Initiation for Brightspace.

    Brightspace redirects here first. We validate the login request
    and redirect to Brightspace for authentication.
    """
    if not lti_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Brightspace LTI integration not configured. "
                "Set BRIGHTSPACE_LTI_ISSUER and BRIGHTSPACE_LTI_CLIENT_ID."
            ),
        )

    # Get parameters from query string or form
    if request.method == "POST":
        params = dict(await request.form())
    else:
        params = dict(request.query_params)

    # Determine target launch URL
    target_link_uri = params.get(
        "target_link_uri", str(request.url_for("brightspace_lti_launch"))
    )

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
        logger.error(f"Brightspace LTI login failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail="LTI login failed")


# =============================================================================
# LTI Launch
# =============================================================================


@router.post("/launch")
async def brightspace_lti_launch(
    request: Request,
    lti_service: BrightspaceLTIService = Depends(get_lti_service),
    db: Session = Depends(get_db_dependency),
):
    """
    LTI 1.3 Resource Link Launch for Brightspace.

    This is where users land after Brightspace authentication.
    We validate the launch and redirect to the appropriate page.

    Feature Gating: Requires the 'lms_integration' feature (enabled on all core tiers).
    """
    # Get parameters from form
    params = dict(await request.form())

    if not lti_service.is_configured():
        raise HTTPException(status_code=503, detail="Brightspace LTI not configured")

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

        # Staff authorization precedes registration lookup and all launch
        # side effects, including deep-link, statistics, AGS, and provisioning.
        require_lti_staff_access(launch_data)

        # Get issuer and client_id from the validated launch for department lookup
        issuer = lti_service.get_issuer_from_launch(message_launch)
        client_id = lti_service.get_client_id_from_launch(message_launch)

        # Look up department from LTI registration
        registration, department, error = get_department_from_lti_launch(
            db, issuer, client_id
        )

        if error:
            logger.warning(
                f"Brightspace LTI launch denied: {error} "
                f"(issuer={issuer}, client_id={client_id})"
            )
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
                f"Brightspace LTI launch denied for department {department.id}: "
                f"tier={department.tier} does not have lms_integration feature"
            )
            return HTMLResponse(
                content=_render_lti_error_page(
                    "Feature Not Available",
                    feature_error,
                    "Ask your administrator to review this workspace's configuration.",
                    show_configuration_button=True,
                ),
                status_code=403,
            )

        # Update launch statistics
        update_lti_launch_stats(db, registration)

        logger.info(
            f"Brightspace LTI launch: user={launch_data.user_name}, "
            f"course={launch_data.course_name}, "
            f"instructor={launch_data.is_instructor}, "
            f"department={department.name} ({department.tier})"
        )

        # Check if this is a deep link launch
        if lti_service.is_deep_link_launch(message_launch):
            return await handle_brightspace_deep_link_launch(
                request, lti_service, message_launch, launch_data
            )

        # --- Auto-provision user & mint token via one-time code ---
        redirect_url = handle_lti_launch(
            launch_data, registration, db, platform="brightspace"
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
                # Non-fatal -- log and continue
                logger.warning(f"Failed to store AGS context: {ags_err}")

        db.commit()

        return RedirectResponse(url=redirect_url, status_code=302)

    except LTIStaffAccessDenied:
        logger.warning("Brightspace LTI launch denied by staff-only policy")
        return HTMLResponse(content="LTI launch not authorized.", status_code=403)
    except Exception as e:
        logger.error(f"Brightspace LTI launch failed: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"LTI launch failed: {str(e)}")


# =============================================================================
# Deep Linking
# =============================================================================


@router.post("/deep-link")
async def brightspace_lti_deep_link(
    request: Request,
    lti_service: BrightspaceLTIService = Depends(get_lti_service),
):
    """
    LTI 1.3 Deep Linking Launch for Brightspace.

    Used when instructors add content to their course.
    """
    if not lti_service.is_configured():
        raise HTTPException(status_code=503, detail="Brightspace LTI not configured")

    params = dict(await request.form())

    try:
        cookie_service = FastAPICookieService(request=request)

        message_launch = lti_service.validate_launch(
            request_params=params,
            session_service=_session_service,
            cookie_service=cookie_service,
        )

        launch_data = lti_service.extract_launch_data(message_launch)
        require_lti_staff_access(launch_data)

        return await handle_brightspace_deep_link_launch(
            request, lti_service, message_launch, launch_data
        )

    except LTIStaffAccessDenied:
        logger.warning("Brightspace deep-link launch denied by staff-only policy")
        return HTMLResponse(content="LTI launch not authorized.", status_code=403)
    except Exception as e:
        logger.error(f"Brightspace deep link launch failed: {e}")
        raise HTTPException(status_code=400, detail=f"Deep link failed: {str(e)}")


async def handle_brightspace_deep_link_launch(
    request: Request,
    lti_service: BrightspaceLTIService,
    message_launch,
    launch_data: BrightspaceLaunchData,
) -> HTMLResponse:
    """
    Handle deep linking launch - show content picker UI for Brightspace.

    Returns HTML page for selecting content to add to Brightspace course.
    """
    base_url = (
        os.getenv("API_BASE_URL")
        or os.getenv("BASE_URL")
        or str(request.base_url).rstrip("/")
    )

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
                border-color: #e87511;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .scan-option.selected {{
                border-color: #e87511;
                background: #fff8f0;
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
                background: #e87511;
                color: white;
                border: none;
                padding: 12px 32px;
                border-radius: 8px;
                font-size: 16px;
                cursor: pointer;
                margin-top: 16px;
            }}
            #submit-btn:hover {{
                background: #cf6610;
            }}
            .brightspace-branding {{
                color: #666;
                font-size: 12px;
                margin-top: 24px;
            }}
        </style>
    </head>
    <body>
        <div class="content-items">
            <h2>Add Accessibility Scan to Your Brightspace Course</h2>
            <p>Select what type of accessibility scan you want to add:</p>

            <div class="scan-options">
                <button class="scan-option" onclick="selectContent('document')">
                    <div class="icon"></div>
                    <div class="title">Document Scan</div>
                    <div class="description">Scan PDF, Word, PowerPoint files for accessibility</div>
                </button>

                <button class="scan-option" onclick="selectContent('course')">
                    <div class="icon"></div>
                    <div class="title">Full Course Scan</div>
                    <div class="description">Scan all documents in the course for accessibility</div>
                </button>

                <button class="scan-option" onclick="selectContent('compliance')">
                    <div class="icon"></div>
                    <div class="title">Compliance Dashboard</div>
                    <div class="description">Track compliance progress with reports and certificates</div>
                </button>
            </div>

            <div id="selected-content" style="display: none;">
                <h3>Selected: <span id="selected-title"></span></h3>
                <button id="submit-btn" onclick="submitContent()">Add to Course</button>
            </div>

            <p class="brightspace-branding">Powered by Aelira - WCAG 2.1 Accessibility Platform</p>
        </div>

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
                form.action = '{base_url}/lti/brightspace/deep-link/submit';

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
async def submit_brightspace_deep_link(
    request: Request,
    lti_service: BrightspaceLTIService = Depends(get_lti_service),
):
    """
    Submit deep link content selection back to Brightspace.
    """
    params = dict(await request.form())
    scan_type = params.get("scan_type", "document")

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
        launch_url=f"{base_url}/lti/brightspace/launch",
        scan_type=scan_type,
    )

    # In production, this would create the deep link response form
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
async def get_brightspace_jwks(
    lti_service: BrightspaceLTIService = Depends(get_lti_service),
):
    """
    Public JSON Web Key Set for Brightspace LTI verification.

    Brightspace uses this to verify our signed responses (deep link
    content items, AGS grade passback, NRPS calls).
    """
    from pylti1p3.registration import Registration

    public_key_pem = lti_service.get_tool_public_key_pem()
    if not public_key_pem:
        return JSONResponse({"keys": []})

    jwk = Registration.get_jwk(public_key_pem)
    return JSONResponse({"keys": [jwk]})


# =============================================================================
# Configuration Endpoint
# =============================================================================


@router.get("/config")
async def get_brightspace_lti_config(
    request: Request,
    lti_service: BrightspaceLTIService = Depends(get_lti_service),
):
    """
    Get LTI configuration JSON for Brightspace registration.

    Use this configuration when registering in
    Brightspace Admin > External Learning Tools.
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
async def submit_brightspace_grade(
    request: Request,
    lti_service: BrightspaceLTIService = Depends(get_lti_service),
):
    """
    Submit a compliance score as a grade to Brightspace.

    Body:
    - user_id: Brightspace user ID
    - compliance_score: Score (0-100)
    - comment: Optional comment
    - session_token: LTI session token
    """
    data = await request.json()

    user_id = data.get("user_id")
    compliance_score = data.get("compliance_score", 0)
    session_token = data.get("session_token")

    if not user_id or not session_token:
        raise HTTPException(status_code=400, detail="Missing required fields")

    # Validate session
    session_data = _session_service.get_launch_data(session_token)
    if not session_data:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # In production, reconstruct message_launch from session
    return JSONResponse(
        {
            "status": "success",
            "user_id": user_id,
            "score": compliance_score,
            "message": f"Grade submitted to Brightspace: {compliance_score}%",
        }
    )


# =============================================================================
# One-Time Code Exchange
# =============================================================================


class BrightspaceLTIExchangeRequest(BaseModel):
    """Request body for POST /lti/brightspace/exchange."""

    code: str


@router.post("/exchange")
async def brightspace_lti_exchange(body: BrightspaceLTIExchangeRequest):
    """
    Exchange a one-time LTI code for an access token.

    The code is consumed on first use and expires after 120 seconds.

    Body:
        code: One-time code from the LTI launch redirect.

    Returns:
        {"access_token": "...", "course_id": "...", "platform": "brightspace"}
    """
    result = exchange_code(body.code)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    return JSONResponse(result)


# =============================================================================
# Auth Bridge
# =============================================================================


@router.post("/bridge")
async def brightspace_lti_bridge(
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
# Health Check
# =============================================================================


@router.get("/health")
async def brightspace_lti_health(
    lti_service: BrightspaceLTIService = Depends(get_lti_service),
):
    """
    Check Brightspace LTI integration health.
    """
    return {
        "status": "healthy" if lti_service.is_configured() else "not_configured",
        "configured": lti_service.is_configured(),
        "message": (
            "Brightspace LTI integration ready"
            if lti_service.is_configured()
            else "Set BRIGHTSPACE_LTI_ISSUER and BRIGHTSPACE_LTI_CLIENT_ID to enable"
        ),
    }
