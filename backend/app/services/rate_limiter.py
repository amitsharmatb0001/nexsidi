"""
Rate Limiter - API Call Rate Limiting
"""

import asyncio
import time
import logging
from collections import deque
from typing import Dict

logger = logging.getLogger("rate_limiter")


class AIRateLimiter:
    """Rate limiter for AI API calls"""
    
    def __init__(self):
        # Limits per minute
        self.claude_limit = 45
        self.gemini_limit = 55
        
        # Call history (timestamps)
        self.claude_calls = deque()
        self.gemini_calls = deque()
        
        # Locks
        self.locks = {
            'claude': asyncio.Lock(),
            'gemini': asyncio.Lock()
        }
    
    async def acquire(self, provider: str):
        """
        Wait for rate limit permission.
        Blocks if at limit.
        """
        async with self.locks[provider]:
            if provider == 'claude':
                limit = self.claude_limit
                window = 60
                calls = self.claude_calls
            else:
                limit = self.gemini_limit
                window = 60
                calls = self.gemini_calls
            
            # Remove old calls
            current_time = time.time()
            while calls and calls[0] < current_time - window:
                calls.popleft()
            
            # Check if at limit
            if len(calls) >= limit:
                oldest = calls[0]
                wait_time = (oldest + window) - current_time
                
                if wait_time > 0:
                    logger.info(f"⏳ Rate limit for {provider}. Waiting {wait_time:.1f}s...")
                    await asyncio.sleep(wait_time)
            
            # Record this call
            calls.append(current_time)
    
    async def get_current_usage(self, provider: str) -> Dict:
        """Get current usage statistics"""
        calls = self.claude_calls if provider == 'claude' else self.gemini_calls
        limit = self.claude_limit if provider == 'claude' else self.gemini_limit
        
        current_time = time.time()
        recent = sum(1 for t in calls if t > current_time - 60)
        
        return {
            'provider': provider,
            'calls_last_minute': recent,
            'limit': limit,
            'percentage': (recent / limit) * 100
        }


# Global instance
rate_limiter = AIRateLimiter()
