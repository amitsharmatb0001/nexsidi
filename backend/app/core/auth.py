# app/core/auth.py — Single source for all auth

from fastapi import Depends, HTTPException
from app.core.security import verify_password, create_access_token, decode_access_token as decode_token
from app.dependencies import get_current_user
from app.models import User

# Admin check (you'll need this soon)
async def get_current_admin(current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
