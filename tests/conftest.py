"""
Pytest configuration and shared fixtures.
"""

import os
import re
import sys
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

# Skip browser/E2E/integration tests in CI environments (industry best practice - testing pyramid)
# These tests require external services and are better suited for staging/nightly runs
# Tests that fail today, each named with what is wrong. They were invisible
# while every integration test was skipped in CI; naming them is how they
# get fixed instead of forgotten. Delete an entry the moment its test
# passes again, and never add one to make a red build green without saying
# here what is broken.
KNOWN_BROKEN = {}


def pytest_collection_modifyitems(config, items):
    """Apply only explicit, individually named quarantines."""
    for item in items:
        reason = KNOWN_BROKEN.get(item.nodeid)
        if reason:
            item.add_marker(pytest.mark.skip(reason=f"Known broken: {reason}"))


# Add the backend directory to Python path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Set test environment variables
os.environ["ENV"] = "test"
os.environ["DEBUG"] = "true"
os.environ["REDIS_ENABLED"] = "false"  # Disable Redis for faster tests unless needed
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-at-least-32-bytes-long")

# Set a valid Fernet encryption key for OAuthTokenManager tests
if "TOKEN_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()


_TEST_DATABASE_NAME = re.compile(
    r"^(?:test_[a-z0-9][a-z0-9_]*|[a-z0-9][a-z0-9_]*_test)$"
)
_PRODUCTION_NAME_PARTS = {"prod", "production", "staging", "live"}
_LOCAL_DATABASE_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}


def _enabled(environment, name: str) -> bool:
    return str(environment.get(name, "")).lower() in {"1", "true", "yes"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def require_disposable_postgres_url(
    database_url: str,
    *,
    destructive: bool,
    environment=None,
) -> str:
    """Validate an unmistakably test-only PostgreSQL target without exposing auth."""
    environment = os.environ if environment is None else environment
    _require(bool(database_url), "TEST_MIGRATION_DATABASE_URL must be set explicitly")
    parsed = make_url(database_url)
    _require(
        parsed.drivername
        in {
            "postgresql",
            "postgresql+psycopg2",
            "postgresql+psycopg",
        },
        "test database must use a synchronous PostgreSQL driver",
    )
    database = parsed.database or ""
    _require(
        bool(_TEST_DATABASE_NAME.fullmatch(database)),
        f"refusing database with ambiguous test name {database!r}",
    )
    name_parts = set(re.split(r"[_\-.]+", database.lower()))
    _require(
        not name_parts.intersection(_PRODUCTION_NAME_PARTS),
        f"refusing production-shaped test database name {database!r}",
    )

    host = (parsed.host or "").lower()
    host_parts = set(re.split(r"[_\-.]+", host))
    _require(
        not host_parts.intersection(_PRODUCTION_NAME_PARTS),
        f"refusing production-shaped test database host {host!r}",
    )
    remote_allowed = _enabled(environment, "ALLOW_REMOTE_TEST_DATABASE") or (
        not destructive and _enabled(environment, "CI")
    )
    _require(
        host in _LOCAL_DATABASE_HOSTS or remote_allowed,
        "non-local test databases require ALLOW_REMOTE_TEST_DATABASE=1",
    )
    if destructive:
        _require(
            _enabled(environment, "ALLOW_DESTRUCTIVE_MIGRATION_TESTS"),
            "destructive database tests require ALLOW_DESTRUCTIVE_MIGRATION_TESTS=1",
        )
    return database_url


def _require_test_database_url(database_url: str) -> str:
    """Fence ordinary suite setup to memory SQLite or test-only PostgreSQL."""
    parsed = make_url(database_url)
    if parsed.drivername == "sqlite" and parsed.database in (None, "", ":memory:"):
        return database_url
    return require_disposable_postgres_url(database_url, destructive=False)


def _select_suite_database_url(environment) -> str:
    explicit = environment.get("TEST_DATABASE_URL")
    if explicit:
        return explicit
    if _enabled(environment, "CI") and environment.get("DATABASE_URL"):
        return environment["DATABASE_URL"]
    return "postgresql://postgres:postgres@localhost:5432/aelira_test"


# A local application DATABASE_URL is never a test fallback. CI may provide its
# isolated service URL explicitly; both paths still pass the same strict fence.
_suite_database_url = _select_suite_database_url(os.environ)
os.environ["DATABASE_URL"] = _require_test_database_url(_suite_database_url)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Create test database tables and mock data before running tests."""
    from src.db.models import Base, Department, User, UserRole
    from src.config.settings import get_settings

    settings = get_settings()
    print(f"\n🔧 Setting up test database at: {settings.database_url}")

    # Test connection — skip gracefully if DB is unavailable (allows pure unit tests to run)
    created_department = False
    created_user = False
    try:
        engine = create_engine(settings.database_url)
        engine.connect().close()
    except Exception:
        print("⚠️  Database unavailable — skipping DB setup (unit tests only)")
        yield
        return

    # Create all tables (in case migrations haven't run yet)
    Base.metadata.create_all(bind=engine)

    # Create session using raw connection
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        print("📝 Creating mock department and user for tests...")

        # Check if department already exists to avoid duplicate key errors
        existing_dept = (
            db.query(Department).filter(Department.id == "test-dept-456").first()
        )
        existing_user = db.query(User).filter(User.id == "test-user-123").first()

        if not existing_dept:
            # Create mock department
            mock_dept = Department(
                id="test-dept-456",
                name="Test Department",
                institution="Test University",
                contact_email="test@university.edu",
            )
            db.add(mock_dept)
            db.flush()  # Ensure department is in database before adding user
            created_department = True
            print("   - Created Department: test-dept-456")
        else:
            print("   - Department test-dept-456 already exists")

        if not existing_user:
            # Create mock user
            mock_user = User(
                id="test-user-123",
                email="testuser@university.edu",
                google_id="test-google-id-123",
                name="Test User",
                department_id="test-dept-456",
                role=UserRole.ADMIN,
            )
            db.add(mock_user)
            created_user = True
            print("   - Created User: test-user-123")
        else:
            print("   - User test-user-123 already exists")

        db.commit()
        print("✅ Test fixtures ready!")

    except Exception as e:
        db.rollback()
        print(f"❌ Error creating test fixtures: {e}")
        import traceback

        traceback.print_exc()
        raise  # Re-raise to fail tests if fixtures can't be created
    finally:
        db.close()

    yield

    # Clean up after all tests
    try:
        db = SessionLocal()
        if created_user:
            db.query(User).filter(User.id == "test-user-123").delete()
        if created_department:
            db.query(Department).filter(Department.id == "test-dept-456").delete()
        db.commit()
        db.close()
        print("\n🧹 Cleaned up test fixtures")
    except Exception as e:
        print(f"Warning: Could not clean up test fixtures: {e}")
