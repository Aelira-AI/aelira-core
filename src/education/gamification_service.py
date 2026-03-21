"""
Gamification Service for Faculty Engagement

Provides achievements, badges, points, and leaderboard variations
to encourage faculty participation in accessibility remediation.

Author: Aelira Team
Created: January 2026
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from enum import Enum

logger = logging.getLogger(__name__)


class BadgeType(str, Enum):
    """Types of achievement badges."""

    # Scan milestones
    FIRST_SCAN = "first_scan"
    SCAN_STREAK_7 = "scan_streak_7"
    SCAN_STREAK_30 = "scan_streak_30"
    SCANS_10 = "scans_10"
    SCANS_50 = "scans_50"
    SCANS_100 = "scans_100"

    # Compliance achievements
    COMPLIANT_FILE = "compliant_file"
    PERFECT_SCORE = "perfect_score"
    COMPLIANCE_CHAMPION = "compliance_champion"
    CONSISTENT_90 = "consistent_90"

    # Issue fixing
    FIRST_FIX = "first_fix"
    FIXES_10 = "fixes_10"
    FIXES_50 = "fixes_50"
    FIXES_100 = "fixes_100"
    AUTO_FIX_MASTER = "auto_fix_master"

    # Improvement
    IMPROVEMENT_5 = "improvement_5"
    IMPROVEMENT_10 = "improvement_10"
    IMPROVEMENT_20 = "improvement_20"
    TURNAROUND = "turnaround"

    # Engagement
    EARLY_ADOPTER = "early_adopter"
    TEAM_PLAYER = "team_player"
    MENTOR = "mentor"

    # Special
    DEADLINE_READY = "deadline_ready"
    ALL_DOCUMENTS = "all_documents"


@dataclass
class Badge:
    """Achievement badge definition."""

    badge_type: BadgeType
    name: str
    description: str
    emoji: str
    points: int
    tier: str  # bronze, silver, gold, platinum


@dataclass
class FacultyStats:
    """Extended faculty statistics with gamification data."""

    user_id: str
    user_name: str
    user_email: str

    # Core metrics
    total_scans: int
    avg_compliance_score: float
    total_files: int
    total_issues_fixed: int

    # Gamification
    total_points: int
    level: int
    level_name: str
    badges_earned: List[Badge]
    next_badge: Optional[Badge]
    progress_to_next: float  # 0-1

    # Rankings
    all_time_rank: int
    monthly_rank: int
    improvement_rank: int

    # Streaks
    current_streak: int
    longest_streak: int

    # Improvement
    score_change_30d: float
    score_change_7d: float


# Badge definitions
BADGE_DEFINITIONS = {
    BadgeType.FIRST_SCAN: Badge(
        BadgeType.FIRST_SCAN,
        "First Steps",
        "Completed your first accessibility scan",
        "🎯",
        10,
        "bronze",
    ),
    BadgeType.SCAN_STREAK_7: Badge(
        BadgeType.SCAN_STREAK_7,
        "Weekly Warrior",
        "Scanned documents 7 days in a row",
        "🔥",
        50,
        "silver",
    ),
    BadgeType.SCAN_STREAK_30: Badge(
        BadgeType.SCAN_STREAK_30,
        "Consistency King",
        "Scanned documents 30 days in a row",
        "👑",
        200,
        "gold",
    ),
    BadgeType.SCANS_10: Badge(
        BadgeType.SCANS_10,
        "Getting Started",
        "Completed 10 accessibility scans",
        "📊",
        25,
        "bronze",
    ),
    BadgeType.SCANS_50: Badge(
        BadgeType.SCANS_50,
        "Dedicated Scanner",
        "Completed 50 accessibility scans",
        "📈",
        100,
        "silver",
    ),
    BadgeType.SCANS_100: Badge(
        BadgeType.SCANS_100,
        "Scan Master",
        "Completed 100 accessibility scans",
        "🏆",
        250,
        "gold",
    ),
    BadgeType.COMPLIANT_FILE: Badge(
        BadgeType.COMPLIANT_FILE,
        "First Victory",
        "Achieved 90%+ compliance on a file",
        "✅",
        15,
        "bronze",
    ),
    BadgeType.PERFECT_SCORE: Badge(
        BadgeType.PERFECT_SCORE,
        "Perfectionist",
        "Achieved 100% compliance on a file",
        "💯",
        50,
        "silver",
    ),
    BadgeType.COMPLIANCE_CHAMPION: Badge(
        BadgeType.COMPLIANCE_CHAMPION,
        "Compliance Champion",
        "Maintained 90%+ average for 30 days",
        "🏅",
        300,
        "gold",
    ),
    BadgeType.CONSISTENT_90: Badge(
        BadgeType.CONSISTENT_90,
        "Steady Excellence",
        "10 consecutive files above 90%",
        "⭐",
        150,
        "silver",
    ),
    BadgeType.FIRST_FIX: Badge(
        BadgeType.FIRST_FIX,
        "Problem Solver",
        "Fixed your first accessibility issue",
        "🔧",
        10,
        "bronze",
    ),
    BadgeType.FIXES_10: Badge(
        BadgeType.FIXES_10,
        "Issue Hunter",
        "Fixed 10 accessibility issues",
        "🎯",
        30,
        "bronze",
    ),
    BadgeType.FIXES_50: Badge(
        BadgeType.FIXES_50,
        "Fix Expert",
        "Fixed 50 accessibility issues",
        "💪",
        100,
        "silver",
    ),
    BadgeType.FIXES_100: Badge(
        BadgeType.FIXES_100,
        "Remediation Hero",
        "Fixed 100 accessibility issues",
        "🦸",
        250,
        "gold",
    ),
    BadgeType.AUTO_FIX_MASTER: Badge(
        BadgeType.AUTO_FIX_MASTER,
        "Automation Advocate",
        "Used auto-fix on 25+ files",
        "🤖",
        75,
        "silver",
    ),
    BadgeType.IMPROVEMENT_5: Badge(
        BadgeType.IMPROVEMENT_5,
        "On the Rise",
        "Improved average score by 5 points",
        "📈",
        25,
        "bronze",
    ),
    BadgeType.IMPROVEMENT_10: Badge(
        BadgeType.IMPROVEMENT_10,
        "Making Progress",
        "Improved average score by 10 points",
        "🚀",
        75,
        "silver",
    ),
    BadgeType.IMPROVEMENT_20: Badge(
        BadgeType.IMPROVEMENT_20,
        "Transformation",
        "Improved average score by 20 points",
        "🌟",
        150,
        "gold",
    ),
    BadgeType.TURNAROUND: Badge(
        BadgeType.TURNAROUND,
        "Turnaround Story",
        "Went from below 50% to above 80%",
        "🔄",
        200,
        "gold",
    ),
    BadgeType.EARLY_ADOPTER: Badge(
        BadgeType.EARLY_ADOPTER,
        "Early Adopter",
        "Joined during the first month",
        "🌅",
        100,
        "silver",
    ),
    BadgeType.TEAM_PLAYER: Badge(
        BadgeType.TEAM_PLAYER,
        "Team Player",
        "Helped a colleague fix 5 issues",
        "🤝",
        50,
        "silver",
    ),
    BadgeType.MENTOR: Badge(
        BadgeType.MENTOR,
        "Mentor",
        "Helped onboard 3 new faculty members",
        "👨‍🏫",
        100,
        "gold",
    ),
    BadgeType.DEADLINE_READY: Badge(
        BadgeType.DEADLINE_READY,
        "Deadline Ready",
        "All your files are 90%+ compliant",
        "🎉",
        500,
        "platinum",
    ),
    BadgeType.ALL_DOCUMENTS: Badge(
        BadgeType.ALL_DOCUMENTS,
        "Document Master",
        "Scanned PDF, PPTX, DOCX, XLSX, and images",
        "📚",
        75,
        "silver",
    ),
}


# Level definitions
LEVELS = [
    (0, "Newcomer", "🌱"),
    (50, "Beginner", "🌿"),
    (150, "Learner", "🌳"),
    (300, "Practitioner", "⭐"),
    (500, "Expert", "🌟"),
    (800, "Master", "💫"),
    (1200, "Champion", "🏆"),
    (1800, "Legend", "👑"),
    (2500, "Accessibility Hero", "🦸"),
]


class GamificationService:
    """Service for managing gamification features."""

    def __init__(self, db: Session):
        self.db = db

    def get_faculty_stats(self, user_id: str, department_id: str) -> FacultyStats:
        """Get comprehensive gamification stats for a faculty member."""
        from ..db.models import Scan, ScanResult, User

        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError(f"User not found: {user_id}")

        # Get all scans
        scans = (
            self.db.query(Scan)
            .filter(Scan.user_id == user_id)
            .filter(Scan.department_id == department_id)
            .order_by(Scan.created_at.desc())
            .all()
        )

        # Calculate core metrics
        total_scans = len(scans)
        total_files = len(set(s.file_name for s in scans if s.file_name))
        total_issues_fixed = 0
        compliance_scores = []

        for scan in scans:
            result = (
                self.db.query(ScanResult).filter(ScanResult.scan_id == scan.id).first()
            )
            if result:
                if result.compliance_score:
                    compliance_scores.append(result.compliance_score)
                # Count fixed issues (remediated)
                if result.remediation_applied:
                    total_issues_fixed += len(result.issues) if result.issues else 0

        avg_score = (
            sum(compliance_scores) / len(compliance_scores) if compliance_scores else 0
        )

        # Calculate badges earned
        badges_earned = self._calculate_badges(
            user_id,
            department_id,
            total_scans,
            total_files,
            total_issues_fixed,
            avg_score,
            scans,
        )

        # Calculate points
        total_points = sum(b.points for b in badges_earned)
        # Add points for scans and fixes
        total_points += total_scans * 5  # 5 points per scan
        total_points += total_issues_fixed * 2  # 2 points per fix

        # Determine level
        level, level_name, _ = self._get_level(total_points)

        # Find next badge
        next_badge, progress = self._find_next_badge(
            badges_earned, total_scans, total_files, total_issues_fixed, avg_score
        )

        # Calculate rankings
        all_time_rank = self._get_rank(user_id, department_id, "all_time")
        monthly_rank = self._get_rank(user_id, department_id, "monthly")
        improvement_rank = self._get_rank(user_id, department_id, "improvement")

        # Calculate streaks
        current_streak, longest_streak = self._calculate_streaks(scans)

        # Calculate improvement
        score_change_30d = self._calculate_improvement(scans, 30)
        score_change_7d = self._calculate_improvement(scans, 7)

        return FacultyStats(
            user_id=user_id,
            user_name=user.name or "Unknown",
            user_email=user.email,
            total_scans=total_scans,
            avg_compliance_score=round(avg_score, 2),
            total_files=total_files,
            total_issues_fixed=total_issues_fixed,
            total_points=total_points,
            level=level,
            level_name=level_name,
            badges_earned=badges_earned,
            next_badge=next_badge,
            progress_to_next=progress,
            all_time_rank=all_time_rank,
            monthly_rank=monthly_rank,
            improvement_rank=improvement_rank,
            current_streak=current_streak,
            longest_streak=longest_streak,
            score_change_30d=score_change_30d,
            score_change_7d=score_change_7d,
        )

    def _calculate_badges(
        self,
        user_id: str,
        department_id: str,
        total_scans: int,
        total_files: int,
        total_issues_fixed: int,
        avg_score: float,
        scans: list,
    ) -> List[Badge]:
        """Calculate which badges a user has earned."""
        badges = []

        # Scan milestones
        if total_scans >= 1:
            badges.append(BADGE_DEFINITIONS[BadgeType.FIRST_SCAN])
        if total_scans >= 10:
            badges.append(BADGE_DEFINITIONS[BadgeType.SCANS_10])
        if total_scans >= 50:
            badges.append(BADGE_DEFINITIONS[BadgeType.SCANS_50])
        if total_scans >= 100:
            badges.append(BADGE_DEFINITIONS[BadgeType.SCANS_100])

        # Issue fixing
        if total_issues_fixed >= 1:
            badges.append(BADGE_DEFINITIONS[BadgeType.FIRST_FIX])
        if total_issues_fixed >= 10:
            badges.append(BADGE_DEFINITIONS[BadgeType.FIXES_10])
        if total_issues_fixed >= 50:
            badges.append(BADGE_DEFINITIONS[BadgeType.FIXES_50])
        if total_issues_fixed >= 100:
            badges.append(BADGE_DEFINITIONS[BadgeType.FIXES_100])

        # Compliance achievements
        if avg_score >= 90:
            badges.append(BADGE_DEFINITIONS[BadgeType.COMPLIANCE_CHAMPION])

        # Check for perfect scores and compliant files
        from ..db.models import ScanResult

        for scan in scans:
            result = (
                self.db.query(ScanResult).filter(ScanResult.scan_id == scan.id).first()
            )
            if result and result.compliance_score:
                if result.compliance_score == 100:
                    if BADGE_DEFINITIONS[BadgeType.PERFECT_SCORE] not in badges:
                        badges.append(BADGE_DEFINITIONS[BadgeType.PERFECT_SCORE])
                if result.compliance_score >= 90:
                    if BADGE_DEFINITIONS[BadgeType.COMPLIANT_FILE] not in badges:
                        badges.append(BADGE_DEFINITIONS[BadgeType.COMPLIANT_FILE])

        # Improvement badges
        improvement = self._calculate_improvement(scans, 30)
        if improvement >= 5:
            badges.append(BADGE_DEFINITIONS[BadgeType.IMPROVEMENT_5])
        if improvement >= 10:
            badges.append(BADGE_DEFINITIONS[BadgeType.IMPROVEMENT_10])
        if improvement >= 20:
            badges.append(BADGE_DEFINITIONS[BadgeType.IMPROVEMENT_20])

        # Streak badges
        current_streak, _ = self._calculate_streaks(scans)
        if current_streak >= 7:
            badges.append(BADGE_DEFINITIONS[BadgeType.SCAN_STREAK_7])
        if current_streak >= 30:
            badges.append(BADGE_DEFINITIONS[BadgeType.SCAN_STREAK_30])

        return badges

    def _get_level(self, points: int) -> tuple:
        """Get level based on total points."""
        level = 1
        name = "Newcomer"
        emoji = "🌱"

        for i, (threshold, level_name, level_emoji) in enumerate(LEVELS):
            if points >= threshold:
                level = i + 1
                name = level_name
                emoji = level_emoji

        return level, name, emoji

    def _find_next_badge(
        self,
        earned: List[Badge],
        total_scans: int,
        total_files: int,
        total_issues_fixed: int,
        avg_score: float,
    ) -> tuple:
        """Find the next badge the user is closest to earning."""
        earned_types = {b.badge_type for b in earned}

        # Check scan badges
        if BadgeType.SCANS_10 not in earned_types and total_scans < 10:
            return BADGE_DEFINITIONS[BadgeType.SCANS_10], total_scans / 10
        if BadgeType.SCANS_50 not in earned_types and total_scans < 50:
            return BADGE_DEFINITIONS[BadgeType.SCANS_50], total_scans / 50
        if BadgeType.SCANS_100 not in earned_types and total_scans < 100:
            return BADGE_DEFINITIONS[BadgeType.SCANS_100], total_scans / 100

        # Check fix badges
        if BadgeType.FIXES_10 not in earned_types and total_issues_fixed < 10:
            return BADGE_DEFINITIONS[BadgeType.FIXES_10], total_issues_fixed / 10
        if BadgeType.FIXES_50 not in earned_types and total_issues_fixed < 50:
            return BADGE_DEFINITIONS[BadgeType.FIXES_50], total_issues_fixed / 50

        # Check compliance badges
        if BadgeType.COMPLIANCE_CHAMPION not in earned_types and avg_score < 90:
            return BADGE_DEFINITIONS[BadgeType.COMPLIANCE_CHAMPION], avg_score / 90

        return None, 0

    def _get_rank(self, user_id: str, department_id: str, rank_type: str) -> int:
        """Get user's rank in a specific leaderboard."""
        from ..db.models import Scan, ScanResult, User

        # Get all faculty scores
        faculty_scores = []

        faculty = (
            self.db.query(User)
            .filter(User.department_id == department_id)
            .filter(User.is_active)
            .all()
        )

        for f in faculty:
            if rank_type == "monthly":
                start_date = datetime.utcnow() - timedelta(days=30)
                scans = (
                    self.db.query(Scan)
                    .filter(Scan.user_id == f.id)
                    .filter(Scan.created_at >= start_date)
                    .all()
                )
            else:
                scans = self.db.query(Scan).filter(Scan.user_id == f.id).all()

            scores = []
            for scan in scans:
                result = (
                    self.db.query(ScanResult)
                    .filter(ScanResult.scan_id == scan.id)
                    .first()
                )
                if result and result.compliance_score:
                    scores.append(result.compliance_score)

            avg = sum(scores) / len(scores) if scores else 0
            faculty_scores.append((f.id, avg))

        # Sort by score
        faculty_scores.sort(key=lambda x: -x[1])

        # Find user's rank
        for i, (fid, _) in enumerate(faculty_scores, start=1):
            if fid == user_id:
                return i

        return len(faculty_scores) + 1

    def _calculate_streaks(self, scans: list) -> tuple:
        """Calculate current and longest scan streaks."""
        if not scans:
            return 0, 0

        # Get unique scan dates
        scan_dates = sorted(
            set(s.created_at.date() for s in scans if s.created_at), reverse=True
        )

        if not scan_dates:
            return 0, 0

        # Current streak
        current_streak = 1
        today = datetime.utcnow().date()

        if scan_dates[0] == today or scan_dates[0] == today - timedelta(days=1):
            for i in range(1, len(scan_dates)):
                if scan_dates[i] == scan_dates[i - 1] - timedelta(days=1):
                    current_streak += 1
                else:
                    break
        else:
            current_streak = 0

        # Longest streak
        longest_streak = 1
        streak = 1
        for i in range(1, len(scan_dates)):
            if scan_dates[i] == scan_dates[i - 1] - timedelta(days=1):
                streak += 1
                longest_streak = max(longest_streak, streak)
            else:
                streak = 1

        return current_streak, longest_streak

    def _calculate_improvement(self, scans: list, days: int) -> float:
        """Calculate score improvement over a period."""
        if not scans:
            return 0

        from ..db.models import ScanResult

        cutoff = datetime.utcnow() - timedelta(days=days)

        old_scores = []
        new_scores = []

        for scan in scans:
            if not scan.created_at:
                continue

            result = (
                self.db.query(ScanResult).filter(ScanResult.scan_id == scan.id).first()
            )
            if result and result.compliance_score:
                if scan.created_at < cutoff:
                    old_scores.append(result.compliance_score)
                else:
                    new_scores.append(result.compliance_score)

        old_avg = sum(old_scores) / len(old_scores) if old_scores else 0
        new_avg = sum(new_scores) / len(new_scores) if new_scores else 0

        return round(new_avg - old_avg, 2)

    def get_department_leaderboard(
        self, department_id: str, leaderboard_type: str = "all_time", limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Get department leaderboard with gamification data."""
        from ..db.models import User

        faculty = (
            self.db.query(User)
            .filter(User.department_id == department_id)
            .filter(User.is_active)
            .all()
        )

        leaderboard = []

        for user in faculty:
            try:
                stats = self.get_faculty_stats(user.id, department_id)
                leaderboard.append(
                    {
                        "user_id": user.id,
                        "user_name": user.name or "Unknown",
                        "user_email": user.email,
                        "total_scans": stats.total_scans,
                        "avg_compliance_score": stats.avg_compliance_score,
                        "total_points": stats.total_points,
                        "level": stats.level,
                        "level_name": stats.level_name,
                        "badges_count": len(stats.badges_earned),
                        "current_streak": stats.current_streak,
                        "score_change_7d": stats.score_change_7d,
                    }
                )
            except Exception as e:
                logger.warning(f"Error getting stats for user {user.id}: {e}")

        # Sort based on leaderboard type
        if leaderboard_type == "monthly":
            leaderboard.sort(key=lambda x: (-x["score_change_7d"], -x["total_scans"]))
        elif leaderboard_type == "improvement":
            leaderboard.sort(key=lambda x: (-x["score_change_7d"]))
        elif leaderboard_type == "points":
            leaderboard.sort(key=lambda x: (-x["total_points"]))
        else:  # all_time
            leaderboard.sort(
                key=lambda x: (-x["avg_compliance_score"], -x["total_scans"])
            )

        # Add ranks
        for i, entry in enumerate(leaderboard[:limit], start=1):
            entry["rank"] = i

        return leaderboard[:limit]


def get_gamification_stats(
    db: Session, user_id: str, department_id: str
) -> Dict[str, Any]:
    """Convenience function to get gamification stats as dict."""
    service = GamificationService(db)
    stats = service.get_faculty_stats(user_id, department_id)

    return {
        "user_id": stats.user_id,
        "user_name": stats.user_name,
        "metrics": {
            "total_scans": stats.total_scans,
            "avg_compliance_score": stats.avg_compliance_score,
            "total_files": stats.total_files,
            "total_issues_fixed": stats.total_issues_fixed,
        },
        "gamification": {
            "total_points": stats.total_points,
            "level": stats.level,
            "level_name": stats.level_name,
            "badges_earned": [
                {
                    "name": b.name,
                    "description": b.description,
                    "emoji": b.emoji,
                    "points": b.points,
                    "tier": b.tier,
                }
                for b in stats.badges_earned
            ],
            "next_badge": (
                {
                    "name": stats.next_badge.name,
                    "description": stats.next_badge.description,
                    "emoji": stats.next_badge.emoji,
                    "progress": stats.progress_to_next,
                }
                if stats.next_badge
                else None
            ),
        },
        "rankings": {
            "all_time": stats.all_time_rank,
            "monthly": stats.monthly_rank,
            "improvement": stats.improvement_rank,
        },
        "streaks": {
            "current": stats.current_streak,
            "longest": stats.longest_streak,
        },
        "improvement": {
            "score_change_30d": stats.score_change_30d,
            "score_change_7d": stats.score_change_7d,
        },
    }
