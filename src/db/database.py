"""
Database Connection and Session Management

Provides SQLAlchemy engine, session factory, and helper functions
for database operations.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
import os
from typing import Generator

from src.db.models import Base

# Database URL from environment - MUST be set via environment variable
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL environment variable must be set. "
        "See backend/.env.example for template."
    )

# Validate no unsafe defaults
unsafe_patterns = ["dev_password_change_in_prod", "aelira_password", "change_me"]
for pattern in unsafe_patterns:
    if pattern in DATABASE_URL:
        raise ValueError(
            f"Unsafe DATABASE_URL detected (contains '{pattern}'). "
            "Set a secure DATABASE_URL via environment variable."
        )

# Create engine
DATABASE_ISOLATION_LEVEL = "READ COMMITTED"
engine = create_engine(
    DATABASE_URL,
    isolation_level=DATABASE_ISOLATION_LEVEL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",  # Log SQL queries in dev
    pool_size=10,  # Connection pool size (per worker process)
    max_overflow=20,  # Max connections beyond pool_size
    pool_pre_ping=True,  # Test connections before using
    pool_recycle=3600,  # Recycle connections after 1 hour to avoid stale connections
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """
    Initialize database - create all tables

    Run this once when setting up the database.
    """
    Base.metadata.create_all(bind=engine)
    print("[Database] All tables created successfully")


def drop_all():
    """
    Drop all tables (USE WITH CAUTION - for testing only)
    """
    Base.metadata.drop_all(bind=engine)
    print("[Database] All tables dropped")


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """
    Get database session (context manager)

    Usage:
        with get_db() as db:
            user = db.query(User).filter_by(email="test@example.com").first()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def get_db_dependency():
    """
    FastAPI dependency for database sessions

    Usage in FastAPI routes:
        @app.get("/users")
        def get_users(db: Session = Depends(get_db_dependency)):
            return db.query(User).all()
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
