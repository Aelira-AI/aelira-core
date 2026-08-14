"""
Pytest configuration and shared fixtures.
"""

import os
import sys
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# Skip browser/E2E/integration tests in CI environments (industry best practice - testing pyramid)
# These tests require external services and are better suited for staging/nightly runs
def pytest_collection_modifyitems(config, items):
    """Skip browser, E2E, and integration tests when running in CI."""
    if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        skip_browser = pytest.mark.skip(
            reason="Browser tests skipped in CI - require running dashboard"
        )
        skip_e2e = pytest.mark.skip(
            reason="E2E tests skipped in CI - require full environment"
        )
        skip_integration = pytest.mark.skip(
            reason="Integration tests skipped in CI - require external services"
        )
        for item in items:
            if "browser" in item.keywords:
                item.add_marker(skip_browser)
            elif "e2e" in item.keywords:
                item.add_marker(skip_e2e)
            elif "integration" in item.keywords:
                item.add_marker(skip_integration)


# Add the backend directory to Python path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Set test environment variables
os.environ["ENV"] = "test"
os.environ["DEBUG"] = "true"
os.environ["REDIS_ENABLED"] = "false"  # Disable Redis for faster tests unless needed

# Set a valid Fernet encryption key for OAuthTokenManager tests
if "TOKEN_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

# Test database URL - use existing DATABASE_URL if set (important for Docker),
# otherwise fallback to local defaults.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = os.getenv(
        "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aelira_test"
    )


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Create test database tables and mock data before running tests."""
    from src.db.models import Base, Department, User, UserRole
    from src.config.settings import get_settings

    settings = get_settings()
    print(f"\n🔧 Setting up test database at: {settings.database_url}")

    # Test connection — skip gracefully if DB is unavailable (allows pure unit tests to run)
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
        db.query(User).filter(User.id == "test-user-123").delete()
        db.query(Department).filter(Department.id == "test-dept-456").delete()
        db.commit()
        db.close()
        print("\n🧹 Cleaned up test fixtures")
    except Exception as e:
        print(f"Warning: Could not clean up test fixtures: {e}")
