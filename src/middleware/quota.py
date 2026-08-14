"""
Quota Enforcement Middleware

Provides quota checking and enforcement for the individual_free tier.
This ensures that free tier users can only process a limited number of
documents and pages per month.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import update
import logging

from ..db.models import Department
from ..config.settings import TIER_QUOTAS, get_tier_quota

logger = logging.getLogger(__name__)


class QuotaResult:
    """Result of a quota check."""

    def __init__(
        self,
        allowed: bool,
        message: str,
        remaining_scans: int = -1,
        remaining_pages: int = -1,
        resets_at: Optional[datetime] = None,
        tier: str = "unknown",
    ):
        self.allowed = allowed
        self.message = message
        self.remaining_scans = remaining_scans
        self.remaining_pages = remaining_pages
        self.resets_at = resets_at
        self.tier = tier

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "allowed": self.allowed,
            "message": self.message,
            "remaining": {
                "scans": self.remaining_scans,
                "pages": self.remaining_pages,
            },
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
            "tier": self.tier,
        }


def get_next_period_end(from_date: datetime = None) -> datetime:
    """
    Get the datetime for the end of the current 30-day period.

    Uses rolling 30-day periods from signup date, not calendar months.
    This is fairer to users who sign up mid-month.

    Args:
        from_date: Starting date for the period (default: now)

    Returns:
        datetime 30 days from the given date
    """
    if from_date is None:
        from_date = datetime.now(timezone.utc)
    return from_date + timedelta(days=30)


# Keep old function name as alias for backwards compatibility during transition
def get_next_month_start() -> datetime:
    """Deprecated: Use get_next_period_end() instead. Returns 30 days from now."""
    return get_next_period_end()


def reset_monthly_quota_if_needed(db: Session, department: Department) -> Department:
    """
    Reset monthly quota counters if the reset date has passed.

    Args:
        db: Database session
        department: Department to check/reset

    Returns:
        Updated department object
    """
    now = datetime.now(timezone.utc)

    # If quota_reset_at is not set or has passed, reset the counters
    if department.quota_reset_at is None or now >= department.quota_reset_at:
        department.scans_this_month = 0
        department.pages_this_month = 0
        department.images_this_month = 0  # Reset image counter too
        department.quota_reset_at = get_next_period_end()  # Rolling 30-day period
        db.commit()
        db.refresh(department)
        logger.info(
            f"Reset monthly quota for department {department.id} "
            f"(next reset: {department.quota_reset_at})"
        )

    return department


async def check_quota(
    db: Session,
    department_id: str,
    pages: int = 0,
) -> QuotaResult:
    """
    Check if a department has quota remaining for a scan operation.

    Args:
        db: Database session
        department_id: ID of the department to check
        pages: Number of pages that will be processed (0 for just checking scans)

    Returns:
        QuotaResult with allowed status and remaining quota info
    """
    # Get department
    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        return QuotaResult(
            allowed=False,
            message="Department not found",
        )

    # Reset monthly quota if needed
    department = reset_monthly_quota_if_needed(db, department)

    # Get tier quota configuration
    tier_quota = get_tier_quota(department.tier)
    scans_limit = tier_quota.get("scans_per_month", -1)
    pages_limit = tier_quota.get("total_pages_per_month", -1)
    pages_per_scan_limit = tier_quota.get("pages_per_scan", -1)

    # Unlimited tiers (department, university) have -1 limits
    if scans_limit == -1:
        return QuotaResult(
            allowed=True,
            message="Unlimited tier - no quota restrictions",
            remaining_scans=-1,
            remaining_pages=-1,
            resets_at=None,
            tier=department.tier,
        )

    # Calculate remaining
    remaining_scans = max(0, scans_limit - department.scans_this_month)
    remaining_pages = max(0, pages_limit - department.pages_this_month)

    # Check if scan count would exceed limit
    if remaining_scans <= 0:
        return QuotaResult(
            allowed=False,
            message=f"Monthly scan limit reached ({scans_limit} scans/month). "
            f"Upgrade to Department plan for unlimited scanning.",
            remaining_scans=0,
            remaining_pages=remaining_pages,
            resets_at=department.quota_reset_at,
            tier=department.tier,
        )

    # Check if pages per scan would exceed limit
    if pages_per_scan_limit > 0 and pages > pages_per_scan_limit:
        return QuotaResult(
            allowed=False,
            message=f"Document exceeds page limit ({pages} pages, limit is {pages_per_scan_limit}). "
            f"Upgrade to Department plan for larger documents.",
            remaining_scans=remaining_scans,
            remaining_pages=remaining_pages,
            resets_at=department.quota_reset_at,
            tier=department.tier,
        )

    # Check if total pages would exceed monthly limit
    if pages > 0 and (department.pages_this_month + pages) > pages_limit:
        return QuotaResult(
            allowed=False,
            message=f"Monthly page limit would be exceeded ({remaining_pages} pages remaining). "
            f"Upgrade to Department plan for unlimited pages.",
            remaining_scans=remaining_scans,
            remaining_pages=remaining_pages,
            resets_at=department.quota_reset_at,
            tier=department.tier,
        )

    # All checks passed
    return QuotaResult(
        allowed=True,
        message="Quota check passed",
        remaining_scans=remaining_scans - 1,  # Account for this scan
        remaining_pages=remaining_pages - pages,  # Account for these pages
        resets_at=department.quota_reset_at,
        tier=department.tier,
    )


async def increment_usage(
    db: Session,
    department_id: str,
    scans: int = 1,
    pages: int = 0,
) -> bool:
    """
    Increment usage counters for a department after a successful scan.

    Args:
        db: Database session
        department_id: ID of the department
        scans: Number of scans to add (default 1)
        pages: Number of pages processed

    Returns:
        True if successful, False otherwise
    """
    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        logger.error(f"Cannot increment usage: department {department_id} not found")
        return False

    # Get tier quota to check if we should track usage
    tier_quota = get_tier_quota(department.tier)
    if tier_quota.get("scans_per_month", -1) == -1:
        # Unlimited tier, no need to track
        return True

    # Atomic increment to prevent race conditions with concurrent requests
    db.execute(
        update(Department)
        .where(Department.id == department_id)
        .values(
            scans_this_month=Department.scans_this_month + scans,
            pages_this_month=Department.pages_this_month + pages,
        )
    )
    db.commit()

    # Refresh to get updated values for logging
    db.refresh(department)

    logger.info(
        f"Incremented usage for department {department_id}: "
        f"+{scans} scans, +{pages} pages "
        f"(total: {department.scans_this_month}/{tier_quota.get('scans_per_month')} scans, "
        f"{department.pages_this_month}/{tier_quota.get('total_pages_per_month')} pages)"
    )

    return True


def increment_usage_sync(
    db: Session,
    department_id: str,
    scans: int = 1,
    pages: int = 0,
) -> bool:
    """
    Synchronous version of increment_usage for background tasks.

    Args:
        db: Database session
        department_id: ID of the department
        scans: Number of scans to add (default 1)
        pages: Number of pages processed

    Returns:
        True if successful, False otherwise
    """
    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        logger.error(f"Cannot increment usage: department {department_id} not found")
        return False

    # Get tier quota to check if we should track usage
    tier_quota = get_tier_quota(department.tier)
    if tier_quota.get("scans_per_month", -1) == -1:
        # Unlimited tier, no need to track
        return True

    # Atomic increment to prevent race conditions with concurrent requests
    db.execute(
        update(Department)
        .where(Department.id == department_id)
        .values(
            scans_this_month=Department.scans_this_month + scans,
            pages_this_month=Department.pages_this_month + pages,
        )
    )
    db.commit()

    # Refresh to get updated values for logging
    db.refresh(department)

    logger.info(
        f"Incremented usage for department {department_id}: "
        f"+{scans} scans, +{pages} pages "
        f"(total: {department.scans_this_month}/{tier_quota.get('scans_per_month')} scans, "
        f"{department.pages_this_month}/{tier_quota.get('total_pages_per_month')} pages)"
    )

    return True


async def check_image_quota(
    db: Session,
    department_id: str,
    count: int = 1,
) -> QuotaResult:
    """
    Check if a department has quota remaining for image analysis operations.

    This is separate from document scan quota - image analyses (standalone image
    endpoints like /image/alt-text) have their own monthly limit.

    Args:
        db: Database session
        department_id: ID of the department to check
        count: Number of images that will be analyzed (default 1)

    Returns:
        QuotaResult with allowed status and remaining quota info
    """
    # Get department
    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        return QuotaResult(
            allowed=False,
            message="Department not found",
        )

    # Reset monthly quota if needed
    department = reset_monthly_quota_if_needed(db, department)

    # Get tier quota configuration
    tier_quota = get_tier_quota(department.tier)
    images_limit = tier_quota.get("image_analyses_per_month", -1)

    # Unlimited tiers have -1 limits
    if images_limit == -1:
        return QuotaResult(
            allowed=True,
            message="Unlimited tier - no image quota restrictions",
            remaining_scans=-1,  # Using scans field for consistency
            remaining_pages=-1,
            resets_at=None,
            tier=department.tier,
        )

    # Calculate remaining
    images_used = department.images_this_month or 0
    remaining_images = max(0, images_limit - images_used)

    # Check if image count would exceed limit
    if remaining_images < count:
        return QuotaResult(
            allowed=False,
            message=f"Monthly image analysis limit reached ({images_limit} images/month). "
            f"Upgrade to unlock more image analyses.",
            remaining_scans=remaining_images,  # Reusing this field for images
            remaining_pages=-1,
            resets_at=department.quota_reset_at,
            tier=department.tier,
        )

    # All checks passed
    return QuotaResult(
        allowed=True,
        message="Image quota check passed",
        remaining_scans=remaining_images - count,  # Account for this analysis
        remaining_pages=-1,
        resets_at=department.quota_reset_at,
        tier=department.tier,
    )


async def increment_image_usage(
    db: Session,
    department_id: str,
    count: int = 1,
) -> bool:
    """
    Increment image analysis counter for a department after successful image processing.

    Args:
        db: Database session
        department_id: ID of the department
        count: Number of images analyzed (default 1)

    Returns:
        True if successful, False otherwise
    """
    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        logger.error(
            f"Cannot increment image usage: department {department_id} not found"
        )
        return False

    # Get tier quota to check if we should track usage
    tier_quota = get_tier_quota(department.tier)
    if tier_quota.get("image_analyses_per_month", -1) == -1:
        # Unlimited tier, no need to track
        return True

    # Increment counter
    department.images_this_month = (department.images_this_month or 0) + count

    db.commit()

    logger.info(
        f"Incremented image usage for department {department_id}: "
        f"+{count} images "
        f"(total: {department.images_this_month}/{tier_quota.get('image_analyses_per_month')} images)"
    )

    return True


def get_quota_status(db: Session, department_id: str) -> dict:
    """
    Get current quota status for a department.

    Args:
        db: Database session
        department_id: ID of the department

    Returns:
        Dictionary with quota status information
    """
    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        return {"error": "Department not found"}

    # Reset monthly quota if needed
    department = reset_monthly_quota_if_needed(db, department)

    # Get tier configuration
    tier_quota = get_tier_quota(department.tier)
    scans_limit = tier_quota.get("scans_per_month", -1)
    pages_limit = tier_quota.get("total_pages_per_month", -1)

    # Get image limit
    images_limit = tier_quota.get("image_analyses_per_month", -1)

    # Calculate remaining (handle unlimited tiers)
    if scans_limit == -1:
        scans_used = department.scans_this_month or 0
        pages_used = department.pages_this_month or 0
        images_used = department.images_this_month or 0
        return {
            "tier": department.tier,
            "unlimited": True,
            "scans": {
                "used": scans_used,
                "limit": -1,
                "remaining": -1,
            },
            "pages": {
                "used": pages_used,
                "limit": -1,
                "remaining": -1,
            },
            "images": {
                "used": images_used,
                "limit": -1,
                "remaining": -1,
            },
            "resets_at": None,
            "features": tier_quota.get("features", []),
            "excluded": tier_quota.get("excluded", []),
        }

    # Calculate for limited tiers
    scans_used = department.scans_this_month or 0
    pages_used = department.pages_this_month or 0
    images_used = department.images_this_month or 0

    return {
        "tier": department.tier,
        "unlimited": False,
        "scans": {
            "used": scans_used,
            "limit": scans_limit,
            "remaining": max(0, scans_limit - scans_used),
        },
        "pages": {
            "used": pages_used,
            "limit": pages_limit,
            "remaining": max(0, pages_limit - pages_used),
        },
        "images": {
            "used": images_used,
            "limit": images_limit,
            "remaining": max(0, images_limit - images_used) if images_limit > 0 else -1,
        },
        "pages_per_scan_limit": tier_quota.get("pages_per_scan", -1),
        "max_file_size_mb": tier_quota.get("max_file_size_mb", 50),
        "resets_at": (
            department.quota_reset_at.isoformat() if department.quota_reset_at else None
        ),
        "features": tier_quota.get("features", []),
        "excluded": tier_quota.get("excluded", []),
        "upgrade_url": "/pricing",
    }


def check_feature_access(tier: str, feature: str) -> bool:
    """
    Check if a tier has access to a specific feature.

    Args:
        tier: The tier name (individual_free, trial, department, university)
        feature: The feature to check (e.g., "lms_integration", "bulk_api")

    Returns:
        True if the tier has access to the feature
    """
    tier_quota = get_tier_quota(tier)
    features = tier_quota.get("features", [])
    excluded = tier_quota.get("excluded", [])

    # Feature is accessible if it's in features list and not in excluded list
    return feature in features and feature not in excluded


async def require_feature(
    db: Session,
    department_id: str,
    feature: str,
    feature_display_name: str = None,
) -> None:
    """
    Require a specific feature for the request to proceed.

    Raises HTTPException 403 if the feature is not available for the department's tier.

    Args:
        db: Database session
        department_id: ID of the department to check
        feature: The feature key (e.g., "latex", "video", "lms_integration")
        feature_display_name: Human-readable name for error message (optional)

    Raises:
        HTTPException 403: If the feature is not available on this tier
    """
    from fastapi import HTTPException

    # Skip for mock auth in dev (check prefix, not exact match for dynamic session IDs)
    from ..config.settings import get_settings

    settings = get_settings()
    if (
        department_id
        and department_id == "dev-dept-local"
        and settings.env.lower() == "development"
    ):
        return

    # Get department to check tier
    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    tier = department.tier

    if not check_feature_access(tier, feature):
        display_name = feature_display_name or feature.replace("_", " ").title()

        # Find what tier includes this feature
        upgrade_tiers = []
        for tier_name, tier_config in TIER_QUOTAS.items():
            if feature in tier_config.get(
                "features", []
            ) and feature not in tier_config.get("excluded", []):
                upgrade_tiers.append(tier_name)

        upgrade_suggestion = upgrade_tiers[0] if upgrade_tiers else "department"

        logger.warning(
            f"Feature '{feature}' denied for department {department_id} "
            f"(tier: {tier})"
        )

        raise HTTPException(
            status_code=403,
            detail={
                "error": "feature_not_available",
                "feature": feature,
                "message": f"{display_name} is not available on your current plan ({tier}). "
                f"Upgrade to {upgrade_suggestion} plan to access this feature.",
                "current_tier": tier,
                "upgrade_required": True,
                "upgrade_url": "/pricing",
                "available_in": upgrade_tiers,
            },
        )


# =============================================================================
# BYOK (Bring Your Own Key) Requirement
# =============================================================================
# Pilot and department tiers must configure their own AI API keys to control
# costs for bootstrapped operations.


async def check_byok_required(
    db: Session,
    department_id: str,
) -> bool:
    """
    Check if a department has BYOK configured (or is approved for founder's key).

    For pilot and department tiers, BYOK is required to control API costs.
    Returns True if the department can proceed with AI operations.

    Args:
        db: Database session
        department_id: ID of the department to check

    Returns:
        True if BYOK is configured or not required

    Raises:
        HTTPException 403: If BYOK is required but not configured
    """
    from fastapi import HTTPException

    # Skip for mock auth in dev (check prefix, not exact match for dynamic session IDs)
    from ..config.settings import get_settings

    settings = get_settings()
    if (
        department_id
        and department_id == "dev-dept-local"
        and settings.env.lower() == "development"
    ):
        return True

    department = db.query(Department).filter(Department.id == department_id).first()

    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # BYOK is only required for pilot and department tiers
    # Free/trial/paid individual tiers use founder's Gemini key (within quotas)
    if department.tier not in ["pilot", "department"]:
        return True

    # Check if BYOK is configured
    if department.byok_provider:
        return True

    # Check if manually approved for founder's Gemini key (high-value pilots)
    if department.pilot_gemini_approved:
        return True

    # BYOK required but not configured
    logger.warning(
        f"BYOK required but not configured for department {department_id} "
        f"(tier: {department.tier})"
    )

    raise HTTPException(
        status_code=403,
        detail={
            "error": "byok_required",
            "message": "Your plan requires you to configure your own AI API key. "
            "Go to Settings > AI Provider to add your Gemini, OpenAI, or Anthropic API key.",
            "current_tier": department.tier,
            "setup_url": "/settings/integrations",
            "docs_url": "/docs/byok-setup",
            "supported_providers": ["gemini", "openai", "anthropic", "ollama"],
        },
    )


def is_byok_configured(department: Department) -> bool:
    """
    Quick check if a department has BYOK configured.

    This is a non-raising version for UI display purposes.

    Args:
        department: Department object to check

    Returns:
        True if BYOK is configured or approved, False otherwise
    """
    if department.tier not in ["pilot", "department"]:
        return True  # Not required for this tier

    return bool(department.byok_provider) or department.pilot_gemini_approved
