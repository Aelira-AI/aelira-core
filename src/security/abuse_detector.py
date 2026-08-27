"""
Abuse Detection System

Detects and prevents:
- Multi-account abuse (same user creating multiple free accounts)
- Quota manipulation
- Automated/bot signups
- Suspicious usage patterns

Uses heuristics and tracking to identify abuse without being overly invasive.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

logger = logging.getLogger(__name__)


@dataclass
class AbuseSignal:
    """A signal indicating potential abuse."""

    signal_type: str
    severity: str  # "low", "medium", "high", "critical"
    description: str
    confidence: float  # 0.0 to 1.0
    action: str  # "allow", "flag", "challenge", "block"


@dataclass
class AbuseCheckResult:
    """Result of an abuse check."""

    allowed: bool
    signals: List[AbuseSignal]
    recommended_action: str
    challenge_type: Optional[str] = None  # "phone", "manual_review", "captcha"
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "signals": [
                {
                    "type": s.signal_type,
                    "severity": s.severity,
                    "description": s.description,
                    "confidence": s.confidence,
                }
                for s in self.signals
            ],
            "recommended_action": self.recommended_action,
            "challenge_type": self.challenge_type,
            "reason": self.reason,
        }


class AbuseDetector:
    """
    Detects abuse patterns in signups and usage.

    Strategies:
    1. Email domain tracking (limit accounts per domain)
    2. IP address tracking (limit signups per IP)
    3. Behavioral analysis (quota hitting patterns)
    4. Fingerprint correlation (device/browser patterns)
    """

    # Thresholds
    MAX_ACCOUNTS_PER_DOMAIN = 100  # Max free accounts per email domain
    MAX_SIGNUPS_PER_IP_24H = 3  # Max signups from same IP in 24 hours
    MAX_SIGNUPS_PER_IP_WEEK = 10  # Max signups from same IP in 7 days
    QUOTA_HIT_THRESHOLD = 3  # Times hitting quota ceiling = suspicious
    RAPID_SIGNUP_WINDOW = 60  # Seconds between signups from same IP = suspicious

    def __init__(self, db: Session):
        self.db = db

    async def check_signup(
        self,
        email: str,
        ip_address: str,
        user_agent: Optional[str] = None,
        fingerprint: Optional[str] = None,
    ) -> AbuseCheckResult:
        """
        Check if a signup attempt shows signs of abuse.

        Args:
            email: Email address being registered
            ip_address: Client IP address
            user_agent: Browser user agent string
            fingerprint: Optional device fingerprint

        Returns:
            AbuseCheckResult with decision and signals
        """
        signals: List[AbuseSignal] = []

        # 0. Hard block known disposable / temp-mail domains
        disposable_signal = self._check_disposable_domain(email)
        if disposable_signal:
            signals.append(disposable_signal)

        # 1. Check email domain account count
        domain_signal = await self._check_domain_abuse(email)
        if domain_signal:
            signals.append(domain_signal)

        # 2. Check IP-based abuse
        ip_signals = await self._check_ip_abuse(ip_address)
        signals.extend(ip_signals)

        # 3. Check for known bad patterns
        pattern_signals = self._check_suspicious_patterns(email, user_agent)
        signals.extend(pattern_signals)

        # 4. Check fingerprint correlation (if provided)
        if fingerprint:
            fp_signal = await self._check_fingerprint_abuse(fingerprint)
            if fp_signal:
                signals.append(fp_signal)

        # Determine action based on signals
        return self._determine_action(signals)

    async def check_usage(
        self,
        department_id: str,
        user_id: str,
    ) -> AbuseCheckResult:
        """
        Check for usage-based abuse patterns.

        Args:
            department_id: Department being checked
            user_id: User making the request

        Returns:
            AbuseCheckResult
        """
        signals: List[AbuseSignal] = []

        # 1. Check for quota gaming (repeatedly hitting ceiling)
        quota_signal = await self._check_quota_gaming(department_id)
        if quota_signal:
            signals.append(quota_signal)

        # 2. Check for suspicious scan patterns
        scan_signal = await self._check_scan_patterns(department_id, user_id)
        if scan_signal:
            signals.append(scan_signal)

        return self._determine_action(signals)

    def _check_disposable_domain(self, email: str) -> Optional[AbuseSignal]:
        """Block known disposable / temp-mail domains (e.g. *.edu.pl temp-mail)."""
        from .disposable_domains import is_disposable_domain

        if is_disposable_domain(email):
            domain = email.split("@")[-1].lower()
            return AbuseSignal(
                signal_type="disposable_email",
                severity="high",
                description=f"Disposable/temp-mail domain: {domain}",
                confidence=0.95,
                action="block",
            )
        return None

    async def _check_domain_abuse(self, email: str) -> Optional[AbuseSignal]:
        """Check if email domain has too many free accounts."""
        from ..db.models import Department

        domain = email.split("@")[1].lower()

        # Count individual workspaces from this domain
        count = (
            self.db.query(func.count(Department.id))
            .filter(
                Department.tier == "individual",
                func.lower(Department.contact_email).like(f"%@{domain}"),
            )
            .scalar()
        )

        if count >= self.MAX_ACCOUNTS_PER_DOMAIN:
            return AbuseSignal(
                signal_type="domain_limit_exceeded",
                severity="high",
                description=f"Domain {domain} has {count} free accounts (limit: {self.MAX_ACCOUNTS_PER_DOMAIN})",
                confidence=0.9,
                action="block",
            )
        elif count >= self.MAX_ACCOUNTS_PER_DOMAIN * 0.8:
            return AbuseSignal(
                signal_type="domain_limit_approaching",
                severity="medium",
                description=f"Domain {domain} approaching account limit ({count}/{self.MAX_ACCOUNTS_PER_DOMAIN})",
                confidence=0.7,
                action="flag",
            )

        return None

    async def _check_ip_abuse(self, ip_address: str) -> List[AbuseSignal]:
        """Check for IP-based abuse patterns."""
        from ..db.models import SignupLog  # We'll need to create this model

        signals = []

        # Hash IP for privacy-preserving storage
        ip_hash = hashlib.sha256(ip_address.encode()).hexdigest()[:32]

        try:
            # Count signups from this IP in last 24 hours
            now = datetime.now(timezone.utc)
            day_ago = now - timedelta(hours=24)
            week_ago = now - timedelta(days=7)

            # Query signup logs (assuming we have this table)
            day_count = (
                self.db.query(func.count(SignupLog.id))
                .filter(SignupLog.ip_hash == ip_hash, SignupLog.created_at >= day_ago)
                .scalar()
            ) or 0

            week_count = (
                self.db.query(func.count(SignupLog.id))
                .filter(SignupLog.ip_hash == ip_hash, SignupLog.created_at >= week_ago)
                .scalar()
            ) or 0

            # Check for rapid signups
            last_signup = (
                self.db.query(SignupLog.created_at)
                .filter(SignupLog.ip_hash == ip_hash)
                .order_by(SignupLog.created_at.desc())
                .first()
            )

            if last_signup:
                time_since_last = (now - last_signup[0]).total_seconds()
                if time_since_last < self.RAPID_SIGNUP_WINDOW:
                    signals.append(
                        AbuseSignal(
                            signal_type="rapid_signup",
                            severity="high",
                            description=f"Signup attempt {time_since_last:.0f}s after previous signup from same IP",
                            confidence=0.85,
                            action="challenge",
                        )
                    )

            if day_count >= self.MAX_SIGNUPS_PER_IP_24H:
                signals.append(
                    AbuseSignal(
                        signal_type="ip_daily_limit",
                        severity="high",
                        description=f"{day_count} signups from this IP in 24h (limit: {self.MAX_SIGNUPS_PER_IP_24H})",
                        confidence=0.9,
                        action="block",
                    )
                )
            elif week_count >= self.MAX_SIGNUPS_PER_IP_WEEK:
                signals.append(
                    AbuseSignal(
                        signal_type="ip_weekly_limit",
                        severity="medium",
                        description=f"{week_count} signups from this IP in 7 days (limit: {self.MAX_SIGNUPS_PER_IP_WEEK})",
                        confidence=0.8,
                        action="challenge",
                    )
                )

        except Exception as e:
            # SignupLog table might not exist yet
            logger.warning(
                "IP abuse check error (table may not exist): %s", type(e).__name__
            )

        return signals

    def _check_suspicious_patterns(
        self, email: str, user_agent: Optional[str]
    ) -> List[AbuseSignal]:
        """Check for suspicious email and user agent patterns."""
        signals = []

        # Check for disposable email patterns
        disposable_patterns = [
            r"\+.*@",  # Plus addressing (e.g., user+test@domain.edu)
            r"@(mailinator|guerrillamail|tempmail|throwaway)",
            r"^test\d+@",
            r"^user\d+@",
            r"^fake",
        ]

        import re

        for pattern in disposable_patterns:
            if re.search(pattern, email.lower()):
                signals.append(
                    AbuseSignal(
                        signal_type="suspicious_email",
                        severity="medium",
                        description="Email matches suspicious pattern",
                        confidence=0.7,
                        action="flag",
                    )
                )
                break

        # Check user agent for bots
        if user_agent:
            bot_patterns = [
                r"bot|crawler|spider|scraper",
                r"python-requests|curl|wget|httpie",
                r"^$",  # Empty user agent
            ]

            for pattern in bot_patterns:
                if re.search(pattern, user_agent.lower()):
                    signals.append(
                        AbuseSignal(
                            signal_type="bot_user_agent",
                            severity="high",
                            description="User agent appears to be automated",
                            confidence=0.8,
                            action="challenge",
                        )
                    )
                    break

        return signals

    async def _check_fingerprint_abuse(self, fingerprint: str) -> Optional[AbuseSignal]:
        """Check for fingerprint-based abuse (same device, multiple accounts)."""
        from ..db.models import SignupLog

        try:
            fp_hash = hashlib.sha256(fingerprint.encode()).hexdigest()[:32]

            # Count accounts with this fingerprint
            count = (
                self.db.query(func.count(SignupLog.id))
                .filter(SignupLog.fingerprint_hash == fp_hash)
                .scalar()
            ) or 0

            if count >= 2:
                return AbuseSignal(
                    signal_type="fingerprint_reuse",
                    severity="high",
                    description=f"Device fingerprint associated with {count} previous signups",
                    confidence=0.85,
                    action="challenge",
                )

        except Exception as e:
            logger.warning("Fingerprint check error: %s", type(e).__name__)

        return None

    async def _check_quota_gaming(self, department_id: str) -> Optional[AbuseSignal]:
        """Check for quota gaming patterns."""
        from ..db.models import Department

        department = (
            self.db.query(Department).filter(Department.id == department_id).first()
        )

        if not department:
            return None

        # Check if department repeatedly hits quota ceiling
        # This would require tracking quota hit history
        # For now, we just check if they're at the limit

        from ..config.settings import get_tier_quota

        tier_quota = get_tier_quota(department.tier)
        scans_limit = tier_quota.get("scans_per_month", -1)

        if scans_limit > 0:
            usage_ratio = (department.scans_this_month or 0) / scans_limit
            if usage_ratio >= 0.95:  # At or near limit
                return AbuseSignal(
                    signal_type="quota_ceiling",
                    severity="low",
                    description="User at quota ceiling",
                    confidence=0.5,
                    action="flag",
                )

        return None

    async def _check_scan_patterns(
        self, department_id: str, user_id: str
    ) -> Optional[AbuseSignal]:
        """Check for suspicious scan patterns."""
        # Future: Check for patterns like:
        # - All scans happening within minutes of quota reset
        # - Scanning same file repeatedly
        # - Unusual scan timing patterns

        return None

    def _determine_action(self, signals: List[AbuseSignal]) -> AbuseCheckResult:
        """Determine final action based on all signals."""
        if not signals:
            return AbuseCheckResult(
                allowed=True,
                signals=[],
                recommended_action="allow",
            )

        # Find most severe action
        action_priority = {"allow": 0, "flag": 1, "challenge": 2, "block": 3}
        max_action = "allow"

        for signal in signals:
            if action_priority.get(signal.action, 0) > action_priority.get(
                max_action, 0
            ):
                max_action = signal.action

        # Determine if allowed
        allowed = max_action in ["allow", "flag"]

        # Determine challenge type if needed
        challenge_type = None
        if max_action == "challenge":
            # Choose challenge based on severity and signal types
            signal_types = [s.signal_type for s in signals]
            if "fingerprint_reuse" in signal_types or "ip_daily_limit" in signal_types:
                challenge_type = "phone"
            else:
                challenge_type = "captcha"

        # Compile reason
        reason = None
        if max_action == "block":
            reason = signals[0].description if signals else "Abuse detected"

        return AbuseCheckResult(
            allowed=allowed,
            signals=signals,
            recommended_action=max_action,
            challenge_type=challenge_type,
            reason=reason,
        )


# Convenience functions


async def check_signup_abuse(
    db: Session,
    email: str,
    ip_address: str,
    user_agent: Optional[str] = None,
    fingerprint: Optional[str] = None,
) -> AbuseCheckResult:
    """
    Check if a signup attempt shows signs of abuse.

    Args:
        db: Database session
        email: Email being registered
        ip_address: Client IP
        user_agent: Browser user agent
        fingerprint: Device fingerprint

    Returns:
        AbuseCheckResult
    """
    detector = AbuseDetector(db)
    return await detector.check_signup(email, ip_address, user_agent, fingerprint)


async def check_usage_abuse(
    db: Session,
    department_id: str,
    user_id: str,
) -> AbuseCheckResult:
    """
    Check for usage-based abuse.

    Args:
        db: Database session
        department_id: Department ID
        user_id: User ID

    Returns:
        AbuseCheckResult
    """
    detector = AbuseDetector(db)
    return await detector.check_usage(department_id, user_id)


def log_signup(
    db: Session,
    email: str,
    ip_address: str,
    user_agent: Optional[str] = None,
    fingerprint: Optional[str] = None,
    success: bool = True,
) -> None:
    """
    Log a signup attempt for abuse tracking.

    Args:
        db: Database session
        email: Email used
        ip_address: Client IP
        user_agent: Browser user agent
        fingerprint: Device fingerprint
        success: Whether signup succeeded
    """
    from ..db.models import SignupLog

    try:
        domain = email.split("@")[1].lower()
        ip_hash = hashlib.sha256(ip_address.encode()).hexdigest()[:32]
        fp_hash = (
            hashlib.sha256(fingerprint.encode()).hexdigest()[:32]
            if fingerprint
            else None
        )

        log_entry = SignupLog(
            email_domain=domain,
            ip_hash=ip_hash,
            user_agent_hash=hashlib.sha256((user_agent or "").encode()).hexdigest()[
                :32
            ],
            fingerprint_hash=fp_hash,
            success=success,
        )

        db.add(log_entry)
        db.commit()

    except Exception as e:
        logger.warning("Failed to log signup: %s", type(e).__name__)
        # Don't fail the signup if logging fails
