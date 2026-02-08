"""
HTTP Rate Limiter for API Endpoints
====================================
Location: app/core/rate_limit.py

This is DIFFERENT from app/services/rate_limiter.py:
- rate_limiter.py = Limits AI API calls (Claude/Gemini)
- rate_limit.py = Limits HTTP requests from users

Purpose: Prevent abuse of API endpoints by limiting requests per user/IP.
"""

from fastapi import HTTPException, Request
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List
import asyncio


class RateLimiter:
    """
    Simple in-memory rate limiter for HTTP endpoints.
    
    How it works:
    - Tracks requests per client (by IP or user ID)
    - Blocks requests when limit exceeded
    - Auto-cleans old request records
    
    For production: Use Redis for distributed rate limiting
    across multiple servers.
    """
    
    def __init__(self, requests_per_minute: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            requests_per_minute: How many requests allowed per client per minute
        """
        self.requests_per_minute = requests_per_minute
        
        # Storage: {client_id: [timestamp1, timestamp2, ...]}
        self.requests: Dict[str, List[datetime]] = defaultdict(list)
        
        # Lock for thread safety
        self.lock = asyncio.Lock()
        
        # Cleanup task
        self._cleanup_task = None
    
    async def check(self, request: Request, user_id: str = None):
        """
        Check if request should be allowed.
        
        Args:
            request: FastAPI request object
            user_id: Optional user ID (if authenticated)
        
        Raises:
            HTTPException: 429 Too Many Requests if limit exceeded
        """
        # Get client identifier
        if user_id:
            client_id = f"user_{user_id}"
        else:
            # Use IP address for unauthenticated requests
            client_id = f"ip_{request.client.host}"
        
        async with self.lock:
            # Get current time
            now = datetime.now()
            minute_ago = now - timedelta(minutes=1)
            
            # Clean old requests for this client
            self.requests[client_id] = [
                req_time for req_time in self.requests[client_id]
                if req_time > minute_ago
            ]
            
            # Check limit
            request_count = len(self.requests[client_id])
            
            if request_count >= self.requests_per_minute:
                # Rate limit exceeded
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": f"Too many requests. Limit: {self.requests_per_minute} requests per minute.",
                        "retry_after": 60,  # seconds
                        "current_usage": request_count
                    }
                )
            
            # Record this request
            self.requests[client_id].append(now)
    
    async def get_usage(self, client_id: str) -> Dict:
        """
        Get current usage for a client.
        
        Args:
            client_id: Client identifier
        
        Returns:
            Dict with usage statistics
        """
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        # Clean old requests
        self.requests[client_id] = [
            req_time for req_time in self.requests[client_id]
            if req_time > minute_ago
        ]
        
        request_count = len(self.requests[client_id])
        
        return {
            "client_id": client_id,
            "requests_last_minute": request_count,
            "limit": self.requests_per_minute,
            "percentage": (request_count / self.requests_per_minute) * 100,
            "remaining": self.requests_per_minute - request_count
        }
    
    async def cleanup_old_records(self):
        """
        Periodic cleanup of old request records.
        Runs in background to prevent memory bloat.
        """
        while True:
            await asyncio.sleep(300)  # Run every 5 minutes
            
            async with self.lock:
                now = datetime.now()
                minute_ago = now - timedelta(minutes=1)
                
                # Clean all clients
                for client_id in list(self.requests.keys()):
                    self.requests[client_id] = [
                        req_time for req_time in self.requests[client_id]
                        if req_time > minute_ago
                    ]
                    
                    # Remove empty entries
                    if not self.requests[client_id]:
                        del self.requests[client_id]
    
    def start_cleanup(self):
        """Start background cleanup task"""
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self.cleanup_old_records())
    
    def stop_cleanup(self):
        """Stop background cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None


# Global instance
rate_limiter = RateLimiter(requests_per_minute=60)


# Convenience function for use in endpoints
async def check_rate_limit(request: Request, user_id: str = None):
    """
    Convenience function to check rate limit.
    
    Usage in API endpoint:
        @app.post("/api/something")
        async def my_endpoint(
            request: Request,
            current_user: User = Depends(get_current_user)
        ):
            await check_rate_limit(request, str(current_user.id))
            # ... rest of endpoint logic
    """
    await rate_limiter.check(request, user_id)