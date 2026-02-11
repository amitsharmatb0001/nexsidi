"""
REDIS CONNECTION POOLING
========================
Location: app/core/redis.py

Centralized Redis client with connection pooling.
Used by all services to prevent connection exhaustion.
"""

import redis
from app.core.config import settings
import logging

logger = logging.getLogger("redis_pool")

# Connection pool setup
# =====================
# This maintains a set of open connections that can be reused
# instead of opening/closing a connection for every request.
redis_pool = redis.ConnectionPool.from_url(
    settings.redis_url,
    max_connections=10,  # Limit total connections
    decode_responses=True
)

def get_redis_client() -> redis.Redis:
    """
    Get a Redis client from the shared pool.
    
    Returns:
        redis.Redis client instance
    """
    return redis.Redis(connection_pool=redis_pool)

# Global client for convenience
redis_client = get_redis_client()

def verify_redis_connection():
    """Verify that Redis is reachable."""
    try:
        print(f"DEBUG: Verifying Redis connection to: {settings.redis_url}")
        # Use a separate client with short timeout for verification
        client = redis.Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5)
        client.ping()
        logger.info("[OK] Redis connection pool verified")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Redis connection failed: {e}")
        return False
