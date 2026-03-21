"""Database package - PostgreSQL models and connection management"""

from src.db.database import (
    engine,
    SessionLocal,
    init_db,
    drop_all,
    get_db,
    get_db_dependency,
)

from src.db.models import (
    Base,
    Department,
    User,
    APIKey,
    Scan,
    ScanResult,
    UsageTracking,
    ScanType,
    ScanStatus,
    UserRole,
)

__all__ = [
    # Database connection
    "engine",
    "SessionLocal",
    "init_db",
    "drop_all",
    "get_db",
    "get_db_dependency",
    # Models
    "Base",
    "Department",
    "User",
    "APIKey",
    "Scan",
    "ScanResult",
    "UsageTracking",
    # Enums
    "ScanType",
    "ScanStatus",
    "UserRole",
]
