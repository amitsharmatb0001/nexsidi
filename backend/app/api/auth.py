from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import UserCreate, UserResponse, UserLogin, Token
from app.auth import create_user, authenticate_user, get_user_by_email
from app.core.security import create_access_token
from app.dependencies import get_current_user
from app.models import User

# Create router for auth endpoints
# All routes here will be prefixed with /api/auth (set in main.py)
router = APIRouter()


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    user_data: UserCreate,
    db: Session = Depends(get_db)
):
    """
    Register new user account
    
    Flow:
    1. Check if email already exists
    2. Create user with hashed password
    3. Return user data (no token yet - they must login)
    
    Why separate signup and login?
    - Security: Forces user to actually know password
    - UX: Can add email verification step here
    - Flexibility: Can add OAuth later without changing flow
    
    Status codes:
    - 201: Successfully created
    - 400: Email already exists
    """
    try:
        # Check duplicate email
        existing_user = get_user_by_email(db, user_data.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        # Create user
        new_user = create_user(db, user_data)
        
        return new_user
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("auth").error(f"Unexpected signup error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during registration"
        )


@router.post("/login", response_model=Token)
async def login(
    login_data: UserLogin,
    db: Session = Depends(get_db)
):
    """
    Authenticate user and return access token
    
    Flow:
    1. Verify email + password
    2. Create JWT token with user_id
    3. Return token
    
    Token lifetime: 7 days (set in core/security.py)
    Client stores this token and sends it with every request
    
    Status codes:
    - 200: Success, returns token
    - 401: Invalid credentials
    """
    try:
        user = authenticate_user(db, login_data.email, login_data.password)
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Create token with user_id in "sub" claim (standard JWT practice)
        access_token = create_access_token(data={"sub": str(user.id)})
        
        return {"access_token": access_token, "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger("auth").error(f"Unexpected login error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during authentication"
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """
    Get currently authenticated user's information
    
    Why this endpoint?
    - Frontend can fetch user data after login
    - Verify token is still valid
    - Get fresh user data (in case profile updated)
    
    Protected endpoint:
    - Requires valid token in Authorization header
    - Returns 401 if token invalid/expired
    
    Usage example:
    GET /api/auth/me
    Headers: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
    """
    return current_user
