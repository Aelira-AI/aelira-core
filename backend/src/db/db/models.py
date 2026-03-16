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
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    JSON,
    Enum as SQLEnum,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from enum import Enum
import uuid

Base = declarative_base()


# Enums
class ScanType(str, Enum):
    """Types of scans supported"""

    PDF = "PDF"
    POWERPOINT = "POWERPOINT"
    LATEX = "LATEX"
    BATCH = "BATCH"
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    WEBSITE = "WEBSITE"
    CODE = "CODE"


class ScanStatus(str, Enum):
    """Scan processing status"""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class UserRole(str, Enum):
    """User roles for permissions"""

    FACULTY = "faculty"  # Individual faculty member
    ADMIN = "admin"  # Department administrator
    SUPER_ADMIN = "super_admin"  # Platform administrator


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

    # Subscription tier
    tier = Column(String(50), default="trial")  # trial, department, university
    max_users = Column(Integer, default=5)  # User limit based on tier

    # Billing
    stripe_customer_id = Column(String(255), nullable=True)
    subscription_status = Column(
        String(50), default="trial"
    )  # trial, active, cancelled, past_due

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)

    # Relationships
    users = relationship("User", back_populates="department")
    api_keys = relationship("APIKey", back_populates="department")
    scans = relationship("Scan", back_populates="department")


class User(Base):
    """Faculty/staff user accounts"""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Google OAuth information
    email = Column(String(255), unique=True, nullable=False)
    google_id = Column(String(255), unique=True, nullable=False)
    name = Column(String(255))
    picture_url = Column(String(512))

    # Department relationship
    department_id = Column(String(36), ForeignKey("departments.id"), nullable=False)
    role = Column(SQLEnum(UserRole), default=UserRole.FACULTY)

    # Preferences
    email_notifications = Column(Boolean, default=True)
    timezone = Column(String(50), default="America/New_York")

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)

    # Relationships
    department = relationship("Department", back_populates="users")
    scans = relationship("Scan", back_populates="user")
    api_keys = relationship("APIKey", back_populates="user")


class APIKey(Base):
    """API keys for programmatic access"""

    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Key details
    key_hash = Column(
        String(255), unique=True, nullable=False
    )  # bcrypt hash of actual key
    key_prefix = Column(
        String(20), nullable=False
    )  # First 8 chars for identification (e.g., "aelira_123...")
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


class Scan(Base):
    """Scan history (PDF, PowerPoint, LaTeX)"""

    __tablename__ = "scans"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Scan details
    scan_type = Column(SQLEnum(ScanType), nullable=False)
    status = Column(SQLEnum(ScanStatus), default=ScanStatus.PENDING)

    # File information
    file_name = Column(String(512), nullable=False)
    file_size_bytes = Column(Integer)
    file_hash = Column(String(64))  # SHA-256 hash for deduplication

    # Ownership
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    department_id = Column(String(36), ForeignKey("departments.id"), nullable=False)

    # Processing details
    processing_time_ms = Column(Integer)  # Time taken to process
    pages = Column(Integer)  # Number of pages/slides

    # Storage location (if we store files)
    storage_path = Column(String(512), nullable=True)  # S3/local path

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)

    # Relationships
    user = relationship("User", back_populates="scans")
    department = relationship("Department", back_populates="scans")
    result = relationship("ScanResult", back_populates="scan", uselist=False)


class ScanResult(Base):
    """Detailed scan results with compliance data"""

    __tablename__ = "scan_results"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Link to scan
    scan_id = Column(String(36), ForeignKey("scans.id"), unique=True, nullable=False)

    # Compliance scoring
    compliance_score = Column(Float, nullable=False)  # 0-100
    wcag_level = Column(String(10), default="AA")  # AA or AAA

    # Issue counts by severity
    critical_issues = Column(Integer, default=0)
    high_issues = Column(Integer, default=0)
    medium_issues = Column(Integer, default=0)
    low_issues = Column(Integer, default=0)

    # Detailed results (JSON)
    issues = Column(JSON)  # Array of issue objects
    structure = Column(
        JSON, nullable=True
    )  # PDF structure, PPT slides, LaTeX equations

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
