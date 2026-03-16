#!/usr/bin/env python3
"""
Create API Key for Testing

This script creates a test API key for the Aelira backend.
It will create a test user and department if they don't exist.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from sqlalchemy.orm import Session
from src.db.database import SessionLocal, engine
from src.db.models import Base, User, Department, APIKey, UserRole
from src.auth.auth_service import AuthService
from datetime import datetime, timedelta

def create_test_department(db: Session) -> Department:
    """Create or get a test department"""
    dept = db.query(Department).filter(Department.institution == "Test University").first()
    
    if dept:
        print(f"✅ Found existing test department: {dept.name} ({dept.id})")
        return dept
    
    dept = Department(
        name="Computer Science Department",
        institution="Test University",
        contact_email="admin@test.university.edu",
        contact_name="Test Admin",
        tier="department",
        max_users=50,
        subscription_status="active",
        trial_ends_at=datetime.utcnow() + timedelta(days=365),
        is_active=True
    )
    
    db.add(dept)
    db.commit()
    db.refresh(dept)
    
    print(f"✅ Created test department: {dept.name} ({dept.id})")
    return dept

def create_test_user(db: Session, department_id: str) -> User:
    """Create or get a test user"""
    user = db.query(User).filter(User.email == "test@aelira.ai").first()
    
    if user:
        print(f"✅ Found existing test user: {user.name} ({user.id})")
        return user
    
    user = User(
        email="test@aelira.ai",
        google_id="test_google_id_123",
        name="Test User",
        department_id=department_id,
        role=UserRole.ADMIN,
        is_active=True
    )
    
    db.add(user)
    db.commit()
    db.refresh(user)
    
    print(f"✅ Created test user: {user.name} ({user.id})")
    return user

def main():
    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)
    
    # Create database session
    db = SessionLocal()
    
    try:
        print("\n🔑 Creating Test API Key for Aelira Backend\n")
        print("=" * 60)
        
        # Create test department
        dept = create_test_department(db)
        
        # Create test user
        user = create_test_user(db, dept.id)
        
        # Create API key
        print("\n🔐 Generating API key...")
        api_key_obj, full_key = AuthService.create_api_key(
            db=db,
            user_id=user.id,
            department_id=dept.id,
            name="Test API Key - Dashboard Testing",
            rate_limit_per_hour=1000,  # High limit for testing
            expires_days=365  # 1 year expiration
        )
        
        print("\n" + "=" * 60)
        print("✅ API KEY CREATED SUCCESSFULLY!")
        print("=" * 60)
        print(f"\n📋 Details:")
        print(f"   • Key ID: {api_key_obj.id}")
        print(f"   • Key Prefix: {api_key_obj.key_prefix}...")
        print(f"   • Name: {api_key_obj.name}")
        print(f"   • User: {user.name} ({user.email})")
        print(f"   • Department: {dept.name}")
        print(f"   • Rate Limit: {api_key_obj.rate_limit_per_hour} requests/hour")
        print(f"   • Expires: {api_key_obj.expires_at.strftime('%Y-%m-%d') if api_key_obj.expires_at else 'Never'}")
        
        print(f"\n🔑 YOUR API KEY (copy this now!):")
        print(f"\n   {full_key}\n")
        
        print("⚠️  IMPORTANT: Save this key now! It will NOT be shown again.")
        print("\n📝 To use this key, add it to your requests as:")
        print(f'   Authorization: Bearer {full_key}')
        
        print("\n🧪 Test it with:")
        print(f'   curl -H "Authorization: Bearer {full_key}" https://aelira.ai/api/health')
        
        print("\n" + "=" * 60)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()

