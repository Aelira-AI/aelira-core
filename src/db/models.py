import os

"""
SQLAlchemy Database Models for Aelira Higher Education Platform

Schema Design:
- users: Faculty/staff accounts (Google OAuth)
- departments: Multi-tenant department accounts
- api_keys: Authentication tokens for API access
- scans: Scan history (PDF, PowerPoint, LaTeX)
- scan_results: Detailed scan results with compliance data
- usage_tracking: API usage for billing
"""

from sqlalchemy import (
    Column,
    String,
    Integer,
    BigInteger,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    JSON,
    Enum as SQLEnum,
    Index,
    UniqueConstraint,
    CheckConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship, DeclarativeBase, validates
from sqlalchemy.sql import func
from enum import Enum
import uuid

JOB_JSON = JSON().with_variant(JSONB, "postgresql")


def _lower_hex_64_constraint(column: str) -> str:
    """Portable PostgreSQL/SQLite exact lowercase SHA-256 constraint."""
    stripped = column
    for character in "0123456789abcdef":
        stripped = f"replace({stripped}, '{character}', '')"
    return f"length({column}) = 64 AND {column} = lower({column}) AND {stripped} = ''"


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 declarative base class for all models."""

    pass


# Enums
class ScanType(str, Enum):
    """Types of scans supported"""

    PDF = "PDF"
    POWERPOINT = "POWERPOINT"
    WORD = "WORD"  # Word documents (.docx)
    EXCEL = "EXCEL"  # Excel spreadsheets (.xlsx)
    LATEX = "LATEX"
    BATCH = "BATCH"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    MULTIMEDIA = "MULTIMEDIA"
    WEBSITE = "WEBSITE"
    CODE = "CODE"
    CANVAS_CONTENT = "CANVAS_CONTENT"


class ScanStatus(str, Enum):
    """Scan processing status"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RemediationOutcome(str, Enum):
    """Durable semantic outcomes for remediation attempts."""

    COMPLETED = "completed"
    NO_OP = "no_op"
    MANUAL_REQUIRED = "manual_required"
    ARTIFACT_UNAVAILABLE = "artifact_unavailable"
    REMEDIATION_FAILED = "remediation_failed"


class IssueStatus(str, Enum):
    """Issue tracking status"""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    WONT_FIX = "WONT_FIX"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class IssuePriority(str, Enum):
    """Issue priority levels"""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class UserRole(str, Enum):
    """User roles for permissions"""

    FACULTY = "faculty"  # Individual faculty member
    ADMIN = "admin"  # Department administrator
    SUPER_ADMIN = "super_admin"  # Platform administrator


class InvitationStatus(str, Enum):
    """Status of user invitations"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class AuthProvider(str, Enum):
    """Authentication providers for user login"""

    MAGIC_LINK = "magic_link"
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    API_KEY = "api_key"  # For programmatic access tracking
    LTI = "lti"  # LTI 1.3 launches from Canvas, Blackboard, etc.


# Database Models


class Department(Base):
    """Multi-tenant department accounts"""

    __tablename__ = "departments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)  # "Computer Science Department"
    institution = Column(String(255), nullable=False)  # "Harvard University"

    # Contact information
    contact_email = Column(String(255), nullable=False)
    contact_name = Column(String(255))

    # Workspace tier: "individual" (single-user) or "department" (multi-user)
    # Default tier comes from DEFAULT_DEPARTMENT_TIER. Self-hosted installs
    # default to "department" (unlimited): quotas exist as an administrative
    # governance tool (e.g. capping a department's cloud-AI spend), not as a
    # paywall. A deployment with its own custom tiers can point this env var at one.
    tier = Column(
        String(50), default=os.getenv("DEFAULT_DEPARTMENT_TIER", "department")
    )
    max_users = Column(Integer, default=5)  # User limit based on tier

    # Usage quota tracking (enforced per TIER_QUOTAS; unlimited by default)
    scans_this_month = Column(Integer, default=0)
    pages_this_month = Column(Integer, default=0)
    images_this_month = Column(Integer, default=0)  # Standalone image API calls
    quota_reset_at = Column(
        DateTime(timezone=True), nullable=True
    )  # First of next month

    # Billing
    stripe_customer_id = Column(String(255), nullable=True)
    stripe_subscription_id = Column(
        String(255), nullable=True
    )  # Stripe subscription ID
    subscription_status = Column(
        String(50), default="none"
    )  # none (no billing relationship — the core default), trial, active, cancelled, past_due

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)

    # Status and durable managed-artifact cleanup fence.
    is_active = Column(Boolean, default=True)
    artifact_cleanup_token = Column(String(64), nullable=True)
    artifact_cleanup_claimed_at = Column(DateTime(timezone=True), nullable=True)

    # Region/Country for deadline tracking
    country_code = Column(String(2), nullable=True, default="US")  # ISO 3166-1 alpha-2
    regulatory_framework = Column(String(50), nullable=True, default="US_ADA_TITLE_II")
    custom_deadline = Column(
        DateTime(timezone=True), nullable=True
    )  # Override default deadline
    timezone = Column(String(50), nullable=True, default="America/New_York")

    # BYOK (Bring Your Own Key) Configuration
    # Optional per-department AI API key override (cost attribution / isolation)
    byok_provider = Column(
        String(50), nullable=True
    )  # gemini, openai, anthropic, ollama
    byok_api_key_encrypted = Column(
        Text, nullable=True
    )  # Fernet-encrypted API key (use encryption.py to encrypt/decrypt)
    byok_configured_at = Column(
        DateTime(timezone=True), nullable=True
    )  # When API key was configured
    pilot_gemini_approved = Column(
        Boolean, default=False
    )  # Manual override: lets a department use the platform's shared Gemini key instead of configuring BYOK

    # LMS AI is a separate, explicit authorization boundary. Existing BYOK and
    # pilot settings are configuration only and never imply permission to use AI.
    lms_ai_enabled = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    lms_ai_provider = Column(String(50), nullable=True)
    lms_ai_purposes = Column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    # Optimistic concurrency token for the account-wide admin policy editor.
    lms_ai_policy_revision = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (
        CheckConstraint(
            "(artifact_cleanup_token IS NULL AND artifact_cleanup_claimed_at IS NULL) OR "
            "(artifact_cleanup_token IS NOT NULL AND artifact_cleanup_claimed_at IS NOT NULL)",
            name="ck_departments_artifact_cleanup_fence",
        ),
        CheckConstraint(
            "lms_ai_provider IS NULL OR lms_ai_provider IN "
            "('ollama', 'gemini', 'openai', 'anthropic', 'xai')",
            name="ck_departments_lms_ai_provider",
        ),
        CheckConstraint(
            "jsonb_typeof(lms_ai_purposes::jsonb) = 'array' AND "
            'lms_ai_purposes::jsonb <@ \'["remediation", "alt_text"]\'::jsonb AND '
            "jsonb_array_length(lms_ai_purposes::jsonb) = ("
            "CASE WHEN lms_ai_purposes::jsonb @> "
            "'[\"remediation\"]'::jsonb THEN 1 ELSE 0 END + "
            "CASE WHEN lms_ai_purposes::jsonb @> "
            "'[\"alt_text\"]'::jsonb THEN 1 ELSE 0 END)",
            name="ck_departments_lms_ai_purposes",
        ),
        CheckConstraint(
            "(NOT lms_ai_enabled AND lms_ai_provider IS NULL AND "
            "lms_ai_purposes::jsonb = '[]'::jsonb) OR "
            "(lms_ai_enabled AND lms_ai_provider IS NOT NULL AND "
            "jsonb_array_length(lms_ai_purposes::jsonb) > 0)",
            name="ck_departments_lms_ai_policy_consistency",
        ),
    )

    # Relationships
    users = relationship("User", back_populates="department")
    api_keys = relationship("APIKey", back_populates="department")
    scans = relationship("Scan", back_populates="department")
    remediation_artifacts = relationship(
        "RemediationArtifact", back_populates="department"
    )


class User(Base):
    """Faculty/staff user accounts"""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Authentication information
    email = Column(String(255), unique=True, nullable=False)
    google_id = Column(
        String(255), unique=True, nullable=True
    )  # Made nullable for magic link users
    microsoft_id = Column(String(255), unique=True, nullable=True)  # Microsoft OAuth ID
    lti_source = Column(
        String(255), nullable=True, index=True
    )  # Format: {issuer}:{lti_user_id}
    auth_provider = Column(
        SQLEnum(AuthProvider, values_callable=lambda x: [e.value for e in x]),
        default=AuthProvider.MAGIC_LINK,
    )  # Primary auth method - uses enum values (lowercase) to match DB

    # Email verification
    email_verified = Column(Boolean, default=False)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)

    # Profile information
    name = Column(String(255))
    picture_url = Column(String(512))

    # Department relationship
    department_id = Column(String(36), ForeignKey("departments.id"), nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.FACULTY)
    artifact_cleanup_token = Column(String(64), nullable=True)
    artifact_cleanup_claimed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(artifact_cleanup_token IS NULL AND artifact_cleanup_claimed_at IS NULL) OR "
            "(artifact_cleanup_token IS NOT NULL AND artifact_cleanup_claimed_at IS NOT NULL)",
            name="ck_users_artifact_cleanup_fence",
        ),
    )

    # Preferences
    timezone = Column(String(50), default="America/New_York")

    # Email notification preferences (granular control)
    email_scan_complete = Column(Boolean, default=True)  # When a scan finishes
    email_remediation_complete = Column(
        Boolean, default=True
    )  # When remediation finishes
    email_critical_alerts = Column(
        Boolean, default=True
    )  # Critical accessibility issues
    email_weekly_summary = Column(Boolean, default=True)  # Weekly summary reports
    email_marketing = Column(Boolean, default=False)  # Marketing/promotional emails

    # Double opt-in confirmation fields (unused by core; retained for schema stability)
    email_marketing_confirmed = Column(Boolean, default=False)
    email_marketing_confirmation_token = Column(
        String(64), unique=True, nullable=True, index=True
    )
    email_marketing_confirmed_at = Column(DateTime(timezone=True), nullable=True)

    # Weekly summary schedule (user can choose when to receive digest)
    weekly_summary_day = Column(Integer, default=0)  # 0=Monday, 6=Sunday
    weekly_summary_hour = Column(Integer, default=9)  # 0-23 UTC

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)
    lti_reauthorization_required = Column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

    # Account deletion / deactivation
    deactivated_at = Column(DateTime(timezone=True), nullable=True)
    deletion_requested_at = Column(DateTime(timezone=True), nullable=True)
    deletion_scheduled_for = Column(
        DateTime(timezone=True), nullable=True
    )  # 30 days after GDPR confirm
    deletion_confirmation_code_hash = Column(
        String(255), nullable=True
    )  # bcrypt hash of 6-digit code
    deletion_confirmation_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    department = relationship("Department", back_populates="users")
    scans = relationship("Scan", back_populates="user")
    api_keys = relationship("APIKey", back_populates="user")
    sessions = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan"
    )


class APIKey(Base):
    """API keys for programmatic access"""

    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Key details
    key_hash = Column(
        String(255), unique=True, nullable=False
    )  # bcrypt hash of actual key
    key_prefix = Column(
        String(20), nullable=False, index=True
    )  # First 20 chars: public label plus 8 random hex characters
    name = Column(String(255))  # User-friendly name (e.g., "Production API Key")

    # Ownership
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    department_id = Column(String(36), ForeignKey("departments.id"), nullable=False)

    # Rate limiting
    rate_limit_per_hour = Column(Integer, default=100)  # 100 requests/hour default

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)

    # Relationships
    user = relationship("User", back_populates="api_keys")
    department = relationship("Department", back_populates="api_keys")
    usage_records = relationship("UsageTracking", back_populates="api_key")


class MagicLink(Base):
    """Magic link tokens for passwordless email authentication"""

    __tablename__ = "magic_links"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Target email (not linked to user yet - user may not exist)
    email = Column(String(255), nullable=False, index=True)

    # Token storage (bcrypt hash of the actual token sent via email)
    token_hash = Column(String(255), unique=True, nullable=False)

    # Expiration (default 15 minutes)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # One-time use tracking
    used_at = Column(DateTime(timezone=True), nullable=True)

    # Signup profile data (stored on magic link, applied when account is created)
    signup_name = Column(String(100), nullable=True)
    signup_institution = Column(String(200), nullable=True)

    # Metadata for security logging
    ip_address = Column(String(45), nullable=True)  # IPv4/IPv6
    user_agent = Column(String(512), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    """User sessions for cookie-based authentication"""

    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # User relationship
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Token storage
    refresh_token_hash = Column(
        String(255), unique=True, nullable=False
    )  # bcrypt hash of current refresh token
    previous_refresh_token_hash = Column(String(255), nullable=True)
    refresh_grace_expires_at = Column(DateTime(timezone=True), nullable=True)
    refresh_replay_used_at = Column(DateTime(timezone=True), nullable=True)
    refresh_replay_ciphertext = Column(Text, nullable=True)
    access_token_jti = Column(
        String(36), nullable=False
    )  # JWT ID of current access token

    # Session validity
    expires_at = Column(DateTime(timezone=True), nullable=False)  # 7 days default
    revoked_at = Column(DateTime(timezone=True), nullable=True)  # Logout timestamp

    # Activity tracking
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    # Security metadata
    ip_address = Column(String(45), nullable=True)  # IPv4/IPv6
    user_agent = Column(String(512), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="sessions")


class Scan(Base):
    """Scan history (PDF, PowerPoint, LaTeX)"""

    __tablename__ = "scans"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Scan details
    scan_type = Column(SQLEnum(ScanType), nullable=False)
    status = Column(SQLEnum(ScanStatus), default=ScanStatus.PENDING)
    remediation_outcome = Column(String(32), nullable=True)

    # File information
    file_name = Column(String(512), nullable=False)
    file_size_bytes = Column(Integer)
    file_hash = Column(String(64))  # SHA-256 hash for deduplication

    # Ownership
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    department_id = Column(String(36), ForeignKey("departments.id"), nullable=False)
    artifact_cleanup_token = Column(String(64), nullable=True)
    artifact_cleanup_claimed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(artifact_cleanup_token IS NULL AND artifact_cleanup_claimed_at IS NULL) OR "
            "(artifact_cleanup_token IS NOT NULL AND artifact_cleanup_claimed_at IS NOT NULL)",
            name="ck_scans_artifact_cleanup_fence",
        ),
    )

    # Processing details
    processing_time_ms = Column(Integer)  # Time taken to process
    pages = Column(Integer)  # Number of pages/slides
    progress = Column(Integer, default=0)  # Progress percentage (0-100)
    progress_message = Column(
        Text, nullable=True
    )  # Current progress message (can be long for Playwright errors)

    # Storage location (if we store files)
    storage_path = Column(String(512), nullable=True)  # S3/local path
    current_remediation_artifact_id = Column(
        String(36),
        ForeignKey("remediation_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Relationships
    user = relationship("User", back_populates="scans")
    department = relationship("Department", back_populates="scans")
    result = relationship("ScanResult", back_populates="scan", uselist=False)
    remediation_artifacts = relationship(
        "RemediationArtifact",
        back_populates="scan",
        foreign_keys="RemediationArtifact.scan_id",
    )
    current_remediation_artifact = relationship(
        "RemediationArtifact",
        foreign_keys=[current_remediation_artifact_id],
        post_update=True,
    )


class ScanResult(Base):
    """Detailed scan results with compliance data"""

    __tablename__ = "scan_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Link to scan
    scan_id = Column(String(36), ForeignKey("scans.id"), unique=True, nullable=False)

    # Compliance scoring
    compliance_score = Column(Float, nullable=False)  # 0-100
    wcag_level = Column(String(10), default="AA")  # AA or AAA

    # Issue counts by severity (legacy - kept for backward compatibility)
    critical_issues = Column(Integer, default=0)
    high_issues = Column(Integer, default=0)
    medium_issues = Column(Integer, default=0)
    low_issues = Column(Integer, default=0)

    # Detailed results (JSON) - legacy
    issues = Column(JSON)  # Array of issue objects
    structure = Column(
        JSON, nullable=True
    )  # PDF structure, PPT slides, LaTeX equations

    # Multi-engine scanning (Pa11y integration)
    scan_mode = Column(String(20), nullable=True)  # quick, comprehensive, deep
    axe_results = Column(JSON, nullable=True)  # Raw axe-core results
    pa11y_results = Column(JSON, nullable=True)  # Raw Pa11y results
    ai_vision_results = Column(JSON, nullable=True)  # AI vision analysis (deep mode)
    merged_results = Column(
        JSON, nullable=True
    )  # Deduplicated results with detected_by attribution
    engines_used = Column(JSON, nullable=True)  # ["axe-core", "pa11y"]

    # Engine-specific issue counts
    axe_issues = Column(Integer, nullable=True)  # Issues found by axe-core
    pa11y_issues = Column(Integer, nullable=True)  # Issues found by Pa11y
    issues_found_by_both = Column(Integer, nullable=True)  # Duplicates
    unique_issues = Column(Integer, nullable=True)  # After deduplication

    # Coverage metrics
    estimated_coverage_pct = Column(Float, nullable=True)  # Based on engines used

    # Performance tracking
    axe_duration_ms = Column(Integer, nullable=True)  # axe-core scan duration
    pa11y_duration_ms = Column(Integer, nullable=True)  # Pa11y scan duration

    # Generated outputs
    html_output = Column(Text, nullable=True)  # Accessible HTML (for PDFs)
    suggestions = Column(JSON, nullable=True)  # AI-generated fix suggestions

    # OCR/AI usage flags
    ocr_used = Column(Boolean, default=False)  # Whether Tesseract was used
    ollama_used = Column(Boolean, default=False)  # Whether Ollama was used
    ollama_calls = Column(Integer, default=0)  # Number of Ollama API calls

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scan = relationship("Scan", back_populates="result")


class UsageTracking(Base):
    """API usage tracking for billing"""

    __tablename__ = "usage_tracking"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Usage details
    api_key_id = Column(String(36), ForeignKey("api_keys.id"), nullable=False)
    endpoint = Column(String(255), nullable=False)  # e.g., "/api/education/pdf/scan"
    scan_type = Column(SQLEnum(ScanType), nullable=True)

    # Request details
    request_ip = Column(String(45))  # IPv4/IPv6
    user_agent = Column(String(512))

    # Response details
    status_code = Column(Integer)  # HTTP status code
    response_time_ms = Column(Integer)  # Response time in milliseconds

    # Resource usage
    pages_processed = Column(Integer, default=0)
    ollama_calls = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    api_key = relationship("APIKey", back_populates="usage_records")


class ComplianceSnapshot(Base):
    """Daily compliance snapshots for historical trending"""

    __tablename__ = "compliance_snapshots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date = Column(DateTime, nullable=False)  # Date of the snapshot

    # Compliance metrics
    avg_compliance_score = Column(Float, default=0.0)
    min_compliance_score = Column(Float, nullable=True)
    max_compliance_score = Column(Float, nullable=True)

    # Scan counts
    total_scans = Column(Integer, default=0)
    scans_today = Column(Integer, default=0)

    # Issue breakdown
    critical_issues = Column(Integer, default=0)
    high_issues = Column(Integer, default=0)
    medium_issues = Column(Integer, default=0)
    low_issues = Column(Integer, default=0)
    total_issues = Column(Integer, default=0)

    # Compliance categories
    files_compliant = Column(Integer, default=0)  # Score >= 90
    files_needs_work = Column(Integer, default=0)  # Score 70-89
    files_critical = Column(Integer, default=0)  # Score < 70

    # Faculty metrics
    active_faculty = Column(Integer, default=0)
    total_faculty = Column(Integer, default=0)

    # Deadline tracking
    days_until_deadline = Column(Integer, nullable=True)
    estimated_hours_remaining = Column(Float, nullable=True)
    on_track = Column(Boolean, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    department = relationship("Department", backref="compliance_snapshots")


class IssueTracking(Base):
    """Persistent issue tracking for team collaboration"""

    __tablename__ = "issue_tracking"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scan_id = Column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )

    # Issue identification
    issue_hash = Column(String(64), nullable=False)  # Unique hash of issue
    issue_type = Column(String(100), nullable=False)
    severity = Column(SQLEnum(IssuePriority), nullable=False)
    wcag_criterion = Column(String(20), nullable=True)  # e.g., "1.1.1", "2.1.1"
    description = Column(Text, nullable=False)
    element_selector = Column(String(512), nullable=True)  # CSS selector or location
    page_number = Column(Integer, nullable=True)
    slide_number = Column(Integer, nullable=True)

    # Status tracking
    status = Column(SQLEnum(IssueStatus), default=IssueStatus.OPEN)

    # Assignment
    assigned_to = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_by = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at = Column(DateTime(timezone=True), nullable=True)

    # Resolution
    resolved_by = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    resolution_method = Column(
        String(50), nullable=True
    )  # 'auto', 'manual', 'wont_fix'

    # Collaboration
    notes = Column(Text, nullable=True)  # Team notes/discussion
    priority_override = Column(
        SQLEnum(IssuePriority), nullable=True
    )  # Manual priority adjustment

    # AI remediation tracking
    auto_fix_available = Column(Boolean, default=False)
    auto_fix_applied = Column(Boolean, default=False)
    auto_fix_result = Column(Text, nullable=True)  # JSON with fix details

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    scan = relationship("Scan", backref="tracked_issues")
    department = relationship("Department", backref="tracked_issues")
    assignee = relationship(
        "User", foreign_keys=[assigned_to], backref="assigned_issues"
    )
    assigner = relationship("User", foreign_keys=[assigned_by])
    resolver = relationship("User", foreign_keys=[resolved_by])


# =============================================================================
# Remediation Review Models (Phase: Human-in-the-Loop Review)
# =============================================================================


class ScanFix(Base):
    """Individual remediation fix with confidence scoring and review tracking.

    Stores each fix applied during AI remediation, including the method used,
    confidence level, and human review status. Low-confidence fixes are flagged
    for manual review before being included in the final remediated document.
    """

    __tablename__ = "scan_fixes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scan_id = Column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )

    # Issue identification
    issue_id = Column(String, nullable=False)
    occurrence_key = Column(String(64), nullable=False)
    category = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    location = Column(Text, nullable=True)

    # Fix content
    original_content = Column(Text, nullable=True)
    fixed_content = Column(Text, nullable=False)

    # Fix method and confidence
    fix_method = Column(
        String(20), nullable=False
    )  # rule / heuristic / ai_text / ai_vision
    provider_used = Column(String(64), nullable=True)
    model_used = Column(String(50), nullable=True)
    source_kind = Column(String(32), nullable=True)
    verification_evidence = Column(JOB_JSON, nullable=True)
    confidence = Column(Float, nullable=False, default=1.0, server_default="1.0")
    needs_review = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Review tracking
    review_status = Column(
        String(20), nullable=False, default="pending", server_default="pending"
    )  # pending / auto_approved / approved / rejected
    reviewed_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_notes = Column(Text, nullable=True)

    # WCAG metadata
    wcag_criteria = Column(String(20), nullable=True)
    page_number = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    scan = relationship("Scan", backref="fixes")
    reviewer = relationship(
        "User", foreign_keys=[reviewed_by], backref="reviewed_fixes"
    )

    __table_args__ = (
        CheckConstraint(
            "source_kind IS NULL OR source_kind = 'image_equation'",
            name="ck_scan_fixes_source_kind",
        ),
        UniqueConstraint(
            "scan_id", "occurrence_key", name="uq_scan_fixes_scan_occurrence"
        ),
        Index("idx_scan_fixes_scan_id", "scan_id"),
        Index("idx_scan_fixes_review", "needs_review", "review_status"),
        Index("idx_scan_fixes_confidence", "confidence"),
    )


class MatterhornResult(Base):
    """Matterhorn Protocol checkpoint result for PDF/UA validation.

    Stores the pass/fail/warning status for each Matterhorn Protocol
    checkpoint evaluated during PDF accessibility scanning.
    """

    __tablename__ = "matterhorn_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scan_id = Column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )

    # Checkpoint identification
    checkpoint_id = Column(String(20), nullable=False)
    checkpoint_name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False)  # pass / fail / warning
    severity = Column(String(20), nullable=True)
    details = Column(Text, nullable=True)
    page_number = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scan = relationship("Scan", backref="matterhorn_results")

    __table_args__ = (Index("idx_matterhorn_scan_id", "scan_id"),)


class ReviewAuditLog(Base):
    """Audit trail for review actions on remediation fixes.

    Records every review action (approve, reject, bulk approve, etc.)
    for compliance auditing and accountability.
    """

    __tablename__ = "review_audit_log"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scan_id = Column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    fix_id = Column(
        String(36), ForeignKey("scan_fixes.id", ondelete="SET NULL"), nullable=True
    )
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)

    # Action details
    action = Column(String(50), nullable=False)
    details = Column(JSON, nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scan = relationship("Scan", backref="review_audit_logs")
    user = relationship("User", backref="review_audit_logs")

    __table_args__ = (Index("idx_review_audit_scan_id", "scan_id"),)


class WcagGuideline(Base):
    """WCAG knowledge base for RAG-powered accessibility guidance"""

    __tablename__ = "wcag_guidelines"

    id = Column(Integer, primary_key=True)

    # Rule identification
    rule_id = Column(String(50), unique=True, nullable=False)
    wcag_criterion = Column(String(20), nullable=False)
    wcag_level = Column(String(5), nullable=False)
    wcag_version = Column(String(10), nullable=False, server_default="2.2")

    # Rule content
    title = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    principle = Column(String(50), nullable=False)
    guideline = Column(String(100), nullable=False)

    # Classification guidelines
    severity_criteria = Column(JSONB, nullable=False)
    business_impact_template = Column(Text, nullable=True)
    technical_impact = Column(Text, nullable=True)

    # Fix guidance
    fix_examples = Column(JSONB, nullable=True)
    best_practices = Column(ARRAY(Text), nullable=True)

    # Tags & metadata
    tags = Column(ARRAY(Text), server_default="{}")
    act_rule_ids = Column(ARRAY(Text), nullable=True)
    related_rules = Column(ARRAY(Text), nullable=True)

    # Vector embedding (for RAG)
    embedding = Column(JSONB, nullable=True)

    # Human-friendly descriptions
    human_issue = Column(Text, nullable=True)
    human_fixed = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_wcag_rule_id", "rule_id"),
        Index("idx_wcag_criterion", "wcag_criterion"),
        Index("idx_wcag_level", "wcag_level"),
    )


class CloudProvider(str, Enum):
    """Supported cloud providers"""

    GOOGLE = "google"
    MICROSOFT = "microsoft"
    CANVAS = "canvas"
    BLACKBOARD = "blackboard"
    MOODLE = "moodle"
    BRIGHTSPACE = "brightspace"
    LOCAL = "local"


class CloudJobType(str, Enum):
    """Types of background jobs for cloud operations"""

    SYNC = "sync"  # Sync file list from cloud
    SCAN = "scan"  # Scan a cloud file
    REMEDIATE = "remediate"  # Remediate and upload fixed file
    UPLOAD = "upload"  # Upload file to cloud
    WEBHOOK_REFRESH = "webhook_refresh"  # Renew webhook subscription
    RECONCILE = "canvas_reconcile"  # Observe an uncertain Canvas writeback
    CANVAS_CONTENT = "canvas_content"  # Remediate immutable Canvas stored HTML


class CloudJobStatus(str, Enum):
    """Status of cloud jobs"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class CloudOAuthCredentials(Base):
    """OAuth credentials for cloud integrations (Google Workspace, Microsoft 365)"""

    __tablename__ = "cloud_oauth_credentials"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )

    # Provider type
    provider = Column(String(20), nullable=False)  # google, microsoft

    # OAuth tokens (encrypted at application layer)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expires_at = Column(DateTime(timezone=True), nullable=False)

    # User info from OAuth provider
    provider_user_id = Column(String(255), nullable=True)
    provider_email = Column(String(255), nullable=True)
    provider_name = Column(String(255), nullable=True)

    # Scopes granted
    scopes = Column(JSON, nullable=True)  # List of OAuth scopes

    # Provider-specific metadata (e.g., canvas_instance_url, user info)
    provider_metadata = Column(JSON, nullable=True)

    # Connection state
    is_active = Column(Boolean, default=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    artifact_cleanup_token = Column(String(64), nullable=True)
    artifact_cleanup_claimed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(artifact_cleanup_token IS NULL AND artifact_cleanup_claimed_at IS NULL) OR "
            "(artifact_cleanup_token IS NOT NULL AND artifact_cleanup_claimed_at IS NOT NULL)",
            name="ck_cloud_oauth_credentials_artifact_cleanup_fence",
        ),
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    department = relationship("Department", backref="cloud_credentials")
    cloud_files = relationship(
        "CloudFile", back_populates="credential", cascade="all, delete-orphan"
    )
    webhook_subscriptions = relationship(
        "CloudWebhookSubscription",
        back_populates="credential",
        cascade="all, delete-orphan",
    )
    jobs = relationship("CloudJobQueue", back_populates="credential")


class CloudFile(Base):
    """Files tracked from cloud storage (Google Drive, OneDrive, SharePoint)"""

    __tablename__ = "cloud_files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    credential_id = Column(
        String(36),
        ForeignKey("cloud_oauth_credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_cleanup_token = Column(String(64), nullable=True)
    artifact_cleanup_claimed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "remediation_origin IS NULL OR "
            "remediation_origin IN ('automatic', 'manual')",
            name="ck_cloud_files_remediation_origin",
        ),
        CheckConstraint(
            "(artifact_cleanup_token IS NULL AND artifact_cleanup_claimed_at IS NULL) OR "
            "(artifact_cleanup_token IS NOT NULL AND artifact_cleanup_claimed_at IS NOT NULL)",
            name="ck_cloud_files_artifact_cleanup_fence",
        ),
        Index(
            "uq_cloud_files_canvas_content_identity",
            "department_id",
            "provider",
            "provider_parent_id",
            text("COALESCE(content_source, 'file')"),
            "provider_file_id",
            unique=True,
            postgresql_where=text(
                "provider = 'canvas' AND provider_parent_id IS NOT NULL"
            ),
        ),
    )

    # Provider-specific IDs
    provider = Column(String(20), nullable=False)  # google, microsoft
    provider_file_id = Column(String(255), nullable=False)  # Google Drive/OneDrive ID
    provider_parent_id = Column(String(255), nullable=True)  # Folder/drive ID

    # File metadata
    file_name = Column(String(512), nullable=False)
    file_type = Column(
        String(20), nullable=False
    )  # docx, pptx, xlsx, pdf, gdoc, gslide
    mime_type = Column(String(100), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    file_hash = Column(String(64), nullable=True)  # For change detection

    # Provider metadata
    web_view_link = Column(String(1024), nullable=True)
    download_link = Column(String(1024), nullable=True)

    # Version tracking
    provider_version = Column(String(100), nullable=True)  # etag for change detection
    provider_modified_at = Column(DateTime(timezone=True), nullable=True)

    # Scan state
    last_scan_id = Column(
        String(36), ForeignKey("scans.id", ondelete="SET NULL"), nullable=True
    )
    last_scanned_at = Column(DateTime(timezone=True), nullable=True)
    last_compliance_score = Column(Float, nullable=True)
    needs_rescan = Column(Boolean, default=True)

    # Remediation state
    has_remediated_version = Column(Boolean, default=False)
    remediation_origin = Column(String(16), nullable=True)
    remediated_file_id = Column(String(255), nullable=True)  # ID of fixed file
    current_remediation_artifact_id = Column(
        String(36),
        ForeignKey("remediation_artifacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Canvas content support
    content_source = Column(
        String(30), nullable=True
    )  # file, page, assignment, announcement, quiz, discussion (NULL = legacy file records, treated as 'file')
    content_body = Column(Text, nullable=True)  # Original HTML body from Canvas
    content_slug = Column(String(255), nullable=True)  # URL slug (pages only)
    content_updated_at = Column(
        DateTime(timezone=True), nullable=True
    )  # Canvas updated_at at scan time
    remediated_body = Column(Text, nullable=True)  # AI-fixed HTML body
    remediated_compliance_score = Column(
        Float, nullable=True
    )  # Score of remediated content
    remediated_issues_fixed = Column(Integer, nullable=True)
    remediated_issues_remaining = Column(Integer, nullable=True)
    provider_metadata = Column(JSON, nullable=True)  # Course name/code from Canvas
    writeback_status = Column(
        String(20), nullable=True
    )  # pending_review, approved, written_back, rejected
    writeback_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    discovered_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    department = relationship("Department", backref="cloud_files")
    credential = relationship("CloudOAuthCredentials", back_populates="cloud_files")
    last_scan = relationship("Scan", backref="cloud_file_source")
    jobs = relationship("CloudJobQueue", back_populates="cloud_file")
    remediation_artifacts = relationship(
        "RemediationArtifact",
        back_populates="cloud_file",
        foreign_keys="RemediationArtifact.cloud_file_id",
    )
    current_remediation_artifact = relationship(
        "RemediationArtifact",
        back_populates="current_for_cloud_files",
        foreign_keys=[current_remediation_artifact_id],
        post_update=True,
    )


class ContentWritebackLog(Base):
    """Audit log for Canvas content write-backs"""

    __tablename__ = "content_writeback_log"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cloud_file_id = Column(
        String(36), ForeignKey("cloud_files.id", ondelete="CASCADE"), nullable=False
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    original_body = Column(Text, nullable=False)
    remediated_body = Column(Text, nullable=False)
    approved_by = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    written_back_at = Column(DateTime(timezone=True), nullable=True)
    canvas_revision = Column(String(255), nullable=True)
    # File write-backs are bound to immutable managed bytes, never a local path.
    artifact_id = Column(
        String(36),
        ForeignKey("remediation_artifacts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    artifact_checksum = Column(String(64), nullable=True)
    correlation_id = Column(String(36), nullable=True, unique=True)
    reconciliation_status = Column(String(32), nullable=True)
    provider_result = Column(JSON, nullable=True)
    reconciliation_attempt_count = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reconciliation_lease_token = Column(String(36), nullable=True)
    reconciliation_leased_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_last_error = Column(String(128), nullable=True)
    reconciliation_resolved_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_resolution = Column(String(32), nullable=True)
    rollback_status = Column(String(20), nullable=True)  # rolled_back
    rolled_back_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    cloud_file = relationship("CloudFile", backref="writeback_logs")

    __table_args__ = (
        CheckConstraint(
            "(artifact_id IS NULL AND artifact_checksum IS NULL) OR "
            "(artifact_id IS NOT NULL AND artifact_checksum ~ '^[0-9a-f]{64}$')",
            name="ck_content_writeback_log_artifact_binding",
        ),
        CheckConstraint(
            "reconciliation_status IS NULL OR reconciliation_status IN "
            "('pending', 'committed', 'reconciliation_required', 'reconciled', "
            "'failed_manual', 'manual_required')",
            name="ck_content_writeback_log_reconciliation",
        ),
        CheckConstraint(
            "(reconciliation_lease_token IS NULL AND reconciliation_leased_at IS NULL "
            "AND reconciliation_lease_expires_at IS NULL) OR "
            "(reconciliation_lease_token IS NOT NULL AND reconciliation_leased_at IS NOT NULL "
            "AND reconciliation_lease_expires_at IS NOT NULL)",
            name="ck_content_writeback_log_reconciliation_lease",
        ),
        CheckConstraint(
            "reconciliation_attempt_count >= 0",
            name="ck_content_writeback_log_reconciliation_attempts",
        ),
        CheckConstraint(
            "reconciliation_resolution IS NULL OR reconciliation_resolution IN "
            "('confirmed', 'failed_manual', 'manual_required')",
            name="ck_content_writeback_log_reconciliation_resolution",
        ),
        CheckConstraint(
            "correlation_id IS NULL OR correlation_id ~ "
            "'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'",
            name="ck_content_writeback_log_correlation_id",
        ),
        Index(
            "ix_content_writeback_log_reconciliation_due",
            "reconciliation_status",
            "reconciliation_next_attempt_at",
        ),
    )


class CanvasContentRemediationEvidence(Base):
    """Bounded, hash-only evidence for invalidated Canvas HTML candidates."""

    __tablename__ = "canvas_content_remediation_evidence"

    id = Column(String(64), primary_key=True)
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    cloud_file_id = Column(
        String(36), ForeignKey("cloud_files.id", ondelete="CASCADE"), nullable=False
    )
    source_sha256 = Column(String(64), nullable=False)
    candidate_sha256 = Column(String(64), nullable=False)
    source_scan_id = Column(String(36), nullable=True)
    producer_job_id = Column(String(36), nullable=True)
    quarantine_reason = Column(String(64), nullable=False)
    diagnostics = Column(
        JOB_JSON, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    stored_bytes = Column(Integer, nullable=False)
    lifecycle_state = Column(
        String(16), nullable=False, default="current", server_default="current"
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"{_lower_hex_64_constraint('source_sha256')} AND "
            f"{_lower_hex_64_constraint('candidate_sha256')}",
            name="ck_canvas_content_evidence_hashes",
        ),
        CheckConstraint(
            "stored_bytes BETWEEN 1 AND 4096",
            name="ck_canvas_content_evidence_size",
        ),
        CheckConstraint(
            "length(quarantine_reason) BETWEEN 1 AND 64",
            name="ck_canvas_content_evidence_reason",
        ),
        CheckConstraint(
            "lifecycle_state IN ('current', 'expired')",
            name="ck_canvas_content_evidence_lifecycle",
        ),
        Index(
            "ix_canvas_content_evidence_file_created",
            "cloud_file_id",
            "created_at",
        ),
        Index(
            "ix_canvas_content_evidence_department_expires",
            "department_id",
            "expires_at",
        ),
    )


class CloudSyncFolder(Base):
    """Folders selected for syncing from cloud storage"""

    __tablename__ = "cloud_sync_folders"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    credential_id = Column(
        String(36),
        ForeignKey("cloud_oauth_credentials.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Provider-specific IDs
    provider = Column(String(20), nullable=False)  # google, microsoft
    provider_folder_id = Column(String(255), nullable=False)  # Drive/OneDrive folder ID
    folder_name = Column(String(512), nullable=False)
    folder_path = Column(Text, nullable=True)  # Human-readable path

    # Sync settings
    is_active = Column(Boolean, default=True, nullable=False)
    sync_subfolders = Column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    department = relationship("Department", backref="cloud_sync_folders")
    credential = relationship("CloudOAuthCredentials", backref="sync_folders")


class CloudWebhookSubscription(Base):
    """Webhook subscriptions for real-time file change notifications"""

    __tablename__ = "cloud_webhook_subscriptions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    credential_id = Column(
        String(36),
        ForeignKey("cloud_oauth_credentials.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider = Column(String(20), nullable=False)
    subscription_id = Column(String(255), nullable=False)  # Provider's subscription ID
    # Exact provider object originally requested for renewal (for example a Drive file ID).
    provider_resource_id = Column(String(1024), nullable=True)
    # Opaque resource identity returned by the provider for the active channel.
    provider_channel_resource_id = Column(String(1024), nullable=True)
    resource_uri = Column(String(1024), nullable=True)  # What we're watching

    # Subscription details
    expiration_time = Column(DateTime(timezone=True), nullable=False)
    notification_url = Column(String(1024), nullable=True)

    # State
    is_active = Column(Boolean, default=True)
    last_notification_at = Column(DateTime(timezone=True), nullable=True)
    last_renewed_at = Column(DateTime(timezone=True), nullable=True)
    renewal_status = Column(String(32), nullable=True)
    renewal_result = Column(JSON, nullable=True)
    pending_renewal_channel_id = Column(String(255), nullable=True, unique=True)
    pending_renewal_started_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    department = relationship("Department", backref="webhook_subscriptions")
    credential = relationship(
        "CloudOAuthCredentials", back_populates="webhook_subscriptions"
    )

    __table_args__ = (
        CheckConstraint(
            "(pending_renewal_channel_id IS NULL AND pending_renewal_started_at IS NULL) "
            "OR (pending_renewal_channel_id IS NOT NULL "
            "AND pending_renewal_started_at IS NOT NULL)",
            name="ck_cloud_webhook_pending_renewal_pair",
        ),
        CheckConstraint(
            "renewal_status NOT IN ('pending', 'requesting', 'indeterminate') "
            "OR (pending_renewal_channel_id IS NOT NULL "
            "AND pending_renewal_started_at IS NOT NULL)",
            name="ck_cloud_webhook_pending_renewal_status",
        ),
        Index(
            "uq_cloud_webhook_initial_intent",
            "department_id",
            "credential_id",
            "provider",
            "provider_resource_id",
            "notification_url",
            unique=True,
            postgresql_where=text(
                "provider = 'google' AND renewal_status IN "
                "('requesting', 'indeterminate', 'created', 'renewed')"
            ),
            sqlite_where=text(
                "provider = 'google' AND renewal_status IN "
                "('requesting', 'indeterminate', 'created', 'renewed')"
            ),
        ),
    )


class CloudJobQueue(Base):
    """Durable, atomically claimed background job queue."""

    __tablename__ = "cloud_job_queue"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    job_type = Column(String(50), nullable=False)
    cloud_file_id = Column(
        String(36), ForeignKey("cloud_files.id", ondelete="CASCADE"), nullable=True
    )
    credential_id = Column(
        String(36),
        ForeignKey("cloud_oauth_credentials.id", ondelete="CASCADE"),
        nullable=True,
    )
    provider = Column(String(20), nullable=True)
    provider_file_id = Column(String(255), nullable=True)

    payload = Column(
        JOB_JSON, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    @validates("payload")
    def validate_payload(self, _key, value):
        """Reject non-object payloads at every ORM enqueue boundary."""
        if type(value) is not dict:
            raise ValueError("job payload must be an object")
        return value

    depends_on_job_id = Column(
        String(36),
        ForeignKey(
            "cloud_job_queue.id",
            name="fk_cloud_job_queue_dependency",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    dedupe_key = Column(String(255), nullable=True)
    status = Column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    priority = Column(Integer, nullable=False, default=5, server_default="5")
    progress = Column(Integer, nullable=False, default=0, server_default="0")
    progress_message = Column(Text, nullable=True)

    execution_context = Column(
        JSON, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result_data = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    last_error_code = Column(String(128), nullable=True)
    last_error_retryable = Column(Boolean, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    max_retries = Column(Integer, nullable=False, default=3, server_default="3")

    claim_token = Column(String(36), nullable=True)
    worker_id = Column(String(255), nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)

    external_effect_state = Column(String(20), nullable=True)
    external_effect_token = Column(String(36), nullable=True)
    external_effect_started_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    scheduled_for = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    department = relationship("Department", backref="cloud_jobs")
    cloud_file = relationship("CloudFile", back_populates="jobs")
    credential = relationship("CloudOAuthCredentials", back_populates="jobs")
    dependency = relationship(
        "CloudJobQueue", remote_side=[id], foreign_keys=[depends_on_job_id]
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_cloud_job_queue_status",
        ),
        CheckConstraint(
            "progress BETWEEN 0 AND 100", name="ck_cloud_job_queue_progress"
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_cloud_job_queue_payload_object",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            "attempt_count >= 0 AND max_retries >= 0",
            name="ck_cloud_job_queue_attempts",
        ),
        CheckConstraint(
            "depends_on_job_id IS NULL OR depends_on_job_id <> id",
            name="ck_cloud_job_queue_not_self_dependent",
        ),
        CheckConstraint(
            "(status = 'processing' AND claim_token IS NOT NULL AND worker_id IS NOT NULL "
            "AND claimed_at IS NOT NULL AND heartbeat_at IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'processing' AND claim_token IS NULL AND worker_id IS NULL "
            "AND claimed_at IS NULL AND heartbeat_at IS NULL AND lease_expires_at IS NULL)",
            name="ck_cloud_job_queue_claim_state",
        ),
        CheckConstraint(
            "status NOT IN ('completed', 'failed') OR completed_at IS NOT NULL",
            name="ck_cloud_job_queue_terminal",
        ),
        CheckConstraint(
            "external_effect_state IS NULL OR external_effect_state IN "
            "('requesting', 'confirmed', 'indeterminate')",
            name="ck_cloud_job_queue_external_effect_state",
        ),
        CheckConstraint(
            "(external_effect_state IS NULL AND external_effect_token IS NULL AND "
            "external_effect_started_at IS NULL) OR (external_effect_state IS NOT NULL "
            "AND external_effect_token IS NOT NULL AND external_effect_started_at IS NOT NULL)",
            name="ck_cloud_job_queue_external_effect_pair",
        ),
        CheckConstraint(
            "job_type = 'upload' OR (external_effect_state IS NULL AND "
            "external_effect_token IS NULL AND external_effect_started_at IS NULL)",
            name="ck_cloud_job_queue_external_effect_upload_only",
        ),
        Index(
            "ix_cloud_job_queue_claim",
            "status",
            "scheduled_for",
            "priority",
            "created_at",
        ),
        Index("ix_cloud_job_queue_lease", "status", "lease_expires_at"),
        Index("ix_cloud_job_queue_dependency", "depends_on_job_id"),
        Index(
            "uq_cloud_job_queue_active_dedupe",
            "department_id",
            "job_type",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "dedupe_key IS NOT NULL AND status IN ('pending', 'processing')"
            ),
        ),
    )


class WorkerHeartbeat(Base):
    """Liveness and drain state for a standalone durable queue worker."""

    __tablename__ = "worker_heartbeats"

    worker_id = Column(String(255), primary_key=True)
    status = Column(String(20), nullable=False, server_default="running")
    started_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    heartbeat_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    stopped_at = Column(DateTime(timezone=True), nullable=True)
    jobs_claimed = Column(Integer, nullable=False, server_default="0", default=0)
    jobs_completed = Column(Integer, nullable=False, server_default="0", default=0)
    jobs_failed = Column(Integer, nullable=False, server_default="0", default=0)
    metadata_json = Column(
        JOB_JSON, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'draining', 'stopped')",
            name="ck_worker_heartbeats_status",
        ),
        Index("ix_worker_heartbeats_liveness", "status", "heartbeat_at"),
    )


class RemediationArtifact(Base):
    """Managed, immutable output bytes produced by a remediation job."""

    __tablename__ = "remediation_artifacts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36),
        ForeignKey(
            "departments.id",
            name="fk_remediation_artifacts_department",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    scan_id = Column(
        String(36),
        ForeignKey(
            "scans.id", name="fk_remediation_artifacts_scan", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    cloud_file_id = Column(
        String(36),
        ForeignKey(
            "cloud_files.id",
            name="fk_remediation_artifacts_cloud_file",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    remediation_job_id = Column(
        String(36),
        ForeignKey(
            "cloud_job_queue.id",
            name="fk_remediation_artifacts_remediation_job",
            ondelete="RESTRICT",
        ),
        nullable=True,
        unique=True,
    )
    created_by_id = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    provider = Column(String(20), nullable=False)
    scan_type = Column(String(32), nullable=False)
    publication_token = Column(String(64), nullable=True, unique=True)
    publication_heartbeat_at = Column(
        DateTime(timezone=True), nullable=True, index=True
    )
    published_at = Column(DateTime(timezone=True), nullable=True, index=True)
    storage_backend = Column(
        String(20), nullable=False, default="local", server_default=text("'local'")
    )
    storage_key = Column(String(1024), nullable=False, unique=True)
    filename = Column(String(512), nullable=False)
    mime_type = Column(String(255), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    sha256 = Column(String(64), nullable=False)
    lifecycle_status = Column(
        String(20),
        nullable=False,
        default="staging",
        server_default=text("'staging'"),
    )
    review_status = Column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    approval_checksum = Column(String(64), nullable=True)
    approved_by_id = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_ref = Column(String(255), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_by_id = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_by_ref = Column(String(255), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    written_back_at = Column(DateTime(timezone=True), nullable=True)
    cleanup_claimed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    cleanup_reason = Column(String(64), nullable=True)
    cleanup_owner = Column(String(255), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    provider_result = Column(JSON, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "provider IN ('google', 'microsoft', 'canvas', 'blackboard', "
            "'moodle', 'brightspace', 'local')",
            name="ck_remediation_artifacts_provider",
        ),
        CheckConstraint(
            "((provider = 'local' AND cloud_file_id IS NULL AND "
            "remediation_job_id IS NULL) OR "
            "(provider <> 'local' AND cloud_file_id IS NOT NULL)) AND "
            "(remediation_job_id IS NULL OR cloud_file_id IS NOT NULL)",
            name="ck_remediation_artifacts_provider_authority",
        ),
        CheckConstraint(
            "scan_type IN ('PDF', 'POWERPOINT', 'WORD', 'EXCEL', 'LATEX', "
            "'IMAGE', 'WEBSITE', 'CANVAS_CONTENT')",
            name="ck_remediation_artifacts_scan_type",
        ),
        CheckConstraint(
            "(lifecycle_status = 'staging' AND publication_token IS NOT NULL AND "
            "publication_heartbeat_at IS NOT NULL) OR "
            "(lifecycle_status <> 'staging' AND publication_token IS NULL AND "
            "publication_heartbeat_at IS NULL)",
            name="ck_remediation_artifacts_publication_lease",
        ),
        CheckConstraint(
            "storage_backend = 'local'",
            name="ck_remediation_artifacts_storage_backend",
        ),
        CheckConstraint(
            "storage_key <> '' AND storage_key NOT LIKE '/%' AND "
            "storage_key NOT LIKE '%..%' AND storage_key NOT LIKE '%\\\\%'",
            name="ck_remediation_artifacts_storage_key",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_remediation_artifacts_size"),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_remediation_artifacts_sha256",
        ),
        CheckConstraint(
            "lifecycle_status IN "
            "('available', 'staging', 'expired', 'deleted', 'superseded')",
            name="ck_remediation_artifacts_lifecycle",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_remediation_artifacts_review",
        ),
        CheckConstraint(
            "(review_status = 'pending' AND approval_checksum IS NULL AND "
            "approved_by_id IS NULL AND approved_by_ref IS NULL AND "
            "approved_at IS NULL AND rejected_by_id IS NULL AND "
            "rejected_by_ref IS NULL AND rejected_at IS NULL) OR "
            "(review_status = 'approved' AND approval_checksum IS NOT NULL AND "
            "approved_by_ref IS NOT NULL AND approved_by_ref <> '' AND "
            "approved_at IS NOT NULL AND rejected_by_id IS NULL AND "
            "rejected_by_ref IS NULL AND rejected_at IS NULL) OR "
            "(review_status = 'rejected' AND approval_checksum IS NULL AND "
            "approved_by_id IS NULL AND approved_by_ref IS NULL AND "
            "approved_at IS NULL AND rejected_by_ref IS NOT NULL AND "
            "rejected_by_ref <> '' AND rejected_at IS NOT NULL)",
            name="ck_remediation_artifacts_review_metadata",
        ),
        CheckConstraint(
            "written_back_at IS NULL OR review_status = 'approved'",
            name="ck_remediation_artifacts_written",
        ),
        CheckConstraint(
            "deleted_at IS NULL OR lifecycle_status = 'deleted'",
            name="ck_remediation_artifacts_deleted",
        ),
        CheckConstraint(
            "(cleanup_claimed_at IS NULL AND cleanup_reason IS NULL AND "
            "cleanup_owner IS NULL) OR (cleanup_claimed_at IS NOT NULL AND "
            "cleanup_reason IS NOT NULL AND cleanup_reason <> '' AND "
            "cleanup_owner IS NOT NULL AND cleanup_owner <> '')",
            name="ck_remediation_artifacts_cleanup_claim",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_remediation_artifacts_expiry",
        ),
        Index(
            "ix_remediation_artifacts_department_lifecycle_expires",
            "department_id",
            "lifecycle_status",
            "expires_at",
        ),
        Index("ix_remediation_artifacts_scan_created", "scan_id", "created_at"),
        Index(
            "ix_remediation_artifacts_cloud_file_review",
            "cloud_file_id",
            "review_status",
        ),
        Index(
            "ix_remediation_artifacts_staging_heartbeat",
            "lifecycle_status",
            "publication_heartbeat_at",
        ),
    )

    department = relationship("Department", back_populates="remediation_artifacts")
    scan = relationship(
        "Scan", back_populates="remediation_artifacts", foreign_keys=[scan_id]
    )
    cloud_file = relationship(
        "CloudFile",
        back_populates="remediation_artifacts",
        foreign_keys=[cloud_file_id],
    )
    remediation_job = relationship("CloudJobQueue", foreign_keys=[remediation_job_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    rejected_by = relationship("User", foreign_keys=[rejected_by_id])
    current_for_cloud_files = relationship(
        "CloudFile",
        back_populates="current_remediation_artifact",
        foreign_keys="CloudFile.current_remediation_artifact_id",
    )


class ArtifactOrphanQuarantine(Base):
    """Unknown artifact-root file retained for explicit human review."""

    __tablename__ = "artifact_orphan_quarantine"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    intent_token = Column(String(32), nullable=False)
    original_key = Column(String(1024), nullable=False, unique=True)
    quarantine_key = Column(String(1024), nullable=False, unique=True)
    size_bytes = Column(BigInteger, nullable=False)
    source_mtime = Column(DateTime(timezone=True), nullable=False)
    source_mtime_ns = Column(BigInteger, nullable=False)
    source_device = Column(BigInteger, nullable=False)
    source_inode = Column(BigInteger, nullable=False)
    kind = Column(String(32), nullable=False, server_default="regular_file")
    status = Column(String(32), nullable=False, server_default="pending_move")
    reason = Column(String(128), nullable=False)
    recovery_error = Column(String(128), nullable=True)
    quarantined_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(String(255), nullable=True)
    purge_claimed_at = Column(DateTime(timezone=True), nullable=True)
    purge_token = Column(String(32), nullable=True)
    purged_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_artifact_orphan_quarantine_size"),
        CheckConstraint(
            "kind IN ('regular_file')",
            name="ck_artifact_orphan_quarantine_kind",
        ),
        CheckConstraint(
            "status IN ('pending_move', 'quarantined', 'restore_required', "
            "'reviewed', 'purging', 'purged')",
            name="ck_artifact_orphan_quarantine_status",
        ),
        CheckConstraint(
            "(purge_claimed_at IS NULL AND purge_token IS NULL) OR "
            "(purge_claimed_at IS NOT NULL AND purge_token IS NOT NULL "
            "AND length(purge_token) = 32)",
            name="ck_artifact_orphan_quarantine_purge_claim",
        ),
        CheckConstraint(
            "(status IN ('pending_move', 'quarantined') "
            "AND reviewed_at IS NULL AND reviewed_by IS NULL "
            "AND purge_claimed_at IS NULL AND purge_token IS NULL "
            "AND purged_at IS NULL) OR "
            "(status = 'restore_required' AND purged_at IS NULL AND "
            "((reviewed_at IS NULL AND reviewed_by IS NULL "
            "AND purge_claimed_at IS NULL AND purge_token IS NULL) OR "
            "(reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND purge_claimed_at IS NOT NULL AND purge_token IS NOT NULL))) OR "
            "(status = 'reviewed' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND purge_claimed_at IS NULL AND purge_token IS NULL "
            "AND purged_at IS NULL) OR "
            "(status = 'purging' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND purge_claimed_at IS NOT NULL AND purge_token IS NOT NULL "
            "AND purged_at IS NULL) OR "
            "(status = 'purged' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND purge_claimed_at IS NOT NULL AND purge_token IS NOT NULL "
            "AND purged_at IS NOT NULL)",
            name="ck_artifact_orphan_quarantine_review",
        ),
        Index(
            "ix_artifact_orphan_quarantine_status_age",
            "status",
            "quarantined_at",
        ),
    )


class MaintenanceCursor(Base):
    """Durable progress for bounded maintenance traversals."""

    __tablename__ = "maintenance_cursors"

    key = Column(String(128), primary_key=True)
    cursor_json = Column(
        JOB_JSON, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EmailAlertSettings(Base):
    """Email notification preferences per department/user"""

    __tablename__ = "email_alert_settings"
    __table_args__ = (
        CheckConstraint(
            "weekly_summary_day BETWEEN 0 AND 6",
            name="ck_email_alert_settings_weekly_summary_day_range",
        ),
        CheckConstraint(
            "weekly_summary_hour BETWEEN 0 AND 23",
            name="ck_email_alert_settings_weekly_summary_hour_range",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )  # NULL = department-wide

    # Alert types
    alert_on_scan_complete = Column(Boolean, default=True)
    alert_on_new_issues = Column(Boolean, default=True)
    alert_on_critical_issues = Column(Boolean, default=True)
    alert_weekly_summary = Column(Boolean, default=True)

    # Delivery preferences
    email_addresses = Column(JSON, nullable=True)  # List of email addresses
    min_severity = Column(String(20), default="medium")  # Only alert >= this severity

    # Quiet hours
    quiet_hours_start = Column(String(5), nullable=True)  # HH:MM format
    quiet_hours_end = Column(String(5), nullable=True)
    timezone = Column(String(50), default="America/New_York")

    # Weekly summary schedule
    weekly_summary_day = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )  # 0=Monday, 6=Sunday
    weekly_summary_hour = Column(
        Integer, nullable=False, default=9, server_default=text("9")
    )  # 0-23 UTC

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    department = relationship("Department", backref="email_alert_settings")
    user = relationship("User", backref="email_alert_settings")


class UserInvitation(Base):
    """Pending user invitations for department faculty/admin"""

    __tablename__ = "user_invitations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )

    # Invitation details
    email = Column(String(255), nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.FACULTY)
    token = Column(String(64), unique=True, nullable=False)  # Secure random token

    # Inviter tracking
    invited_by = Column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Status
    status = Column(
        SQLEnum(InvitationStatus, values_callable=lambda x: [e.value for e in x]),
        default=InvitationStatus.PENDING,
    )

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)  # Default 7 days
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    department = relationship("Department", backref="invitations")
    inviter = relationship(
        "User", foreign_keys=[invited_by], backref="sent_invitations"
    )


# =============================================================================
# LTI Registration Models (Multi-tenant LTI support)
# =============================================================================


class LTIPlatform(str, Enum):
    """Supported LTI platforms"""

    CANVAS = "canvas"
    BLACKBOARD = "blackboard"
    MOODLE = "moodle"
    BRIGHTSPACE = "brightspace"


class LTIRegistration(Base):
    """
    LTI 1.3 tool registration linking LTI client IDs to departments.

    This enables multi-tenant LTI support where each institution's
    LTI tool registration is linked to their department account.
    """

    __tablename__ = "lti_registrations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )

    # LTI Platform identification
    platform = Column(SQLEnum(LTIPlatform), nullable=False)
    platform_name = Column(String(255), nullable=True)  # Human-readable name

    # LTI 1.3 registration details
    issuer = Column(
        String(512), nullable=False
    )  # LTI issuer URL (e.g., https://blackboard.institution.edu)
    client_id = Column(
        String(255), nullable=False
    )  # LTI client_id assigned by platform
    deployment_id = Column(String(255), nullable=True)  # Optional deployment ID

    # Platform endpoints (for tool-initiated flows)
    auth_login_url = Column(String(1024), nullable=True)  # OIDC login endpoint
    auth_token_url = Column(String(1024), nullable=True)  # Token endpoint
    jwks_url = Column(String(1024), nullable=True)  # Platform's JWKS endpoint

    # Our tool's key pair (for signing messages)
    public_key_pem = Column(Text, nullable=True)  # RSA public key
    private_key_pem = Column(Text, nullable=True)  # RSA private key (encrypted)

    # Configuration metadata
    scopes = Column(JSON, nullable=True)  # Granted LTI scopes
    capabilities = Column(JSON, nullable=True)  # deep_linking, grade_passback, etc.

    # Status
    is_active = Column(Boolean, default=True)
    last_launch_at = Column(DateTime(timezone=True), nullable=True)
    launch_count = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    department = relationship("Department", backref="lti_registrations")

    # Unique constraint on platform + issuer + client_id
    __table_args__ = (
        Index(
            "idx_lti_registrations_lookup",
            "platform",
            "issuer",
            "client_id",
            unique=True,
        ),
        Index("idx_lti_registrations_department", "department_id"),
    )


class LTIAGSContext(Base):
    """Stored AGS context for async grade passback."""

    __tablename__ = "lti_ags_context"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_id = Column(String(36), ForeignKey("departments.id"), nullable=False)
    course_id = Column(String(255), nullable=False)
    lineitem_url = Column(String(1024), nullable=True)
    token_endpoint = Column(String(1024), nullable=False)
    client_id = Column(String(255), nullable=False)
    scopes = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("department_id", "course_id", name="uq_ags_dept_course"),
    )


# =============================================================================
# Security & Abuse Detection Models
# =============================================================================


class SignupLog(Base):
    """
    Log of signup attempts for abuse detection.

    Stores hashed values only for privacy:
    - IP addresses are hashed
    - Fingerprints are hashed
    - Only email domain is stored (not full email)
    """

    __tablename__ = "signup_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Privacy-preserving identifiers (hashed)
    email_domain = Column(
        String(255), nullable=False
    )  # Just the domain, e.g., "stanford.edu"
    ip_hash = Column(String(64), nullable=False)  # SHA256 hash of IP
    user_agent_hash = Column(String(64), nullable=True)  # SHA256 hash of user agent
    fingerprint_hash = Column(
        String(64), nullable=True
    )  # SHA256 hash of device fingerprint

    # Outcome
    success = Column(Boolean, default=False)
    failure_reason = Column(
        String(255), nullable=True
    )  # e.g., "abuse_detected", "validation_failed"

    # Abuse signals detected
    abuse_signals = Column(JSON, nullable=True)  # List of detected signals

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SecurityScanResult(Base):
    """
    Results of security scans on uploaded documents.

    Tracks validation results for audit and incident response.
    """

    __tablename__ = "security_scan_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scan_id = Column(
        String(36), ForeignKey("scans.id", ondelete="CASCADE"), nullable=True
    )
    department_id = Column(
        String(36), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False
    )

    # File info
    filename = Column(String(255), nullable=False)
    file_hash = Column(String(64), nullable=False)  # SHA256
    file_type = Column(String(50), nullable=False)  # Detected type
    file_size = Column(Integer, nullable=False)

    # Security assessment
    is_safe = Column(Boolean, nullable=False)
    threat_level = Column(
        String(20), nullable=False
    )  # safe, low, medium, high, critical
    findings = Column(JSON, nullable=True)  # List of SecurityFinding objects

    # Actions taken
    was_sanitized = Column(Boolean, default=False)
    was_blocked = Column(Boolean, default=False)
    blocked_reason = Column(String(255), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    scan = relationship("Scan", backref="security_scan_results")
    department = relationship("Department", backref="security_scan_results")


class AuditLogAction(str, Enum):
    """Types of audit log actions."""

    # Authentication
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    MAGIC_LINK_REQUEST = "magic_link_request"
    MAGIC_LINK_VERIFY = "magic_link_verify"

    # API Keys
    API_KEY_CREATE = "api_key_create"
    API_KEY_REVOKE = "api_key_revoke"

    # Sessions
    SESSION_CREATE = "session_create"
    SESSION_REVOKE = "session_revoke"
    SESSION_REVOKE_ALL = "session_revoke_all"

    # Users
    USER_INVITE_SENT = "user_invite_sent"
    USER_INVITE_ACCEPTED = "user_invite_accepted"
    USER_ROLE_CHANGE = "user_role_change"
    USER_PROFILE_UPDATE = "user_profile_update"

    # Cloud Integrations
    CLOUD_CONNECT = "cloud_connect"
    CLOUD_DISCONNECT = "cloud_disconnect"

    # Account Lifecycle
    ACCOUNT_DEACTIVATE = "account_deactivate"
    ACCOUNT_DELETION_REQUESTED = "account_deletion_requested"
    ACCOUNT_DELETION_CONFIRMED = "account_deletion_confirmed"
    ACCOUNT_DELETION_CANCELLED = "account_deletion_cancelled"
    ACCOUNT_DELETION_EXECUTED = "account_deletion_executed"
    ACCOUNT_DATA_EXPORT = "account_data_export"

    # Scans
    SCAN_START = "scan_start"
    SCAN_COMPLETE = "scan_complete"

    # Remediation
    REMEDIATION_COMPLETE = "remediation_complete"
    REMEDIATION_FAILED = "remediation_failed"
    REMEDIATION_DOWNLOAD = "remediation_download"

    # LMS AI policy governance and execution
    LMS_AI_POLICY_UPDATE = "lms_ai_policy_update"
    LMS_AI_EXECUTION = "lms_ai_execution"


class AuditLogStatus(str, Enum):
    """Status of the audit log action."""

    SUCCESS = "success"
    FAILURE = "failure"


class AuditLog(Base):
    """
    Audit log for security-sensitive actions.

    Records all authentication, authorization, and sensitive operations
    for compliance and security auditing purposes.
    """

    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Who performed the action
    user_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    department_id = Column(String(36), ForeignKey("departments.id"), nullable=True)

    # What action was performed
    action = Column(String(100), nullable=False)  # AuditLogAction value
    resource_type = Column(String(50), nullable=True)  # user, api_key, session, etc.
    resource_id = Column(String(36), nullable=True)  # ID of affected resource

    # Where the action came from
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6
    user_agent = Column(String(512), nullable=True)

    # Additional context
    details = Column(JSON, nullable=True)  # Action-specific details
    status = Column(String(20), nullable=False)  # success, failure

    # When
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", backref="audit_logs", foreign_keys=[user_id])
    department = relationship(
        "Department", backref="audit_logs", foreign_keys=[department_id]
    )


class DeletedEmail(Base):
    """
    Stores SHA-256 hashes of deleted/deactivated account emails to prevent
    re-registration abuse. The original email is never stored — only the hash.

    For deactivated accounts: 90-day cooldown before re-registration allowed.
    For GDPR-deleted accounts: permanent block (cooldown_until is NULL).
    """

    __tablename__ = "deleted_emails"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email_hash = Column(
        String(64), unique=True, nullable=False, index=True
    )  # SHA-256 hex
    deletion_type = Column(
        String(20), nullable=False
    )  # "deactivated" or "gdpr_deleted"
    deleted_at = Column(DateTime(timezone=True), server_default=func.now())
    cooldown_until = Column(
        DateTime(timezone=True), nullable=True
    )  # NULL = permanent block
    previous_tier = Column(String(50), nullable=True)
    reason = Column(Text, nullable=True)


# Indexes for performance
from sqlalchemy import Index  # noqa: E402

# Fast lookup by email (login)
Index("idx_users_email", User.email)
Index("idx_users_google_id", User.google_id)

# Fast lookup by department (multi-tenant isolation)
Index("idx_scans_department_id", Scan.department_id)
Index("idx_users_department_id", User.department_id)

# Fast lookup by date (scan history)
Index("idx_scans_created_at", Scan.created_at.desc())

# Fast lookup by API key
Index("idx_api_keys_key_hash", APIKey.key_hash)

# Fast usage queries (billing)
Index(
    "idx_usage_tracking_api_key_created",
    UsageTracking.api_key_id,
    UsageTracking.created_at.desc(),
)

# Fast invitation token lookup
Index("idx_user_invitations_token", UserInvitation.token)
Index(
    "idx_user_invitations_department_status",
    UserInvitation.department_id,
    UserInvitation.status,
)

# Fast audit log queries
Index(
    "idx_audit_logs_user_created",
    AuditLog.user_id,
    AuditLog.created_at.desc(),
)
Index(
    "idx_audit_logs_department_created",
    AuditLog.department_id,
    AuditLog.created_at.desc(),
)
Index("idx_audit_logs_action", AuditLog.action)

# Security abuse detection indexes
Index("idx_signup_logs_ip_hash", SignupLog.ip_hash)
Index("idx_signup_logs_email_domain", SignupLog.email_domain)
Index("idx_signup_logs_created_at", SignupLog.created_at.desc())
Index("idx_signup_logs_fingerprint_hash", SignupLog.fingerprint_hash)

# Security scan results lookup
Index("idx_security_scan_results_file_hash", SecurityScanResult.file_hash)
Index(
    "idx_security_scan_results_department",
    SecurityScanResult.department_id,
    SecurityScanResult.created_at.desc(),
)

# Magic link indexes
Index("idx_magic_links_email", MagicLink.email)
Index("idx_magic_links_token_hash", MagicLink.token_hash)
Index("idx_magic_links_expires_at", MagicLink.expires_at)

# User session indexes
Index("idx_user_sessions_user_id", UserSession.user_id)
Index("idx_user_sessions_refresh_token_hash", UserSession.refresh_token_hash)
Index("idx_user_sessions_expires_at", UserSession.expires_at)

# User authentication indexes
Index("idx_users_microsoft_id", User.microsoft_id)
Index("idx_users_auth_provider", User.auth_provider)
