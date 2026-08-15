"""Gamification endpoints — leaderboards, user stats, badges, levels."""

import logging
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db.database import get_db_dependency
from ...db.models import APIKey
from ._shared import get_api_key_or_mock

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/compliance/{department_id}/leaderboard")
async def get_faculty_leaderboard(
    department_id: str,
    limit: int = 20,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
       Get faculty compliance leaderboard

        Faculty engagement tracking
       REQUIRES API KEY IN PRODUCTION

       Shows faculty members ranked by average compliance score.
       Encourages friendly competition and recognizes accessibility champions.

       Features:
       - Ranked by average compliance score
    - Includes badges ("Accessibility Champion", "Highly Compliant", etc.)
       - Shows total scans, files, and issues fixed
       - Great for department chair dashboards
    """
    _, user_id, department_id_from_token = api_key_info
    if department_id != department_id_from_token:
        raise HTTPException(
            status_code=403, detail="Access denied: department mismatch"
        )
    logger.info(f"Getting faculty leaderboard for department: {department_id}")

    try:
        from ...education.compliance_dashboard import ComplianceDashboard

        # Get leaderboard
        leaderboard = ComplianceDashboard.get_faculty_leaderboard(
            db, department_id, limit=limit
        )

        return {
            "total_faculty": len(leaderboard),
            "leaderboard": [
                {
                    "rank": entry.rank,
                    "user_id": entry.user_id,
                    "user_name": entry.user_name,
                    "user_email": entry.user_email,
                    "total_scans": entry.total_scans,
                    "avg_compliance_score": entry.avg_compliance_score,
                    "total_files": entry.total_files,
                    "total_issues_fixed": entry.total_issues_fixed,
                    "badge": entry.badge,
                }
                for entry in leaderboard
            ],
        }

    except Exception as e:
        logger.error(f"Error getting faculty leaderboard: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get faculty leaderboard. Please try again.",
        )


@router.get("/gamification/{department_id}/leaderboard")
async def get_gamified_leaderboard(
    department_id: str,
    leaderboard_type: str = "all_time",  # all_time, monthly, improvement, points
    limit: int = 20,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get enhanced faculty leaderboard with gamification

     GAMIFIED LEADERBOARD
    REQUIRES API KEY IN PRODUCTION

    Enhanced leaderboard with points, levels, badges, and multiple ranking types.

    Leaderboard types:
    - all_time: Ranked by average compliance score (default)
    - monthly: Ranked by improvement in last 30 days
    - improvement: Ranked by week-over-week score change
    - points: Ranked by total gamification points

    Features:
    - Points system (scans, fixes, achievements)
    - 9 levels from Newcomer to Accessibility Hero
    - 20+ achievement badges
    - Current streak tracking
    """
    _, user_id, department_id_from_token = api_key_info
    if department_id != department_id_from_token:
        raise HTTPException(
            status_code=403, detail="Access denied: department mismatch"
        )
    logger.info(f"Getting gamified leaderboard for department: {department_id}")

    try:
        from ...education.gamification_service import GamificationService

        service = GamificationService(db)
        leaderboard = service.get_department_leaderboard(
            department_id, leaderboard_type, limit
        )

        return {
            "success": True,
            "leaderboard_type": leaderboard_type,
            "total_faculty": len(leaderboard),
            "leaderboard": leaderboard,
        }

    except Exception as e:
        logger.error(f"Error getting gamified leaderboard: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to get leaderboard. Please try again."
        )


@router.get("/gamification/{department_id}/user/{user_id}")
async def get_user_gamification_stats(
    department_id: str,
    user_id: str,
    db: Session = Depends(get_db_dependency),
    api_key_info: Tuple[Optional[APIKey], str, str] = Depends(get_api_key_or_mock),
):
    """
    Get gamification stats for a specific user

     USER GAMIFICATION STATS
    REQUIRES API KEY IN PRODUCTION

    Returns comprehensive gamification data for a faculty member including:
    - Total points and current level
    - All earned badges with descriptions
    - Progress towards next badge
    - Rankings (all-time, monthly, improvement)
    - Current and longest streaks
    - Score improvements (7-day and 30-day)
    """
    _, auth_user_id, auth_dept_id = api_key_info
    if department_id != auth_dept_id:
        raise HTTPException(
            status_code=403, detail="Access denied: department mismatch"
        )
    logger.info(f"Getting gamification stats for user: {user_id}")

    try:
        from ...education.gamification_service import get_gamification_stats

        stats = get_gamification_stats(db, user_id, department_id)
        return {"success": True, **stats}

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting gamification stats: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get gamification stats. Please try again.",
        )


@router.get("/gamification/badges")
async def list_all_badges():
    """
    List all available achievement badges

     ACHIEVEMENT BADGES
    No authentication required.

    Returns all badges that faculty can earn, grouped by tier.
    """
    from ...education.gamification_service import BADGE_DEFINITIONS

    badges_by_tier = {"bronze": [], "silver": [], "gold": [], "platinum": []}

    for badge in BADGE_DEFINITIONS.values():
        badges_by_tier[badge.tier].append(
            {
                "key": badge.badge_type.value,
                "name": badge.name,
                "description": badge.description,
                "emoji": badge.emoji,
                "points": badge.points,
            }
        )

    return {
        "success": True,
        "total_badges": len(BADGE_DEFINITIONS),
        "badges_by_tier": badges_by_tier,
    }


@router.get("/gamification/levels")
async def list_all_levels():
    """
    List all achievement levels

     ACHIEVEMENT LEVELS
    No authentication required.

    Returns all levels and point thresholds.
    """
    from ...education.gamification_service import LEVELS

    return {
        "success": True,
        "total_levels": len(LEVELS),
        "levels": [
            {
                "level": i + 1,
                "name": name,
                "emoji": emoji,
                "points_required": threshold,
            }
            for i, (threshold, name, emoji) in enumerate(LEVELS)
        ],
    }
