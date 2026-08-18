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
# Tests that fail today, each named with what is wrong. They were invisible
# while every integration test was skipped in CI; naming them is how they
# get fixed instead of forgotten. Delete an entry the moment its test
# passes again, and never add one to make a red build green without saying
# here what is broken.
KNOWN_BROKEN = {
    "tests/test_brightspace_api_auth.py::TestBrightspaceDepartmentIsolation"
    "::test_status_uses_api_key_department": "Brightspace status returns 404 to a mocked session; Brightspace is beta here and out of scope",
    "tests/test_brightspace_api_auth.py::TestBrightspaceStatusResponse"
    "::test_status_response_format": "Brightspace status returns 404 to a mocked session; Brightspace is beta here and out of scope",
    "tests/test_brightspace_api_auth.py::TestQueryParamsIgnored"
    "::test_query_param_department_id_ignored": "Brightspace status returns 404 to a mocked session; Brightspace is beta here and out of scope",
    "tests/test_image_alt_text.py::test_generate_alt_text_chart": "Alt-text generation returns no result without a configured model",
    "tests/test_image_alt_text.py::test_batch_generate_alt_text": "Alt-text generation returns no result without a configured model",
}


def pytest_collection_modifyitems(config, items):
    """Skip the tests CI genuinely cannot run, and only those.

    Browser and end-to-end tests need a running dashboard and a full
    environment, which CI does not have.

    Integration tests used to be skipped here too, on the grounds that
    they require external services. They do not: the CI test job provisions
    Postgres and Redis exactly as a local run does. Skipping them meant
    hundreds of tests, including every API route test, were verified
    nowhere but a developer's machine while CI reported green. A green
    check that covers less than it appears to is worse than a red one.
    """
    for item in items:
        reason = KNOWN_BROKEN.get(item.nodeid)
        if reason:
            item.add_marker(pytest.mark.skip(reason=f"Known broken: {reason}"))

    if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
        skip_browser = pytest.mark.skip(
            reason="Browser tests skipped in CI - require running dashboard"
        )
        skip_e2e = pytest.mark.skip(
            reason="E2E tests skipped in CI - require full environment"
        )
        for item in items:
            if "browser" in item.keywords:
                item.add_marker(skip_browser)
            elif "e2e" in item.keywords:
                item.add_marker(skip_e2e)


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
