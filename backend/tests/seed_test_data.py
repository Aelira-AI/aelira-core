"""
Seed test database with mock department and user for development/testing.

This script is run after migrations in CI to ensure mock credentials work.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os


def seed_test_data():
    """Create mock department and user for tests."""
    from src.db.models import Department, User, UserRole

    database_url = os.getenv(
        "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aelira_test"
    )

    print(f"\n🌱 Seeding test database at: {database_url}")

    engine = create_engine(database_url)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        # Delete existing test data
        db.query(User).filter(User.id == "test-user-123").delete()
        db.query(Department).filter(Department.id == "test-dept-456").delete()
        db.commit()

        # Create mock department
        mock_dept = Department(
            id="test-dept-456",
            name="Test Department",
            institution="Test University",
            contact_email="test@university.edu",
        )
        db.add(mock_dept)
        db.flush()

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
        db.commit()

        print("✅ Test data seeded successfully!")
        print(f"   - Department: {mock_dept.id}")
        print(f"   - User: {mock_user.id}")

    except Exception as e:
        db.rollback()
        print(f"❌ Error seeding test data: {e}")
        import traceback

        traceback.print_exc()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_test_data()
