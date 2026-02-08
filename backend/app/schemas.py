from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Any
from datetime import datetime
from uuid import UUID

# Why Pydantic?
# - Automatic validation (email format, string length, etc)
# - Type hints for IDE autocomplete
# - Automatic API documentation
# - Conversion (string "123" → int 123)


# ============================================
# USER SCHEMAS
# ============================================

class UserCreate(BaseModel):
    """
    What we need to CREATE a new user
    Used in: POST /api/auth/signup
    """
    email: EmailStr  # Validates email format automatically
    password: str = Field(..., min_length=8)  # Minimum 8 characters
    full_name: str = Field(..., min_length=2)
    phone: Optional[str] = None
    
    class Config:
        # Example for API documentation
        json_schema_extra = {
            "example": {
                "email": "amit@yugnex.com",
                "password": "securepass123",
                "full_name": "Amit Sharma",
                "phone": "+91-9876543210"
            }
        }


class UserLogin(BaseModel):
    """
    What we need to LOGIN
    Used in: POST /api/auth/login
    """
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """
    What we RETURN to client (never return password!)
    Used in: Response from signup/login/get-user
    """
    id: UUID
    email: str
    full_name: str
    phone: Optional[str]
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True  # Allow creating from SQLAlchemy models


class Token(BaseModel):
    """
    JWT token response
    Used in: Login response
    """
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    """
    What's inside the decoded token
    Internal use only
    """
    user_id: Optional[str] = None


# ============================================
# PROJECT SCHEMAS
# ============================================

class ProjectCreate(BaseModel):
    """
    Create new project (after user commits to building something)
    """
    title: str = Field(..., min_length=5, max_length=500)
    description: str = Field(..., min_length=10)
    
    class Config:
        json_schema_extra = {
            "example": {
                "title": "E-commerce website for my clothing brand",
                "description": "Need a React-based online store with product catalog, cart, and Razorpay payment integration"
            }
        }


class ProjectResponse(BaseModel):
    """
    Project data returned to client
    """
    id: UUID
    user_id: UUID
    title: str
    description: str
    status: str
    complexity_score: Optional[int]
    quoted_price: Optional[float]
    current_agent: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# ============================================
# CONVERSATION SCHEMAS
# ============================================

class MessageCreate(BaseModel):
    """
    User sends a message to an agent
    Used in: POST /api/chat
    """
    content: str = Field(..., min_length=1)
    project_id: Optional[UUID] = None  # None if chatting before project creation
    
    class Config:
        json_schema_extra = {
            "example": {
                "content": "I want to build a restaurant booking website",
                "project_id": None
            }
        }


class MessageResponse(BaseModel):
    """
    Agent's response to user
    """
    id: UUID
    role: str  # 'user' or 'assistant'
    content: str
    agent_name: str
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============================================
# CHAT CONTEXT RESPONSE
# ============================================

class ChatContextResponse(BaseModel):
    """
    Full conversation history with context
    """
    messages: List[MessageResponse]
    current_agent: Optional[str]
    project_status: Optional[str]
    
    class Config:
        json_schema_extra = {
            "example": {
                "messages": [
                    {"role": "assistant", "content": "Hi! I'm Tilotma..."},
                    {"role": "user", "content": "I need a website"}
                ],
                "current_agent": "tilotma",
                "project_status": "requirements_gathering"
            }
        }


# ============================================
# COMMON SCHEMAS
# ============================================

class SuccessResponse(BaseModel):
    """Standard success response"""
    success: bool = True
    message: str
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    """Standard error response"""
    success: bool = False
    error: str
    message: str
    details: Optional[Any] = None


class QueueResponse(BaseModel):
    """Queue status response"""
    queued: bool
    project_id: str
    queue_position: int
    estimated_wait: str
    message: str
