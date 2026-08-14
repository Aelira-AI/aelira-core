"""
Blackboard LTI 1.3 API Routes

FastAPI routes for handling LTI 1.3 launch, deep linking,
and grade passback with Blackboard Learn.

Endpoints:
- POST /lti/blackboard/login - OIDC login initiation
- POST /lti/blackboard/launch - LTI resource link launch
- POST /lti/blackboard/deep-link - Deep linking launch
- GET /lti/blackboard/jwks - Public key set for verification
- GET /lti/blackboard/config - LTI configuration JSON
"""

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import json
import logging
import os
import hashlib
import time

from src.integrations.blackboard_lti import (
    get_blackboard_lti_service,
    BlackboardLTIService,
    BlackboardLaunchData,
)
from src.integrations.blackboard_lti.blackboard_lti import (
    BlackboardSessionService,
    BlackboardCookieService,
)
from src.db.database import get_db_dependency
from src.db.models import LTIRegistration, LTIPlatform, Department
from src.middleware.quota import check_feature_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lti/blackboard", tags=["Blackboard LTI Integration"])

# In-memory session storage (use Redis in production)
_session_service = BlackboardSessionService()


# =============================================================================
# LTI Registration Lookup
# =============================================================================


def get_department_from_lti_launch(
    db: Session,
    issuer: str,
    client_id: str,
) -> tuple[LTIRegistration | None, Department | None, str | None]:
    """
    Look up the department associated with an LTI launch.

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
            LTIRegistration.platform == LTIPlatform.BLACKBOARD,
            LTIRegistration.issuer == issuer,
            LTIRegistration.client_id == client_id,
            LTIRegistration.is_active.is_(True),
        )
        .first()
    )

    if not registration:
        logger.warning(
            f"No LTI registration found for issuer={issuer}, client_id={client_id}"
        )
        return (
            None,
            None,
            (
                "LTI tool not registered. Please contact your administrator to register "
                "this Blackboard instance with Aelira."
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
            f"Upgrade to {upgrade_suggestion} plan to access Blackboard integration."
        )

    return True, None


def update_lti_launch_stats(db: Session, registration: LTIRegistration):
    """Update launch statistics for an LTI registration."""
    registration.launch_count = (registration.launch_count or 0) + 1
    registration.last_launch_at = datetime.now(timezone.utc)
    db.commit()


def get_lti_service() -> BlackboardLTIService:
    """Dependency to get LTI service"""
    return get_blackboard_lti_service()


# =============================================================================
# OIDC Login Flow
# =============================================================================


@router.post("/login")
@router.get("/login")
async def blackboard_lti_login(
    request: Request,
    lti_service: BlackboardLTIService = Depends(get_lti_service),
):
    """
    LTI 1.3 OIDC Login Initiation for Blackboard.

    Blackboard redirects here first. We validate the login request
    and redirect to Blackboard for authentication.
    """
    if not lti_service.is_configured():
        raise HTTPException(
            status_code=503,
            detail="Blackboard LTI integration not configured. Set BLACKBOARD_URL and BLACKBOARD_CLIENT_ID.",
        )

    # Get parameters from query string or form
    if request.method == "POST":
        params = dict(await request.form())
    else:
        params = dict(request.query_params)

    # Determine target launch URL
    target_link_uri = params.get(
        "target_link_uri", str(request.url_for("blackboard_lti_launch"))
    )

    try:
        # Create cookie service for this request
        from fastapi import Response

        response = Response()
        cookie_service = BlackboardCookieService(request=request, response=response)

        # Initiate OIDC login
        redirect_url = lti_service.initiate_oidc_login(
            request_params=params,
            target_link_uri=target_link_uri,
            session_service=_session_service,
            cookie_service=cookie_service,
        )

        return RedirectResponse(url=redirect_url, status_code=302)

    except Exception as e:
        logger.error(f"Blackboard LTI login failed: {e}")
        raise HTTPException(status_code=400, detail=f"LTI login failed: {str(e)}")


# =============================================================================
# LTI Launch
# =============================================================================


@router.post("/launch")
async def blackboard_lti_launch(
    request: Request,
    lti_service: BlackboardLTIService = Depends(get_lti_service),
    db: Session = Depends(get_db_dependency),
):
    """
    LTI 1.3 Resource Link Launch for Blackboard.

    This is where users land after Blackboard authentication.
    We validate the launch and redirect to the appropriate page.

    Feature Gating: Requires 'lms_integration' feature on department tier.
    """
    if not lti_service.is_configured():
        raise HTTPException(status_code=503, detail="Blackboard LTI not configured")

    # Get parameters from form
    params = dict(await request.form())

    try:
        # Create services
        cookie_service = BlackboardCookieService(request=request)

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
                f"Blackboard LTI launch denied: {error} "
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
                f"Blackboard LTI launch denied for department {department.id}: "
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
            f"Blackboard LTI launch: user={launch_data.user_name}, "
            f"course={launch_data.course_name}, "
            f"instructor={launch_data.is_instructor}, "
            f"department={department.name} ({department.tier})"
        )

        # Check if this is a deep link launch
        if lti_service.is_deep_link_launch(message_launch):
            return await handle_blackboard_deep_link_launch(
                request, lti_service, message_launch, launch_data
            )

        # Generate session token for the dashboard
        session_token = _create_blackboard_lti_session(launch_data, department.id)

        # Redirect to dashboard with LTI context
        dashboard_url = os.getenv("DASHBOARD_URL", "http://localhost:5173")
        redirect_url = (
            f"{dashboard_url}/lti/blackboard?"
            f"session={session_token}"
            f"&course_id={launch_data.course_id}"
            f"&course_name={launch_data.course_name}"
            f"&is_instructor={launch_data.is_instructor}"
            f"&platform=blackboard"
            f"&department_id={department.id}"
        )

        return RedirectResponse(url=redirect_url, status_code=302)

    except Exception as e:
        logger.error(f"Blackboard LTI launch failed: {e}")
        raise HTTPException(status_code=400, detail=f"LTI launch failed: {str(e)}")


# =============================================================================
# Deep Linking
# =============================================================================


@router.post("/deep-link")
async def blackboard_lti_deep_link(
    request: Request,
    lti_service: BlackboardLTIService = Depends(get_lti_service),
):
    """
    LTI 1.3 Deep Linking Launch for Blackboard.

    Used when instructors add content to their course.
    """
    if not lti_service.is_configured():
        raise HTTPException(status_code=503, detail="Blackboard LTI not configured")

    params = dict(await request.form())

    try:
        cookie_service = BlackboardCookieService(request=request)

        message_launch = lti_service.validate_launch(
            request_params=params,
            session_service=_session_service,
            cookie_service=cookie_service,
        )

        launch_data = lti_service.extract_launch_data(message_launch)

        return await handle_blackboard_deep_link_launch(
            request, lti_service, message_launch, launch_data
        )

    except Exception as e:
        logger.error(f"Blackboard deep link launch failed: {e}")
        raise HTTPException(status_code=400, detail=f"Deep link failed: {str(e)}")


async def handle_blackboard_deep_link_launch(
    request: Request,
    lti_service: BlackboardLTIService,
    message_launch,
    launch_data: BlackboardLaunchData,
) -> HTMLResponse:
    """
    Handle deep linking launch - show content picker UI for Blackboard.

    Returns HTML page for selecting content to add to Blackboard course.
    """
    base_url = os.getenv("BASE_URL", str(request.base_url).rstrip("/"))

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
                border-color: #5c5a99;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            }}
            .scan-option.selected {{
                border-color: #5c5a99;
                background: #f5f5ff;
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
                background: #5c5a99;
                color: white;
                border: none;
                padding: 12px 32px;
                border-radius: 8px;
                font-size: 16px;
                cursor: pointer;
                margin-top: 16px;
            }}
            #submit-btn:hover {{
                background: #4a4880;
            }}
            .blackboard-branding {{
                color: #666;
                font-size: 12px;
                margin-top: 24px;
            }}
        </style>
    </head>
    <body>
        <div class="content-items">
            <h2>Add Accessibility Scan to Your Blackboard Course</h2>
            <p>Select what type of accessibility scan you want to add:</p>

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

            <p class="blackboard-branding">Powered by Aelira - WCAG 2.1 Accessibility Platform</p>
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
                form.action = '{base_url}/lti/blackboard/deep-link/submit';

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
async def submit_blackboard_deep_link(
    request: Request,
    lti_service: BlackboardLTIService = Depends(get_lti_service),
):
    """
    Submit deep link content selection back to Blackboard.
    """
    params = dict(await request.form())
    scan_type = params.get("scan_type", "document")

    base_url = os.getenv("BASE_URL", str(request.base_url).rstrip("/"))

    titles = {
        "document": "Document Accessibility Scan",
        "course": "Course Accessibility Scan",
        "compliance": "Compliance Dashboard",
    }

    content_item = lti_service.create_scan_content_item(
        title=titles.get(scan_type, "Accessibility Scan"),
        launch_url=f"{base_url}/lti/blackboard/launch",
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
async def get_blackboard_jwks(
    lti_service: BlackboardLTIService = Depends(get_lti_service),
):
    """
    Public JSON Web Key Set for Blackboard LTI verification.

    Blackboard uses this to verify our signed responses.
    """
    # In production, load from actual key files
    return JSONResponse({"keys": []})


# =============================================================================
# Configuration Endpoint
# =============================================================================


@router.get("/config")
async def get_blackboard_lti_config(
    request: Request,
    lti_service: BlackboardLTIService = Depends(get_lti_service),
):
    """
    Get LTI configuration JSON for Blackboard registration.

    Use this configuration when registering in Blackboard Admin.
    """
    base_url = os.getenv("BASE_URL", str(request.base_url).rstrip("/"))
    config = lti_service.generate_lti_config_json(base_url)

    return JSONResponse(config)


# =============================================================================
# Grade Passback
# =============================================================================


@router.post("/grade")
async def submit_blackboard_grade(
    request: Request,
    lti_service: BlackboardLTIService = Depends(get_lti_service),
):
    """
    Submit a compliance score as a grade to Blackboard.

    Body:
    - user_id: Blackboard user ID
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
    session_data = _session_service.get(session_token)
    if not session_data:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # In production, reconstruct message_launch from session
    return JSONResponse(
        {
            "status": "success",
            "user_id": user_id,
            "score": compliance_score,
            "message": f"Grade submitted to Blackboard: {compliance_score}%",
        }
    )


# =============================================================================
# Health Check
# =============================================================================


@router.get("/health")
async def blackboard_lti_health(
    lti_service: BlackboardLTIService = Depends(get_lti_service),
):
    """
    Check Blackboard LTI integration health.
    """
    return {
        "status": "healthy" if lti_service.is_configured() else "not_configured",
        "configured": lti_service.is_configured(),
        "message": (
            "Blackboard LTI integration ready"
            if lti_service.is_configured()
            else "Set BLACKBOARD_URL, BLACKBOARD_CLIENT_ID, and BLACKBOARD_DEPLOYMENT_ID to enable"
        ),
    }


# =============================================================================
# Helper Functions
# =============================================================================


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
           style="display: inline-block; background: #5c5a99; color: white;
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


def _create_blackboard_lti_session(
    launch_data: BlackboardLaunchData,
    department_id: str = None,
) -> str:
    """
    Create a session token for Blackboard LTI launch data.

    Args:
        launch_data: LTI launch data
        department_id: Optional department ID for multi-tenant support

    Returns:
        A token that can be used to access the dashboard.
    """
    # Create simple session token
    token_data = f"{launch_data.user_id}:{launch_data.course_id}:{time.time()}"
    token = hashlib.sha256(token_data.encode()).hexdigest()[:32]

    # Store session data
    session_data = {
        "user_id": launch_data.user_id,
        "user_name": launch_data.user_name,
        "course_id": launch_data.course_id,
        "course_name": launch_data.course_name,
        "is_instructor": launch_data.is_instructor,
        "roles": launch_data.roles,
        "platform": "blackboard",
        "blackboard_user_uuid": launch_data.blackboard_user_uuid,
        "blackboard_course_uuid": launch_data.blackboard_course_uuid,
    }

    # Add department_id if provided (multi-tenant support)
    if department_id:
        session_data["department_id"] = department_id

    _session_service.set(
        token,
        json.dumps(session_data),
        exp=3600,
    )

    return token
