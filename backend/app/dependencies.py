from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.core.security import decode_access_token
from app.auth import get_user_by_id
from uuid import UUID

# OAuth2PasswordBearer tells FastAPI:
# - Tokens come from Authorization header
# - Format: "Bearer <token>"
# - Automatically adds /docs login UI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    CRITICAL DEPENDENCY: Used by ALL protected endpoints
    
    What it does:
    1. Extract token from Authorization header
    2. Decode and validate token
    3. Get user from database
    4. Return user object
    
    If ANY step fails → 401 Unauthorized
    
    Usage in endpoints:
    @app.get("/protected")
    def protected_route(current_user: User = Depends(get_current_user)):
        # current_user is now available!
        return {"message": f"Hello {current_user.full_name}"}
    
    Why HTTPException here?
    - FastAPI automatically converts this to proper HTTP response
    - Status 401 = Unauthorized (token invalid/expired)
    - Client knows to redirect to login
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Decode token
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    
    # Extract user_id from token
    user_id_str: str = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception
    
    # Get user from database
    try:
        user_id = UUID(user_id_str)
    except ValueError:
        raise credentials_exception
    
    user = get_user_by_id(db, user_id)
    if user is None:
        raise credentials_exception
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    
    return user


async def get_current_user_ws(
    token: str,
    db: Session = Depends(get_db)
) -> User:
    """
    WebSocket authentication dependency.
    
    Args:
        token: JWT token from query param (ws://host/path?token=...)
        db: Database session
    
    Returns:
        User: Authenticated user
        
    Raises:
        HTTPException: If token is invalid
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
        
    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception
        
    user = get_user_by_id(db, UUID(user_id))
    if user is None:
        raise credentials_exception
        
    return user


# You can add more dependencies here later:
# - get_current_admin_user (check if user.is_admin)
# - rate_limit_check (prevent API abuse)
# - verify_project_access (check if user owns project)