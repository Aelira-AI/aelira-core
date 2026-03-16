"""
Seed Test Data - Create mock user and department for testing

This script creates the test user and department used by the API endpoints.
Run this once after database migrations.
"""

from src.db import get_db, Department, User, UserRole
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Match mock credentials in education.py
MOCK_USER_ID = "test-user-123"
MOCK_DEPARTMENT_ID = "test-dept-456"


def seed_test_data():
    """Create test department and user"""

    with get_db() as db:
        # Check if already exists
        existing_dept = db.query(Department).filter_by(id=MOCK_DEPARTMENT_ID).first()
        if existing_dept:
            logger.info(f"Test department already exists: {existing_dept.id}")
        else:
            # Create department
            dept = Department(
                id=MOCK_DEPARTMENT_ID,
                name="Test University - Computer Science",
                institution="Test University",
                contact_email="cs@test.edu",
                contact_name="Dr. Test Admin",
                tier="trial",
                max_users=50,
                trial_ends_at=datetime.utcnow() + timedelta(days=30)
            )
            db.add(dept)
            logger.info(f"✅ Created test department: {dept.id}")

        # Check if user exists
        existing_user = db.query(User).filter_by(id=MOCK_USER_ID).first()
        if existing_user:
            logger.info(f"Test user already exists: {existing_user.id}")
        else:
            # Create user
            user = User(
                id=MOCK_USER_ID,
                email="test.professor@test.edu",
                google_id="google_test_12345",
                name="Professor Test User",
                department_id=MOCK_DEPARTMENT_ID,
                role=UserRole.FACULTY
            )
            db.add(user)
            logger.info(f"✅ Created test user: {user.id}")

        db.commit()
        logger.info("✅ Test data seeded successfully!")

        # Print summary
        dept = db.query(Department).filter_by(id=MOCK_DEPARTMENT_ID).first()
        user = db.query(User).filter_by(id=MOCK_USER_ID).first()

        print("\n=== Test Data Summary ===")
        print(f"Department: {dept.name}")
        print(f"  ID: {dept.id}")
        print(f"  Tier: {dept.tier}")
        print(f"  Max Users: {dept.max_users}")
        print(f"\nUser: {user.name}")
        print(f"  ID: {user.id}")
        print(f"  Email: {user.email}")
        print(f"  Role: {user.role.value}")
        print("\n✅ Ready to test API endpoints!")


if __name__ == "__main__":
    seed_test_data()
