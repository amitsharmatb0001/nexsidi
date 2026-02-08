from sqlalchemy.orm import Session
from app.models import User
from app.schemas import UserCreate, UserLogin
from app.core.security import verify_password, get_password_hash, create_access_token
from typing import Optional
from uuid import UUID

# Why separate this from API routes?
# - Reusability (can be used by multiple endpoints or background tasks)
# - Testing (easy to test functions without HTTP layer)
# - Clarity (business logic separate from HTTP handling)


def create_user(db: Session, user_data: UserCreate) -> User:
    """
    Create new user account
    
    Steps:
    1. Hash the password (NEVER store plain text!)
    2. Create User object
    3. Save to database
    4. Return user object
    
    Note: This doesn't check if email exists!
    That's handled in the API layer (see api/auth.py)
    """
    hashed_password = get_password_hash(user_data.password)
    
    db_user = User(
        email=user_data.email,
        hashed_password=hashed_password,
        full_name=user_data.full_name,
        phone=user_data.phone,
        is_active=True
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)  # Get the id and created_at from database
    
    return db_user


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """
    Verify user credentials
    
    Returns:
    - User object if credentials valid
    - None if email doesn't exist or password wrong
    
    Security note:
    - We return None for both "user not found" and "wrong password"
    - This prevents attackers from knowing which emails exist
    """
    user = db.query(User).filter(User.email == email).first()
    
    if not user:
        return None
    
    if not verify_password(password, user.hashed_password):
        return None
    
    return user


def get_user_by_id(db: Session, user_id: UUID) -> Optional[User]:
    """
    Fetch user by ID
    Used by: Dependency injection to get current user from token
    """
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """
    Check if email already exists
    Used by: Signup endpoint to prevent duplicate accounts
    """
    return db.query(User).filter(User.email == email).first()
